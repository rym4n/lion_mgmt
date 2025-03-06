# coding=utf-8
import base64
import datetime
import re
from typing import Union

import jwt
from sanic import Request, HTTPResponse
from tortoise import fields
from tortoise.exceptions import DoesNotExist
from tortoise.validators import RegexValidator

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.utils import is_expired, get_ip, get_expiration_date, get_code
from models.base import BaseModel
from settings import SECRET, SESSION_ENCODING_ALGORITHM, SESSION_DOMAIN, MAX_CHALLENGE_ATTEMPTS, TWO_STEP_SESSION_EXPIRATION, AUTHENTICATION_SESSION_EXPIRATION, AUTHENTICATION_REFRESH_EXPIRATION, SESSION_HTTPONLY, SESSION_SAMESITE, SESSION_SECURE


class Account(BaseModel):
    """
    账号模型，包含以下属性：用户名、邮箱、手机号、密码、OAuth ID、是否禁用、角色
    """

    username: str = fields.CharField(
        unique=True,
        max_length=32,
    )
    email: str = fields.CharField(
        unique=True,
        max_length=255,
        validators=[
            RegexValidator(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", re.I)
        ],
    )
    phone: str = fields.CharField(
        unique=True,
        max_length=15,
        null=True,
        validators=[
            RegexValidator(r"^(\+\d{1,2}\s)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$", re.I)
        ],
    )
    password: str = fields.CharField(max_length=255)
    oauth_id: str = fields.CharField(unique=True, null=True, max_length=255)
    disabled: bool = fields.BooleanField(default=False)
    roles: fields.ManyToManyRelation["Role"] = fields.ManyToManyField("models.Role", through="account_role")

    def validate(self) -> None:
        if self.deleted:
            raise CustomError(ErrorCode.AccountNotFound)
        elif self.disabled:
            raise CustomError(ErrorCode.AccountDisabled)

    async def disable(self):
        if self.disabled:
            raise CustomError(ErrorCode.AccountDisabled)
        else:
            self.disabled = True
            await self.save(update_fields=["disabled"])

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "email": self.email,
            "username": self.username,
            "phone": self.phone,
            "disabled": self.disabled
        }

    @staticmethod
    async def get_via_username(username: str):
        """
        根据用户名获取用户信息
        """
        try:
            return await Account.filter(username=username, deleted=False).get()
        except:
            raise CustomError(ErrorCode.AccountNotFound)

    @staticmethod
    async def get_via_header(request: Request):
        if request.headers.get("Authorization"):
            authorization_type, credentials = request.headers.get("Authorization").split()
            if authorization_type == "Basic":
                username, password = base64.b64decode(credentials).decode().split(":")
                account = await Account.get_via_username(username)
                return account, password
            else:
                raise CustomError(ErrorCode.CredentialsError)
        else:
            raise CustomError(ErrorCode.CredentialsError)


class Session(BaseModel):
    """
    记录会话信息
    """

    expiration_date = fields.DatetimeField(null=True)
    active: bool = fields.BooleanField(default=True)
    ip: str = fields.CharField(max_length=16)
    bearer: fields.ForeignKeyRelation["Account"] = fields.ForeignKeyField("models.Account", null=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def validate(self) -> None:
        """
        Raises an error with respect to session state.

        Raises:
            DeletedError
            ExpiredError
            DeactivatedError
        """
        if self.deleted:
            raise CustomError(ErrorCode.SessionDeleted)
        elif not self.active:
            raise CustomError(ErrorCode.SessionDeactivated)
        elif is_expired(self.expiration_date):
            raise CustomError(ErrorCode.SessionExpired)

    async def deactivate(self) -> None:
        """
        Renders session deactivated and therefor unusable.

        Raises:
            DeactivatedError
        """
        if self.active:
            self.active = False
            await self.save(update_fields=["active"])
        else:
            raise CustomError(ErrorCode.SessionDeactivated)

    def encode(self, response: HTTPResponse) -> None:
        """
        生成会话token
        """
        encoded_session = jwt.encode(
            {
                "id": self.id,
                "ctime": str(self.ctime),
                "expiration_date": str(self.expiration_date),
                "bearer": self.bearer.id if isinstance(self.bearer, Account) else None,
                "ip": self.ip,
            },
            SECRET,
            SESSION_ENCODING_ALGORITHM,
        )
        response.cookies.add_cookie(
            f"aioe_{self.__class__.__name__[:7].lower()}",
            str(encoded_session),
            httponly=SESSION_HTTPONLY,
            samesite=SESSION_SAMESITE,
            secure=SESSION_SECURE,
            domain=SESSION_DOMAIN,
            expires=getattr(self, "refresh_expiration_date", None) or self.expiration_date,
        )

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "expiration_date": str(self.expiration_date),
            "bearer": self.bearer.id if isinstance(self.bearer, Account) else None,
            "active": self.active,
        }

    @property
    def anonymous(self) -> bool:
        """
        判断是否是匿名会话
        """
        return self.bearer is None

    @classmethod
    async def new(
            cls,
            request: Request,
            account: Account,
            **kwargs: dict[str, Union[int, str, bool, float, list, dict]],
    ):
        """
        新建会话
        """
        raise NotImplementedError

    @classmethod
    async def get_associated(cls, account: Account):
        """
        获取账号所关联的会话信息
        """
        sessions = await cls.filter(bearer=account, deleted=False).all()
        if not sessions:
            raise CustomError(ErrorCode.NotFound)
        return sessions

    @classmethod
    def decode_raw(cls, request: Request) -> dict:
        """
        JWT 解码为 dict
        """
        cookie = request.cookies.get(f"aioe_{cls.__name__[:7].lower()}")
        try:
            if not cookie:
                raise CustomError(ErrorCode.CredentialsError)
            else:
                return jwt.decode(cookie, SECRET, SESSION_ENCODING_ALGORITHM)
        except:
            raise CustomError(ErrorCode.JWTDecodeError)

    @classmethod
    async def decode(cls, request: Request):
        """
        JWT 解码为 session
        """
        try:
            decoded_raw = cls.decode_raw(request)
            decoded_session = (
                await cls.filter(id=decoded_raw["id"]).prefetch_related("bearer").get()
            )
            request.ctx.session = decoded_session
        except DoesNotExist:
            raise CustomError(ErrorCode.NotFound)
        return decoded_session

    class Meta:
        abstract = True


class VerificationSession(Session):
    """
    用于客户端验证挑战，需要某种形式的代码或密钥。
    """

    attempts: int = fields.IntField(default=0)
    code: str = fields.CharField(max_length=6, null=True)

    async def check_code(self, code: str) -> None:
        if not code or self.code != code.upper():
            self.attempts += 1
            if self.attempts < MAX_CHALLENGE_ATTEMPTS:
                await self.save(update_fields=["attempts"])
                raise CustomError(ErrorCode.MFAError)
            else:
                raise CustomError(ErrorCode.MaxChallenge)
        else:
            await self.deactivate()

    @classmethod
    async def new(
            cls,
            request: Request,
            account: Account,
            **kwargs: Union[int, str, bool, float, list, dict],
    ):
        raise NotImplementedError

    class Meta:
        abstract = True


class TwoStepSession(VerificationSession):
    """二次认证会话信息"""

    @classmethod
    async def new(
            cls,
            request: Request,
            account: Account,
            **kwargs: Union[int, str, bool, float, list, dict],
    ):
        return await cls.create(
            **kwargs,
            ip=get_ip(request),
            bearer=account,
            expiration_date=get_expiration_date(TWO_STEP_SESSION_EXPIRATION),
            code=get_code(True),
        )

    class Meta:
        table = "two_step_session"


class AuthenticationSession(Session):
    """
    认证会话信息
    """

    refresh_expiration_date: datetime.datetime = fields.DatetimeField(null=True)
    requires_second_factor: bool = fields.BooleanField(default=False)
    is_refresh: bool = False

    def validate(self) -> None:
        super().validate()
        if self.requires_second_factor:
            raise CustomError(ErrorCode.SecondFactorRequired)

    async def refresh(self, request: Request):
        """
        Refreshes session if within refresh date.
        """
        if self.active and not is_expired(self.refresh_expiration_date):
            await self.deactivate()
            return await self.new(request, self.bearer)
        else:
            raise CustomError(ErrorCode.SessionExpired)

    @classmethod
    async def new(
            cls,
            request: Request,
            account: Account = None,
            **kwargs: Union[int, str, bool, float, list, dict],
    ):
        authentication_session = await cls.create(
            **kwargs,
            bearer=account,
            ip=get_ip(request),
            expiration_date=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
            refresh_expiration_date=get_expiration_date(AUTHENTICATION_REFRESH_EXPIRATION),
        )
        return authentication_session

    class Meta:
        table = "authentication_session"


class Role(BaseModel):
    """
    角色权限
    """

    name: str = fields.CharField(unique=True, max_length=255)
    description: str = fields.CharField(max_length=255, null=True)
    permissions: list[str] = fields.JSONField(null=True)

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "name": self.name,
            "description": self.description,
            "permissions": self.permissions,
        }

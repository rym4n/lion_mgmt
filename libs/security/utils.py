# coding=utf-8

import datetime
import random
from string import ascii_uppercase, digits

from argon2 import PasswordHasher
from sanic.request import Request
from sanic.utils import str_to_bool as sanic_str_to_bool

password_hasher: PasswordHasher = PasswordHasher()


def get_ip(request: Request) -> str:
    return request.remote_addr or request.ip


def get_code(digits_only: bool = False) -> str:
    return "".join(
        random.choice(("" if digits_only else ascii_uppercase) + digits)
        for _ in range(6)
    )


def is_expired(date):
    return date and datetime.datetime.now(datetime.timezone.utc) >= date


def get_expiration_date(seconds: int) -> datetime.datetime:
    return (
        datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=seconds)
        if seconds > 0
        else None
    )


def str_to_bool(val: str) -> bool:
    return sanic_str_to_bool(val) if val else False

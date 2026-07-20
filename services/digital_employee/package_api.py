# coding=utf-8
from ipaddress import ip_address

from sanic import Request

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.authorization import check_permissions

from .package_format import (
    PackageFormatError,
    UnsupportedPackageTypeError,
    inspect_package,
)
from .package_importer import ImportResult, import_package


def _package_type_or_error(file_bytes: bytes) -> str:
    try:
        package_type, _ = inspect_package(file_bytes)
    except UnsupportedPackageTypeError as exc:
        raise CustomError(ErrorCode.PACKAGE_TYPE_UNSUPPORTED) from exc
    except PackageFormatError as exc:
        raise CustomError(ErrorCode.PACKAGE_INVALID) from exc
    return package_type


async def _require_package_import_permission(
    request: Request, file_bytes: bytes
) -> None:
    permission = (
        "digital_employee_skill:c"
        if _package_type_or_error(file_bytes) == "skill"
        else "digital_employee:c"
    )
    await check_permissions(request, permission)


def _package_file_bytes(request: Request) -> bytes:
    uploaded = request.files.get("file") if request.files else None
    if uploaded:
        return uploaded.body
    if request.body:
        return request.body
    raise CustomError(ErrorCode.PACKAGE_INVALID)


def request_is_loopback(request: Request) -> bool:
    try:
        address = ip_address(getattr(request, "ip", None))
    except (TypeError, ValueError):
        return False
    mapped_address = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or bool(mapped_address and mapped_address.is_loopback)


def _tenant_id(request: Request) -> str | None:
    return getattr(request.ctx, "session", {}).get("tenant_id")


def _operator_id(request: Request) -> str | None:
    account = getattr(request.ctx, "session", {}).get("account_info")
    if isinstance(account, dict):
        return account.get("id")
    return getattr(account, "id", None)


async def import_package_from_request(
    request: Request, *, is_builtin: bool
) -> ImportResult:
    file_bytes = _package_file_bytes(request)
    await _require_package_import_permission(request, file_bytes)
    return await import_package(
        file_bytes,
        _tenant_id(request),
        is_builtin=is_builtin,
        operator_id=_operator_id(request),
        request=request,
    )

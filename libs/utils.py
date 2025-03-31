# coding=utf-8

import datetime
import ipaddress
import os.path
import random
import zipfile
from io import BytesIO
from pathlib import Path, PurePath
from string import ascii_uppercase, digits

import aiofile
from aiofile import async_open
from argon2 import PasswordHasher
from sanic.utils import str_to_bool as sanic_str_to_bool

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError

password_hasher: PasswordHasher = PasswordHasher()


def get_code(digits_only: bool = False) -> str:
    return "".join(
        random.choice(("" if digits_only else ascii_uppercase) + digits)
        for _ in range(6)
    )


def get_expiration_date(seconds: int) -> datetime.datetime:
    return (
        datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=seconds)
        if seconds > 0
        else None
    )


def str_to_bool(val: str) -> bool:
    return sanic_str_to_bool(val) if val else False


def ip_in_list(ip_str: str, ip_list: list) -> bool:
    # 将目标 IP 转换为 ipaddress 对象
    target_ip = ipaddress.ip_address(ip_str)

    # 分割逗号分隔的 IP 字符串，并遍历每个 IP 或 CIDR
    for ip_item in ip_list:
        ip_item = ip_item.strip()  # 去除可能的空白字符
        try:
            # 如果是 CIDR 格式
            if '/' in ip_item:
                network = ipaddress.ip_network(ip_item, strict=False)
                if target_ip in network:
                    return True
            else:
                # 单个 IP 比较
                if target_ip == ipaddress.ip_address(ip_item):
                    return True
        except ValueError as e:
            # 如果IP格式不合法，继续下一项
            continue
    return False


async def unzip(zip_data, extra_path):
    """
    解压 ZIP 文件，需要传入zip文件字节流数据已经存储路径
    """
    zip_buffer = BytesIO(zip_data)

    if not zipfile.is_zipfile(zip_buffer):
        raise CustomError(ErrorCode.ZipFileFormatError)

    original_zip_path = os.path.join(str(extra_path), "SourceFile.zip")
    async with aiofile.AIOFile(original_zip_path, 'wb') as zip_file:
        await zip_file.write(zip_data)

    with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
        files_info = zip_ref.infolist()

        for in_file in files_info:
            file_name = in_file.filename
            unpack_filename_path = Path(str(PurePath(extra_path, file_name)))

            if in_file.is_dir():
                unpack_filename_path.mkdir(parents=True, exist_ok=True)
            else:
                unpack_filename_path.parent.mkdir(parents=True, exist_ok=True)
                with zip_ref.open(file_name) as file_data:
                    async with async_open(str(unpack_filename_path), 'wb+') as out:
                        await out.write(file_data.read())
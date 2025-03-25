# coding=utf-8

import datetime
import ipaddress
import random
from string import ascii_uppercase, digits

from argon2 import PasswordHasher
from sanic.utils import str_to_bool as sanic_str_to_bool

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

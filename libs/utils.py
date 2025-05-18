# coding=utf-8
import base64
import datetime
import ipaddress
import os.path
import random
import socket
import time
import uuid
import zipfile
from io import BytesIO
from pathlib import Path, PurePath
from string import ascii_uppercase, digits

import aiofile
import jwt
import orjson
import psutil
from aiokafka import AIOKafkaProducer
from argon2 import PasswordHasher
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import RY_KEY

password_hasher: PasswordHasher = PasswordHasher()


def clean_ip(ip_str: str) -> str:
    # 去除方括号、引号及前后空白
    return ip_str.strip().strip('"').strip("[]")


def get_client_ip(request) -> str:
    """
    从 Sanic Request 中提取真实客户端 IP，支持 IPv4/IPv6 和多级代理。

    优先级：
      1. X-Forwarded-For（取第一个）
      2. X-Real-IP
      3. request.remote_addr

    会自动去除方括号 []、引号 " 以及多余空白。
    """

    # 1. 多级代理时的原始客户端 IP
    xff = request.headers.get('x-forwarded-for')
    if xff:
        # 可能形如 "203.0.113.5, 2001:db8::1"
        first = xff.split(',')[0]
        return clean_ip(first)

    # 2. 单级代理时 Nginx 设的真实 IP
    xri = request.headers.get('x-real-ip')
    if xri:
        return clean_ip(xri)

    # 3. 回退到 request.remote_addr（通常是 Nginx 的 IP）
    return clean_ip(request.client_ip)


def gen_random_code(digits_only: bool = True, length=6) -> str:
    return "".join(
        random.choice(("" if digits_only else ascii_uppercase) + digits)
        for _ in range(length)
    )


def get_expiration_date(seconds: int) -> datetime.datetime:
    return (
        datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=seconds)
        if seconds > 0
        else None
    )


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
        except ValueError:
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
                    async with aiofile.async_open(str(unpack_filename_path), 'wb+') as out:
                        await out.write(file_data.read())


def gen_order_version(length=5):
    """
    生成版本号，按时间顺序
    """
    number = gen_random_code(True, length)
    times = time.strftime("%Y%m%d%H%M%S", time.localtime(time.time()))
    return str(times) + '' + str(number)


async def send_kafka_message(kafka_bootstrap_servers, topic, message):
    """
    发送kafka消息
    """
    async with AIOKafkaProducer(
            bootstrap_servers=kafka_bootstrap_servers,
            value_serializer=lambda m: orjson.dumps(m)
    ) as producer:
        await producer.start()
        await producer.send_and_wait(topic, message)


def strip_timezone_to_seconds(dt_str):
    dt = datetime.datetime.fromisoformat(str(dt_str))
    dt_local = dt.astimezone()
    return dt_local.strftime("%Y-%m-%d %H:%M:%S")


# 计算密文块大小（密钥长度 / 8）
def get_cipher_block_size(privkey: rsa.RSAPrivateKey) -> int:
    return privkey.key_size // 8  # 4096 位 → 512 字节


# 分块解密函数
def rsa_chunk_decrypt(ciphertext: bytes, rsa_priv_key: rsa.RSAPrivateKey) -> bytes:
    """
    ciphertext: 整段拼接好的 RSA 分块加密结果
    rsa_priv_key: 加载后的 RSAPrivateKey 对象
    return: 解密后的原始明文 bytes
    """
    block_size = get_cipher_block_size(rsa_priv_key)
    if len(ciphertext) % block_size != 0:
        raise ValueError("密文长度不是 RSA 块大小的整数倍，可能被篡改或截断")

    blocks = [
        ciphertext[i:i + block_size]
        for i in range(0, len(ciphertext), block_size)
    ]

    plaintext = b""
    for idx, block in enumerate(blocks, 1):
        try:
            decrypted_block = rsa_priv_key.decrypt(
                block,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
            plaintext += decrypted_block
        except ValueError as e:
            print(f"[Error] 解密第 {idx} 块失败，块大小 {len(block)} 字节:", e)
            raise
    return plaintext


def verify_license(data: bytes):
    try:
        lic_enc = base64.b32decode(data)
        pkey = serialization.load_pem_private_key(base64.b64decode(RY_KEY.encode()), password=None)
        decrypted = rsa_chunk_decrypt(lic_enc, pkey)
        machine_id = generate_mac_uuid()
        lic = jwt.decode(decrypted, str(machine_id), "HS256")
    except Exception:
        raise CustomError(ErrorCode.InvalidLicenseError)

    if lic.get("expireTime", 0) > int(time.time() * 1000):
        return lic
    else:
        raise CustomError(ErrorCode.LicenseExpired)


def get_default_interface():
    # 方法1或方法2的实现（此处以psutil为例）
    for interface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address != "127.0.0.1":
                return interface
    return None


def get_mac_address(interface):
    addrs = psutil.net_if_addrs().get(interface, [])
    for addr in addrs:
        if addr.family == psutil.AF_LINK:
            return addr.address.replace("-", ":").upper()
    return None


def generate_mac_uuid():
    interface = get_default_interface()
    mac_address = get_mac_address(interface)
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, mac_address))

# coding=utf-8
import asyncio
import base64
import datetime
import ipaddress
import os
import os.path
import random
import secrets
import subprocess
import time
import uuid
import zipfile
from io import BytesIO
from pathlib import Path, PurePath
from string import ascii_uppercase, digits

import aiofile
import jwt
import psutil
from aiokafka import AIOKafkaConsumer, TopicPartition
from argon2 import PasswordHasher
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import RY_RES_KEY

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


def validate_ips(ip_str):
    for ip in ip_str.split(','):
        cleaned_ip = ip.strip()
        if not cleaned_ip:
            return False
        try:
            if '/' in cleaned_ip:
                ipaddress.IPv4Network(cleaned_ip, strict=True)
            else:
                ipaddress.IPv4Address(cleaned_ip)
        except ValueError:
            return False
    return True


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
            file_new_name = fix_zip_filename(file_name)
            unpack_filename_path = Path(str(PurePath(extra_path, file_new_name)))

            if in_file.is_dir():
                unpack_filename_path.mkdir(parents=True, exist_ok=True)
            else:
                unpack_filename_path.parent.mkdir(parents=True, exist_ok=True)
                with zip_ref.open(file_name) as file_data:
                    async with aiofile.async_open(str(unpack_filename_path), 'wb+') as out:
                        await out.write(file_data.read())


def fix_zip_filename(filename: str) -> str:
    """
    修复中文编码问题
    """
    try:
        return filename.encode('cp437').decode('gbk')
    except UnicodeDecodeError:
        try:
            return filename.encode('cp437').decode('utf-8')
        except:
            return filename


def gen_order_version(length=5):
    """
    生成版本号，按时间顺序
    """
    number = gen_random_code(True, length)
    times = time.strftime("%Y%m%d%H%M%S", time.localtime(time.time()))
    return str(times) + '' + str(number)


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
        pkey = serialization.load_pem_private_key(base64.b64decode('AIOE_LICENSE_KEY'), password=None)
        decrypted = rsa_chunk_decrypt(lic_enc, pkey)
        machine_id = generate_mac_uuid()
        lic = jwt.decode(decrypted, str(machine_id), "HS256")
        if lic.get("expireTime", 0) > int(time.time() * 1000):
            for m in lic['moduleList']:
                m['expireTime'] = lic['grouplist'][m['group']]['expireTime']
            return lic
        else:
            raise CustomError(ErrorCode.LicenseExpired)
    except Exception:
        raise CustomError(ErrorCode.InvalidLicenseError)


def generate_mac_uuid():
    for _, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == psutil.AF_LINK and addr.address != "00:00:00:00:00:00":
                mac_address = addr.address.replace("-", ":").upper()
                return str(uuid.uuid5(uuid.NAMESPACE_DNS, mac_address))
    return ''


# 获取kafka指定主题最新消息
async def fetch_latest_messages(bootstrap_servers, topic, num_messages=10):
    consumer = AIOKafkaConsumer(
        bootstrap_servers=bootstrap_servers,
        group_id=None,
        enable_auto_commit=False,
        auto_offset_reset="latest"
    )

    try:
        await consumer.start()

        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            return f"主题 {topic} 不存在或无可访问分区"

        tps = [TopicPartition(topic, p) for p in partitions]
        consumer.assign(tps)

        end_offsets = await consumer.end_offsets(tps)

        for tp in tps:
            latest_offset = end_offsets[tp]
            seek_offset = max(0, latest_offset - num_messages)
            consumer.seek(tp, seek_offset)

        messages = []
        batch = await consumer.getmany(max_records=num_messages, timeout_ms=1000)
        for tp_msgs in batch.values():
            for msg in tp_msgs:
                messages.append(msg)
                if len(messages) >= num_messages:
                    break

        return [m.value.decode(errors='ignore') for m in messages]

    finally:
        try:
            await consumer.stop()
        except asyncio.CancelledError:
            logger.info("停止消费者时被取消")


def run_linux_command(command):
    """
    在 Linux 上执行命令，并返回执行结果。

    :param command: 字符串或字符串列表，表示要执行的命令。
    :return: 字典，包含 stdout、stderr 和 returncode。
    """
    result = subprocess.run(
        command,
        shell=isinstance(command, str),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    return {
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "code": result.returncode
    }


def derive_key(password: str, salt: bytes) -> bytes:
    """使用PBKDF2HMAC从密码派生密钥"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=5000,
        backend=default_backend()
    )
    return kdf.derive(password.encode())


def encrypt_aes256_gcm(plaintext: str) -> str:
    """AES-GCM加密返回字典格式结果"""
    # 生成随机盐
    salt = secrets.token_bytes(16)
    # 生成随机nonce (12字节是GCM推荐长度)
    nonce = secrets.token_bytes(12)
    key = derive_key(RY_RES_KEY, salt)

    # 创建加密器并加密数据
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext.encode()) + encryptor.finalize()
    logger.info(ciphertext + b'|||' + nonce + b'|||' + salt + b'|||' + encryptor.tag)

    return base64.b64encode(ciphertext + b'|||' + nonce + b'|||' + salt + b'|||' + encryptor.tag).decode()


def decrypt_aes256_gcm(encrypted_data: str) -> str:
    """AES-GCM解密，输入为字典格式"""
    try:
        logger.info(encrypted_data)
        data = base64.b64decode(encrypted_data.encode())
        if b'|||' not in data:
            return data.decode()
        ciphertext, nonce, salt, tag = data.split(b'|||')

        key = derive_key(RY_RES_KEY, salt)
        # 创建解密器
        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag), backend=default_backend())
        decryptor = cipher.decryptor()

        # 解密并返回UTF-8字符串
        decrypted = decryptor.update(ciphertext) + decryptor.finalize()

        return decrypted.decode()

    except (KeyError, InvalidTag, UnicodeDecodeError) as e:
        # 处理可能的错误：数据篡改、密码错误或格式错误
        raise ValueError("解密失败: 密码错误或数据损坏") from e

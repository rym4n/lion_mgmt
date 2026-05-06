# coding=utf-8
import asyncio
import base64
import binascii
import datetime
import hashlib
import ipaddress
import os
import random
import secrets
import subprocess
import time
import traceback
import uuid
from string import ascii_uppercase, digits
from typing import Optional

import aioboto3
import httpx
import jwt
import orjson
import psutil
from aiokafka import AIOKafkaConsumer, TopicPartition
from argon2 import PasswordHasher
from botocore.config import Config
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import FS_AK, FS_SK, REAL_IP_HEADER, RY_RES_KEY, S3_HOST, VL_HOST

password_hasher: PasswordHasher = PasswordHasher()


def get_client_ip(request) -> str:
    xri = request.headers.get(REAL_IP_HEADER)
    xff = request.headers.get("X-Forwarded-For")
    if xri:
        return xri
    elif xff:
        return xff.split(",")[0].strip()
    else:
        return request.ip


def gen_random_code(length=6, digits_only: bool = True) -> str:
    return "".join(random.choice(("" if digits_only else ascii_uppercase) + digits) for _ in range(length))


def get_expiration_date(seconds: int) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=seconds)


def validate_ips(ip_str):
    for ip in ip_str.split(","):
        cleaned_ip = ip.strip()
        if not cleaned_ip:
            return False
        try:
            if "/" in cleaned_ip:
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
            if "/" in ip_item:
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


def gen_run_id(length=4):
    """
    生成版本号，按时间顺序
    """
    number = gen_random_code(length)
    times = time.strftime("%Y%m%d%H%M%S", time.localtime(time.time()))
    return str(times) + "" + str(number)


def strip_timezone_to_seconds(dt_str):
    if not dt_str:
        return None
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

    blocks = [ciphertext[i : i + block_size] for i in range(0, len(ciphertext), block_size)]

    plaintext = b""
    for idx, block in enumerate(blocks, 1):
        try:
            decrypted_block = rsa_priv_key.decrypt(
                block,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            plaintext += decrypted_block
        except ValueError as e:
            print(f"[Error] 解密第 {idx} 块失败，块大小 {len(block)} 字节:", e)
            raise
    return plaintext


def verify_license(data: bytes):
    # 2026-05-01 00:00:00 UTC (毫秒)，此日期前签发的 License 使用旧 MAC 方案
    _LEGACY_CUTOFF_MS = 1777564800000
    try:
        lic_enc = base64.b32decode(data)
        pkey = serialization.load_pem_private_key(base64.b64decode("AIOE_LICENSE_KEY"), password=None)
        decrypted = rsa_chunk_decrypt(lic_enc, pkey)

        # 先解码获取 createTime（不验签）
        lic_unverified = jwt.decode(decrypted, options={"verify_signature": False})
        create_time = lic_unverified.get("createTime", 0)

        # 调试日志：输出 License 创建时间
        logger.info(f"License createTime: {create_time} (截止时间: {_LEGACY_CUTOFF_MS})")
        logger.info(f"使用{'旧' if create_time < _LEGACY_CUTOFF_MS else '新'}验证方案")

        if create_time < _LEGACY_CUTOFF_MS:
            # 旧 License：使用 MAC 地址方案
            logger.info("使用 MAC 地址验证方案")
            machine_id = generate_mac_uuid()
            if not machine_id:
                raise CustomError(ErrorCode.MACHINE_CODE_MISMATCH)
        else:
            # 新 License：使用宿主机指纹方案
            logger.info("使用宿主机指纹验证方案")
            machine_id = generate_fingerprint()

        lic = jwt.decode(decrypted, str(machine_id), "HS256")
        if lic.get("expireTime", 0) > int(time.time() * 1000):
            for m in lic["moduleList"]:
                m["expireTime"] = lic["grouplist"][m["group"]]["expireTime"]
            return lic
        else:
            raise CustomError(ErrorCode.LICENSE_EXPIRED)
    except CustomError:
        raise
    except jwt.InvalidSignatureError:
        raise CustomError(ErrorCode.MACHINE_CODE_MISMATCH)
    except Exception:
        raise CustomError(ErrorCode.INVALID_LICENSE_ERROR)


def generate_mac_uuid():
    for _, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == psutil.AF_LINK and addr.address != "00:00:00:00:00:00":
                mac_address = addr.address.replace("-", ":").upper()
                return str(uuid.uuid5(uuid.NAMESPACE_DNS, mac_address))
    return ""


def extract_stable_fields(content: str, fields: list[str]) -> str:
    """
    从文本内容中提取指定字段的稳定值

    Args:
        content: 原始文本内容（如 /proc/cpuinfo）
        fields: 要提取的字段名列表

    Returns:
        提取字段的拼接字符串
    """
    result = []
    for field in fields:
        # 查找字段行（格式: "field_name : value"）
        for line in content.splitlines():
            if line.strip().lower().startswith(field.lower()):
                # 提取冒号后的值
                if ":" in line:
                    value = line.split(":", 1)[1].strip()
                    result.append(f"{field}={value}")
                    break
    return "|".join(result)


def generate_fingerprint() -> str:
    """
    生成稳定的机器指纹（容器环境适配）
    """
    machine_id_path = "/app/.host-identity/machine-id"
    product_uuid_path = "/app/.host-identity/product_uuid"

    if not os.path.exists(machine_id_path):
        logger.error(f"License机器指纹锚点文件缺失: {machine_id_path}")
        raise CustomError(ErrorCode.ANCHOR_NOT_FOUND)

    if not os.path.exists(product_uuid_path):
        logger.error(f"License机器指纹锚点文件缺失: {product_uuid_path}")
        raise CustomError(ErrorCode.ANCHOR_NOT_FOUND)

    with open(machine_id_path, "r") as f:
        machine_id = f.read().strip()
    factor_machine_id = hashlib.sha256(machine_id.encode()).hexdigest()[:16]

    with open(product_uuid_path, "r") as f:
        product_uuid = f.read().strip()
    factor_product_uuid = hashlib.sha256(product_uuid.encode()).hexdigest()[:16]

    with open("/proc/cpuinfo", "r") as f:
        cpu_info = f.read()
    cpu_sig = extract_stable_fields(cpu_info, ["model name", "cpu cores", "flags"])
    factor_cpu = hashlib.sha256(cpu_sig.encode()).hexdigest()[:16]

    factor_deploy_salt = get_or_create_deploy_salt("/app/data/license/.deploy_id")

    fingerprint = "|".join([factor_machine_id, factor_product_uuid, factor_cpu, factor_deploy_salt])
    return hashlib.sha256(fingerprint.encode()).hexdigest()


def get_or_create_deploy_salt(path: str) -> str:
    """首次部署时生成随机盐，后续读取"""
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read().strip()
    else:
        salt = secrets.token_hex(32)
        with open(path, "w") as f:
            f.write(salt)
        os.chmod(path, 0o400)  # 只读
        return salt


# 获取kafka指定主题最新消息
async def fetch_latest_messages(bootstrap_servers, topic, num_messages=10):
    consumer = AIOKafkaConsumer(
        bootstrap_servers=bootstrap_servers,
        group_id=None,
        enable_auto_commit=False,
        auto_offset_reset="latest",
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

        return [m.value.decode(errors="ignore") for m in messages]

    finally:
        try:
            await consumer.stop()
        except asyncio.CancelledError:
            logger.warning("停止消费者时被取消")


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
        text=True,
        check=False,
    )
    return {
        "stdout": result.stdout.strip() if result.stdout else "",
        "stderr": result.stderr.strip() if result.stderr else "",
        "code": result.returncode,
    }


def derive_key(password: str, salt: bytes) -> bytes:
    """使用PBKDF2HMAC从密码派生密钥"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=5000,
        backend=default_backend(),
    )
    return kdf.derive(password.encode())


def encrypt_aes256_gcm(plaintext: Optional[str | bytes]) -> str:
    """AES-GCM加密返回字典格式结果"""
    # 生成随机盐
    salt = secrets.token_bytes(16)
    # 生成随机nonce (12字节是GCM推荐长度)
    nonce = secrets.token_bytes(12)
    key = derive_key(RY_RES_KEY, salt)

    if isinstance(plaintext, str):
        plaintext = plaintext.encode()

    # 创建加密器并加密数据
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()

    return base64.b64encode(ciphertext + b"....." + nonce + b"....." + salt + b"....." + encryptor.tag).decode()


def decrypt_aes256_gcm(encrypted_data: str) -> str:
    """AES-GCM解密，输入为字典格式"""
    try:
        data = base64.b64decode(encrypted_data.encode(), validate=True)
        parts = data.split(b".....")
        if len(parts) != 4:
            raise ValueError("解密失败: 数据格式错误")
        ciphertext, nonce, salt, tag = parts

        key = derive_key(RY_RES_KEY, salt)
        # 创建解密器
        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag), backend=default_backend())
        decryptor = cipher.decryptor()

        # 解密并返回UTF-8字符串
        decrypted = decryptor.update(ciphertext) + decryptor.finalize()

        return decrypted.decode()

    except (binascii.Error, ValueError, KeyError, InvalidTag, UnicodeDecodeError) as e:
        # 处理可能的错误：数据篡改、密码错误或格式错误
        raise ValueError("解密失败: 密码错误或数据损坏") from e


def local_time_to_utc(time_str):
    """将本地时间转换为UTC时间"""
    if not time_str:
        return ""
    try:
        time_str = time_str.strip()
        try:
            dt = datetime.datetime.fromisoformat(time_str)
        except ValueError:
            # 尝试处理一些常见的非标准格式，比如空格分隔
            if " " in time_str and "T" not in time_str:
                dt = datetime.datetime.fromisoformat(time_str.replace(" ", "T"))
            else:
                dt = datetime.datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        # 转换为 UTC
        if dt.tzinfo is None:
            # 如果是 naive 时间，认为是本地时间
            dt = dt.astimezone().astimezone(datetime.timezone.utc)
        else:
            dt = dt.astimezone(datetime.timezone.utc)
        # 格式化
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        # 如果解析失败，为了兼容性抛出 ValueError 或者让调用者处理
        # 这里为了保持行为一致性（原函数也是报错），我们抛出异常
        raise ValueError(f"Invalid time format: {time_str}")


async def query_vlog(query, project_id, start=None, end=None, account_id=0, limit=None):
    # 构建VictoriaLogs查询参数
    params = {"query": query}
    if start:
        params["start"] = local_time_to_utc(start)
    if end:
        params["end"] = local_time_to_utc(end)
    if limit:
        params["limit"] = limit

    header = {
        "AccountID": str(account_id),
        "ProjectID": str(project_id),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # VictoriaLogs查询接口: /select/logsql/query
            resp = await client.get(f"{VL_HOST}/select/logsql/query", headers=header, params=params)
            resp.raise_for_status()
            result = []
            for line in resp.text.strip().splitlines():
                if line:
                    result.append(orjson.loads(line))
            return result
        except httpx.HTTPStatusError as e:
            logger.error(f"VictoriaLogs HTTP error: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"VictoriaLogs query error: {e}")
            return None


# async def setup_ilm_policy(policy_name, retention_age="180d"):
#     """设置ILM策略"""
#     async with AsyncElasticsearch(ES_HOST, basic_auth=(ES_USER, ES_PASS)) as es:
#         policy = {
#             "phases": {
#                 "hot": {
#                     "min_age": "0ms",
#                     "actions": {
#                         "set_priority": {
#                             "priority": 100
#                         }
#                     }
#                 },
#                 "delete": {
#                     "min_age": retention_age,
#                     "actions": {
#                         "delete": {}
#                     }
#                 }
#             }
#         }
#         await es.ilm.put_lifecycle(name=policy_name, policy=policy)
#
#
# async def delete_ilm_policy(policy_name):
#     """删除ILM策略"""
#     async with AsyncElasticsearch(ES_HOST, basic_auth=(ES_USER, ES_PASS)) as es:
#         try:
#             await es.ilm.delete_lifecycle(name=policy_name)
#         except NotFoundError:
#             pass
#
#
# async def setup_logs_template(alias_name, ilm_policy):
#     async with AsyncElasticsearch(ES_HOST, basic_auth=(ES_USER, ES_PASS)) as es:
#         template = {
#             "aliases": {alias_name: {}},
#             "settings": {
#                 "number_of_shards": 1, "number_of_replicas": 0, "index.refresh_interval": "5s",
#                 "index.lifecycle.name": ilm_policy
#             },
#             "mappings": {
#                 "dynamic_templates": [
#                     {"strings_as_keywords": {"match_mapping_type": "string", "mapping": {"type": "keyword"}}},
#                     {"longs_as_numbers": {"match_mapping_type": "long", "mapping": {"type": "long"}}}
#                 ],
#                 "properties": {
#                     "message": {
#                         "type": "text"
#                     }
#                 }
#             }
#         }
#         await es.indices.put_index_template(name=f"template-{alias_name}", index_patterns=[alias_name + "-*"], priority=500,
#                                             data_stream=None, template=template)


async def upload_to_s3(
    file_content: bytes,
    bucket_name: str,
    file_name: str,
    prefix="",
    content_type: Optional[str] = None,
) -> str:
    """
    上传文件到存储桶，返回下载的链接
    file_content: 文件内容
    bucket_name: 存储桶名称
    file_name: 文件名称
    prefix: 功能前缀
    """
    async with aioboto3.Session().client(
        "s3",
        endpoint_url=S3_HOST,
        aws_access_key_id=FS_AK,
        aws_secret_access_key=FS_SK,
        config=Config(signature_version="s3v4"),
    ) as s3_client:
        try:
            s3_key = f"{prefix}/{file_name}"
            # 上传文件
            await s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=file_content,
                ContentType=content_type or "application/octet-stream",
            )
            return f"/files/{bucket_name}/{s3_key}"
        except Exception as e:
            logger.error(f"上传文件失败: {e} | {traceback.format_exc()}")
    return ""


async def delete_from_s3(bucket_name: str, file_name: str, prefix="") -> bool:
    """
    删除存储桶中的文件
    bucket_name: 存储桶名称
    file_name: 文件名称
    prefix: 功能前缀
    """
    async with aioboto3.Session().client(
        "s3",
        endpoint_url=S3_HOST,
        aws_access_key_id=FS_AK,
        aws_secret_access_key=FS_SK,
        config=Config(signature_version="s3v4"),
    ) as s3_client:
        try:
            await s3_client.delete_object(Bucket=bucket_name, Key=f"{prefix}/{file_name}")
            return True
        except Exception as e:
            logger.error(f"删除文件失败: {e} | {traceback.format_exc()}")
    return False


async def get_from_s3(bucket_name: str, file_name: str, prefix="") -> bytes | None:
    """
    获取存储桶中的文件
    bucket_name: 存储桶名称
    file_name: 文件名称
    prefix: 功能前缀
    """
    async with aioboto3.Session().client(
        "s3",
        endpoint_url=S3_HOST,
        aws_access_key_id=FS_AK,
        aws_secret_access_key=FS_SK,
        config=Config(signature_version="s3v4"),
    ) as s3_client:
        try:
            obj = await s3_client.get_object(Bucket=bucket_name, Key=f"{prefix}/{file_name}")
            return await obj["Body"].read()
        except Exception as e:
            logger.error(f"获取文件失败: {e} | {traceback.format_exc()}")
    return None


async def get_url_from_s3(bucket_name: str, file_name: str, host, prefix="") -> bytes | None:
    """
    获取存储桶中的文件的临时下载连接
    bucket_name: 存储桶名称
    file_name: 文件名称
    prefix: 功能前缀
    """
    async with aioboto3.Session().client(
        "s3",
        endpoint_url="https://" + host,
        aws_access_key_id=FS_AK,
        aws_secret_access_key=FS_SK,
        config=Config(signature_version="s3v4"),
    ) as s3_client:
        try:
            file_url = await s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": bucket_name, "Key": f"{prefix}/{file_name}"},
                ExpiresIn=600,
            )  # 10 分钟有效期
            file_url = file_url.replace("https://" + host, "https://" + host + "/files")
            return file_url
        except Exception as e:
            logger.error(f"获取文件失败: {e} | {traceback.format_exc()}")
    return None


async def runlog_list(all_logs):
    """
    统一处理 runlog 返回格式

    Args:
        all_logs: Runlog 对象列表

    Returns:
        三层嵌套列表结构：
        - 最外层：按 run_id 分组
        - 中间层：按 node_id 分组
        - 最内层：具体的日志数据
    """
    from models import Account

    run_logs = []
    logs_by_run_id = {}
    for log in all_logs:
        # 处理runlog中的过期的审核节点
        log_data = log.json
        creator = await Account.get_or_none(id=log_data["creator"])
        log_data["creator_username"] = creator.username if creator else None
        log_data["creator_display_name"] = creator.display_name if creator else None
        if (log.run_id, log.node_id) not in logs_by_run_id.setdefault(log.run_id, {}).keys():
            logs_by_run_id[log.run_id][(log.run_id, log.node_id)] = [log_data]
        else:
            logs_by_run_id[log.run_id][(log.run_id, log.node_id)].append(log_data)

    for value in logs_by_run_id.values():
        data = []
        for son_value in value.values():
            data.append(son_value)
        run_logs.append(data)

    return run_logs

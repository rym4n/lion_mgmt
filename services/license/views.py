# coding=utf-8

import base64
import datetime
import hashlib
import json
import os
import time

import aiofile
import orjson
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sanic import Blueprint
from sanic import response
from sanic.request import Request

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from settings import PUBLIC_KEY, LICENSE_DIR

license_bp = Blueprint('license', url_prefix='v1/license')
_all__ = ["license_bp"]


@license_bp.post("/verify")
async def verify_license_api(request: Request):
    """
    验证license文件

    请求参数：
        - file: license文件
    """
    if "file" not in request.files:
        return response.json({"success": False, "message": "缺少必要字段"}, status=400)

    license_file = request.files.get("file")
    encoded = license_file.body.decode("utf-8")
    json_str = base64.b64decode(encoded.encode("utf-8")).decode("utf-8")
    license_data = json.loads(json_str)
    is_valid = verify_license(license_data)
    if is_valid:
        if license_data.get("expireTime"):
            if int(time.time() * 1000) >= license_data.get("expireTime"):
                raise CustomError(ErrorCode.LicenseExpired)
            else:
                license_path = hashlib.md5(f"{encoded}".encode()).hexdigest() + ".lic"
                for root, dirs, files in os.walk(LICENSE_DIR):
                    for file in files:
                        full_path = os.path.join(root, file)
                        new_path = os.path.join(root, license_path)
                        async with aiofile.AIOFile(full_path, 'w', encoding='utf-8') as f:
                            await f.write(encoded)
                        if os.path.exists(full_path):
                            os.rename(full_path, new_path)
                return response_ok(license_data)
    else:
        raise CustomError(ErrorCode.InvalidLicenseError)


@license_bp.post("/get")
async def get_license(request: Request):
    """
    获取许可文件权限，加载页面

    无需请求参数
    """
    for root, dirs, files in os.walk(LICENSE_DIR):
        for file in files:
            filename, ext = os.path.splitext(file)
            full_path = os.path.join(root, file)
            async with aiofile.AIOFile(full_path, 'r', encoding='utf-8') as f:
                data = await f.read()
                if filename != hashlib.md5(f"{data}".encode()).hexdigest():
                    raise CustomError(ErrorCode.InvalidLicenseError)

    menu_dict = {}

    if data:
        json_str = base64.b64decode(data.encode("utf-8")).decode("utf-8")
        license_data = orjson.loads(json_str)
        is_valid = verify_license(license_data)
        if is_valid:
            if license_data.get("expireTime"):
                if int(time.time() * 1000) < license_data.get("expireTime"):
                    for item in license_data["menuInfo"]["moduleList"]:
                        module = item["module"]
                        auth = item["auth"]

                        if module not in menu_dict:
                            menu_dict[module] = False
                        if auth and int(item["expireTime"]) > int(time.time() * 1000):
                            menu_dict[module] = True

    return response_ok(menu_dict)


@license_bp.post("/show")
async def get_all_license(request: Request):
    """
    获取所有license文件信息用于页面展示
    """
    for root, dirs, files in os.walk(LICENSE_DIR):
        for file in files:
            filename, ext = os.path.splitext(file)
            full_path = os.path.join(root, file)
            async with aiofile.AIOFile(full_path, 'r', encoding='utf-8') as f:
                data = await f.read()
                if filename != hashlib.md5(f"{data}".encode()).hexdigest():
                    raise CustomError(ErrorCode.InvalidLicenseError)

    menu_dict = {}

    if data:
        json_str = base64.b64decode(data.encode("utf-8")).decode("utf-8")
        license_data = orjson.loads(json_str)
        is_valid = verify_license(license_data)
        if is_valid:
            if license_data.get("expireTime"):
                if int(time.time() * 1000) < license_data.get("expireTime"):
                    menu_dict["name"] = license_data["company"]
                    menu_dict["numbering"] = filename
                    menu_dict["version"] = license_data["version"]
                    menu_dict["createTime"] = formatted_time(license_data["createTime"])
                    menu_dict["expireTime"] = formatted_time(license_data["expireTime"])
                    menu_dict["menuInfo"] = []
                    for item in license_data["menuInfo"]["moduleList"]:
                        name = item["name"]
                        auth = item["auth"]

                        if auth and int(item["expireTime"]) > int(time.time() * 1000):
                            if name in ["应用数量", "剧本数量限制"]:
                                menu_dict["menuInfo"].append({name: item["value"]})
                                continue
                            menu_dict["menuInfo"].append({name: True})
                        else:
                            menu_dict["menuInfo"].append({name: False})

    return response_ok(menu_dict)


def formatted_time(timestamp: int) -> str:
    """
    格式化时间戳
    """
    dt = datetime.datetime.fromtimestamp(timestamp / 1000)
    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
    return formatted_time


def verify_license(license_data: dict) -> bool:
    signature_hex = license_data.get("signature")
    if not signature_hex:
        return False

    public_key = serialization.load_pem_public_key(PUBLIC_KEY.encode())

    signature = bytes.fromhex(signature_hex)

    license_str = json.dumps(
        {key: value for key, value in license_data.items() if key != "signature"},
        sort_keys=True
    ).encode()

    try:
        public_key.verify(
            signature,
            license_str,
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        return True
    except InvalidSignature:
        return False

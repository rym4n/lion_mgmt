# coding=utf-8

import base64
import json
import time

import aiofile
import orjson
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sanic import Blueprint
from sanic import response
from sanic.request import Request

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
                return response.json({
                    "message": f"验证失败，您的license已过期。"
                })
            else:
                async with aiofile.AIOFile(LICENSE_DIR + "/license.lic", 'w', encoding='utf-8') as f:
                    await f.write(encoded)

                return response.json({
                    "license_data": license_data
                })
    else:
        return response.json({
            "message": f"验证失败，请检查您的license文件是否正确。"
        })

# @license_bp.post("/get")
# async def get_license(request: Request):
#     """
#     获取许可文件权限
#
#     无需请求参数
#     """
#     async with aiofile.AIOFile(LICENSE_DIR + "/license.lic", 'r', encoding='utf-8') as f:
#         data = await f.read()
#
#     menu_list = []
#
#     if data:
#         json_str = base64.b64decode(data.encode("utf-8")).decode("utf-8")
#         license_data = orjson.loads(json_str)
#         is_valid = verify_license(license_data)
#         if is_valid:
#             if license_data.get("expireTime"):
#                 if int(time.time() * 1000) < license_data.get("expireTime"):
#                     settings.LICENSE_DATA = license_data



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

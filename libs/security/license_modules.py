# coding=utf-8
import time


def is_licensed_module_active(license_data: dict, item: dict) -> bool:
    if not item.get("auth"):
        return False

    expire_time = item.get("expireTime")
    if expire_time is None:
        group = item.get("group")
        expire_time = license_data.get("grouplist", {}).get(group, {}).get("expireTime")

    if expire_time is None:
        return False

    try:
        return int(expire_time) > int(time.time() * 1000)
    except (TypeError, ValueError):
        return False

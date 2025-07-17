import os
from datetime import datetime

import aiofile
import orjson
from aiocache import cached
from sanic import Blueprint
from sanic import json
from sanic import response
from tortoise.transactions import in_transaction

import settings
from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import strip_timezone_to_seconds
from models import AuditLog, Config

system_bp = Blueprint('system', url_prefix='/system')
_all__ = ["system_bp"]


@system_bp.post("/upload")
@requires_permission("system:c")
async def upload_image(request):
    """
    保存系统定制配置（如果 system_version 存在则更新，否则创建）  需要系统配置新增和修改权限

    请求参数：
        copyright: 版权,
        system_name: 系统名,
        system_version: 系统版本,
        background: 背景图
        webico: 网页ico
        logo: logo
    """
    copyright_text = request.form.get("copyright")
    system_name = request.form.get("system_name")
    system_version = request.form.get("system_version")

    if not system_version:
        raise CustomError(ErrorCode.ParameterError)

    cfg = await Config.get_or_none(key="platform_config", module="system")
    if cfg:
        platform_config = orjson.loads(cfg.value)

        platform_config["Version"] = system_version
        platform_config["Content"] = copyright_text
        platform_config["Title"] = system_name
        await Config.filter(key="platform_config", module="system").update(value=orjson.dumps(platform_config))
    else:
        default = {
            "Version": system_version or "3.0.0",
            "Title": system_name or "AIOE智能运营与自动化系统",
            "Content": copyright_text or "Copyright © 2025 虹云数据",
            "FixedHeader": True,
            "HiddenSideBar": False,
            "MultiTagsCache": False,
            "KeepAlive": True,
            "Layout": "horizontal",
            "Theme": "default",
            "DarkMode": False,
            "OverallStyle": "default",
            "Grey": False,
            "Weak": False,
            "HideTabs": True,
            "HideFooter": False,
            "Stretch": False,
            "SidebarStatus": True,
            "EpThemeColor": "#2c7fe4",
            "ShowLogo": True,
            "ShowModel": "smart",
            "MenuArrowIconNoTransition": False,
            "CachingAsyncRoutes": False,
            "TooltipEffect": "default",
            "ResponsiveStorageNameSpace": "responsive-",
            "MenuSearchHistory": 6
        }
        await Config.create(key="platform_config", module="system", value=orjson.dumps(default))

        # 处理文件上传
        for field in ["background", "webico", "logo"]:
            file = request.files.get(field)
            filename = ''
            if field == "background":
                filename = f"bg.png"
            elif field == "webico":
                filename = f"favicon.ico"
            elif field == "logo":
                filename = f"logo.svg"
            if file and filename:
                file_path = os.path.join(settings.UPLOAD_DIR, filename)

                with open(file_path, "wb") as f:
                    f.write(file.body)
                await Config.update_or_create(key=field, module="system", value=file_path)

    return response_ok("success")


@system_bp.get("/picture")
async def get_bgpic(request):
    """
    获取自定义图片

    请求参数：
        type: background, webico, logo
    """
    pic_type = request.args.get("type")
    if pic_type not in ("background", "webico", "logo"):
        raise CustomError(ErrorCode.ParameterError)
    cfg = await Config.get_or_none(key=pic_type, module="system")
    if cfg:
        async with aiofile.AIOFile(cfg.value, 'rb') as f:
            content = await f.read()
            return response.raw(content, content_type="image/" + cfg.value.split('.')[-1])
    return response.raw(b'', content_type="image/png")


@system_bp.get("/platform")
async def get_platform_config(request):
    """
    获取platform-config

    无请求参数，直接调用
    """
    cfg = await Config.get_or_none(key="platform_config", module="system")
    default = {
        "Version": "3.0.0",
        "Title": "AIOE智能运营与自动化系统",
        "Content": "Copyright © 2025 虹云数据",
        "FixedHeader": True,
        "HiddenSideBar": False,
        "MultiTagsCache": False,
        "KeepAlive": True,
        "Layout": "horizontal",
        "Theme": "default",
        "DarkMode": False,
        "OverallStyle": "default",
        "Grey": False,
        "Weak": False,
        "HideTabs": True,
        "HideFooter": False,
        "Stretch": False,
        "SidebarStatus": True,
        "EpThemeColor": "#2c7fe4",
        "ShowLogo": True,
        "ShowModel": "smart",
        "MenuArrowIconNoTransition": False,
        "CachingAsyncRoutes": False,
        "TooltipEffect": "default",
        "ResponsiveStorageNameSpace": "responsive-",
        "MenuSearchHistory": 6
    }

    if cfg:
        return json(orjson.loads(cfg.value), dumps=orjson.dumps)
    else:
        return json(default, dumps=orjson.dumps)


@system_bp.post("/status/all")
@requires_permission("status:r")
@cached(timeout=120)
async def get_system_status(request):
    """
    获取全部系统状况    需要系统配置查看权限

    无需参数直接请求则返回全部系统状况数据
    """
    name_map = {
        'lion-mgmt': '控制台服务', 'lion-web': 'Web服务器', 'lion-db': '数据库服务', 'lion-kb': '数据库服务', 'lion-redis': '缓存服务',
        'lion-ship': 'ETL服务', 'lion-job': '任务调度服务', 'lion-worker': '执行引擎', 'lion-kafka': '消息队列', 'lion-ai': 'AI服务',
        'lion-fs': '文件存储服务', 'lion-es': 'ES存储服务'
    }
    # 遍历目录获取 status_ 开头的json文件
    data_dir = os.path.join(settings.BASE_DIR, 'data')
    status_files = [f for f in os.listdir(data_dir) if f.startswith('status_') and f.endswith('.json')]
    if not status_files:
        raise CustomError(ErrorCode.CheckStatusNotFound)
    result = []
    for file in status_files:
        ip = file.split('_')[1].strip('.json')
        file_path = os.path.join(data_dir, file)
        modify_time = datetime.strftime(datetime.fromtimestamp(os.path.getmtime(file_path)), "%Y-%m-%d %H:%M:%S")
        async with aiofile.AIOFile(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            data = orjson.loads(content)
            for item in data:
                item['host'] = ip
                item['name'] = name_map.get(item['name'], item['name'])
                item['start_time'] = strip_timezone_to_seconds(item['start_time'])
                item['check_time'] = modify_time
                result.append(item)

    return response_ok(result)


@system_bp.post("/oauth/status")
async def get_oauth_status(request):
    """
    获取SSO状态

    无请求参数，直接调用，返回SSO状态
    """
    record = await Config.get_or_none(module='sso', key='use_oauth2')
    return response_ok({
        "use_oauth2": record.value == 'true' if record else False
    })


@system_bp.post("/oauth/get")
@requires_permission("sso:r")
async def get_oauth_config(request):
    """
    获取SSO配置

    无请求参数，直接调用，返回SSO配置
    """
    records = await Config.filter(module='sso').values('key', 'value')
    oauth_config = {c['key']: c['value'] for c in records}
    secret = oauth_config.get("client_secret", "")
    return response_ok({
        "use_oauth2": oauth_config.get("use_oauth2", "false"),
        "auto_create": oauth_config.get("auto_create", "false"),
        "client_id": oauth_config.get("client_id", ""),
        "client_id_alias": oauth_config.get("client_id_alias", ""),
        "client_secret": secret[:3] + "******" + secret[-3:] if secret else "",
        "client_secret_alias": oauth_config.get("client_secret_alias", ""),
        "authorize_uri": oauth_config.get("authorize_uri", ""),
        "token_uri": oauth_config.get("token_uri", ""),
        "token_path": oauth_config.get("token_path", ""),
        "user_info_uri": oauth_config.get("user_info_uri", ""),
        "user_id_path": oauth_config.get("user_id_path", ""),
        "display_name_path": oauth_config.get("display_name_path", ""),
        "email_path": oauth_config.get("email_path", ""),
        "phone_path": oauth_config.get("phone_path", ""),
        "scopes": oauth_config.get("scopes", ""),
    })


@system_bp.post("/oauth/update")
@requires_permission("sso:u")
async def update_oauth_config(request):
    """
    更新SSO配置

    请求参数：
        use_oauth2: 字符串类型, true 表示启用oauth认证, false 表示禁用, 必填参数
        auto_create: 字符串类型, true 表示认证用户不存在的话自动新建, false 表示不新建, 必填参数
        client_id: 客户端ID, 必填参数
        client_id_alias: 客户端ID的名称，某些SSO可能叫app_id，可选参数
        client_secret: 客户端密钥, 必填参数
        client_secret_alias：客户端密钥的名称，某些SSO可能叫app_secret，可选参数
        authorize_uri: 获取授权码的URI, 必填参数
        token_uri: 获取Token的URI, 必填参数
        token_path: Token的取值路径, 必填参数
        user_info_uri: 获取用户信息的URI, 必填参数
        user_id_path: 用户唯一标识的取值路径, 必填参数
        display_name_path: 用户姓名的取值路径, 可选参数
        email_path: 用户邮箱的取值路径, 可选参数
        phone_path: 用户手机号的取值路径, 可选参数
        scopes_alias: 请求资源范围的名称, 可选参数
        scopes: 请求资源范围, 可选参数
    """
    params = request.json
    use_oauth2 = params.get("use_oauth2")
    if use_oauth2 not in ('true', 'false'):
        raise CustomError(ErrorCode.ParameterError)

    if use_oauth2 == 'false':
        old = await Config.get_or_none(key="use_oauth2")
        if old:
            old.value = "false"
            await old.save()
        else:
            await Config.create(key="use_oauth2", value="false", module="sso")
    else:
        required_fields = ["auto_create", "client_id", "client_secret", "authorize_uri", "token_uri", "user_info_uri", "user_id_path", "token_path"]
        optional_fields = ["display_name_path", "email_path", "phone_path", "scopes", "client_id_alias", "client_secret_alias", "scopes_alias"]
        # 校验必填字段是否为空或不存在
        for field in required_fields:
            if not params.get(field):
                raise CustomError(ErrorCode.ParameterError)

        # 收集所有要更新的键值对
        all_keys = required_fields + optional_fields
        updates = {key: params.get(key, '') for key in all_keys}
        updates['use_oauth2'] = 'true'

        # 使用事务保证原子性
        async with in_transaction():  # 假设 ORM 支持异步事务
            # 批量获取现有配置
            existing_configs = await Config.filter(key__in=updates.keys()).all()
            existing_dict = {cfg.key: cfg for cfg in existing_configs}

            # 准备批量更新和创建的数据
            to_update = []
            to_create = []

            for key, value in updates.items():
                if key in existing_dict:
                    config = existing_dict[key]
                    config.value = value
                    to_update.append(config)
                else:
                    to_create.append(Config(key=key, value=value, module='sso'))

            # 批量更新
            if to_update:
                await Config.bulk_update(to_update, fields=['value'])

            # 批量创建
            if to_create:
                await Config.bulk_create(to_create)

    request.app.m.restart(zero_downtime=True, all_workers=True)
    return response_ok('success')


@system_bp.post("/audit")
@requires_permission("audit:r")
async def search_audit(request):
    """
    查询审计日志

    请求参数：
        username: 用户名
        start_time: 开始时间
        end_time: 结束时间
        ip: ip,
        page: 页码
        size: 每页条数
    """
    page = int(request.json.get('page', 1))
    size = int(request.json.get('size', 10))
    offset = (page - 1) * size
    limit = size

    username = request.json.get("username")
    start_time = request.json.get("start_time")
    end_time = request.json.get("end_time")
    ip = request.json.get("ip")
    filters = {}
    if start_time:
        filters["ctime__gte"] = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
    if end_time:
        filters["ctime__lte"] = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
    if ip:
        filters["client_ip"] = ip
    if username:
        filters["username"] = username

    total = await AuditLog.filter(**filters).count()
    data = await AuditLog.filter(**filters).offset(offset).limit(limit)

    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": [item.json for item in data]
    })

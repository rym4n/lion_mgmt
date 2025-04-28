from sanic import Blueprint
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Scene

scene_bp = Blueprint('scene', url_prefix='v1/scene')
_all__ = ["scene_bp"]


@scene_bp.post("/add")
@requires_permission("scene:c")
async def add_scene(request):
    """
    添加场景 (需要场景创建权限)

    请求参数：
        name str 场景名称 必填
        remark str 场景描述 必填
        status int 场景状态 必填 0-禁用 1-启用 2-启用并设置成当前场景
    """
    name = request.json.get('name')
    remark = request.json.get('remark')
    status = request.json.get('status')
    if not all([name, remark, status]):
        raise CustomError(ErrorCode.ParameterError)

    if await Scene.filter(name=request.json.get("name")).exists():
        raise CustomError(ErrorCode.SceneExistsError)

    scene = await Scene.create(name=name, remark=remark, status=status, creator=request.ctx.session['account_info'],
                               updator=request.ctx.session['account_info'])

    return response_ok(scene.json)


@scene_bp.post("/update")
@requires_permission("scene:u")
async def update_scene(request):
    """
    更新场景 (需要场景更新权限)

    请求参数：
        id str 场景id 必填
        remark str 场景描述 非必填
        status int 场景状态 非必填 0-禁用 1-启用 2-启用并设置成当前场景
    """
    id = request.json.get('id')
    remark = (request.json.get('remark') or None)
    status = (request.json.get('status') or None)
    if not id:
        raise CustomError(ErrorCode.ParameterError)

    scene = await Scene.get_or_none(id=id)
    if not scene:
        raise CustomError(ErrorCode.NotFound)

    if remark:
        scene.remark = remark
    if status:
        scene.status = status
    scene.updator = request.ctx.session['account_info']
    await scene.save()

    return response_ok(scene.json)


@scene_bp.post("/all")
@requires_permission("scene:r")
async def get_all_scene(request):
    """
    获取所有场景 (需要场景查看权限)

    请求参数:
        page int 页码 非必填 默认为1
        page_size int 每页数量 非必填 默认为10
        key str 搜索关键字 非必填 默认为空
        status int 场景状态 非必填 0-禁用 1-启用 2-启用并设置成当前场景,隐藏禁用场景时传递
    """
    page = int(request.json.get('page') or 1)
    page_size = int(request.json.get('page_size') or 10)
    key = request.json.get('key') or None
    status = request.json.get('status' or None)

    query = Scene.all()

    if status is not None:
        query = query.filter(status__not=status)

    if key:
        query = query.filter(Q(name__icontains=key) | Q(remark__icontains=key))

    scenes = await query.limit(page_size).offset((page - 1) * page_size)

    scene_list = []

    for scene in scenes:
        await scene.fetch_related("updator")
        scene_list.append(scene.json)

    return response_ok({
        "scenes": scene_list,
        "page": page,
        "page_size": page_size,
        "total": await query.count()
    })


@scene_bp.post("/current")
@requires_permission("scene:u")
async def set_current_scene(request):
    """
    设置当前场景 (需要场景更新权限)

    请求参数：
        id str 场景id 必填
    """
    id = request.json.get('id')
    if not id:
        raise CustomError(ErrorCode.ParameterError)
    scene = await Scene.filter(id=id).first()
    if not scene:
        raise CustomError(ErrorCode.NotFound)

    scene.status = 2
    scene.updator = request.ctx.session['account_info']
    await scene.save()

    return response_ok("设置成功")

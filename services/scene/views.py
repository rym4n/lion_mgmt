from sanic import Blueprint
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Scene, Playbook

scene_bp = Blueprint('scene', url_prefix='/scene')
_all__ = ["scene_bp"]


@scene_bp.post("/add")
@requires_permission("scene:c")
async def add_scene(request):
    """
    添加场景 (需要场景创建权限)

    请求参数：
        name str 场景名称 必填
        remark str 场景描述 必填
        status int 场景状态 必填 0-禁用 1-启用
    """
    name = request.json.get('name')
    remark = request.json.get('remark')
    status = int(request.json.get('status'))
    if not all([name, remark]):
        raise CustomError(ErrorCode.ParameterError)

    if status is None and status not in [0, 1]:
        raise CustomError(ErrorCode.ParameterError)

    if await Scene.filter(name=request.json.get("name")).exists():
        raise CustomError(ErrorCode.SceneExistsError)
    if status == 1:
        await Scene.filter(status=1).update(status=0)
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
    """
    id = request.json.get('id')
    remark = (request.json.get('remark') or None)
    if not id:
        raise CustomError(ErrorCode.ParameterError)

    scene = await Scene.get_or_none(id=id).prefetch_related('creator')
    if not scene:
        raise CustomError(ErrorCode.NotFound)

    if remark:
        scene.remark = remark
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
        status int 场景状态 非必填 0-禁用 1-启用
    """
    page = int(request.json.get('page') or 1)
    page_size = int(request.json.get('page_size') or 10)
    key = request.json.get('key') or None
    status = request.json.get('status')

    condition = Q()

    if status is not None:
        condition = condition & Q(status__not=status)

    if key:
        condition = condition & (Q(name__icontains=key) | Q(remark__icontains=key))

    total = await Scene.filter(condition).count()
    scenes = list()
    if total > 0:
        scenes = await Scene.filter(condition).limit(page_size).offset((page - 1) * page_size).all().prefetch_related(
            'creator', 'updator')

    return response_ok({
        "scenes": [s.json for s in scenes],
        "page": page,
        "page_size": page_size,
        "total": total
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
    await Scene.filter(status=1).update(status=0)
    scene = await Scene.get_or_none(id=id)
    if not scene:
        raise CustomError(ErrorCode.NotFound)

    scene.status = 1
    scene.updator = request.ctx.session['account_info']
    await scene.save()

    return response_ok("设置成功")


@scene_bp.post("/remove")
@requires_permission("scene:d")
async def remove_scene(request):
    """
    删除场景 (需要场景删除权限)

    请求参数：
        id str 场景id 必填
    """
    id = request.json.get('id')
    if not id:
        raise CustomError(ErrorCode.ParameterError)

    scene = await Scene.get_or_none(id=id)

    if not scene:
        raise CustomError(ErrorCode.NotFound)

    if scene.status == 1:
        raise CustomError(ErrorCode.SceneDeleteNotAllowed)

    async with in_transaction():
        playbooks = await Playbook.filter(scenes__id=id).all()

        for playbook in playbooks:
            await playbook.scenes.remove(scene)

        await scene.delete()

    return response_ok("删除成功")

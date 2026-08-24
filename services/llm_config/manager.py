# coding=utf-8
"""LLM 模型配置的 CRUD 业务逻辑。

四条不显然的规则：
- purpose 非空值在同租户下唯一，靠「设新的先清旧的」保证（不用 DB 部分唯一索引）。
  收敛到 default 租户后（SPEC §5.6），这就等价于全局唯一。
- api_key 留空表示不修改（编辑表单不回填明文）。
- 删除是软删除，不触发 on_delete=SET_NULL，必须显式清空员工绑定。
- 本模块的 tenant_id 参数由 views 侧的 _tenant() 保证恒为 DEFAULT_TENANT_ID。
"""

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError

# embedding 由本次 ReMe 迁移引入（SPEC §5.6）。
# knowledge_chat / knowledge_embedding 属阶段三，此处不加。
VALID_PURPOSES = ("chat", "lite", "embedding")


async def list_configs(tenant_id: str, keyword: str | None = None) -> list[dict]:
    from models import LlmModelConfig, mask_api_key

    query = LlmModelConfig.filter(tenant_id=tenant_id, deleted=False)
    if keyword:
        query = LlmModelConfig.filter(
            tenant_id=tenant_id, deleted=False, name__icontains=keyword
        )
    rows = await query.order_by("-utime").all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "base_url": row.base_url,
            "api_key": mask_api_key(row.api_key),
            "model_name": row.model_name,
            "purpose": row.purpose,
            # 前端编辑表单要回填维度：不返回的话，embedding 配置每次编辑
            # 都会以空维度提交，用户被迫重选，选错就换了向量空间。
            # list_catalog 是闭集（见其 docstring），那边不加。
            "dimensions": getattr(row, "dimensions", None),
            "enabled": row.enabled,
            "ctime": row.ctime,
            "utime": row.utime,
        }
        for row in rows
    ]


async def list_catalog() -> list[dict]:
    """只读目录：任意租户可调，固定查 default 租户，只列启用的配置。

    字段白名单是**闭集**（SPEC §5.6 / 契约 §3）：id / name / model_name /
    purpose / enabled。绝不返回 base_url / api_key——即使 api_key
    经 mask_api_key 脱敏，它仍暴露前 3 后 4 位，对普通租户没有必要。

    无参：目录只有一个作用域。留一个 tenant_id 参数位会诱使调用方
    传 session 租户，从而把按租户查询悄悄加回来。
    """
    from core.maintenance import DEFAULT_TENANT_ID
    from models import LlmModelConfig

    rows = (
        await LlmModelConfig.filter(
            tenant_id=DEFAULT_TENANT_ID, deleted=False, enabled=True
        )
        .order_by("-utime")
        .all()
    )
    return [
        {
            "id": row.id,
            "name": row.name,
            "model_name": row.model_name,
            "purpose": row.purpose,
            "enabled": row.enabled,
        }
        for row in rows
    ]


async def assert_bindable_config(config_id: str | None) -> None:
    """校验员工可绑定该模型配置（SPEC §5.6）。

    catalog 只列 default 租户下 enabled 的配置，但任何人都能绕过前端
    直接构造 employee/create 请求塞一个任意 id——「不能只信前端传什么」。

    空值表示解绑，是合法操作。非空则三个条件缺一不可：
    属 default 租户、未软删、已启用。

    注意与 resolve_llm 的分工：这里是**写入时**拒绝非法值；
    resolve_llm 是**运行时**对事后失效的绑定回落到全局默认
    （resolver.py 的既有契约）。两者都必须保留。
    """
    if not config_id:
        return

    from core.maintenance import DEFAULT_TENANT_ID
    from models import LlmModelConfig

    record = await LlmModelConfig.filter(
        id=config_id,
        tenant_id=DEFAULT_TENANT_ID,
        deleted=False,
        enabled=True,
    ).first()
    if record is None:
        raise CustomError(ErrorCode.PARAM_ERROR)


async def _assert_name_available(
    tenant_id: str, name: str, exclude_id: str | None = None
) -> None:
    """名称查重。注意 unique_together 不区分 deleted，软删的记录仍占用名称。"""
    from models import LlmModelConfig

    existing = await LlmModelConfig.get_or_none(tenant_id=tenant_id, name=name)
    if existing is not None and existing.id != exclude_id:
        raise CustomError(ErrorCode.LLM_CONFIG_NAME_DUPLICATED)


async def create_config(
    tenant_id: str,
    *,
    name: str,
    base_url: str,
    api_key: str,
    model_name: str,
    purpose: str | None = None,
    dimensions: int | None = None,
) -> dict:
    from models import LlmModelConfig

    if dimensions is not None and not (1 <= dimensions <= 65536):
        raise CustomError(ErrorCode.PARAM_ERROR)

    await _assert_name_available(tenant_id, name)
    if purpose:
        await _clear_purpose(tenant_id, purpose)
    row = await LlmModelConfig.create(
        tenant_id=tenant_id,
        name=name,
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        purpose=purpose,
        dimensions=dimensions,
    )
    return {"id": row.id}


async def update_config(tenant_id: str, config_id: str, **changes) -> dict:
    from models import LlmModelConfig

    row = await LlmModelConfig.get_or_none(
        id=config_id, tenant_id=tenant_id, deleted=False
    )
    if row is None:
        raise CustomError(ErrorCode.LLM_CONFIG_NOT_CONFIGURED)

    # dimensions 范围验证
    dimensions = changes.get("dimensions")
    if dimensions is not None and not (1 <= dimensions <= 65536):
        raise CustomError(ErrorCode.PARAM_ERROR)

    new_name = changes.get("name")
    if new_name and new_name != row.name:
        await _assert_name_available(tenant_id, new_name, exclude_id=config_id)

    # api_key 留空表示不修改
    if not changes.get("api_key"):
        changes.pop("api_key", None)

    for key in (
        "name",
        "base_url",
        "api_key",
        "model_name",
        "enabled",
        "dimensions",
    ):
        if key in changes and changes[key] is not None:
            setattr(row, key, changes[key])
    await row.save()
    return {"id": row.id}


async def _clear_purpose(tenant_id: str, purpose: str) -> None:
    from models import LlmModelConfig

    await LlmModelConfig.filter(
        tenant_id=tenant_id, deleted=False, purpose=purpose
    ).update(purpose=None)


async def set_purpose(tenant_id: str, config_id: str, purpose: str | None) -> dict:
    from models import LlmModelConfig

    if purpose is not None and purpose not in VALID_PURPOSES:
        raise CustomError(ErrorCode.PARAM_ERROR)
    row = await LlmModelConfig.get_or_none(
        id=config_id, tenant_id=tenant_id, deleted=False
    )
    if row is None:
        raise CustomError(ErrorCode.LLM_CONFIG_NOT_CONFIGURED)
    if purpose is not None:
        await _clear_purpose(tenant_id, purpose)
    row.purpose = purpose
    await row.save()
    return {"id": row.id, "purpose": purpose}


async def delete_config(tenant_id: str, config_id: str) -> int:
    """软删除配置，并显式清空员工绑定。返回受影响员工数。"""
    from models import CommandAgent, LlmModelConfig

    row = await LlmModelConfig.get_or_none(
        id=config_id, tenant_id=tenant_id, deleted=False
    )
    if row is None:
        raise CustomError(ErrorCode.LLM_CONFIG_NOT_CONFIGURED)

    affected = await CommandAgent.filter(
        tenant_id=tenant_id, deleted=False, llm_config_id=config_id
    ).update(llm_config_id=None)

    row.deleted = True
    await row.save()
    return affected or 0

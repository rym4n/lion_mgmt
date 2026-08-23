# coding=utf-8
"""执行 payload 的 knowledge 段（SPEC §11.1）。

只下发空间清单，不下发任何凭证。OpenViking 的 api_key 只在
lion_mgmt 持有；agent 通过 /knowledge/agent/* 访问知识库，隔离在服务端完成。
"""

from sanic.log import logger

from models.knowledge import KnowledgeSpace


async def build_knowledge_payload(employee) -> dict | None:
    """构造 knowledge 段；无绑定、空间已删或查询失败时返回 None。"""
    try:
        space_keys = list(getattr(employee, "knowledge_space_keys", None) or [])
        if not space_keys:
            return None

        spaces = await KnowledgeSpace.filter(
            tenant_id=employee.tenant_id,
            space_key__in=space_keys,
            deleted=False,
        ).all()
        if not spaces:
            return None

        write_space = getattr(employee, "knowledge_write_space", None)
        return {
            "enabled": True,
            "spaces": [
                {
                    "key": space.space_key,
                    "name": space.name,
                    "description": space.description or "",
                    "writable": space.space_key == write_space,
                }
                for space in spaces
            ],
        }
    except Exception as exc:
        # 知识库故障绝不能让数字员工无法执行。
        logger.warning(
            "knowledge_payload_failed employee=%s error_type=%s",
            getattr(employee, "id", None),
            type(exc).__name__,
        )
        return None

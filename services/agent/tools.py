# coding=utf-8
import json
from typing import Any, Dict, List, Optional

from sanic.log import logger

from libs.kafka import aproduce
from libs.utils import gen_run_id
from models import PlaybookVersion, Room
from settings import TASK_TOPIC


async def call_playbook(playbook_name: str, params: Dict[str, Any], warroom_id: Optional[str] = None, executor_id: Optional[str] = None, tenant_id: Optional[str] = None) -> str:
    """
    执行剧本 (核心工具)

    Args:
        playbook_name: 剧本名称 (如 "查询IP信誉", "封禁IP")
        params: 剧本输入参数
        warroom_id: 作战室 ID (用于关联执行记录)
        executor_id: 执行者 ID (Agent 会传入)
        tenant_id: 租户 ID (从 Agent metadata 获取)

    Returns:
        执行结果描述 (成功: run_id, 失败: 错误信息)
    """
    try:
        # 1. 查找剧本
        pv = await PlaybookVersion.filter(playbook__name=playbook_name, deleted=False, status=1, tenant_id=tenant_id).order_by("-ctime").first()  # 已发布

        if not pv:
            return f"错误: 未找到已发布的剧本 '{playbook_name}'"

        # 2. 准备执行参数
        run_params = {"flow_id": pv.id, "flow_input": params, "executor": executor_id or "agent"}

        # 3. 生成 run_id
        run_id = gen_run_id()
        msg = {"run_type": "flow", "run_id": run_id, "run_args": run_params, "tenant_id": tenant_id}

        # 4. 关联到作战室 (如果提供)
        if warroom_id:
            room = await Room.get_or_none(id=warroom_id, tenant_id=tenant_id)
            if room:
                room.run_id.append(run_id)
                await room.save()

        # 5. 发送到 Kafka
        await aproduce(TASK_TOPIC, msg)

        logger.info(f"Agent triggered playbook: {playbook_name}, run_id: {run_id}")

        return f"剧本 '{playbook_name}' 执行已启动，运行ID: {run_id}。请稍后查看结果。"

    except Exception as e:
        logger.error(f"call_playbook error: {e}")
        return f"执行剧本时出错: {str(e)}"


async def list_available_playbooks(category: Optional[str] = None, tenant_id: Optional[str] = None) -> str:
    """
    查询可用剧本列表

    Args:
        category: 剧本分类 (可选: 威胁研判, 应急处置, 日志查询, 资产管理)
        tenant_id: 租户 ID

    Returns:
        JSON 格式的剧本列表
    """
    try:
        filters = {"deleted": False, "status": 1, "tenant_id": tenant_id}

        # 按场景过滤 (category 映射到 scene)
        query = PlaybookVersion.filter(**filters)

        if category:
            # 通过关联的 Scene 过滤
            query = query.filter(playbook__scenes__name__icontains=category)

        playbooks = await query.prefetch_related("playbook", "playbook__scenes").limit(50).all()

        result = []
        for pv in playbooks:
            # 提取输入参数 Schema
            input_schema = _extract_playbook_inputs(pv.flow_json)

            result.append({"name": pv.playbook.name, "description": pv.playbook.remark, "scenes": [s.name for s in pv.playbook.scenes], "version": pv.version, "inputs": input_schema})

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"list_available_playbooks error: {e}")
        return f"查询剧本时出错: {str(e)}"


async def submit_case_learning(
    summary: str,
    attack_type: str,
    key_indicators: List[str],
    lessons_learned: str,
    false_positive: bool = False,
    warroom_id: Optional[str] = None,
    event_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> str:
    """
    提交案例学习到 L5 知识库

    Args:
        summary: 案例总结 (必填)
        attack_type: 攻击类型 (如: 横向移动, DDoS, 恶意软件)
        key_indicators: 关键 IOC 列表
        lessons_learned: 经验教训
        false_positive: 是否为误报
        warroom_id: 来源作战室
        event_id: 来源事件
        agent_id: 提交的 Agent ID
        tenant_id: 租户 ID

    Returns:
        提交结果
    """
    try:
        from models.agent import ArchivalKnowledge
        from libs.embedding import get_embedding

        # 1. 构建完整内容文本
        content = f"""
案例总结:
{summary}

攻击类型: {attack_type}

关键指标:
{', '.join(key_indicators)}

经验教训:
{lessons_learned}

{"[注意: 此案例为误报]" if false_positive else ""}
"""

        # 2. 生成 Embedding
        embedding = await get_embedding(content)

        # 3. 提取标签
        tags = [attack_type]
        if false_positive:
            tags.append("误报")
        tags.extend(key_indicators[:5])  # 最多取 5 个 IOC 作为标签

        # 4. 插入数据库
        knowledge = await ArchivalKnowledge.create(
            tenant_id=tenant_id,
            content=content,
            embedding=embedding,
            knowledge_type="case_learning",
            attack_type=attack_type,
            tags=tags,
            source_warroom_id=warroom_id,
            source_event_id=event_id,
            submitted_by=agent_id,
            review_status="pending",  # 需要人工审核
        )

        logger.info(f"Agent submitted case learning: {knowledge.id}")

        return f"案例学习已提交（ID: {knowledge.id}），等待管理员审核后将加入知识库。"

    except Exception as e:
        logger.error(f"submit_case_learning error: {e}")
        return f"提交案例学习时出错: {str(e)}"


def _extract_playbook_inputs(flow_json: dict) -> List[Dict[str, str]]:
    """从 flow_json 提取输入参数 Schema"""
    if not flow_json or "nodes" not in flow_json:
        return []

    nodes = flow_json.get("nodes", [])
    if not nodes:
        return []

    start_node = nodes[0]
    if "properties" not in start_node or "start_params" not in start_node["properties"]:
        return []

    start_params = start_node["properties"]["start_params"]

    return [{"name": p["name"], "type": p["type"], "description": p.get("desc", "")} for p in start_params]


async def register_tools_to_agent_server(client, tenant_id: str):
    """
    将工具注册到 Agent Server (使用 httpx)
    """
    tools = [
        {
            "name": "call_playbook",
            "description": "Execute a playbook (automation script) to perform security operations.",
            "source_type": "json",
            "json_schema": {
                "type": "object",
                "properties": {
                    "playbook_name": {"type": "string", "description": "Name of the playbook to execute"},
                    "params": {"type": "object", "description": "Input parameters for the playbook"},
                    "warroom_id": {"type": "string"},
                    "executor_id": {"type": "string"},
                },
                "required": ["playbook_name", "params"],
            },
        },
        {
            "name": "list_available_playbooks",
            "description": "List available playbooks filtered by category.",
            "source_type": "json",
            "json_schema": {"type": "object", "properties": {"category": {"type": "string", "description": "Playbook category"}}},
        },
        {
            "name": "submit_case_learning",
            "description": "Submit case learning to knowledge base.",
            "source_type": "json",
            "json_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "attack_type": {"type": "string"},
                    "key_indicators": {"type": "array", "items": {"type": "string"}},
                    "lessons_learned": {"type": "string"},
                    "false_positive": {"type": "boolean"},
                },
                "required": ["summary", "attack_type", "key_indicators", "lessons_learned"],
            },
        },
    ]

    for tool in tools:
        try:
            payload = {"name": tool["name"], "description": tool["description"], "source_type": "json", "json_schema": tool["json_schema"]}

            response = await client.post("/v1/tools/", json=payload)
            if response.status_code == 409:  # Already exists
                continue
            response.raise_for_status()

        except Exception as e:
            logger.warning(f"Tool {tool['name']} registration warning: {e}")

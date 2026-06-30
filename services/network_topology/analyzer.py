# coding=utf-8
"""
网络拓扑 AI 解析模块。

流程：
  1. 接收原始设备配置文本（纯文本，不限厂商）
  2. 通过 Nanobot/lion_agent 解析，使用流式 SSE 推送进度
  3. 汇总 Nanobot 输出，解析为标准化拓扑 JSON

拓扑 JSON 格式：
{
  "nodes": [
    {"id": "...", "type": "firewall|switch|router|host|unknown",
     "label": "...", "ip": "...", "policies": [...]}
  ],
  "edges": [
    {"source": "...", "target": "...", "vlan": "...", "port": "...", "bandwidth": "..."}
  ]
}
"""
import json
import orjson
from typing import AsyncGenerator
from sanic.log import logger

from services.digital_employee.lion_agent_adapter import stream_prompt_events, workspace_id_for

_SYSTEM_PROMPT = """\
你是一名专业的网络工程师和拓扑分析专家。
用户会向你提供网络设备的配置文本（可能包含防火墙策略、交换机配置、路由表、ACL、VLAN、接口信息等，不限厂商格式）。
请从中提取所有网络设备及其连接关系，输出**严格符合**以下 JSON Schema 的拓扑数据，不要输出任何其他内容。

Schema：
{
  "nodes": [
    {
      "id": "设备唯一标识（字符串，如设备名或IP）",
      "type": "firewall | switch | router | host | unknown",
      "label": "设备显示名称",
      "ip": "主要管理IP（可为空字符串）",
      "policies": ["策略摘要字符串列表，每条不超过80字符"]
    }
  ],
  "edges": [
    {
      "source": "源节点id",
      "target": "目标节点id",
      "vlan": "VLAN编号或名称（可为空字符串）",
      "port": "接口或端口号（可为空字符串）",
      "bandwidth": "带宽描述（可为空字符串）"
    }
  ]
}

规则：
- 每个设备只出现一次（以 id 去重）
- 边是无向的，不要重复添加反向边
- 无法确定的字段留空字符串，不要省略字段
- 只输出 JSON，不要 Markdown 代码块，不要解释文字
"""


def _build_nanobot_message(config_text: str) -> str:
    """构造交给 Nanobot/lion_agent 的拓扑解析任务。"""
    message_context = {
        "task": "network_topology_parse",
        "input_type": "network_device_config_text",
        "expected_output": "strict_json_only",
        "response_schema": {
            "nodes": [
                {
                    "id": "string",
                    "type": "firewall|switch|router|host|unknown",
                    "label": "string",
                    "ip": "string",
                    "policies": ["string"],
                }
            ],
            "edges": [
                {
                    "source": "string",
                    "target": "string",
                    "vlan": "string",
                    "port": "string",
                    "bandwidth": "string",
                }
            ],
        },
        "rules": [
            "只返回 JSON，不要输出 Markdown 代码块或解释文字。",
            "无法确定的字段使用空字符串，不要省略字段。",
            "每个设备只出现一次，边按无向连接去重。",
            "如果配置中包含防火墙策略或交换机 ACL/VLAN/interface 信息，提炼到对应节点 policies 或边字段中。",
        ],
    }
    return f"""<system_instruction>
{_SYSTEM_PROMPT}
</system_instruction>

<message_context>
{orjson.dumps(message_context, option=orjson.OPT_INDENT_2).decode()}
</message_context>

<user_message>
请解析以下防火墙、交换机或其他网络设备配置，生成可交互拓扑图所需的 nodes/edges JSON。

<device_config>
{config_text}
</device_config>
</user_message>"""


def _event_content(event_type: str, payload: dict) -> str:
    """从 Nanobot/lion_agent SSE 事件里提取可累积的正文片段。"""
    if event_type not in {"delta", "message", "end"}:
        return ""

    content = payload.get("content") or payload.get("delta")
    if content:
        return str(content)

    data = payload.get("data")
    if isinstance(data, dict):
        content = data.get("content") or data.get("delta")
        if content:
            return str(content)

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = delta.get("content") or message.get("content") or first.get("text")
        if content:
            return str(content)

    return ""


async def _call_nanobot_stream(config_text: str, *, tenant_id: str, session_id: str) -> AsyncGenerator[str, None]:
    """
    调用 Nanobot/lion_agent，以 SSE 方式流式返回正文片段。
    每次 yield 一段文本片段（delta content）。
    """
    message = _build_nanobot_message(config_text)
    saw_delta = False
    pending_end_content = ""

    async for event_type, payload in stream_prompt_events(
        workspace_id=workspace_id_for(tenant_id, "network-topology"),
        session_id=session_id,
        request_id=session_id,
        message=message,
    ):
        if event_type == "error":
            raise RuntimeError(payload.get("message") or payload)

        content = _event_content(event_type, payload)
        if not content:
            continue

        if event_type == "end":
            pending_end_content = content
            continue

        saw_delta = True
        yield content

    # 有些 lion_agent 实现只在 end 事件返回完整内容；如果已经收到 delta，则避免重复拼接。
    if pending_end_content and not saw_delta:
        yield pending_end_content


def _parse_topology(raw_text: str) -> dict:
    """
    从模型输出中提取并校验拓扑 JSON。
    尽力修复常见问题（代码块包裹、前后杂文）。
    返回合规 dict 或抛出 ValueError。
    """
    text = raw_text.strip()

    # 去掉 markdown 代码块
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end]).strip()

    # 找第一个 { 和最后一个 }
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx == -1 or end_idx == -1:
        raise ValueError("模型输出中未找到有效的 JSON 结构")

    text = text[start_idx : end_idx + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e}")

    # 基础结构校验
    if "nodes" not in data or "edges" not in data:
        raise ValueError("拓扑 JSON 缺少 nodes 或 edges 字段")

    # 补全 node 缺失字段
    for node in data.get("nodes", []):
        for field in ("id", "type", "label", "ip"):
            node.setdefault(field, "")
        if "policies" not in node:
            node["policies"] = []

    # 补全 edge 缺失字段
    for edge in data.get("edges", []):
        for field in ("source", "target", "vlan", "port", "bandwidth"):
            edge.setdefault(field, "")

    return data


async def analyze_topology_stream(config_text: str, *, tenant_id: str, session_id: str) -> AsyncGenerator[str, None]:
    """
    主入口：以 SSE 格式生成拓扑解析进度事件。

    事件格式（每行 `data: <json>\\n\\n`）：
      {"type": "progress", "message": "..."}   — 进度提示
      {"type": "token", "content": "..."}       — Nanobot 输出片段（用于让用户感知运作）
      {"type": "result", "topology": {...}}      — 最终拓扑结果
      {"type": "error", "message": "..."}        — 错误信息
    """

    def _sse(payload: dict) -> str:
        return f"data: {orjson.dumps(payload).decode()}\n\n"

    yield _sse({"type": "progress", "message": "正在接收配置文本，准备调用 Nanobot 解析..."})

    raw_chunks = []
    try:
        yield _sse({"type": "progress", "message": "已连接 Nanobot，正在分析设备配置..."})

        async for token in _call_nanobot_stream(config_text, tenant_id=tenant_id, session_id=session_id):
            raw_chunks.append(token)
            yield _sse({"type": "token", "content": token})

    except Exception as e:
        logger.exception(f"[NetworkTopology] Nanobot error: {e}")
        yield _sse({"type": "error", "message": f"Nanobot 调用异常: {str(e)}"})
        return

    yield _sse({"type": "progress", "message": "Nanobot 分析完成，正在解析拓扑结构..."})

    raw_text = "".join(raw_chunks)
    try:
        topology = _parse_topology(raw_text)
    except ValueError as e:
        logger.error(f"[NetworkTopology] parse error: {e}\nraw:\n{raw_text[:500]}")
        yield _sse({"type": "error", "message": f"拓扑结构解析失败: {str(e)}"})
        return

    yield _sse({"type": "progress", "message": f"解析完成，共识别 {len(topology['nodes'])} 个节点，{len(topology['edges'])} 条连接"})
    yield _sse({"type": "result", "topology": topology})

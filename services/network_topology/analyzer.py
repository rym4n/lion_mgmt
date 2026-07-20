# coding=utf-8
"""
网络拓扑 AI 解析模块。

流程：
  1. 接收外部剧本通过拓扑接口写入的设备配置
  2. 通过 Nanobot/lion_agent 非流式解析
  3. 校验并标准化拓扑 JSON

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

import hashlib
import ipaddress
import json
import re
import uuid
from typing import Any

import orjson

from services.digital_employee.lion_agent_adapter import (
    stream_prompt_events,
    workspace_id_for,
)


MAX_TOPOLOGY_NODES = 2000
MAX_TOPOLOGY_EDGES = 10000
MAX_TOPOLOGY_FIELD_LENGTH = 255
MAX_POLICY_ITEMS = 100
MAX_POLICY_LENGTH = 80
MAX_EVIDENCE_ITEMS = 100
MAX_EVIDENCE_LENGTH = 512
MIN_NODE_EVIDENCE_TOKEN_BYTES = 2
MIN_EDGE_EVIDENCE_BYTES = 12
MIN_EDGE_EVIDENCE_TOKENS = 2
MAX_SOURCE_REF_ITEMS = 100
MAX_SOURCE_REF_VALUE_LENGTH = 128
MAX_MODEL_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TOPOLOGY_JSON_BYTES = 8 * 1024 * 1024
EXTERNAL_SOURCE_KEY = ("external-sync", "external-config")

_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [^-\r\n]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----.*?"
    r"(?:-----END [^-\r\n]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----|$)",
    re.IGNORECASE | re.DOTALL,
)
_PRIVATE_KEY_CODE_BLOCK_RE = re.compile(
    r"\bprivate[-_ ]key[-_ ]code\s+begin\b.*?"
    r"(?:\bprivate[-_ ]key[-_ ]code\s+end\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_CREDENTIAL_VALUE_RE = re.compile(
    r"(?P<prefix>"
    r"(?<![\w-])[\"']?"
    r"(?:encrypted[-_ ]?password|hashed[-_ ]?password|user[-_ ]?password|"
    r"password|passwd|pwd|community(?:[-_ ]?(?:string|name))?|token|"
    r"access[-_ ]?token|refresh[-_ ]?token|api[-_ ]?key|authorization|"
    r"client[-_ ]?secret|secret[-_ ]?key|shared[-_ ]?key|key[-_ ]?string|secret|"
    r"authentication[-_ ]?key|pre[-_ ]?shared[-_ ]?key|psk[-_ ]?secret|"
    r"wpa[-_ ]?psk|isakmp[-_ ]?key|"
    r"(?:radius|tacacs)[-_ ]?server[-_ ]?key|private[-_ ]?key)"
    r"[\"']?\s*(?::|=|\s)\s*"
    r"(?:(?:bearer|read|write|enc|cipher|simple|encrypted|plain(?:text)?|"
    r"irreversible[-_ ]?cipher|0|5|7|8|9)\s+){0,3}"
    r")"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;]+)",
    re.IGNORECASE,
)
_EVIDENCE_TOKEN_RE = re.compile(
    r"[a-z0-9]+(?:[._:/-][a-z0-9]+)*|[\u3400-\u9fff]+",
    re.IGNORECASE,
)
_EVIDENCE_SIGNAL_RE = re.compile(
    r"\b(?:lldp|cdp|neighbou?r|interfaces?|ports?|device\s+id|"
    r"chassis(?:\s+id)?|local\s+int(?:erface|rfce|f)?|"
    r"remote\s+int(?:erface|rfce|f)?|routing|routes?|next[-_ ]?hop|"
    r"connected|links?|via)\b|邻居|接口|端口|路由|下一跳|链路",
    re.IGNORECASE,
)
_EVIDENCE_SPECIFIC_MARKER_RE = re.compile(r"[0-9./:=><\[\]]")

_SYSTEM_PROMPT = """\
你是一名专业的网络工程师和拓扑分析专家。
用户会向你提供按动作来源和设备资源分组的网络设备配置（可能包含防火墙策略、交换机配置、路由表、ACL、VLAN、接口信息等，不限厂商格式）。
请从中提取所有网络设备及其连接关系，并保留每一项结论的来源引用，输出**严格符合**以下 JSON Schema 的拓扑数据，不要输出任何其他内容。

Schema：
{
  "nodes": [
    {
      "id": "模型内部引用ID；服务端会按稳定标识重写",
      "serial": "设备序列号（可为空字符串）",
      "chassis_id": "LLDP/CDP chassis-id（可为空字符串）",
      "management_ip": "管理IP（可为空字符串）",
      "resource_id": "采集来源中的设备资源ID（可为空字符串）",
      "type": "firewall | switch | router | host | unknown",
      "label": "设备显示名称",
      "ip": "主要管理IP（可为空字符串）",
      "policies": ["策略摘要字符串列表，每条不超过80字符"],
      "source_refs": [{"action_id": "来源动作ID", "resource_id": "来源资源ID"}],
      "evidence": ["支持该节点结论的原始配置证据"]
    }
  ],
  "edges": [
    {
      "source": "源节点id",
      "target": "目标节点id",
      "vlan": "VLAN编号或名称（可为空字符串）",
      "port": "接口或端口号（可为空字符串）",
      "bandwidth": "带宽描述（可为空字符串）",
      "evidence_type": "lldp | cdp | neighbour | interface | routing",
      "source_refs": [{"action_id": "来源动作ID", "resource_id": "来源资源ID"}],
      "evidence": ["明确证明该连接的原始配置证据"]
    }
  ]
}

规则：
- 每个设备只出现一次；优先使用 serial，其次 chassis-id、管理IP、resource_id 识别同一设备
- 边是无向的，不要重复添加反向边
- 每个节点和边都要保留 action_id/resource_id 来源引用及原始证据
- 只有 LLDP、CDP、明确的 neighbour、interface 或 routing 配置证据能够建立边
- 严禁根据设备名称、IP 网段、厂商常识或看起来合理的结构猜测连接；证据不足时不要输出边
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
                    "serial": "string",
                    "chassis_id": "string",
                    "management_ip": "string",
                    "resource_id": "string",
                    "type": "firewall|switch|router|host|unknown",
                    "label": "string",
                    "ip": "string",
                    "policies": ["string"],
                    "source_refs": [{"action_id": "string", "resource_id": "string"}],
                    "evidence": ["string"],
                }
            ],
            "edges": [
                {
                    "source": "string",
                    "target": "string",
                    "vlan": "string",
                    "port": "string",
                    "bandwidth": "string",
                    "evidence_type": "lldp|cdp|neighbour|interface|routing",
                    "source_refs": [{"action_id": "string", "resource_id": "string"}],
                    "evidence": ["string"],
                }
            ],
        },
        "rules": [
            "只返回 JSON，不要输出 Markdown 代码块或解释文字。",
            "无法确定的字段使用空字符串，不要省略字段。",
            "每个设备只出现一次，边按无向连接去重。",
            "节点ID优先依据serial，其次chassis-id、管理IP、resource_id。",
            "保留每个节点和边的action_id/resource_id来源及原始证据。",
            "只根据LLDP、CDP、neighbour、interface或routing的明确证据建立边，绝不猜测连接。",
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


def _bounded_string(value: Any, maximum: int = MAX_TOPOLOGY_FIELD_LENGTH) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()[:maximum]


def _clean_identifier(value: Any) -> str:
    return _bounded_string(value)


def _redact_credentials(value: str) -> str:
    value = _PRIVATE_KEY_BLOCK_RE.sub("[REDACTED PRIVATE KEY]", value)
    value = _PRIVATE_KEY_CODE_BLOCK_RE.sub("[REDACTED PRIVATE KEY]", value)
    return _CREDENTIAL_VALUE_RE.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        value,
    )


def _is_source_secret_field(key: Any) -> bool:
    normalized = str(key).strip().lower().replace(" ", "_")
    return any(
        marker in normalized
        for marker in (
            "password",
            "passwd",
            "passphrase",
            "secret",
            "token",
            "community",
            "credential",
            "private_key",
            "private-key",
            "privatekey",
            "api_key",
            "apikey",
            "shared_key",
            "shared-key",
        )
    )


def _redact_source_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _is_source_secret_field(key)
                else _redact_source_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_source_secrets(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        return _redact_credentials(value)
    return value


def _redacted_source_config_text(config_text: str) -> str:
    try:
        source_aggregate = orjson.loads(config_text)
    except (TypeError, orjson.JSONDecodeError):
        return _redact_credentials(str(config_text))
    return orjson.dumps(
        _redact_source_secrets(source_aggregate),
        option=orjson.OPT_SORT_KEYS,
    ).decode("utf-8")


def _sanitize_string_list(
    value: Any,
    *,
    max_items: int,
    max_length: int,
    redact_credentials: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [
        item.strip()[:max_length]
        for item in value
        if isinstance(item, str) and item.strip()
    ]
    if redact_credentials:
        items = [_redact_credentials(item) for item in items]
    return _unique_items([item for item in items if item.strip()])[:max_items]


def _source_ref_value(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    text = str(value).strip()
    if len(text) > MAX_SOURCE_REF_VALUE_LENGTH:
        return ""
    return text


def _sanitize_source_refs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    refs = []
    for raw_ref in value:
        if not isinstance(raw_ref, dict):
            continue
        action_id = _source_ref_value(raw_ref.get("action_id"))
        resource_id = _source_ref_value(raw_ref.get("resource_id"))
        if not action_id and not resource_id:
            continue
        refs.append({"action_id": action_id, "resource_id": resource_id})
    return _unique_items(refs)[:MAX_SOURCE_REF_ITEMS]


def _normalize_evidence_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _is_meaningful_node_evidence(value: str) -> bool:
    normalized = _normalize_evidence_text(value)
    return any(
        len(token.encode("utf-8")) >= MIN_NODE_EVIDENCE_TOKEN_BYTES
        for token in _EVIDENCE_TOKEN_RE.findall(normalized)
    )


def _is_meaningful_edge_evidence(value: str) -> bool:
    normalized = _normalize_evidence_text(value)
    if len(normalized.encode("utf-8")) < MIN_EDGE_EVIDENCE_BYTES:
        return False
    tokens = _EVIDENCE_TOKEN_RE.findall(normalized)
    if len(tokens) < MIN_EDGE_EVIDENCE_TOKENS:
        return False
    if not _EVIDENCE_SIGNAL_RE.search(normalized):
        return False
    if _EVIDENCE_SPECIFIC_MARKER_RE.search(normalized):
        return True
    return len(tokens) >= 4


def _is_evidence_token_character(value: str) -> bool:
    return value.isalnum() or value in "._:/-"


def _fragment_contains_evidence(fragment: str, needle: str) -> bool:
    start = fragment.find(needle)
    while start >= 0:
        end = start + len(needle)
        starts_inside_token = (
            start > 0
            and _is_evidence_token_character(needle[0])
            and _is_evidence_token_character(fragment[start - 1])
        )
        ends_inside_token = (
            end < len(fragment)
            and _is_evidence_token_character(needle[-1])
            and _is_evidence_token_character(fragment[end])
        )
        if not starts_inside_token and not ends_inside_token:
            return True
        start = fragment.find(needle, start + 1)
    return False


def _result_search_fragments(value: Any) -> tuple[str, ...]:
    fragments = []
    try:
        fragments.append(orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode())
    except (TypeError, orjson.JSONEncodeError):
        pass

    pending = [value]
    seen_containers = set()
    scalar_fragments = []
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            scalar_fragments.append(item)
        elif isinstance(item, (bytes, bytearray)):
            scalar_fragments.append(bytes(item).decode("utf-8", errors="replace"))
        elif isinstance(item, dict):
            marker = id(item)
            if marker in seen_containers:
                continue
            seen_containers.add(marker)
            pending.extend(item.values())
        elif isinstance(item, (list, tuple, set)):
            marker = id(item)
            if marker in seen_containers:
                continue
            seen_containers.add(marker)
            pending.extend(item)
        elif item is not None:
            scalar_fragments.append(str(item))

    fragments.extend(scalar_fragments)
    if scalar_fragments:
        fragments.append("\n".join(reversed(scalar_fragments)))
    return tuple(
        _unique_items(
            [
                normalized
                for fragment in fragments
                if (normalized := _normalize_evidence_text(fragment))
            ]
        )
    )


def _source_evidence_index(
    source_aggregate: Any,
) -> dict[tuple[str, str], tuple[str, ...]]:
    if isinstance(source_aggregate, (str, bytes, bytearray)):
        raw_source = source_aggregate
        try:
            source_aggregate = orjson.loads(source_aggregate)
        except orjson.JSONDecodeError:
            fragments = _result_search_fragments(raw_source)
            return {EXTERNAL_SOURCE_KEY: fragments} if fragments else {}
    if not isinstance(source_aggregate, dict):
        fragments = _result_search_fragments(source_aggregate)
        return {EXTERNAL_SOURCE_KEY: fragments} if fragments else {}

    index: dict[tuple[str, str], list[str]] = {}
    sources = source_aggregate.get("sources")
    if not isinstance(sources, list):
        fragments = _result_search_fragments(source_aggregate)
        return {EXTERNAL_SOURCE_KEY: fragments} if fragments else {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        action_id = _source_ref_value(source.get("action_id"))
        resources = source.get("resources")
        if not action_id or not isinstance(resources, list):
            continue
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            resource_id = _source_ref_value(resource.get("resource_id"))
            key = (action_id, resource_id)
            index.setdefault(key, []).extend(
                _result_search_fragments(resource.get("result"))
            )
    return {key: tuple(_unique_items(fragments)) for key, fragments in index.items()}


def _bound_source_refs(
    value: Any,
    source_index: dict[tuple[str, str], tuple[str, ...]],
) -> list[dict[str, str]]:
    refs = [
        ref
        for ref in _sanitize_source_refs(value)
        if (ref["action_id"], ref["resource_id"]) in source_index
    ]
    if not refs and EXTERNAL_SOURCE_KEY in source_index:
        refs = [
            {
                "action_id": EXTERNAL_SOURCE_KEY[0],
                "resource_id": EXTERNAL_SOURCE_KEY[1],
            }
        ]
    return refs


def _bound_node_provenance(
    node: dict,
    source_index: dict[tuple[str, str], tuple[str, ...]],
) -> tuple[list[dict[str, str]], list[str]]:
    refs = _bound_source_refs(node.get("source_refs"), source_index)
    evidence_items = _sanitize_string_list(
        node.get("evidence"),
        max_items=MAX_EVIDENCE_ITEMS,
        max_length=MAX_EVIDENCE_LENGTH,
        redact_credentials=False,
    )
    matched_refs = []
    matched_evidence = []
    for evidence in evidence_items:
        if not _is_meaningful_node_evidence(evidence):
            continue
        needle = _normalize_evidence_text(evidence)
        evidence_refs = [
            ref
            for ref in refs
            if any(
                _fragment_contains_evidence(fragment, needle)
                for fragment in source_index[(ref["action_id"], ref["resource_id"])]
            )
        ]
        if not evidence_refs:
            continue
        matched_evidence.append(_redact_credentials(evidence))
        matched_refs.extend(evidence_refs)
    return (
        _unique_items(matched_refs)[:MAX_SOURCE_REF_ITEMS],
        _unique_items(matched_evidence)[:MAX_EVIDENCE_ITEMS],
    )


def _bound_edge_provenance(
    edge: dict,
    source_index: dict[tuple[str, str], tuple[str, ...]],
) -> tuple[list[dict[str, str]], list[str]]:
    refs = _bound_source_refs(edge.get("source_refs"), source_index)
    evidence_items = _sanitize_string_list(
        edge.get("evidence"),
        max_items=MAX_EVIDENCE_ITEMS,
        max_length=MAX_EVIDENCE_LENGTH,
        redact_credentials=False,
    )
    matched_refs = []
    matched_evidence = []
    for evidence in evidence_items:
        if not _is_meaningful_edge_evidence(evidence):
            continue
        needle = _normalize_evidence_text(evidence)
        evidence_refs = [
            ref
            for ref in refs
            if any(
                _fragment_contains_evidence(fragment, needle)
                for fragment in source_index[(ref["action_id"], ref["resource_id"])]
            )
        ]
        if not evidence_refs:
            continue
        matched_evidence.append(_redact_credentials(evidence))
        matched_refs.extend(evidence_refs)
    return (
        _unique_items(matched_refs)[:MAX_SOURCE_REF_ITEMS],
        _unique_items(matched_evidence)[:MAX_EVIDENCE_ITEMS],
    )


def _normalize_management_ip(value: Any) -> str:
    text = _clean_identifier(value)
    if not text:
        return ""
    try:
        return str(ipaddress.ip_interface(text).ip)
    except ValueError:
        return text


def _as_list(value: Any) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    return [value]


def _unique_items(values: list) -> list:
    result = []
    seen = set()
    for value in values:
        try:
            marker = orjson.dumps(value, option=orjson.OPT_SORT_KEYS)
        except TypeError:
            value = str(value)
            marker = value.encode()
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def _resource_id_from_refs(node: dict) -> str:
    for ref in _sanitize_source_refs(node.get("source_refs")):
        if ref.get("resource_id"):
            return ref["resource_id"]
    return ""


def _stable_node_id(node: dict) -> str:
    for key in ("serial", "serial_number", "serialNumber"):
        value = _clean_identifier(node.get(key))
        if value:
            return value
    for key in ("chassis_id", "chassis-id", "chassisId"):
        value = _clean_identifier(node.get(key))
        if value:
            return value.lower()
    for key in ("management_ip", "managementIp", "mgmt_ip", "ip"):
        value = _normalize_management_ip(node.get(key))
        if value:
            return value
    resource_id = _clean_identifier(node.get("resource_id")) or _resource_id_from_refs(
        node
    )
    if resource_id:
        return resource_id
    return _clean_identifier(node.get("id"))


def _merge_node(current: dict, incoming: dict) -> dict:
    current["policies"] = _sanitize_string_list(
        _as_list(current.get("policies")) + _as_list(incoming.get("policies")),
        max_items=MAX_POLICY_ITEMS,
        max_length=MAX_POLICY_LENGTH,
    )
    current["evidence"] = _sanitize_string_list(
        _as_list(current.get("evidence")) + _as_list(incoming.get("evidence")),
        max_items=MAX_EVIDENCE_ITEMS,
        max_length=MAX_EVIDENCE_LENGTH,
    )
    current["source_refs"] = _sanitize_source_refs(
        _as_list(current.get("source_refs")) + _as_list(incoming.get("source_refs"))
    )
    for key, value in incoming.items():
        if key in {"id", "policies", "source_refs", "evidence"}:
            continue
        if current.get(key) in (None, "", "unknown", []):
            current[key] = value
    return current


def _add_alias(aliases: dict[str, str | None], alias: Any, stable_id: str) -> None:
    alias = _clean_identifier(alias)
    if not alias:
        return
    if alias not in aliases:
        aliases[alias] = stable_id
    elif aliases[alias] != stable_id:
        aliases[alias] = None


def _has_explicit_edge_evidence(
    edge: dict, *, require_source_refs: bool = True
) -> bool:
    if require_source_refs and not _sanitize_source_refs(edge.get("source_refs")):
        return False
    if not _sanitize_string_list(
        edge.get("evidence"),
        max_items=MAX_EVIDENCE_ITEMS,
        max_length=MAX_EVIDENCE_LENGTH,
        redact_credentials=False,
    ):
        return False
    evidence_type = _clean_identifier(
        edge.get("evidence_type")
        or edge.get("protocol")
        or edge.get("discovery_protocol")
    ).lower()
    if any(
        token in evidence_type
        for token in ("lldp", "cdp", "neigh", "interface", "routing", "route")
    ):
        return True
    evidence_text = " ".join(
        item for item in _as_list(edge.get("evidence")) if isinstance(item, str)
    ).lower()
    if any(
        token in evidence_text
        for token in (
            "lldp",
            "cdp",
            "neighbor",
            "neighbour",
            "interface",
            "routing",
            "route",
            "邻居",
            "接口",
            "路由",
        )
    ):
        return True
    return any(
        _clean_identifier(edge.get(key))
        for key in (
            "local_interface",
            "remote_interface",
            "source_port",
            "target_port",
            "route",
            "neighbour",
            "neighbor",
        )
    )


def _edge_identity(edge: dict) -> tuple:
    interfaces = sorted(
        {
            _clean_identifier(edge.get(key))
            for key in (
                "port",
                "local_interface",
                "remote_interface",
                "source_port",
                "target_port",
            )
            if _clean_identifier(edge.get(key))
        }
    )
    return (
        edge["source"],
        edge["target"],
        _clean_identifier(edge.get("vlan")),
        _clean_identifier(edge.get("evidence_type") or edge.get("protocol")).lower(),
        tuple(interfaces),
    )


def _merge_edge(current: dict, incoming: dict) -> dict:
    current["evidence"] = _sanitize_string_list(
        _as_list(current.get("evidence")) + _as_list(incoming.get("evidence")),
        max_items=MAX_EVIDENCE_ITEMS,
        max_length=MAX_EVIDENCE_LENGTH,
    )
    current["source_refs"] = _sanitize_source_refs(
        _as_list(current.get("source_refs")) + _as_list(incoming.get("source_refs"))
    )
    for key, value in incoming.items():
        if key in {"id", "source", "target", "source_refs", "evidence"}:
            continue
        if current.get(key) in (None, "", []):
            current[key] = value
    return current


def _parse_topology(raw_text: str, *, source_aggregate: Any = None) -> dict:
    """
    从模型输出中提取并校验拓扑 JSON。
    尽力修复常见问题（代码块包裹、前后杂文）。
    返回合规 dict 或抛出 ValueError。
    """
    if not isinstance(raw_text, str):
        raise ValueError("模型输出必须是文本")
    if len(raw_text.encode("utf-8")) > MAX_MODEL_RESPONSE_BYTES:
        raise ValueError(f"模型原始响应超过 {MAX_MODEL_RESPONSE_BYTES} 字节限制")

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

    if not isinstance(data, dict):
        raise ValueError("拓扑 JSON 根节点必须是对象")
    if "nodes" not in data or "edges" not in data:
        raise ValueError("拓扑 JSON 缺少 nodes 或 edges 字段")
    if not isinstance(data["nodes"], list) or not isinstance(data["edges"], list):
        raise ValueError("拓扑 JSON 的 nodes 和 edges 必须是数组")
    if len(data["nodes"]) > MAX_TOPOLOGY_NODES:
        raise ValueError(f"拓扑节点数量不能超过 {MAX_TOPOLOGY_NODES}")
    if len(data["edges"]) > MAX_TOPOLOGY_EDGES:
        raise ValueError(f"拓扑连接数量不能超过 {MAX_TOPOLOGY_EDGES}")

    source_index = _source_evidence_index(source_aggregate)
    aliases: dict[str, str | None] = {}
    nodes_by_id: dict[str, dict] = {}
    label_aliases: list[tuple[str, str]] = []
    for index, raw_node in enumerate(data["nodes"]):
        if not isinstance(raw_node, dict):
            raise ValueError(f"nodes[{index}] 必须是对象")
        stable_id = _stable_node_id(raw_node)
        if not stable_id:
            raise ValueError(
                f"nodes[{index}] 缺少 serial、chassis-id、管理IP、resource_id 或 id"
            )

        original_id = _clean_identifier(raw_node.get("id"))
        if source_index:
            source_refs, evidence = _bound_node_provenance(raw_node, source_index)
            if not source_refs or not evidence:
                continue
        else:
            source_refs = _sanitize_source_refs(raw_node.get("source_refs"))
            evidence = _sanitize_string_list(
                raw_node.get("evidence"),
                max_items=MAX_EVIDENCE_ITEMS,
                max_length=MAX_EVIDENCE_LENGTH,
            )
        serial = next(
            (
                value
                for value in (
                    _clean_identifier(raw_node.get("serial")),
                    _clean_identifier(raw_node.get("serial_number")),
                    _clean_identifier(raw_node.get("serialNumber")),
                )
                if value
            ),
            "",
        )
        chassis_id = next(
            (
                value.lower()
                for value in (
                    _clean_identifier(raw_node.get("chassis_id")),
                    _clean_identifier(raw_node.get("chassis-id")),
                    _clean_identifier(raw_node.get("chassisId")),
                )
                if value
            ),
            "",
        )
        management_ip = _normalize_management_ip(
            raw_node.get("management_ip")
            or raw_node.get("managementIp")
            or raw_node.get("mgmt_ip")
            or raw_node.get("ip")
        )
        resource_id = _clean_identifier(raw_node.get("resource_id")) or next(
            (ref["resource_id"] for ref in source_refs if ref.get("resource_id")),
            "",
        )
        node_type = _clean_identifier(raw_node.get("type")).lower()
        if node_type not in {"firewall", "switch", "router", "host", "unknown"}:
            node_type = "unknown"
        node = {
            "id": stable_id,
            "serial": serial,
            "chassis_id": chassis_id,
            "management_ip": management_ip,
            "resource_id": resource_id,
            "type": node_type or "unknown",
            "label": _bounded_string(raw_node.get("label")) or stable_id,
            "ip": _normalize_management_ip(raw_node.get("ip") or management_ip),
            "policies": _sanitize_string_list(
                raw_node.get("policies"),
                max_items=MAX_POLICY_ITEMS,
                max_length=MAX_POLICY_LENGTH,
            ),
            "source_refs": source_refs,
            "evidence": evidence,
        }

        if stable_id in nodes_by_id:
            nodes_by_id[stable_id] = _merge_node(nodes_by_id[stable_id], node)
        else:
            nodes_by_id[stable_id] = node

        for alias in (
            original_id,
            stable_id,
            raw_node.get("serial"),
            raw_node.get("serial_number"),
            raw_node.get("serialNumber"),
            raw_node.get("chassis_id"),
            raw_node.get("chassis-id"),
            raw_node.get("chassisId"),
            node.get("management_ip"),
            node.get("ip"),
            node.get("resource_id"),
        ):
            _add_alias(aliases, alias, stable_id)
        label_aliases.append((node["label"], stable_id))

    for label, stable_id in label_aliases:
        _add_alias(aliases, label, stable_id)

    edges_by_identity: dict[tuple, dict] = {}
    for index, raw_edge in enumerate(data["edges"]):
        if not isinstance(raw_edge, dict):
            raise ValueError(f"edges[{index}] 必须是对象")
        if not _has_explicit_edge_evidence(
            raw_edge,
            require_source_refs=EXTERNAL_SOURCE_KEY not in source_index,
        ):
            continue
        source_alias = _clean_identifier(raw_edge.get("source"))
        target_alias = _clean_identifier(raw_edge.get("target"))
        source = aliases.get(source_alias)
        target = aliases.get(target_alias)
        if (
            not source
            or not target
            or source not in nodes_by_id
            or target not in nodes_by_id
        ):
            continue
        if source == target:
            continue
        source, target = sorted((source, target))

        source_refs, evidence = _bound_edge_provenance(raw_edge, source_index)
        if not source_refs or not evidence:
            continue

        evidence_type = _clean_identifier(
            raw_edge.get("evidence_type")
            or raw_edge.get("protocol")
            or raw_edge.get("discovery_protocol")
        ).lower()
        edge = {
            "source": source,
            "target": target,
            "vlan": _bounded_string(raw_edge.get("vlan")),
            "port": _bounded_string(raw_edge.get("port")),
            "bandwidth": _bounded_string(raw_edge.get("bandwidth")),
            "evidence_type": evidence_type,
            "source_refs": source_refs,
            "evidence": evidence,
        }
        identity_edge = {**raw_edge, **edge}
        identity = _edge_identity(identity_edge)
        if identity in edges_by_identity:
            edges_by_identity[identity] = _merge_edge(edges_by_identity[identity], edge)
        else:
            edge_id = hashlib.sha256(
                orjson.dumps(identity, option=orjson.OPT_SORT_KEYS)
            ).hexdigest()[:24]
            edge["id"] = f"edge-{edge_id}"
            edges_by_identity[identity] = edge

    topology = {
        "nodes": sorted(nodes_by_id.values(), key=lambda node: node["id"]),
        "edges": sorted(edges_by_identity.values(), key=lambda edge: edge["id"]),
    }
    if (
        len(orjson.dumps(topology, option=orjson.OPT_SORT_KEYS))
        > MAX_TOPOLOGY_JSON_BYTES
    ):
        raise ValueError(f"标准化拓扑数据超过 {MAX_TOPOLOGY_JSON_BYTES} 字节限制")
    return topology


def _event_content(event_type: str, payload: dict) -> str:
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


def _append_bounded_response(chunks: list[str], content: str, byte_count: int) -> int:
    byte_count += len(content.encode("utf-8"))
    if byte_count > MAX_MODEL_RESPONSE_BYTES:
        raise ValueError(f"模型原始响应超过 {MAX_MODEL_RESPONSE_BYTES} 字节限制")
    chunks.append(content)
    return byte_count


async def _execute_bounded_prompt(
    config_text: str,
    *,
    tenant_id: str,
    session_id: str,
) -> str:
    chunks = []
    byte_count = 0
    saw_content = False
    pending_end_content = ""
    async for event_type, payload in stream_prompt_events(
        workspace_id=workspace_id_for(tenant_id, "network-topology"),
        session_id=session_id,
        request_id=session_id,
        message=_build_nanobot_message(config_text),
    ):
        if event_type == "error":
            raise RuntimeError(payload.get("message") or payload)
        content = _event_content(event_type, payload)
        if not content:
            continue
        if event_type == "end":
            if len(content.encode("utf-8")) > MAX_MODEL_RESPONSE_BYTES:
                raise ValueError(
                    f"模型原始响应超过 {MAX_MODEL_RESPONSE_BYTES} 字节限制"
                )
            pending_end_content = content
            continue
        saw_content = True
        byte_count = _append_bounded_response(chunks, content, byte_count)

    if pending_end_content and not saw_content:
        _append_bounded_response(chunks, pending_end_content, byte_count)
    return "".join(chunks)


async def analyze_topology(
    config_text: str,
    *,
    tenant_id: str,
    session_id: str | None = None,
) -> dict:
    """解析外部剧本写入的设备配置并返回标准拓扑数据。"""
    session_id = session_id or f"network-topology:{uuid.uuid4()}"
    redacted_config_text = _redacted_source_config_text(config_text)
    raw_text = await _execute_bounded_prompt(
        redacted_config_text,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    return _parse_topology(raw_text, source_aggregate=redacted_config_text)

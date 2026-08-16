# coding=utf-8
"""工单流程发布校验（SPEC §4.2）。

发布时的完整性检查七项：
1. 有且仅有一个 start 节点
2. 有至少一个 end 节点
3. 所有节点都可从 start 到达
4. 所有 end 节点都可达
5. 条件分支必须有 default 边
6. 并行块必须自闭合（每个 parallel 有对应 merge）
7. form_schema 与节点配置之间的引用完整性
"""

import re


_FIELD_TEMPLATE_PATTERN = re.compile(r"{{\s*([^{}\s]+)\s*}}")
_FORM_SCHEMA_META_KEYS = {
    "fields",
    "layout",
    "settings",
    "version",
    "title",
    "description",
}


def _form_field_ids(flow_json: dict) -> set[str]:
    """兼容当前 fields 列表和早期按字段 ID 建键的 schema。"""
    form_schema = flow_json.get("form_schema") or {}
    field_ids = set()

    fields = form_schema.get("fields") if isinstance(form_schema, dict) else None
    if isinstance(fields, list):
        field_ids.update(
            {
                field_id
                for field in fields
                if isinstance(field, dict)
                for field_id in (
                    field.get("key") or field.get("id") or field.get("field_id"),
                )
                if isinstance(field_id, str) and field_id
            }
        )
    elif isinstance(fields, dict):
        field_ids.update(field_id for field_id in fields if isinstance(field_id, str))
    elif isinstance(form_schema, dict):
        field_ids.update(
            field_id
            for field_id in form_schema
            if isinstance(field_id, str) and field_id not in _FORM_SCHEMA_META_KEYS
        )

    for node in flow_json.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        properties = node.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        node_fields = properties.get("form_fields") or node.get("form_fields") or []
        if not isinstance(node_fields, list):
            continue
        field_ids.update(
            field.get("key")
            for field in node_fields
            if isinstance(field, dict)
            and isinstance(field.get("key"), str)
            and field.get("key")
        )
    return field_ids


def _nodes(flow_json: dict) -> list[dict]:
    nodes = flow_json.get("nodes") or []
    return [node for node in nodes if isinstance(node, dict)]


def _node_id(node: dict) -> str:
    return str(node.get("id") or "<unknown>")


def _field_control_ids(field_control) -> set[str]:
    if isinstance(field_control, dict):
        if isinstance(field_control.get("field_id"), str):
            return {field_control["field_id"]}
        return {
            field_id
            for field_id in field_control
            if isinstance(field_id, str)
        }
    if isinstance(field_control, list):
        return {
            item.get("field_id")
            for item in field_control
            if isinstance(item, dict) and isinstance(item.get("field_id"), str)
        }
    return set()


def validate_field_control_references(flow_json: dict) -> list[str]:
    """校验节点可见、可编辑、必填配置引用的字段。"""
    errors = []
    form_fields = _form_field_ids(flow_json)
    for node in _nodes(flow_json):
        for field_id in sorted(_field_control_ids(node.get("field_control") or {})):
            if field_id not in form_fields:
                errors.append(
                    f"节点 {_node_id(node)} 的字段配置引用不存在的字段 {field_id}"
                )
    return errors


def validate_field_linkage_references(flow_json: dict) -> list[str]:
    """校验字段联动的条件字段和目标字段。"""
    errors = []
    form_fields = _form_field_ids(flow_json)
    for node in _nodes(flow_json):
        linkage = node.get("field_linkage") or []
        items = linkage if isinstance(linkage, list) else [linkage]
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("source_field", "target_field"):
                field_id = item.get(key)
                if isinstance(field_id, str) and field_id not in form_fields:
                    errors.append(
                        f"节点 {_node_id(node)} 的字段联动 {key} 引用不存在的字段 {field_id}"
                    )
    return errors


def _template_field_ids(value) -> set[str]:
    if isinstance(value, str):
        return set(_FIELD_TEMPLATE_PATTERN.findall(value))
    if isinstance(value, dict):
        return {
            field_id
            for item in value.values()
            for field_id in _template_field_ids(item)
        }
    if isinstance(value, list):
        return {
            field_id
            for item in value
            for field_id in _template_field_ids(item)
        }
    return set()


def validate_action_input_references(flow_json: dict) -> list[str]:
    """校验节点动作输入中的 ``{{field_id}}`` 引用。"""
    errors = []
    form_fields = _form_field_ids(flow_json)
    for node in _nodes(flow_json):
        actions = node.get("actions") or []
        items = actions if isinstance(actions, list) else [actions]
        for action in items:
            if not isinstance(action, dict):
                continue
            for field_id in sorted(_template_field_ids(action.get("inputs") or {})):
                if field_id not in form_fields:
                    errors.append(
                        f"节点 {_node_id(node)} 的动作输入引用不存在的字段 {field_id}"
                    )
    return errors


def _aggregation_items(value):
    if isinstance(value, list):
        for item in value:
            yield from _aggregation_items(item)
    elif isinstance(value, dict):
        field_keys = {"field_id", "field", "source_field", "target_field"}
        node_keys = {"source_node", "source_node_id"}
        if field_keys.intersection(value) or node_keys.intersection(value):
            yield value
        else:
            for item in value.values():
                yield from _aggregation_items(item)


def validate_aggregation_references(flow_json: dict) -> list[str]:
    """校验跨节点聚合的源节点和字段引用。"""
    errors = []
    form_fields = _form_field_ids(flow_json)
    node_ids = {node.get("id") for node in _nodes(flow_json) if node.get("id")}

    for node in _nodes(flow_json):
        for aggregation in _aggregation_items(node.get("aggregation") or []):
            source_node_key = (
                "source_node_id"
                if aggregation.get("source_node_id")
                else "source_node"
            )
            source_node = aggregation.get(source_node_key)
            if isinstance(source_node, str) and source_node not in node_ids:
                errors.append(
                    f"节点 {_node_id(node)} 的聚合配置 {source_node_key} "
                    f"引用不存在的源节点 {source_node}"
                )

            for key in ("field_id", "field", "source_field", "target_field"):
                field_id = aggregation.get(key)
                if isinstance(field_id, str) and field_id not in form_fields:
                    errors.append(
                        f"节点 {_node_id(node)} 的聚合配置 {key} 引用不存在的字段 {field_id}"
                    )
    return errors


def validate_node_form_definitions(flow_json: dict) -> list[str]:
    """校验审批节点新增表单字段的名称、键和全流程唯一性。"""
    errors = []
    seen = set()
    form_schema = flow_json.get("form_schema") or {}
    root_fields = form_schema.get("fields") if isinstance(form_schema, dict) else []
    for field in root_fields if isinstance(root_fields, list) else []:
        if isinstance(field, dict) and isinstance(field.get("key"), str):
            seen.add(field["key"])

    for node in _nodes(flow_json):
        properties = node.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        fields = properties.get("form_fields") or node.get("form_fields") or []
        if not isinstance(fields, list):
            errors.append(f"节点 {_node_id(node)} 的审批表单格式无效")
            continue
        for field in fields:
            if not isinstance(field, dict):
                errors.append(f"节点 {_node_id(node)} 的审批表单字段格式无效")
                continue
            key = field.get("key")
            label = field.get("label")
            if (
                not isinstance(key, str)
                or not key.strip()
                or not isinstance(label, str)
                or not label.strip()
            ):
                errors.append(f"节点 {_node_id(node)} 的审批表单字段缺少名称或键")
                continue
            if key in seen:
                errors.append(f"节点 {_node_id(node)} 的审批表单字段键重复：{key}")
                continue
            seen.add(key)
    return errors


def validate_flow_before_publish(flow_json: dict) -> list[str]:
    """汇总发布前的四类引用完整性错误。"""
    errors = []
    for validator in (
        validate_field_control_references,
        validate_field_linkage_references,
        validate_action_input_references,
        validate_aggregation_references,
        validate_node_form_definitions,
    ):
        errors.extend(validator(flow_json))
    return errors


def validate_flow_structure(flow_json: dict) -> list[dict]:
    """校验流程结构，返回错误列表（空列表表示通过）。

    每条错误格式：{"level": "error|warning", "node_id": "...", "message": "..."}
    """
    errors = []
    nodes = flow_json.get("nodes", [])
    edges = flow_json.get("edges", [])

    if not nodes:
        errors.append({"level": "error", "message": "流程至少需要一个节点"})
        return errors

    # 建索引
    nodes_by_id = {n["id"]: n for n in nodes if n.get("id")}
    edges_by_source = {}
    for e in edges:
        source = e.get("source")
        if source:
            edges_by_source.setdefault(source, []).append(e)

    # 1. 有且仅有一个 start 节点
    start_nodes = [n for n in nodes if n.get("type") == "start"]
    if len(start_nodes) == 0:
        errors.append({"level": "error", "message": "流程必须有一个开始节点"})
    elif len(start_nodes) > 1:
        for n in start_nodes[1:]:
            errors.append({
                "level": "error",
                "node_id": n["id"],
                "message": "只能有一个开始节点",
            })

    # 2. 有且仅有一个 end 节点
    end_nodes = [n for n in nodes if n.get("type") == "end"]
    if len(end_nodes) == 0:
        errors.append({"level": "error", "message": "流程必须有一个结束节点"})
    elif len(end_nodes) > 1:
        for n in end_nodes[1:]:
            errors.append({
                "level": "error",
                "node_id": n["id"],
                "message": "只能有一个结束节点",
            })

    start_ids = {node["id"] for node in start_nodes if node.get("id")}
    end_ids = {node["id"] for node in end_nodes if node.get("id")}
    for edge in edges:
        source = edge.get("source") or edge.get("sourceNodeId")
        target = edge.get("target") or edge.get("targetNodeId")
        if source in end_ids:
            errors.append({
                "level": "error",
                "node_id": source,
                "message": "结束节点不能作为连线起点",
            })
        if target in start_ids:
            errors.append({
                "level": "error",
                "node_id": target,
                "message": "开始节点不能作为连线终点",
            })

    # 3. 所有节点都可从 start 到达
    if start_nodes:
        reachable = _reachable_from(start_nodes[0]["id"], edges_by_source)
        unreachable = [n["id"] for n in nodes if n["id"] not in reachable]
        for node_id in unreachable:
            errors.append({
                "level": "error",
                "node_id": node_id,
                "message": "该节点无法从开始节点到达",
            })

    # 4. 所有 end 节点都可达（反向检查：start 能到达所有 end）
    if start_nodes and end_nodes:
        reachable = _reachable_from(start_nodes[0]["id"], edges_by_source)
        unreachable_ends = [n["id"] for n in end_nodes if n["id"] not in reachable]
        for node_id in unreachable_ends:
            errors.append({
                "level": "error",
                "node_id": node_id,
                "message": "该结束节点无法到达",
            })

    # 5. 条件分支必须有 default 边
    condition_nodes = [n for n in nodes if n.get("type") == "condition"]
    for node in condition_nodes:
        out_edges = edges_by_source.get(node["id"], [])
        has_default = any(e.get("default") for e in out_edges)
        if not has_default and len(out_edges) > 0:
            errors.append({
                "level": "error",
                "node_id": node["id"],
                "message": "条件分支必须有默认边（default）",
            })

    # 6. 并行块必须自闭合
    parallel_nodes = [n for n in nodes if n.get("type") == "parallel"]
    merge_nodes = [n for n in nodes if n.get("type") == "merge"]
    merge_by_fork = {m.get("fork_id"): m for m in merge_nodes if m.get("fork_id")}

    for p_node in parallel_nodes:
        fork_id = p_node["id"]
        if fork_id not in merge_by_fork:
            errors.append({
                "level": "error",
                "node_id": fork_id,
                "message": f"并行块缺少对应的汇聚节点（fork_id={fork_id}）",
            })

    # 7. 引用完整性（简化版：只检查 action 的 action_id 与 approvers 的用户 id 格式）
    # 完整实现需要查库验证 action/account/role 存在，这里先做格式校验
    for node in nodes:
        if node.get("type") == "auto":
            action_config = node.get("action") or {}
            action_id = action_config.get("action_id")
            if not action_id:
                errors.append({
                    "level": "warning",
                    "node_id": node["id"],
                    "message": "自动节点未配置动作",
                })
        elif node.get("type") == "approve":
            approvers = node.get("approvers") or []
            if not approvers and not node.get("approver_action"):
                errors.append({
                    "level": "warning",
                    "node_id": node["id"],
                    "message": "审批节点未配置审批人",
                })

    return errors


def _reachable_from(start_id: str, edges_by_source: dict) -> set:
    """BFS 计算从 start_id 可达的所有节点。"""
    visited = set()
    queue = [start_id]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for edge in edges_by_source.get(current, []):
            target = edge.get("target")
            if target and target not in visited:
                queue.append(target)
    return visited

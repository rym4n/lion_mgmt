# coding=utf-8
import copy
import re

from core.error_code import ErrorCode
from core.maintenance import DEFAULT_TENANT_ID
from libs.error_code.exception import CustomError
from models import PlaybookVersion


def find_start_node(flow_json):
    if not isinstance(flow_json, dict):
        return None
    for node in flow_json.get("nodes") or []:
        if isinstance(node, dict) and node.get("type") == "start-node":
            return node
    return None


def referenced_start_paths(flow_json, start_node_id):
    if not start_node_id:
        return []

    pattern = re.compile(rf"\$\{{{re.escape(str(start_node_id))}\.output\.([^}}]+)\}}")
    paths = []
    seen = set()

    def visit(value):
        if isinstance(value, str):
            for match in pattern.finditer(value):
                path = tuple(part for part in match.group(1).split(".") if part)
                if path and path not in seen:
                    seen.add(path)
                    paths.append(path)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(flow_json)
    return paths


def _start_params(start_node):
    properties = (start_node or {}).get("properties") or {}
    params = properties.get("start_params") or []
    return [param for param in params if isinstance(param, dict)]


def merge_and_validate_flow_input(flow_json, caller_input):
    if caller_input is None:
        caller_input = {}
    if not isinstance(caller_input, dict):
        raise CustomError(ErrorCode.PLAYBOOK_INPUT_ERROR)

    start_node = find_start_node(flow_json)
    if not start_node:
        return copy.deepcopy(caller_input)

    merged = {}
    for param in _start_params(start_node):
        name = param.get("name")
        if name and "default" in param:
            merged[name] = copy.deepcopy(param.get("default"))
    merged.update(copy.deepcopy(caller_input))

    for path in referenced_start_paths(flow_json, start_node.get("id")):
        value = merged
        for part in path:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                raise CustomError(ErrorCode.PLAYBOOK_INPUT_ERROR)
        if value is None:
            raise CustomError(ErrorCode.PLAYBOOK_INPUT_ERROR)

    return merged


def playbook_input_schema(flow_json):
    start_node = find_start_node(flow_json)
    if not start_node:
        return []

    referenced_names = {
        path[0]
        for path in referenced_start_paths(flow_json, start_node.get("id"))
        if path
    }
    result = []
    for param in _start_params(start_node):
        name = param.get("name")
        if not name or name not in referenced_names:
            continue
        result.append(
            {
                name: {
                    "type": param.get("type"),
                    "description": param.get("desc"),
                    "default": param.get("default"),
                }
            }
        )
    return result


async def normalize_flow_task_message(message, playbook_version_model=PlaybookVersion):
    run_args = message.get("run_args") if isinstance(message, dict) else None
    if not isinstance(run_args, dict):
        raise CustomError(ErrorCode.PLAYBOOK_INPUT_ERROR)

    flow_id = run_args.get("flow_id")
    tenant_id = message.get("tenant_id")
    if not flow_id:
        raise CustomError(ErrorCode.PLAYBOOK_INPUT_ERROR)

    playbook = await playbook_version_model.get_or_none(id=flow_id, tenant_id=tenant_id)
    if not playbook and tenant_id != DEFAULT_TENANT_ID:
        # 内置巡检剧本保存在 default 租户，但会在业务租户上下文中执行。
        playbook = await playbook_version_model.get_or_none(
            id=flow_id,
            tenant_id=DEFAULT_TENANT_ID,
        )
    if not playbook:
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

    normalized = dict(message)
    normalized_args = dict(run_args)
    normalized_args["flow_input"] = merge_and_validate_flow_input(
        playbook.flow_json,
        run_args.get("flow_input"),
    )
    normalized["run_args"] = normalized_args
    return normalized

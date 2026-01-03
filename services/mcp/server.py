# coding=utf-8


import re

from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
from tortoise.expressions import Q
from tortoise.functions import Count, Max

from libs.kafka import aproduce
from libs.utils import gen_run_id
from models import Room, Playbook, PlaybookVersion
from models.user import Account
from settings import TASK_TOPIC

# Initialize MCP Server
mcp_server = Server("lion-mgmt")


@mcp_server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="execute_script",
            description="Execute a warroom automation script (playbook/flow) or action",
            inputSchema={
                "type": "object",
                "properties": {
                    "room_id": {
                        "type": "string",
                        "description": "The ID of the warroom where the task is executed",
                    },
                    "type": {
                        "type": "string",
                        "description": "Task type: flow or action",
                        "enum": ["flow", "action"],
                    },
                    "playbook_input": {
                        "type": "object",
                        "description": "Input parameters for playbook (required if type is flow)",
                        "properties": {
                            "flow_id": {
                                "type": "string",
                                "description": "Playbook version ID",
                            },
                            "flow_input": {
                                "type": "object",
                                "description": "Playbook input parameters",
                            },
                        },
                    },
                    "action_input": {
                        "type": "object",
                        "description": "Input parameters for action (required if type is action)",
                        "properties": {
                            "app_name": {
                                "type": "string",
                                "description": "App Name (English)",
                            },
                            "name": {
                                "type": "string",
                                "description": "App Name (Chinese)",
                            },
                            "app_ver": {"type": "string", "description": "App Version"},
                            "main": {
                                "type": "string",
                                "description": "Main Program File",
                            },
                            "action_input": {
                                "type": "object",
                                "description": "Action Input Parameters",
                            },
                            "resource": {
                                "type": "string",
                                "description": "Resource IDs (comma separated)",
                            },
                            "node_name": {"type": "string", "description": "Node Name"},
                            "action": {
                                "type": "string",
                                "description": "Action Name (English)",
                            },
                            "action_name": {
                                "type": "string",
                                "description": "Action Name (Chinese)",
                            },
                            "retry_times": {
                                "type": "integer",
                                "description": "Retry Times",
                                "default": 1,
                            },
                            "timeout": {
                                "type": "integer",
                                "description": "Timeout (seconds)",
                                "default": 300,
                            },
                        },
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "The ID of the agent requesting execution (optional)",
                    },
                },
                "required": ["room_id", "type"],
            },
        ),
        Tool(
            name="list_playbooks",
            description="Get a list of playbooks",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Search keyword for playbook name, remark or ID",
                    },
                    "page": {
                        "type": "integer",
                        "description": "Page number (default 1)",
                        "default": 1,
                    },
                    "size": {
                        "type": "integer",
                        "description": "Page size (default 10)",
                        "default": 10,
                    },
                },
            },
        ),
        Tool(
            name="get_playbook_inputs",
            description="Get input parameters for a specific playbook",
            inputSchema={
                "type": "object",
                "properties": {
                    "flow_id": {
                        "type": "string",
                        "description": "Playbook version ID (optional if playbook_id is provided)",
                    },
                    "playbook_id": {
                        "type": "string",
                        "description": "Playbook ID (optional, used to find latest version if flow_id is missing)",
                    },
                },
            },
        ),
    ]


async def get_playbook_input_logic(playbook_version_id):
    playbook = await PlaybookVersion.get_or_none(id=playbook_version_id)
    if not playbook:
        raise ValueError(f"Playbook version {playbook_version_id} not found")

    input_list = []

    if playbook.flow_json:
        nodes = playbook.flow_json.get("nodes", [])
        if (
            not nodes
            or "properties" not in nodes[0]
            or "start_params" not in nodes[0]["properties"]
        ):
            return input_list

        input_id = nodes[0]["id"]
        start_params = nodes[0]["properties"]["start_params"]

        param_map = {
            param["name"]: {"type": param["type"], "description": param["desc"]}
            for param in start_params
        }

        pattern = rf"{re.escape(input_id)}\.output\.([a-zA-Z0-9_\u4e00-\u9fff]+)(?=[\)}}])"

        matches = re.findall(pattern, str(playbook.flow_json))
        unique_matches = list(set(matches))  # De-duplicate
        for param_key in unique_matches:
            if param_key in param_map:
                input_list.append({param_key: param_map[param_key]})

    return input_list


@mcp_server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[TextContent | ImageContent | EmbeddedResource]:
    if name == "execute_script":
        if not arguments:
            raise ValueError("Missing arguments")

        room_id = arguments.get("room_id")
        run_type = arguments.get("type")
        playbook_input = arguments.get("playbook_input")
        action_input = arguments.get("action_input")
        agent_id = arguments.get("agent_id")

        if not room_id or not run_type:
            raise ValueError("Missing room_id or type")

        # Logic adapted from warroom/views.py
        # We need to access the request context or pass necessary dependencies.
        # Since this is running inside the server process, we can access DB models.

        # Try to find a system account or create one for the agent
        user = None
        if agent_id:
            user = await Account.get_or_none(username=f"agent_{agent_id}")

        if not user:
            # Fallback to a default system admin or error out?
            # Let's try to find 'admin' or 'system'
            user = await Account.get_or_none(username="admin")
            if not user:
                user = await Account.first()

        room = await Room.get_or_none(id=room_id)
        if not room:
            return [TextContent(type="text", text=f"Error: Room {room_id} not found.")]

        params = playbook_input if run_type == "flow" else action_input
        if not params:
            raise ValueError(f"Missing input parameters for type {run_type}")

        params["executor"] = user.id

        run_id = gen_run_id()
        msg = {"run_type": run_type, "run_id": run_id, "run_args": params}

        room.updator = user
        room.run_id.append(run_id)
        await room.save()

        # Send to Kafka
        await aproduce(TASK_TOPIC, msg)

        return [
            TextContent(
                type="text",
                text=f"Execution started. Run ID: {run_id}. Room: {room.name}",
            )
        ]

    elif name == "list_playbooks":
        args = arguments or {}
        page = args.get("page", 1)
        size = args.get("size", 10)
        keyword = args.get("keyword")

        offset = (page - 1) * size
        limit = size

        condition = Q(deleted=False)
        if keyword:
            condition = condition & (
                Q(name__icontains=keyword)
                | Q(remark__icontains=keyword)
                | Q(id=keyword)
            )

        # Simplify: List all non-deleted playbooks (no complex permission filtering for MCP)
        playbooks = (
            await Playbook.filter(condition)
            .annotate(
                versions=Count("playbook_versions", _filter=Q(playbook_versions__deleted=False)),
                max_flow_timeout=Max("playbook_versions__flow_timeout"),
            )
            .order_by("-utime")
            .offset(offset)
            .limit(limit)
            .prefetch_related("scenes", "tags")
        )

        total = await Playbook.filter(condition).count()

        data = []
        for p in playbooks:
            data.append(
                {
                    **p.json,
                    "flow_timeout": p.max_flow_timeout or 300,
                    "versions": p.versions,
                }
            )

        return [
            TextContent(
                type="text",
                text=str(
                    {
                        "total": total,
                        "page": page,
                        "size": size,
                        "total_pages": (total + size - 1) // size,
                        "data": data,
                    }
                ),
            )
        ]

    elif name == "get_playbook_inputs":
        args = arguments or {}
        flow_id = args.get("flow_id")
        playbook_id = args.get("playbook_id")

        if not flow_id and not playbook_id:
            raise ValueError("Missing flow_id or playbook_id")

        if not flow_id:
            # Find latest version for playbook_id
            pv = (
                await PlaybookVersion.filter(playbook__id=playbook_id, deleted=False)
                .order_by("-version")
                .first()
            )
            if not pv:
                raise ValueError(f"No versions found for playbook {playbook_id}")
            flow_id = pv.id

        inputs = await get_playbook_input_logic(flow_id)
        return [TextContent(type="text", text=str(inputs))]

    raise ValueError(f"Unknown tool: {name}")

# coding=utf-8


from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource

from libs.kafka import aproduce
from libs.utils import gen_run_id
from models.warroom import Room
from settings import TASK_TOPIC, KAFKA_CONFIG

# Initialize MCP Server
mcp_server = Server("lion-mgmt")


@mcp_server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [Tool(name="execute_script", description="Execute a warroom automation script (playbook/flow)", inputSchema={"type": "object", "properties": {"script_id": {"type": "string", "description": "The ID of the script/playbook to execute"}, "params": {"type": "object", "description": "Parameters for the script", "additionalProperties": True}, "agent_id": {"type": "string", "description": "The ID of the agent requesting execution"}}, "required": ["script_id", "agent_id"]})]


@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent | ImageContent | EmbeddedResource]:

    if name == "execute_script":
        if not arguments:
            raise ValueError("Missing arguments")

        script_id = arguments.get("script_id")
        agent_id = arguments.get("agent_id")
        params = arguments.get("params", {})

        if not script_id or not agent_id:
            raise ValueError("Missing script_id or agent_id")

        # Logic adapted from warroom/views.py
        # We need to access the request context or pass necessary dependencies.
        # Since this is running inside the server process, we can access DB models.

        # Note: We need a way to get the "user" context.
        # For MCP, we might map agent_id to a system user or specific account.
        # Here we will try to find a user or create a temporary context.
        # For now, we'll assume a system user or the agent is the user.

        from models.user import Account

        # Try to find a system account or create one for the agent
        user = await Account.get_or_none(username=f"agent_{agent_id}")
        if not user:
            # Fallback to a default system admin or error out?
            # Let's try to find 'admin' or 'system'
            user = await Account.get_or_none(username="admin")
            if not user:
                user = await Account.first()

        if not user:
            return [TextContent(type="text", text="Error: No execution user found.")]

        # Create/Get Room
        room_name = f"MCP_Room_{agent_id}"
        room = await Room.filter(name=room_name).first()
        if not room:
            room = await Room.create(name=room_name, creator=user)
            await room.users.add(user)

        run_params = {"flow_id": script_id, "flow_input": params, "executor": user.id}

        run_id = gen_run_id()
        msg = {"run_type": "flow", "run_id": run_id, "run_args": run_params}

        room.updator = user
        room.run_id.append(run_id)
        await room.save()

        # Send to Kafka
        await aproduce(TASK_TOPIC, msg)

        return [TextContent(type="text", text=f"Execution started. Run ID: {run_id}. Room: {room_name}")]

    raise ValueError(f"Unknown tool: {name}")

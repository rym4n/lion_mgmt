# coding=utf-8
from typing import Optional

from sanic.log import logger

from libs.utils import gen_random_code
from models import Account, Events, Room

from .gateway import AgentGateway


class AgentLifecycleManager:
    """
    Agent 生命周期管理 - 与 Warroom 事件联动
    """

    def __init__(self, gateway: AgentGateway):
        self.agent_gateway = gateway

    async def on_warroom_created(self, warroom_id: str, tenant_id: str, event: Optional[dict], creator: dict, initial_members: list) -> str:
        """
        作战室创建时触发 - 初始化 Agent
        """
        logger.info(f"Initializing agent for warroom {warroom_id}")

        agent_id = await self.agent_gateway.get_or_create_agent(warroom_id=warroom_id, tenant_id=tenant_id, members=initial_members)

        # 注入初始案件上下文
        if event:
            await self.agent_gateway._inject_case_context(warroom_id, agent_id, event, creator)

        return agent_id

    async def on_member_joined(self, warroom_id: str, new_member: dict):
        """成员加入 - 更新 human block"""
        # Note: Updating human block dynamically requires Agent update.
        # Current Gateway implementation doesn't expose _update_members_roster.
        # Ideally we should fetch current memory, update it, and set it back.
        # Or send a message to agent "User X joined".
        # For L3 consistency, updating memory block is better.
        pass

    async def on_member_left(self, warroom_id: str, member_id: str):
        """成员离开 - 更新 human block"""
        pass

    async def on_warroom_archived(self, warroom_id: str, archived_by: dict) -> dict:
        """
        作战室归档时触发 - 反思 + 销毁 Agent
        """
        logger.info(f"Archiving agent for warroom {warroom_id}")
        return await self.agent_gateway.archive_agent(warroom_id=warroom_id, trigger_reflection=True)

    async def on_event_auto_trigger(self, event: dict, tenant_id: str) -> dict:
        """
        告警自动触发 - 创建作战室 + Agent + 自动开始研判
        """
        # 1. 创建作战室
        warroom = await self._create_warroom_for_event(event, tenant_id)

        # 2. 初始化 Agent
        system_user = {"id": "system", "display_name": "System", "role": "System"}

        agent_id = await self.on_warroom_created(warroom_id=warroom.id, tenant_id=tenant_id, event=event, creator=system_user, initial_members=[])

        # 3. 触发自动研判
        auto_prompt = f"""
        [System Auto-Trigger] 新告警已接入，请立即开始研判：

        事件名称: {event.get('event_name')}
        事件来源: {event.get('event_source')}
        日志数据: {event.get('log_data', 'N/A')}

        请执行初步研判，更新你的 Core Memory 中的 hypothesis，
        并给出建议的下一步操作。
        """

        response = await self.agent_gateway.send_message(warroom_id=warroom.id, user=system_user, content=auto_prompt)

        return {"warroom_id": warroom.id, "agent_id": agent_id, "initial_analysis": response}

    async def _create_warroom_for_event(self, event_data: dict, tenant_id: str) -> Room:
        """为自动触发创建作战室"""
        room_name = f"Auto_{event_data.get('event_name', 'Event')}_{gen_random_code(6)}"

        # Try to find system user or first admin
        creator = await Account.filter(username="admin").first()
        if not creator:
            creator = await Account.first()

        room = await Room.create(name=room_name, creator=creator, tenant_id=tenant_id, room_type="public")

        # Update event with room
        if event_data.get("id"):
            event_obj = await Events.get_or_none(id=event_data["id"])
            if event_obj:
                event_obj.room = room
                event_obj.event_process_status = "处理中"
                await event_obj.save()

        return room

    async def _inject_case_context(self, warroom_id: str, agent_id: str, event: dict, creator: dict):
        """注入初始案件上下文"""
        prompt = f"""
        [System Initialization]
        当前作战室已创建。
        关联案件信息：
        - ID: {event.get('id')}
        - 名称: {event.get('event_name')}
        - 来源: {event.get('event_source')}
        - 概要: {event.get('event_summary')}

        请初始化 Core Memory，将状态设置为 'investigating'。
        """

        await self.agent_gateway.send_message(warroom_id=warroom_id, user={"id": "system", "display_name": "System", "role": "System"}, content=prompt)

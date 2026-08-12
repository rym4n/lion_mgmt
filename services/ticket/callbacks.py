# coding=utf-8
"""工单终态回传。"""

from models.ticket import SOURCE_PLAYBOOK


async def on_ticket_closed(ticket, actor_id=None, connection=None) -> None:
    if ticket.source == SOURCE_PLAYBOOK:
        from services.ticket.playbook_bridge import callback_playbook

        await callback_playbook(ticket, operator=actor_id, connection=connection)
    return None

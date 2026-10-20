"""Failure notifications for collection runs using inspection conventions."""
from __future__ import annotations

import inspect
import logging

from libs.kafka import aproduce
from libs.utils import gen_run_id
from settings import TASK_TOPIC

logger = logging.getLogger(__name__)


async def notify_failure(plan, run):
    """Publish configured failure notification actions using inspection conventions."""
    from services.inspection.views import _build_action_input, _extract_config_actions, _safe_int
    from services.inspection.views import _get_account_id
    actions = _extract_config_actions(getattr(plan, "notify_config", None))
    for action in actions:
        params = {
            "resource": action.get("resource", ""), "node_name": action.get("description", ""),
            "action": action.get("func", ""), "action_name": action.get("name", ""),
            "app_name": action.get("tool_name", action.get("app_name", "")),
            "name": action.get("description", ""), "app_ver": action.get("version", ""),
            "main": action.get("tool_main", ""),
            "action_input": _build_action_input(action, {"${采集结果}": str(getattr(run, "error_message", "") or "")}, {}),
            "retry_times": _safe_int(action.get("retry_times"), 3),
            "timeout": _safe_int(action.get("timeout"), 300),
            "executor": _get_account_id(getattr(run, "triggered_by", None)),
        }
        await aproduce(TASK_TOPIC, {"run_type": "action", "run_id": gen_run_id(),
                                     "run_args": params, "tenant_id": plan.tenant_id})


async def notify_after_commit(plan, run, *, notify=None):
    if plan is None or run.state != "FAILED" or not plan.notify_enabled or plan.consecutive_failures < 3:
        return
    try:
        outcome = (notify or notify_failure)(plan, run)
        if inspect.isawaitable(outcome):
            await outcome
    except Exception:
        # Notification delivery cannot undo committed terminal accounting or
        # replace the caller's stable error. Do not log executor/credential text.
        logger.warning("Collection failure notification failed for run %s", run.id)

# coding=utf-8
from typing import Any


def compose_payload(task: Any, actions_draft: list[dict] | None = None) -> dict:
    actions = actions_draft or []
    result = getattr(task, "result", None)
    title = f"{getattr(task, 'title', '任务')} - 数字员工报告"
    conclusion = result or "任务已结束，暂无额外结论。"
    if getattr(task, "status", None) == "failed":
        severity = "high"
        observation = "任务执行失败。"
    else:
        severity = "info"
        observation = "任务执行完成。"
    return {
        "title": title[:255],
        "severity": severity,
        "observation": observation,
        "analysis": result or getattr(task, "description", None),
        "actions": actions,
        "conclusion": conclusion,
        "evidence_urls": [],
        "auto_generated": True,
    }

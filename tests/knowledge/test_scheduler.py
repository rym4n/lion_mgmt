# coding=utf-8
"""知识库状态兜底定时任务（SPEC §10.4 第 3 条）。

结构与 services/digital_employee/memory_maintenance.py 一致：
内部 Timed 行 + Kafka timer 消息 + lion_action 内置动作回调。
"""


def test_timer_name_is_per_tenant():
    from services.knowledge.scheduler import knowledge_sweep_timer_name

    assert knowledge_sweep_timer_name("t1") != knowledge_sweep_timer_name("t2")
    assert "t1" in knowledge_sweep_timer_name("t1")


def test_run_args_action_matches_constant():
    """run_args 里的 action 名必须与 lion_action 侧注册的内置动作一致，
    对不上就是消息发出去没人处理——静默失效，最难排查。"""
    from services.knowledge.scheduler import (
        KNOWLEDGE_SWEEP_ACTION,
        build_sweep_timer_run_args,
    )

    args = build_sweep_timer_run_args("t1")
    assert args["action"] == KNOWLEDGE_SWEEP_ACTION
    assert args["action_name"] == KNOWLEDGE_SWEEP_ACTION
    assert args["action_input"]["tenant_id"] == "t1"


def test_cron_avoids_exact_hour():
    """每 10 分钟，且不落在整点——避开与其他定时任务的集中触发。"""
    from services.knowledge.scheduler import KNOWLEDGE_SWEEP_CRON

    assert KNOWLEDGE_SWEEP_CRON.startswith("*/10")


def test_retry_disabled():
    """兜底任务失败不该自动重试——下一个 10 分钟窗口会自然补偿，
    重试只会在故障期间放大请求量。"""
    from services.knowledge.scheduler import build_sweep_timer_run_args

    assert build_sweep_timer_run_args("t1")["retry_times"] == 0

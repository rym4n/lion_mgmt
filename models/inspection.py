#!/usr/bin/env python
# coding=utf-8

"""
巡检模块数据模型
Inspection Module Data Models
"""

from tortoise import fields
from models.base import BaseModel


class InspectionSchedule(BaseModel):
    """
    巡检调度配置（每个租户一个配置）
    Inspection Schedule Configuration (one per tenant)
    """
    name = fields.CharField(max_length=255, default="默认巡检计划", description="配置名称")
    schedule_cron = fields.CharField(max_length=100, description="Cron表达式")
    enabled = fields.BooleanField(default=True, description="是否启用")
    description = fields.TextField(null=True, description="配置描述")

    class Meta:
        table = "inspection_schedule"


class InspectionTarget(BaseModel):
    """
    巡检目标配置
    Inspection Target Configuration
    """
    name = fields.CharField(max_length=255, description="目标名称")
    content = fields.TextField(description="巡检内容")
    category = fields.CharField(max_length=100, description="巡检分类")
    standard = fields.TextField(description="巡检标准")
    action_name = fields.CharField(max_length=100, description="巡检动作名称")
    app_name = fields.CharField(max_length=100, description="应用名称")
    resource_config = fields.JSONField(description="资源配置(JSON格式)")
    enabled = fields.BooleanField(default=True, description="是否启用")
    description = fields.TextField(null=True, description="目标描述")

    class Meta:
        table = "inspection_target"


class InspectionResult(BaseModel):
    """
    巡检结果记录
    Inspection Result Record
    """
    target_id = fields.CharField(max_length=36, description="巡检目标ID")
    start_time = fields.DatetimeField(auto_now_add=True, description="开始时间")
    end_time = fields.DatetimeField(null=True, description="结束时间")
    status = fields.CharField(max_length=20, description="执行状态: success/failed/timeout")
    result_data = fields.JSONField(null=True, description="巡检结果数据")
    error_message = fields.TextField(null=True, description="错误信息")
    duration = fields.IntField(null=True, description="执行耗时(秒)")

    class Meta:
        table = "inspection_result"
        indexes = [("target_id", "start_time")]

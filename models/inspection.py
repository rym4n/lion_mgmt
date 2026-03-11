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
    巡检任务（最上层）
    Inspection Task (Top Level)
    """
    name = fields.CharField(max_length=255, default="默认巡检计划", description="任务名称")
    schedule_cron = fields.CharField(max_length=100, description="定时周期(Cron表达式)")
    owner = fields.CharField(max_length=255, null=True, description="负责人")
    enabled = fields.BooleanField(default=True, description="是否启用")
    description = fields.TextField(null=True, description="任务描述")

    class Meta:
        table = "inspection_schedule"


class InspectionCategory(BaseModel):
    """
    巡检类别（中间层）
    Inspection Category (Middle Level)
    """
    name = fields.CharField(max_length=255, description="类别名称")
    schedule_id = fields.CharField(max_length=36, description="关联巡检任务ID")
    app_name = fields.CharField(max_length=100, description="应用名称")
    resource_ids = fields.JSONField(description="资源ID列表(数组格式)")
    enabled = fields.BooleanField(default=True, description="是否启用")

    class Meta:
        table = "inspection_category"


class InspectionTarget(BaseModel):
    """
    巡检项（最底层）
    Inspection Item (Bottom Level)
    """
    name = fields.CharField(max_length=255, description="巡检项名称")
    content = fields.TextField(description="巡检内容")
    category_id = fields.CharField(max_length=36, description="关联巡检类别ID")
    standard = fields.TextField(description="巡检标准")
    action_name = fields.CharField(max_length=100, description="巡检动作名称")
    enabled = fields.BooleanField(default=True, description="是否启用")
    description = fields.TextField(null=True, description="巡检项描述")

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

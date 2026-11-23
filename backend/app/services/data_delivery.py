"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class DataDeliveryService(BaseModuleService):
    MODULE = "data_delivery"
    ENTITY = "投递任务"
    OPERATION_TERM = "采集"
    LIST_FIELDS = [
        "任务编号", "数据来源", "目标系统", "投递格式", "投递频率", "分区策略",
        "最近投递", "投递状态", "放行报告", "已投分区", "投递行数", "收据编号",
    ]
    REQUIRED_FIELDS = ["任务编号", "数据来源", "目标系统"]
    STATUS_ORDER = ["待投递", "投递中", "已完成", "投递失败"]
    ACTION_RULES = {"启动投递": "投递中", "重试投递": "已完成", "记录失败": "投递失败"}
    NEGATIVE_ACTIONS = []

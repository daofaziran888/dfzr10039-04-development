"""投递收据领域服务：登记已批准分区的实际投递行数，收据不可变、重放幂等。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class DeliveryReceiptService(BaseModuleService):
    MODULE = "delivery_receipt"
    ENTITY = "投递收据"
    OPERATION_TERM = "采集"
    LIST_FIELDS = [
        "收据编号", "报告编号", "目标批次", "任务编号", "目标系统",
        "分区", "签收行数", "采样数量", "有效数量", "签收时间",
    ]
    REQUIRED_FIELDS = ["收据编号", "目标批次", "分区", "签收行数"]
    STATUS_ORDER = ["已签收", "已回查"]
    ACTION_RULES = {"登记签收": "已签收", "回查凭据": "已回查"}
    NEGATIVE_ACTIONS = []

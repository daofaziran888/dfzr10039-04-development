"""分层放行领域服务：按分区最小样本数与有效率阈值出具放行报告。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class LayeredReleaseService(BaseModuleService):
    MODULE = "layered_release"
    ENTITY = "分层放行报告"
    OPERATION_TERM = "采集"
    LIST_FIELDS = [
        "报告编号", "目标批次", "修订版本", "最小样本", "有效率阈值",
        "批准分区", "未放行分区", "分区明细", "出具时间",
    ]
    REQUIRED_FIELDS = ["报告编号", "目标批次", "最小样本", "有效率阈值"]
    STATUS_ORDER = ["待评审", "部分放行", "全部放行"]
    ACTION_RULES = {"提交评审": "待评审", "部分放行": "部分放行", "确认全量放行": "全部放行"}
    NEGATIVE_ACTIONS = ["部分放行"]

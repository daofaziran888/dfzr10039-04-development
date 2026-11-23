"""分层放行与分层投递测试。

场景：同批次含价格区（8 条）与库存区（2 条）两个来源分区，
阈值 90%、最小样本 3 条。首轮库存只检到 1 条样本而被挡下，
仅价格区可单独投递；补采库存后重检放行，再用原回执重放，
验证修订只增量增加有效库存数据、收据不可变、重复提交只记一次。
"""
from __future__ import annotations

from app.integration import (
    build_layered_release_report, deliver_approved_partitions,
    lookup_receipt, list_batch_receipts, replay_delivery_receipts,
    _receipt_registry,
)
from app.models import CleanedContent, StorageBatch
from app.store import store


def _record(zone: str, idx: int, *, valid: bool = True) -> CleanedContent:
    return CleanedContent(
        source_url=f"https://{zone}.example.com/item/{idx}",
        extract_rule_id=f"RULE-{zone}",
        # 无效记录保留分区标记，但留空业务字段使 is_valid() 为 False
        fields={"分区": zone, "title": f"{zone}-{idx}", "price": "100"} if valid
        else {"分区": zone, "title": "", "price": ""},
    )


def _build_batch(batch_id: str, price_n: int, inventory_n: int) -> StorageBatch:
    records = [_record("价格区", i) for i in range(price_n)]
    records += [_record("库存区", i) for i in range(inventory_n)]
    return StorageBatch(
        batch_id=batch_id, source_job_id="JOB-LAYERED",
        row_count=len(records), status="校验通过", records=records,
        storage_path=f"/data/batches/{batch_id}/",
    )


def test_first_round_price_approved_inventory_blocked_by_min_samples() -> None:
    """首轮：价格区完整达标；库存区样本有效但只有 1 条，低于下限 3 条。"""
    batch = _build_batch("BATCH-LAYERED-R1", price_n=8, inventory_n=2)

    report = build_layered_release_report(
        batch, min_samples=3, valid_rate_threshold=0.9,
        sample_sizes={"价格区": 3, "库存区": 1}, rng_seed=20261123,
    )

    strata = {s.zone: s for s in report.strata}
    assert set(strata) == {"价格区", "库存区"}

    price = strata["价格区"]
    assert price.total_rows == 8
    assert price.sample_size == 3, f"价格区应采样3条，实际 {price.sample_size}"
    assert price.valid_count == 3
    assert price.approved is True
    assert price.delivered_rows == 8

    inventory = strata["库存区"]
    assert inventory.total_rows == 2
    assert inventory.sample_size == 1, f"库存区首轮只检到1条，实际 {inventory.sample_size}"
    assert inventory.valid_count == 1
    assert inventory.meets_valid_rate is True, "样本本身有效，有效率不是拦点"
    assert inventory.approved is False, "样本低于下限3条，不能放行"
    assert "低于下限" in inventory.reason()

    assert report.approved_zones == ["价格区"]
    assert report.rejected_zones == ["库存区"]
    assert report.status == "部分放行"
    assert report.revision == 1


def test_deliver_only_approved_partition_with_receipt() -> None:
    """投递只发送已批准分区，收据登记分区和行数，未放行分区不得登记为已收。"""
    batch = _build_batch("BATCH-LAYERED-DEL", price_n=8, inventory_n=2)
    report = build_layered_release_report(
        batch, min_samples=3, valid_rate_threshold=0.9,
        sample_sizes={"价格区": 3, "库存区": 1}, rng_seed=20261123,
    )

    task, receipts = deliver_approved_partitions(batch, report)

    # 只投递价格区 8 条，库存区不能登记为已收
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.partition == "价格区"
    assert receipt.row_count == 8
    assert receipt.sample_size == 3 and receipt.valid_count == 3
    assert receipt.status == "已签收"

    # 抽样结论同步到数据投递清单
    assert task.delivered_partitions == ["价格区"]
    assert task.delivered_row_count == 8
    assert task.release_report_id == report.report_id
    assert task.receipt_ids == [receipt.receipt_id]
    # 整批尚未全部放行：可单独投递价格数据，但任务不能出具整批完成结论
    assert task.status == "投递中"

    persisted = store.rows("delivery_receipt")
    assert all(row.get("分区") != "库存区" for row in persisted), "库存区不得出现签收收据"
    delivery_rows = [r for r in store.rows("data_delivery")
                     if r.get("数据来源") == batch.batch_id]
    assert delivery_rows[-1]["已投分区"] == "价格区"
    assert delivery_rows[-1]["投递行数"] == 8


def test_recheck_after_replenish_then_replay_only_adds_inventory() -> None:
    """补采库存后重检：生成修订版报告；原回执重放只新增库存数据，价格收据原样保留。"""
    batch = _build_batch("BATCH-LAYERED-R2", price_n=8, inventory_n=2)
    report_r1 = build_layered_release_report(
        batch, min_samples=3, valid_rate_threshold=0.9,
        sample_sizes={"价格区": 3, "库存区": 1}, rng_seed=20261123,
    )
    task_r1, receipts_r1 = deliver_approved_partitions(batch, report_r1)
    price_receipt_id = receipts_r1[0].receipt_id
    price_receipt_snapshot = lookup_receipt(price_receipt_id)
    assert price_receipt_snapshot is not None
    snapshot_created_at = price_receipt_snapshot.created_at
    snapshot_rows = price_receipt_snapshot.row_count

    # 补采 2 条库存，库存区达到 4 条，重检时样本数达到下限 3 条
    batch.records.extend([_record("库存区", 10), _record("库存区", 11)])
    batch.row_count = len(batch.records)

    report_r2 = build_layered_release_report(
        batch, min_samples=3, valid_rate_threshold=0.9,
        sample_sizes={"价格区": 3, "库存区": 3}, rng_seed=20261123,
    )
    assert report_r2.revision == 2, "补检应生成第2版报告"
    assert report_r2.status == "全部放行"
    strata_r2 = {s.zone: s for s in report_r2.strata}
    assert strata_r2["库存区"].sample_size == 3
    assert strata_r2["库存区"].approved is True

    # 历史报告原样保留，可回查
    report_rows = [r for r in store.rows("layered_release")
                   if r.get("目标批次") == batch.batch_id]
    assert [r["修订版本"] for r in report_rows] == [1, 2]
    assert report_rows[0]["status"] == "部分放行"

    # 用原回执重放：价格区收据原样返回，仅新增库存区收据
    replayed = replay_delivery_receipts(batch, report_r2)
    assert len(replayed) == 2
    by_zone = {r.partition: r for r in replayed}
    assert set(by_zone) == {"价格区", "库存区"}

    price_after = by_zone["价格区"]
    assert price_after.receipt_id == price_receipt_id
    assert price_after.row_count == snapshot_rows == 8, "已签收范围必须保留原样"
    assert price_after.created_at == snapshot_created_at, "历史收据时间戳不得改写"

    inventory_after = by_zone["库存区"]
    assert inventory_after.row_count == 4
    assert inventory_after.sample_size == 3 and inventory_after.valid_count == 3

    # 重复提交补检回执只记一次
    replayed_again = replay_delivery_receipts(batch, report_r2)
    assert len(replayed_again) == 2
    batch_receipts = list_batch_receipts(batch.batch_id)
    assert len(batch_receipts) == 2
    assert sum(1 for r in _receipt_registry.values()
               if r.target_batch == batch.batch_id) == 2

    # 投递清单更新为两个分区，行数 = 价格 8 + 库存 4
    task_r2, _ = deliver_approved_partitions(batch, report_r2)
    assert task_r2.delivered_partitions == ["价格区", "库存区"]
    assert task_r2.delivered_row_count == 12
    assert task_r2.status == "已完成"

    # 周报凭据可按编号回查
    assert lookup_receipt(inventory_after.receipt_id) is inventory_after
    assert lookup_receipt("NOT-EXIST") is None


def test_zone_rejected_when_valid_rate_below_threshold() -> None:
    """有效率不足（低于90%）即使样本数达标也不放行、不登记已收。"""
    records = [
        _record("价格区", 0), _record("价格区", 1),
        _record("价格区", 2, valid=False),
    ]
    batch = StorageBatch(
        batch_id="BATCH-LAYERED-BADRATE", source_job_id="JOB-BADRATE",
        row_count=3, status="校验通过", records=records,
        storage_path="/data/batches/BATCH-LAYERED-BADRATE/",
    )
    report = build_layered_release_report(
        batch, min_samples=3, valid_rate_threshold=0.9,
        sample_sizes={"价格区": 3}, rng_seed=42,
    )
    stratum = report.strata[0]
    assert stratum.sample_size == 3
    assert stratum.meets_min_samples is True
    assert stratum.valid_rate < 0.9
    assert stratum.approved is False

    _, receipts = deliver_approved_partitions(batch, report)
    assert receipts == []
    assert all(r.get("目标批次") != batch.batch_id
               for r in store.rows("delivery_receipt"))

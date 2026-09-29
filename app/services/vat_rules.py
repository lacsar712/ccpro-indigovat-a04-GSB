"""染缸状态、布样留底与浸染登记的业务规则。

约束（与浸染保存入口共用同一套校验，展开区禁止另写放行）：
- 留底留样米数须为正且 <= 2 米；
- 同一染缸同时只能有一条未销号留底（新建与更新共用）；
- 登记新浸染时，本缸须存在未销号留底，且留样时刻距登记时刻不超过 12 小时；
- 销号仅主管（is_superuser）可做，染缸工操作必须拒绝；
- 两人几乎同时给同一缸建未销号时，至多一笔入库（数据库部分唯一索引兜底），
  另一笔由 IntegrityError 转中文拒绝。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ClothRetain, DipLot, User, Vat

RETAIN_MAX_METERS = Decimal("2")
RETAIN_FRESH_HOURS = 12


class VatRuleError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """naive 时间按服务器本地时区解释后转 UTC（表单 datetime-local 不带时区）。"""
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc)


def assert_can_mark_ready(latest: Optional[DipLot]) -> None:
    """不能将染缸标为 ready，除非最新浸染批次 redoxMv 已填且 <= -500。"""
    if latest is None or latest.redoxMv is None or Decimal(latest.redoxMv) > Decimal("-500"):
        raise VatRuleError(
            "无法设为可染色：最新浸染批次的氧化还原电位为空或高于 -500 mV。"
        )


def validate_vat_status_change(vat: Vat, new_status: str, latest: Optional[DipLot]) -> None:
    if new_status == Vat.STATUS_READY:
        assert_can_mark_ready(latest)


# ---------------------------------------------------------------------------
# 布样留底
# ---------------------------------------------------------------------------

def validate_retain_meters(value: Decimal) -> Decimal:
    """留样米数须为正，上限固定为 2。新建与更新共用。"""
    meters = Decimal(value)
    if meters <= 0:
        raise VatRuleError("留样米数须为正数。")
    if meters > RETAIN_MAX_METERS:
        raise VatRuleError(f"留样米数上限为 {RETAIN_MAX_METERS} 米，超出无法登记。")
    return meters


def find_open_retain(db: Session, vat_id: int) -> Optional[ClothRetain]:
    return (
        db.query(ClothRetain)
        .filter(ClothRetain.vat_id == vat_id, ClothRetain.voided.is_(False))
        .one_or_none()
    )


def validate_retain_upsert(
    db: Session,
    vat_id: int,
    meters: Decimal,
    *,
    retain_id: Optional[int] = None,
) -> Decimal:
    """新建与更新留底共用：米数校验 + 未销号唯一性校验。

    retain_id 给定时为更新场景，放行当前这条自身。
    """
    meters = validate_retain_meters(meters)
    open_one = find_open_retain(db, vat_id)
    if open_one is not None and (retain_id is None or open_one.id != retain_id):
        raise VatRuleError("该染缸已有未销号留底，同一染缸同时只能保留一条未销号记录。")
    return meters


def open_retain_for_dip(open_retain: Optional[ClothRetain], at: datetime) -> None:
    """浸染保存统一校验：须有未销号留底，且留样时刻距今不超过 12 小时。

    浸染保存入口（pages.bay_log_lot）与留底相关入口共用本函数，
    展开区不得另行放行。
    """
    if open_retain is None:
        raise VatRuleError("本缸没有未销号的布样留底，须先在布样留底专页留底后才能登记浸染。")
    retained_at = ensure_utc(open_retain.retainedAt)
    at = ensure_utc(at)
    age = at - retained_at
    if age < timedelta(0):
        raise VatRuleError("留样时刻晚于当前时间，无法作为本次浸染的留底依据。")
    if age > timedelta(hours=RETAIN_FRESH_HOURS):
        raise VatRuleError(
            f"未销号留底已超过 {RETAIN_FRESH_HOURS} 小时（留样时刻 {retained_at:%Y-%m-%d %H:%M}），"
            "须重新留底后才能登记浸染。"
        )


def assert_can_void(user: User) -> None:
    """销号仅主管可做。"""
    if not user or not user.is_superuser:
        raise VatRuleError("销号仅主管可操作，染缸工无权销号。")

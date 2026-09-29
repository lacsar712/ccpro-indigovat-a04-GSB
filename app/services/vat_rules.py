"""染缸状态与布样留底业务规则。

留底有效性校验与浸染保存共用本模块，浸染保存入口（routers/pages.py 的
bay_log_lot）直接调用 validate_dip_recording；缸位展开区不另写放行逻辑。
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ClothRetain, DipLot, Vat

# 留样米数：正数，上限固定 2 米
RETAIN_METERS_MAX = Decimal("2")
# 留底对登记浸染的有效时长
RETAIN_VALID_WINDOW = timedelta(hours=12)


class VatRuleError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def assert_can_mark_ready(latest: Optional[DipLot]) -> None:
    """不能将染缸标为 ready，除非最新浸染批次 redoxMv 已填且 <= -500。"""
    if latest is None or latest.redoxMv is None or Decimal(latest.redoxMv) > Decimal("-500"):
        raise VatRuleError(
            "无法设为可染色：最新浸染批次的氧化还原电位为空或高于 -500 mV。"
        )


def validate_vat_status_change(vat: Vat, new_status: str, latest: Optional[DipLot]) -> None:
    if new_status == Vat.STATUS_READY:
        assert_can_mark_ready(latest)


def _as_local_naive(value: datetime) -> datetime:
    """统一到服务器本地墙上时间的 naive 时刻，与 datetime-local 表单一致。"""
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def validate_retain_meters(meters: Decimal) -> Decimal:
    """留样米数须为正且上限固定 2 米。"""
    value = Decimal(meters)
    if not value.is_finite() or value <= 0 or value > RETAIN_METERS_MAX:
        raise VatRuleError("留样米数无效：须为正数，且最多 2 米。")
    return value


def find_open_retain(
    db: Session, vat_id: int, exclude_id: Optional[int] = None
) -> Optional[ClothRetain]:
    """取该缸当前未销号留底（可排除自身，供更新时用）。"""
    query = db.query(ClothRetain).filter(
        ClothRetain.vat_id == vat_id,
        ClothRetain.reconciled.is_(False),
    )
    if exclude_id is not None:
        query = query.filter(ClothRetain.id != exclude_id)
    return query.order_by(ClothRetain.id.desc()).first()


def validate_retain(
    db: Session,
    vat_id: int,
    sample_meters: Decimal,
    *,
    retain_id: Optional[int] = None,
    will_be_open: bool = True,
) -> Decimal:
    """新建与更新留底共用的米数 + 未销号唯一校验。

    更新传 retain_id 以排除自身；will_be_open=False（更新对象已销号）时
    不再占用未销号名额。
    """
    meters = validate_retain_meters(sample_meters)
    if will_be_open and find_open_retain(db, vat_id, exclude_id=retain_id):
        raise VatRuleError(
            "该染缸已有一条未销号留底，同一染缸同时只能保留一条未销号记录。"
        )
    return meters


def validate_dip_recording(db: Session, vat_id: int) -> ClothRetain:
    """浸染保存入口共用：须存在未销号留底且留样时刻在 12 小时内。"""
    retain = find_open_retain(db, vat_id)
    if retain is None:
        raise VatRuleError(
            "登记被拒绝：本缸没有未销号的布样留底，请先到布样留底专页留底。"
        )
    retained = _as_local_naive(retain.retainedAt)
    if retained < datetime.now() - RETAIN_VALID_WINDOW:
        raise VatRuleError(
            "登记被拒绝：未销号留底的留样时刻距现在已超过 12 小时，"
            "留样已失效，请重新留底后再登记浸染。"
        )
    return retain

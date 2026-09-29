from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    DDL,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)


class Workshop(Base):
    __tablename__ = "workshops"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(80))
    notes: Mapped[str] = mapped_column(Text, default="")

    vats: Mapped[list["Vat"]] = relationship(back_populates="workshop")


class Vat(Base):
    __tablename__ = "vats"
    __table_args__ = (
        UniqueConstraint("workshop_id", "code", name="uniq_vat_code_per_workshop"),
    )

    STATUS_IDLE = "idle"
    STATUS_REDUCING = "reducing"
    STATUS_READY = "ready"

    id: Mapped[int] = mapped_column(primary_key=True)
    workshop_id: Mapped[int] = mapped_column(ForeignKey("workshops.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(40))
    dyeType: Mapped[str] = mapped_column(String(80))
    volumeL: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default=STATUS_IDLE)

    workshop: Mapped["Workshop"] = relationship(back_populates="vats")
    lots: Mapped[list["DipLot"]] = relationship(back_populates="vat")
    retains: Mapped[list["ClothRetain"]] = relationship(back_populates="vat")

    def latest_lot(self) -> Optional["DipLot"]:
        if not self.lots:
            return None
        return sorted(self.lots, key=lambda x: (x.dippedAt, x.id), reverse=True)[0]


class DipLot(Base):
    __tablename__ = "dip_lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    vat_id: Mapped[int] = mapped_column(ForeignKey("vats.id", ondelete="CASCADE"))
    dippedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clothMeters: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    redoxMv: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2), nullable=True)

    vat: Mapped["Vat"] = relationship(back_populates="lots")


class ClothRetain(Base):
    """布样留底簿记录：登记新浸染前须留有一条未销号记录。"""

    __tablename__ = "cloth_retains"

    id: Mapped[int] = mapped_column(primary_key=True)
    vat_id: Mapped[int] = mapped_column(ForeignKey("vats.id", ondelete="CASCADE"))
    clothMeters: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    retainedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lockerCode: Mapped[str] = mapped_column(String(40))
    voided: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    registeredBy: Mapped[int] = mapped_column(ForeignKey("users.id"))
    voidedAt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    voidedBy: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    vat: Mapped["Vat"] = relationship(foreign_keys=[vat_id], back_populates="retains")
    registrar: Mapped["User"] = relationship(foreign_keys=[registeredBy])
    voider: Mapped[Optional["User"]] = relationship(foreign_keys=[voidedBy])


# 同一染缸同时只能有一条未销号。用方言相关的部分唯一索引在数据库层兜底，
# 保证两人几乎同时登记时至多一笔入库（IntegrityError -> 中文拒绝）。
# 不能直接用 Index(..., postgresql_where/sqlite_where=...)：在非本方方言上
# 谓词会被忽略而退化成全量唯一索引，误伤已销号历史。
event.listen(
    ClothRetain.__table__,
    "after_create",
    DDL(
        "CREATE UNIQUE INDEX IF NOT EXISTS uniq_open_retain_per_vat "
        "ON cloth_retains (vat_id) WHERE voided = false"
    ).execute_if(dialect="postgresql"),
)
event.listen(
    ClothRetain.__table__,
    "after_create",
    DDL(
        "CREATE UNIQUE INDEX IF NOT EXISTS uniq_open_retain_per_vat "
        "ON cloth_retains (vat_id) WHERE voided = 0"
    ).execute_if(dialect="sqlite"),
)

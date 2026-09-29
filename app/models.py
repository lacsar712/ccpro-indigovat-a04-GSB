from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
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
    """布样留底：可染色缸登记新浸染前须先留一条未销号记录。"""

    __tablename__ = "cloth_retains"
    __table_args__ = (
        # 同一染缸同时只能有一条未销号；用部分唯一索引兜底并发（DB 层拒第二笔）。
        Index(
            "uniq_open_retain_per_vat",
            "vat_id",
            unique=True,
            postgresql_where=text("reconciled = false"),
            sqlite_where=text("reconciled = 0"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vat_id: Mapped[int] = mapped_column(ForeignKey("vats.id", ondelete="CASCADE"))
    sampleMeters: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    retainedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    binCode: Mapped[str] = mapped_column(String(40))
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reconciledAt: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registeredBy: Mapped[int] = mapped_column(ForeignKey("users.id"))

    vat: Mapped["Vat"] = relationship(back_populates="retains")
    registrar: Mapped["User"] = relationship(foreign_keys=[registeredBy])

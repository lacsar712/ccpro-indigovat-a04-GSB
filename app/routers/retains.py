"""布样留底簿专页：未销号筛选、新建/更新留底、主管销号。

规则一律走 app.services.vat_rules，浸染保存入口与本页共用同一套校验。
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import ClothRetain, Vat, Workshop
from app.services.vat_rules import (
    VatRuleError,
    assert_can_void,
    ensure_utc,
    now_utc,
    validate_retain_upsert,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _render(request, user, db, *, show_open, error=None, form=None, status_code=200):
    workshops = db.query(Workshop).order_by(Workshop.name).all()
    vats = (
        db.query(Vat)
        .options(joinedload(Vat.workshop), joinedload(Vat.retains))
        .order_by(Vat.code)
        .all()
    )
    vat_choices = [
        {
            "id": v.id,
            "code": v.code,
            "workshopName": v.workshop.name if v.workshop else "",
            "dyeType": v.dyeType,
        }
        for v in vats
    ]
    records_q = (
        db.query(ClothRetain)
        .options(
            joinedload(ClothRetain.vat).joinedload(Vat.workshop),
            joinedload(ClothRetain.registrar),
            joinedload(ClothRetain.voider),
        )
        .order_by(ClothRetain.voided, ClothRetain.retainedAt.desc(), ClothRetain.id.desc())
    )
    if show_open:
        records_q = records_q.filter(ClothRetain.voided.is_(False))
    records = records_q.all()
    open_retain_count = sum(1 for v in vats for r in v.retains if not r.voided)

    rows = []
    for r in records:
        rows.append(
            {
                "id": r.id,
                "vatId": r.vat_id,
                "vatCode": r.vat.code if r.vat else f"#{r.vat_id}",
                "workshopName": r.vat.workshop.name if r.vat and r.vat.workshop else "",
                "clothMeters": float(r.clothMeters),
                "retainedAt": r.retainedAt.strftime("%Y-%m-%d %H:%M"),
                "lockerCode": r.lockerCode,
                "voided": r.voided,
                "registrar": r.registrar.username if r.registrar else f"#{r.registeredBy}",
                "voidedAt": r.voidedAt.strftime("%Y-%m-%d %H:%M") if r.voidedAt else None,
                "voider": r.voider.username if r.voider else (f"#{r.voidedBy}" if r.voidedBy else None),
            }
        )

    return templates.TemplateResponse(
        request,
        "retains.html",
        {
            "user": user,
            "error": error,
            "form": form or {},
            "show_open": show_open,
            "vat_choices": vat_choices,
            "records": rows,
            "open_retain_count": open_retain_count,
            "active": "retains",
            "is_supervisor": bool(user.is_superuser),
        },
        status_code=status_code,
    )


def _parse_retain_form(vat_id_raw: str, meters_raw: str, retained_raw: str, locker: str):
    try:
        vat_id = int(vat_id_raw)
    except (TypeError, ValueError):
        raise VatRuleError("请选择染缸。")
    try:
        meters = Decimal(meters_raw)
    except (InvalidOperation, ValueError):
        raise VatRuleError("留样米数须为数字。")
    try:
        retained_at = datetime.fromisoformat(retained_raw)
    except (TypeError, ValueError):
        raise VatRuleError("留样时刻格式无效。")
    if not locker.strip():
        raise VatRuleError("柜格代号不能为空。")
    return vat_id, meters, retained_at, locker.strip()


@router.get("/retains", response_class=HTMLResponse)
async def retains_page(
    request: Request,
    show: str = "open",
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return _render(request, user, db, show_open=(show != "all"))


@router.post("/retains", response_class=HTMLResponse)
async def create_retain(
    request: Request,
    vat_id: str = Form(...),
    clothMeters: str = Form(...),
    retainedAt: str = Form(...),
    lockerCode: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = {
        "vat_id": vat_id,
        "clothMeters": clothMeters,
        "retainedAt": retainedAt,
        "lockerCode": lockerCode,
    }
    try:
        vat_pk, meters, retained_at, locker = _parse_retain_form(
            vat_id, clothMeters, retainedAt, lockerCode
        )
        retained_at = ensure_utc(retained_at)
        if not db.get(Vat, vat_pk):
            raise VatRuleError("所选染缸不存在。")
        # 新建校验：米数（0 < x <= 2）与未销号唯一，与更新共用同一函数
        validate_retain_upsert(db, vat_pk, meters)
        record = ClothRetain(
            vat_id=vat_pk,
            clothMeters=meters,
            retainedAt=retained_at,
            lockerCode=locker,
            voided=False,
            registeredBy=user.id,
        )
        db.add(record)
        db.commit()
        return RedirectResponse("/retains", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        return _render(request, user, db, show_open=True, error=exc.message, form=form, status_code=400)
    except IntegrityError:
        # 两人几乎同时给同一缸建未销号：部分唯一索引兜底，至多一笔入库
        db.rollback()
        return _render(
            request,
            user,
            db,
            show_open=True,
            error="该染缸刚被他人登记了未销号留底，同一染缸同时只能有一条，本次登记未入库。",
            form=form,
            status_code=409,
        )


@router.post("/retains/{pk}/update", response_class=HTMLResponse)
async def update_retain(
    pk: int,
    request: Request,
    clothMeters: str = Form(...),
    retainedAt: str = Form(...),
    lockerCode: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    record = db.get(ClothRetain, pk)
    if not record:
        return RedirectResponse("/retains", status_code=303)
    try:
        if record.voided:
            raise VatRuleError("已销号留底不可修改。")
        _, meters, retained_at, locker = _parse_retain_form(
            str(record.vat_id), clothMeters, retainedAt, lockerCode
        )
        retained_at = ensure_utc(retained_at)
        # 更新与新建共用同一校验函数（未销号唯一 + 米数）
        validate_retain_upsert(db, record.vat_id, meters, retain_id=record.id)
        record.clothMeters = meters
        record.retainedAt = retained_at
        record.lockerCode = locker
        db.commit()
        return RedirectResponse("/retains", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        return _render(request, user, db, show_open=True, error=exc.message, status_code=400)
    except IntegrityError:
        db.rollback()
        return _render(
            request,
            user,
            db,
            show_open=True,
            error="该染缸刚被他人登记了未销号留底，本次更新与未销号唯一性冲突，未保存。",
            status_code=409,
        )


@router.post("/retains/{pk}/void", response_class=HTMLResponse)
async def void_retain(
    pk: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    record = db.get(ClothRetain, pk)
    if not record:
        return RedirectResponse("/retains", status_code=303)
    try:
        # 销号仅主管；染缸工被拒且会话保留（不做任何登出/清会话动作）
        assert_can_void(user)
        if record.voided:
            raise VatRuleError("该留底已销号，无需重复销号。")
        record.voided = True
        record.voidedAt = now_utc()
        record.voidedBy = user.id
        db.commit()
        return RedirectResponse("/retains", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        return _render(request, user, db, show_open=True, error=exc.message, status_code=403)

"""布样留底专页：未销号筛选、新建/更新留底、主管销号。

未销号唯一与米数校验统一走 services.vat_rules.validate_retain，
浸染保存入口的留底有效性检查走 validate_dip_recording，两处不各写一套。
"""

from datetime import datetime
from decimal import InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import ClothRetain, User, Vat, Workshop
from app.services.vat_rules import VatRuleError, validate_retain

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

STATUS_LABELS = {
    Vat.STATUS_IDLE: "闲置",
    Vat.STATUS_REDUCING: "还原中",
    Vat.STATUS_READY: "可染色",
}


def render(request: Request, name: str, context: dict, status_code: int = 200):
    ctx = {k: v for k, v in context.items() if k != "request"}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _open_count(db: Session) -> int:
    return (
        db.query(ClothRetain)
        .filter(ClothRetain.reconciled.is_(False))
        .count()
    )


def _page_context(
    request: Request,
    db: Session,
    user: User,
    scope: str = "open",
    error: Optional[str] = None,
):
    vats = (
        db.query(Vat)
        .options(joinedload(Vat.workshop))
        .order_by(Vat.code)
        .all()
    )
    query = (
        db.query(ClothRetain)
        .options(joinedload(ClothRetain.vat).joinedload(Vat.workshop), joinedload(ClothRetain.registrar))
        .order_by(ClothRetain.reconciled, ClothRetain.retainedAt.desc(), ClothRetain.id.desc())
    )
    if scope == "open":
        query = query.filter(ClothRetain.reconciled.is_(False))
    retains = query.all()

    def row(r: ClothRetain) -> dict:
        return {
            "id": r.id,
            "vatId": r.vat_id,
            "vatCode": r.vat.code if r.vat else f"#{r.vat_id}",
            "vatStatus": STATUS_LABELS.get(r.vat.status, r.vat.status) if r.vat else "",
            "workshopName": r.vat.workshop.name if r.vat and r.vat.workshop else "",
            "sampleMeters": float(r.sampleMeters),
            "retainedAt": r.retainedAt.strftime("%Y-%m-%dT%H:%M"),
            "retainedAtLabel": r.retainedAt.strftime("%Y-%m-%d %H:%M"),
            "binCode": r.binCode,
            "reconciled": r.reconciled,
            "reconciledAt": r.reconciledAt.strftime("%Y-%m-%d %H:%M") if r.reconciledAt else "",
            "registrar": r.registrar.username if r.registrar else f"#{r.registeredBy}",
        }

    return {
        "request": request,
        "user": user,
        "is_superuser": bool(user.is_superuser),
        "scope": scope,
        "error": error,
        "retains": [row(r) for r in retains],
        "vats": [
            {
                "id": v.id,
                "code": v.code,
                "status": v.status,
                "statusLabel": STATUS_LABELS.get(v.status, v.status),
                "workshopName": v.workshop.name if v.workshop else "",
            }
            for v in vats
        ],
        "open_count": _open_count(db),
        "active": "retains",
    }


@router.get("/retains", response_class=HTMLResponse)
async def retains_page(
    request: Request,
    scope: str = "open",
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if scope not in ("open", "all"):
        scope = "open"
    return render(request, "retains.html", _page_context(request, db, user, scope))


@router.post("/retains", response_class=HTMLResponse)
async def create_retain(
    request: Request,
    vat_id: int = Form(...),
    sampleMeters: str = Form(...),
    retainedAt: str = Form(...),
    binCode: str = Form(...),
    scope: str = Form("open"),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if scope not in ("open", "all"):
        scope = "open"
    if not db.get(Vat, vat_id):
        return render(
            request,
            "retains.html",
            _page_context(request, db, user, scope, "登记被拒绝：所选染缸不存在。"),
            status_code=400,
        )
    try:
        meters = validate_retain(db, vat_id, sampleMeters)
        retain = ClothRetain(
            vat_id=vat_id,
            sampleMeters=meters,
            retainedAt=datetime.fromisoformat(retainedAt),
            binCode=binCode.strip(),
            registeredBy=user.id,
        )
        db.add(retain)
        db.commit()
        return RedirectResponse(f"/retains?scope={scope}", status_code=303)
    except (ValueError, InvalidOperation) as exc:
        db.rollback()
        error = f"登记被拒绝：留样时刻格式无效（{exc}）。"
    except VatRuleError as exc:
        db.rollback()
        error = exc.message
    except IntegrityError:
        # 并发兜底：两人几乎同时给同一缸建未销号，DB 部分唯一索引拒第二笔
        db.rollback()
        error = "登记被拒绝：该染缸的未销号留底刚刚已被他人登记，同一染缸同时只能有一条未销号，本笔未入库。"
    return render(
        request,
        "retains.html",
        _page_context(request, db, user, scope, error),
        status_code=400,
    )


@router.post("/retains/{pk}/update", response_class=HTMLResponse)
async def update_retain(
    pk: int,
    request: Request,
    sampleMeters: str = Form(...),
    retainedAt: str = Form(...),
    binCode: str = Form(...),
    scope: str = Form("open"),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if scope not in ("open", "all"):
        scope = "open"
    retain = db.get(ClothRetain, pk)
    if not retain:
        return RedirectResponse(f"/retains?scope={scope}", status_code=303)
    try:
        # 与新建共用同一校验；更新已销号记录不再占未销号名额
        meters = validate_retain(
            db,
            retain.vat_id,
            sampleMeters,
            retain_id=retain.id,
            will_be_open=not retain.reconciled,
        )
        retain.sampleMeters = meters
        retain.retainedAt = datetime.fromisoformat(retainedAt)
        retain.binCode = binCode.strip()
        db.commit()
        return RedirectResponse(f"/retains?scope={scope}", status_code=303)
    except (ValueError, InvalidOperation) as exc:
        db.rollback()
        error = f"更新被拒绝：留样时刻格式无效（{exc}）。"
    except VatRuleError as exc:
        db.rollback()
        error = exc.message
    except IntegrityError:
        db.rollback()
        error = "更新被拒绝：该染缸已存在另一条未销号留底，本笔更新未保存。"
    return render(
        request,
        "retains.html",
        _page_context(request, db, user, scope, error),
        status_code=400,
    )


@router.post("/retains/{pk}/reconcile", response_class=HTMLResponse)
async def reconcile_retain(
    pk: int,
    request: Request,
    scope: str = Form("open"),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if scope not in ("open", "all"):
        scope = "open"
    retain = db.get(ClothRetain, pk)
    # 销号仅主管：染缸工拒绝且不清会话（不退出登录）
    if not user.is_superuser:
        return render(
            request,
            "retains.html",
            _page_context(
                request, db, user, scope,
                "销号被拒绝：布样留底仅主管可以销号，染缸工无权操作。",
            ),
            status_code=403,
        )
    if not retain:
        return RedirectResponse(f"/retains?scope={scope}", status_code=303)
    if not retain.reconciled:
        retain.reconciled = True
        retain.reconciledAt = datetime.now()
        db.commit()
    return RedirectResponse(f"/retains?scope={scope}", status_code=303)

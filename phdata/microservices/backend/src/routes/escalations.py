"""Caller-owned simulated escalation records and record-based metrics."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from src.models.escalation import L2Escalation
from src.routes.users import DB, CurrentUser
from src.services.escalations import REASON_CODES, SIMULATION_NOTE, public_escalation

router = APIRouter(prefix="/escalations", tags=["escalations"])


@router.get("")
def list_escalations(
    db: DB,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
) -> list[dict]:
    rows = db.scalars(
        select(L2Escalation)
        .where(L2Escalation.user_id == user.id)
        .order_by(L2Escalation.created_at.desc(), L2Escalation.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return [public_escalation(row) for row in rows]


@router.get("/stats")
def escalation_stats(db: DB, user: CurrentUser) -> dict:
    counts = db.execute(
        select(L2Escalation.reason_code, func.count(L2Escalation.id))
        .where(
            L2Escalation.user_id == user.id,
            L2Escalation.dispatch_status == "simulated",
        )
        .group_by(L2Escalation.reason_code)
    ).all()
    by_reason = dict.fromkeys(REASON_CODES, 0)
    by_reason.update({reason: count for reason, count in counts})
    return {
        "total": sum(by_reason.values()),
        "by_reason": by_reason,
        "dispatch_status": "simulated",
        "team_contacted": False,
        "note": SIMULATION_NOTE,
    }


@router.get("/{escalation_id}")
def get_escalation(escalation_id: str, db: DB, user: CurrentUser) -> dict:
    row = db.scalar(
        select(L2Escalation).where(
            L2Escalation.id == escalation_id,
            L2Escalation.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Escalation not found")
    return public_escalation(row)

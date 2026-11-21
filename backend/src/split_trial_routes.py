"""整批拆周试算 HTTP 路由。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .auth import current_user, require_roles
from .db import connection
from psycopg2.extras import RealDictCursor
from . import split_trial_service as service

router = APIRouter(prefix="/split-trials", tags=["拆周试算"])


class WeekTrialIn(BaseModel):
    week_start: str
    machine_run_id: int
    forecast_lower_tons: float = Field(gt=0)


class CreateOrderIn(BaseModel):
    order_code: str = Field(min_length=3, max_length=40)
    order_tons: float = Field(gt=0)
    weeks: list[WeekTrialIn] = Field(min_length=2, max_length=2)
    notes: str = ""


class RetryTrialIn(BaseModel):
    weeks: list[WeekTrialIn] = Field(min_length=2, max_length=2)


def _week_payload(weeks):
    return [w.model_dump() for w in weeks]


@router.get("")
def list_orders(user=Depends(current_user)):
    return service.list_orders()


@router.get("/quota-overview")
def quota_overview(user=Depends(current_user)):
    return service.quota_overview()


@router.get("/{order_id}")
def get_order(order_id: int, user=Depends(current_user)):
    return service.get_order(order_id)


@router.post("", status_code=201)
def create_order(payload: CreateOrderIn, user=Depends(require_roles("operator", "supervisor"))):
    data = payload.model_dump()
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            order_id = service.create_order(cur, data, user)
    # 创建后立即执行首轮试算，返回分周结果。
    return service.trial_order(order_id, data["weeks"], user)


@router.post("/{order_id}/retry")
def retry_trial(order_id: int, payload: RetryTrialIn, user=Depends(require_roles("operator", "supervisor"))):
    return service.trial_order(order_id, _week_payload(payload.weeks), user)


@router.post("/{order_id}/submit")
def submit_order(order_id: int, user=Depends(require_roles("operator", "supervisor"))):
    return service.submit_order(order_id, user)


@router.post("/{order_id}/approve")
def approve_order(order_id: int, user=Depends(require_roles("supervisor"))):
    return service.approve_order(order_id, user)

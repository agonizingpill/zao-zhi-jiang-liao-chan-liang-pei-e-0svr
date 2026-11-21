"""整批拆周试算领域服务。

职责
----
* 创建拆周试算单（订单吨位 + 同一纸机的两个周次与各自工况）；
* 两周分别试算，首周尽量多交，单周排量受预测下界与机台余量约束；
* 试算失败可重试，重试不重复生成周计划（幂等：先清旧周行再按同一口径重算）；
* 两周凑足订单吨位才允许送审；
* 主管并发审批时用 ``SELECT ... FOR UPDATE`` 锁机台行，任一周余量不足则整单退回、
  两周都不占用余量；
* 提供配额页面所需的分周吨位与缺口查询。

机台余量口径
------------
某机台在某周的已批占用 = 已批准 ``quota_plans`` 的 ``planned_tons``
（按周起始聚合）+ 本模块已 ``occupied`` 的拆周分周行。
机台余量 = 机台安全容量 − 上述已批占用。
"""

from decimal import Decimal

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from .db import connection, query
from .split_allocation import WeekInput, run_split_trial
from . import services as shared


def _d(value):
    return Decimal(str(value))


def _two(value):
    return value.quantize(Decimal("0.01"))


# --------------------------------------------------------------------------- #
# 机台余量
# --------------------------------------------------------------------------- #
def _occupied_elsewhere(cur, machine_run_id, week_start, exclude_order_id=None):
    """返回该机台该周被其他已批单据占用的吨位。"""
    cur.execute(
        """
        SELECT COALESCE(SUM(tons), 0) AS tons FROM (
          SELECT COALESCE(q.planned_tons, 0) AS tons
          FROM quota_plans q
          WHERE q.machine_run_id = %s AND q.week_start = %s AND q.status = 'approved'
          UNION ALL
          SELECT w.allocated_tons AS tons
          FROM split_trial_weeks w
          JOIN split_trial_orders o ON o.id = w.order_id
          WHERE w.machine_run_id = %s AND w.week_start = %s
            AND w.occupied = true
            AND (%s IS NULL OR o.id <> %s)
        ) occupied
        """,
        (machine_run_id, week_start, machine_run_id, week_start,
         exclude_order_id, exclude_order_id),
    )
    return _d(cur.fetchone()["tons"])


def _machine_capacity(cur, machine_run_id):
    cur.execute("SELECT safety_capacity_tons FROM machine_runs WHERE id=%s", (machine_run_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "机台工况不存在")
    return _d(row["safety_capacity_tons"])


def _remaining(cur, machine_run_id, week_start, exclude_order_id=None):
    capacity = _machine_capacity(cur, machine_run_id)
    occupied = _occupied_elsewhere(cur, machine_run_id, week_start, exclude_order_id)
    return _two(capacity - occupied)


# --------------------------------------------------------------------------- #
# 创建与试算
# --------------------------------------------------------------------------- #
def create_order(cur, payload, user):
    if len(payload["weeks"]) != 2:
        raise HTTPException(422, "拆周试算必须选择恰好两个周次")
    machine_ids = {w["machine_run_id"] for w in payload["weeks"]}
    if len(machine_ids) != 1:
        raise HTTPException(422, "两个周次必须选择同一台纸机")
    week_starts = [w["week_start"] for w in payload["weeks"]]
    if week_starts[0] >= week_starts[1]:
        raise HTTPException(422, "首周起始日必须早于第二周")

    machine_id = next(iter(machine_ids))
    cur.execute("SELECT id, machine_name FROM machine_runs WHERE id=%s", (machine_id,))
    if not cur.fetchone():
        raise HTTPException(404, "所选纸机工况不存在")

    cur.execute("SELECT id FROM split_trial_orders WHERE order_code=%s", (payload["order_code"],))
    if cur.fetchone():
        raise HTTPException(409, "拆周试算单编号已存在")

    cur.execute(
        """INSERT INTO split_trial_orders(order_code, machine_run_id, order_tons, status, created_by, notes)
           VALUES (%s,%s,%s,'draft',%s,%s) RETURNING id""",
        (payload["order_code"], machine_id,
         payload["order_tons"], user["id"], payload.get("notes", "")),
    )
    order_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO audit_logs(actor_id, action, entity, entity_id, detail) VALUES (%s,'split_order_created','split_trial_order',%s,%s)",
        (user["id"], order_id, shared_json({"order_code": payload["order_code"],
                                            "order_tons": payload["order_tons"]})),
    )
    return order_id


def _build_week_inputs(cur, payload_weeks, exclude_order_id):
    inputs = []
    for idx, w in enumerate(payload_weeks, start=1):
        remaining = _remaining(cur, w["machine_run_id"], w["week_start"], exclude_order_id)
        inputs.append(
            WeekInput.of(
                week_index=idx,
                week_start=w["week_start"],
                machine_run_id=w["machine_run_id"],
                forecast_lower_tons=w["forecast_lower_tons"],
                machine_remaining_tons=remaining,
            )
        )
    return inputs


def trial_order(order_id, payload_weeks, user):
    """执行（或重试）拆周试算。

    幂等口径：周计划行按 (order_id, week_index) 唯一，重试先删除该单旧的未占用周行，
    再按当前机台余量重新计算并插入，因此重试不会重复生成周计划。
    已送审/已批准的单据不允许再试算。
    """
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM split_trial_orders WHERE id=%s FOR UPDATE", (order_id,))
            order = cur.fetchone()
            if not order:
                raise HTTPException(404, "拆周试算单不存在")
            if order["status"] in ("submitted", "approved"):
                raise HTTPException(409, "试算单已送审或已批准，不能重新试算")
            if len(payload_weeks) != 2:
                raise HTTPException(422, "拆周试算必须提供两个周次")
            machine_ids = {w["machine_run_id"] for w in payload_weeks}
            if len(machine_ids) != 1 or order["machine_run_id"] not in machine_ids:
                raise HTTPException(422, "两个周次必须与试算单使用同一台纸机")

            inputs = _build_week_inputs(cur, payload_weeks, exclude_order_id=order_id)
            result = run_split_trial(order["order_tons"], inputs)

            # 重试：清理上一轮未占用的周行，避免重复生成周计划。
            cur.execute("DELETE FROM split_trial_weeks WHERE order_id=%s AND occupied=false", (order_id,))
            for w in result.weeks:
                cur.execute(
                    """INSERT INTO split_trial_weeks(order_id, week_index, week_start, machine_run_id,
                          forecast_lower_tons, machine_remaining_tons, cap_tons, allocated_tons,
                          gap_tons, constrained_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (order_id, w.week_index, w.week_start, w.machine_run_id,
                     w.forecast_lower_tons, w.machine_remaining_tons, w.cap_tons,
                     w.allocated_tons, w.gap_tons, w.constrained_by),
                )

            new_status = "ready" if result.feasible else "trial_failed"
            cur.execute(
                """UPDATE split_trial_orders
                   SET status=%s, total_allocated_tons=%s, total_gap_tons=%s,
                       trial_attempts=trial_attempts+1, reject_reason=NULL
                   WHERE id=%s""",
                (new_status, result.total_allocated_tons, result.total_gap_tons, order_id),
            )
            cur.execute(
                "INSERT INTO audit_logs(actor_id, action, entity, entity_id, detail) VALUES (%s,'split_trial_run','split_trial_order',%s,%s)",
                (user["id"], order_id, shared_json(result.as_dict())),
            )
    return get_order(order_id)


def submit_order(order_id, user):
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM split_trial_orders WHERE id=%s FOR UPDATE", (order_id,))
            order = cur.fetchone()
            if not order:
                raise HTTPException(404, "拆周试算单不存在")
            if order["status"] != "ready":
                raise HTTPException(409, "只有试算成功（两周凑足订单吨位）的单据才能送审")
            cur.execute(
                """UPDATE split_trial_orders
                   SET status='submitted', submitted_by=%s, submitted_at=now() WHERE id=%s""",
                (user["id"], order_id),
            )
            cur.execute(
                "INSERT INTO audit_logs(actor_id, action, entity, entity_id, detail) VALUES (%s,'split_order_submitted','split_trial_order',%s,%s)",
                (user["id"], order_id, shared_json({})),
            )
    return get_order(order_id)


# --------------------------------------------------------------------------- #
# 并发审批：整单通过或整单退回
# --------------------------------------------------------------------------- #
def approve_order(order_id, user):
    """主管审批。并发安全：锁机台行后逐周复核实时余量。

    任一周当前余量不足以承载试算排量，则整单退回（status='returned'），
    已写入的周行 occupied 始终为 false，即两周都不占机台余量。
    """
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 锁机台行，串行化同一纸机上的并发审批。
            cur.execute("SELECT * FROM split_trial_orders WHERE id=%s FOR UPDATE", (order_id,))
            order = cur.fetchone()
            if not order:
                raise HTTPException(404, "拆周试算单不存在")
            cur.execute("SELECT id FROM machine_runs WHERE id=%s FOR UPDATE", (order["machine_run_id"],))

            if order["status"] not in ("submitted",):
                raise HTTPException(409, "只有已送审的试算单才能审批")

            cur.execute(
                "SELECT * FROM split_trial_weeks WHERE order_id=%s ORDER BY week_index",
                (order_id,),
            )
            weeks = cur.fetchall()
            shortages = []
            for w in weeks:
                # 审批时点重新计算实时余量（排除本单自身），捕捉并发期间的余量变化。
                remaining = _remaining(cur, w["machine_run_id"], w["week_start"], exclude_order_id=order_id)
                if remaining < _d(w["allocated_tons"]):
                    shortages.append({
                        "week_index": w["week_index"],
                        "week_start": str(w["week_start"]),
                        "allocated_tons": float(w["allocated_tons"]),
                        "remaining_tons": float(remaining),
                        "short_tons": float(_two(_d(w["allocated_tons"]) - remaining)),
                    })

            conflict = None
            if shortages:
                reason = "并发审批时机台余量不足，整单退回且两周均不占用余量"
                cur.execute(
                    "UPDATE split_trial_orders SET status='returned', reject_reason=%s, approved_by=%s, approved_at=now() WHERE id=%s",
                    (reason, user["id"], order_id),
                )
                cur.execute(
                    "INSERT INTO audit_logs(actor_id, action, entity, entity_id, detail) VALUES (%s,'split_order_returned','split_trial_order',%s,%s)",
                    (user["id"], order_id, shared_json({"reason": reason, "shortages": shortages})),
                )
                conflict = {
                    "code": "SPLIT_CAPACITY_SHORTAGE",
                    "message": reason,
                    "shortages": shortages,
                }
            else:
                cur.execute(
                    "UPDATE split_trial_weeks SET occupied=true WHERE order_id=%s",
                    (order_id,),
                )
                cur.execute(
                    "UPDATE split_trial_orders SET status='approved', approved_by=%s, approved_at=now(), reject_reason=NULL WHERE id=%s",
                    (user["id"], order_id),
                )
                cur.execute(
                    "INSERT INTO audit_logs(actor_id, action, entity, entity_id, detail) VALUES (%s,'split_order_approved','split_trial_order',%s,%s)",
                    (user["id"], order_id, shared_json({"allocated_tons": float(order["total_allocated_tons"])})),
                )
    # 事务已提交后再抛出冲突，保证“整单退回”状态持久化。
    if conflict is not None:
        raise HTTPException(409, conflict)
    return get_order(order_id)


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def list_orders():
    orders = query("""SELECT o.*, m.machine_name, m.run_code,
                        u.display_name AS creator_name
                      FROM split_trial_orders o
                      JOIN machine_runs m ON m.id=o.machine_run_id
                      JOIN users u ON u.id=o.created_by
                      ORDER BY o.created_at DESC""")
    result = []
    for o in orders:
        weeks = query("""SELECT * FROM split_trial_weeks WHERE order_id=%s ORDER BY week_index""", (o["id"],))
        row = shared.serialize_row(o)
        row["weeks"] = [shared.serialize_row(w) for w in weeks]
        result.append(row)
    return result


def get_order(order_id):
    order = query("""SELECT o.*, m.machine_name, m.run_code, u.display_name AS creator_name
                     FROM split_trial_orders o
                     JOIN machine_runs m ON m.id=o.machine_run_id
                     JOIN users u ON u.id=o.created_by
                     WHERE o.id=%s""", (order_id,), one=True)
    if not order:
        raise HTTPException(404, "拆周试算单不存在")
    weeks = query("SELECT * FROM split_trial_weeks WHERE order_id=%s ORDER BY week_index", (order_id,))
    row = shared.serialize_row(order)
    row["weeks"] = [shared.serialize_row(w) for w in weeks]
    return row


def quota_overview():
    """配额页面：按机台/周展示分周吨位、机台余量与缺口。

    机台余量口径与试算/审批一致：安全容量 − 同周已批 quota_plans − 已占用拆周行。
    """
    rows = query("""
      SELECT dim.week_start, dim.machine_run_id, m.machine_name, m.run_code,
             m.safety_capacity_tons,
             COALESCE(split.alloc, 0)  AS split_allocated_tons,
             COALESCE(split.gap, 0)    AS split_gap_tons,
             COALESCE(split.occupied, 0) AS occupied_tons,
             COALESCE(qp.approved_tons, 0) AS approved_quota_tons
      FROM (
        SELECT DISTINCT week_start, machine_run_id FROM split_trial_weeks
      ) dim
      JOIN machine_runs m ON m.id = dim.machine_run_id
      LEFT JOIN (
        SELECT week_start, machine_run_id,
               SUM(allocated_tons) AS alloc,
               SUM(gap_tons) AS gap,
               SUM(allocated_tons) FILTER (WHERE occupied) AS occupied
        FROM split_trial_weeks GROUP BY week_start, machine_run_id
      ) split ON split.week_start = dim.week_start AND split.machine_run_id = dim.machine_run_id
      LEFT JOIN (
        SELECT week_start, machine_run_id, SUM(planned_tons) AS approved_tons
        FROM quota_plans WHERE status = 'approved'
        GROUP BY week_start, machine_run_id
      ) qp ON qp.week_start = dim.week_start AND qp.machine_run_id = dim.machine_run_id
      ORDER BY dim.week_start, m.run_code
    """)
    out = []
    for r in rows:
        r = shared.serialize_row(r)
        r["remaining_tons"] = round(
            float(r["safety_capacity_tons"])
            - float(r["occupied_tons"])
            - float(r["approved_quota_tons"]), 2)
        out.append(r)
    return out


def shared_json(payload):
    import json
    return json.dumps(payload, ensure_ascii=False, default=str)

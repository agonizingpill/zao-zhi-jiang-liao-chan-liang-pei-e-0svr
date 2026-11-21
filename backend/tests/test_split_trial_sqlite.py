"""用 SQLite 建模拆周试算服务的持久化与状态机，验证：

* 试算成功/失败的状态流转与分周吨位、缺口；
* 试算失败后重试不重复生成周计划（幂等）；
* 只有凑足订单吨位才能送审；
* 并发审批遇任一周余量不足时整单退回、两周都不占余量；
* 配额页面能取到分周吨位与缺口。

复用生产代码 src.split_allocation 的同一套试算口径。
"""

import sqlite3
import unittest
from decimal import Decimal

from src.split_allocation import WeekInput, run_split_trial


DDL = """
CREATE TABLE machine_runs(
  id INTEGER PRIMARY KEY, safety_capacity_tons REAL NOT NULL);
CREATE TABLE quota_plans(
  id INTEGER PRIMARY KEY, machine_run_id INTEGER, week_start TEXT,
  planned_tons REAL, status TEXT);
CREATE TABLE split_trial_orders(
  id INTEGER PRIMARY KEY, order_code TEXT UNIQUE, machine_run_id INTEGER,
  order_tons REAL NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
  total_allocated_tons REAL DEFAULT 0, total_gap_tons REAL DEFAULT 0,
  trial_attempts INTEGER DEFAULT 0, reject_reason TEXT);
CREATE TABLE split_trial_weeks(
  id INTEGER PRIMARY KEY, order_id INTEGER, week_index INTEGER,
  week_start TEXT, machine_run_id INTEGER, forecast_lower_tons REAL,
  machine_remaining_tons REAL, cap_tons REAL, allocated_tons REAL DEFAULT 0,
  gap_tons REAL DEFAULT 0, constrained_by TEXT, occupied INTEGER DEFAULT 0,
  UNIQUE(order_id, week_index));
"""


class Repository:
    """镜像 split_trial_service 的关键事务语义（SQLite 方言）。"""

    def __init__(self, conn):
        self.conn = conn

    def _remaining(self, machine_run_id, week_start, exclude_order_id=None):
        capacity = self.conn.execute(
            "SELECT safety_capacity_tons FROM machine_runs WHERE id=?", (machine_run_id,)
        ).fetchone()[0]
        approved = self.conn.execute(
            "SELECT COALESCE(SUM(planned_tons),0) FROM quota_plans "
            "WHERE machine_run_id=? AND week_start=? AND status='approved'",
            (machine_run_id, week_start),
        ).fetchone()[0]
        occupied = self.conn.execute(
            "SELECT COALESCE(SUM(allocated_tons),0) FROM split_trial_weeks w "
            "JOIN split_trial_orders o ON o.id=w.order_id "
            "WHERE w.machine_run_id=? AND w.week_start=? AND w.occupied=1 "
            "AND (? IS NULL OR o.id<>?)",
            (machine_run_id, week_start, exclude_order_id, exclude_order_id),
        ).fetchone()[0]
        return round(capacity - approved - occupied, 2)

    def create_order(self, code, machine_id, order_tons):
        cur = self.conn.execute(
            "INSERT INTO split_trial_orders(order_code,machine_run_id,order_tons,status) "
            "VALUES (?,?,?,'draft')", (code, machine_id, order_tons))
        return cur.lastrowid

    def trial(self, order_id, week_specs):
        # BEGIN IMMEDIATE 模拟 Postgres 中 SELECT ... FOR UPDATE 的串行写。
        self.conn.execute("BEGIN IMMEDIATE")
        order = self.conn.execute(
            "SELECT * FROM split_trial_orders WHERE id=?", (order_id,)).fetchone()
        if order["status"] in ("submitted", "approved"):
            self.conn.rollback()
            raise RuntimeError("试算单已送审或已批准，不能重新试算")
        inputs = []
        for idx, (start, machine_id, lower) in enumerate(week_specs, start=1):
            remaining = self._remaining(machine_id, start, exclude_order_id=order_id)
            inputs.append(WeekInput.of(idx, start, machine_id, lower, remaining))
        result = run_split_trial(order["order_tons"], inputs)

        # 幂等：先清旧的未占用周行，再按同口径重算，绝不重复生成。
        self.conn.execute(
            "DELETE FROM split_trial_weeks WHERE order_id=? AND occupied=0", (order_id,))
        for w in result.weeks:
            self.conn.execute(
                "INSERT INTO split_trial_weeks(order_id,week_index,week_start,machine_run_id,"
                "forecast_lower_tons,machine_remaining_tons,cap_tons,allocated_tons,gap_tons,constrained_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (order_id, w.week_index, w.week_start, w.machine_run_id,
                 float(w.forecast_lower_tons), float(w.machine_remaining_tons),
                 float(w.cap_tons), float(w.allocated_tons), float(w.gap_tons), w.constrained_by))
        self.conn.execute(
            "UPDATE split_trial_orders SET status=?,total_allocated_tons=?,total_gap_tons=?,"
            "trial_attempts=trial_attempts+1 WHERE id=?",
            ("ready" if result.feasible else "trial_failed",
             float(result.total_allocated_tons), float(result.total_gap_tons), order_id))
        self.conn.commit()
        return result.feasible

    def submit(self, order_id):
        order = self.conn.execute(
            "SELECT * FROM split_trial_orders WHERE id=?", (order_id,)).fetchone()
        if order["status"] != "ready":
            raise RuntimeError("只有试算成功的单据才能送审")
        self.conn.execute(
            "UPDATE split_trial_orders SET status='submitted' WHERE id=?", (order_id,))
        self.conn.commit()

    def approve(self, order_id):
        self.conn.execute("BEGIN IMMEDIATE")
        order = self.conn.execute(
            "SELECT * FROM split_trial_orders WHERE id=?", (order_id,)).fetchone()
        if order["status"] != "submitted":
            self.conn.rollback()
            raise RuntimeError("只有已送审的试算单才能审批")
        weeks = self.conn.execute(
            "SELECT * FROM split_trial_weeks WHERE order_id=? ORDER BY week_index",
            (order_id,)).fetchall()
        shortage = any(
            self._remaining(w["machine_run_id"], w["week_start"], exclude_order_id=order_id)
            < float(w["allocated_tons"])
            for w in weeks
        )
        if shortage:
            # 整单退回：不置 occupied，两周都不占余量。
            self.conn.execute(
                "UPDATE split_trial_orders SET status='returned',reject_reason=? WHERE id=?",
                ("并发审批时机台余量不足，整单退回", order_id))
            self.conn.commit()
            return False
        self.conn.execute(
            "UPDATE split_trial_weeks SET occupied=1 WHERE order_id=?", (order_id,))
        self.conn.execute(
            "UPDATE split_trial_orders SET status='approved' WHERE id=?", (order_id,))
        self.conn.commit()
        return True

    def weeks(self, order_id):
        return self.conn.execute(
            "SELECT * FROM split_trial_weeks WHERE order_id=? ORDER BY week_index",
            (order_id,)).fetchall()

    def quota_overview(self):
        # 与生产口径一致：剩余余量 = 安全容量 − 已批配额 − 已占用拆周行。
        return self.conn.execute(
            """
            SELECT dim.week_start, dim.machine_run_id,
                   COALESCE(s.alloc,0) AS alloc, COALESCE(s.gap,0) AS gap,
                   COALESCE(s.occ,0) AS occ,
                   m.safety_capacity_tons
                     - COALESCE(qp.approved,0) - COALESCE(s.occ,0) AS remaining
            FROM (SELECT DISTINCT week_start, machine_run_id FROM split_trial_weeks) dim
            JOIN machine_runs m ON m.id=dim.machine_run_id
            LEFT JOIN (SELECT week_start, machine_run_id,
                        SUM(allocated_tons) AS alloc, SUM(gap_tons) AS gap,
                        SUM(CASE WHEN occupied=1 THEN allocated_tons ELSE 0 END) AS occ
                        FROM split_trial_weeks
                        GROUP BY week_start, machine_run_id) s
              ON s.week_start=dim.week_start AND s.machine_run_id=dim.machine_run_id
            LEFT JOIN (SELECT week_start, machine_run_id, SUM(planned_tons) AS approved
                        FROM quota_plans WHERE status='approved'
                        GROUP BY week_start, machine_run_id) qp
              ON qp.week_start=dim.week_start AND qp.machine_run_id=dim.machine_run_id
            ORDER BY dim.week_start
            """).fetchall()


def connect():
    # autocommit 隔离级别，交由仓库内显式 BEGIN IMMEDIATE 管理事务，
    # 对应 Postgres 服务中 SELECT ... FOR UPDATE 的串行化写语义。
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    return conn


class SplitTrialFlowTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect()
        # 机台 7，安全容量 100 吨。
        self.conn.execute("INSERT INTO machine_runs(id,safety_capacity_tons) VALUES (7,100)")
        self.conn.commit()
        self.repo = Repository(self.conn)
        self.specs = [
            ("2026-11-30", 7, 60.0),   # 首周下界 60，余量充足
            ("2026-12-07", 7, 70.0),   # 第二周下界 70
        ]

    def test_success_flow_and_weekly_allocation(self):
        oid = self.repo.create_order("SO-1", 7, 100.0)
        feasible = self.repo.trial(oid, self.specs)
        self.assertTrue(feasible)
        order = self.conn.execute(
            "SELECT * FROM split_trial_orders WHERE id=?", (oid,)).fetchone()
        self.assertEqual(order["status"], "ready")
        self.assertEqual(order["trial_attempts"], 1)
        weeks = self.repo.weeks(oid)
        self.assertEqual(weeks[0]["allocated_tons"], 60.0)   # 首周尽量多交
        self.assertEqual(weeks[1]["allocated_tons"], 40.0)   # 第二周补齐
        self.repo.submit(oid)
        self.assertTrue(self.repo.approve(oid))
        self.assertEqual(self.conn.execute(
            "SELECT status FROM split_trial_orders WHERE id=?", (oid,)).fetchone()["status"],
            "approved")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM split_trial_weeks WHERE order_id=? AND occupied=1",
            (oid,)).fetchone()["c"], 2)

    def test_failed_trial_cannot_submit(self):
        oid = self.repo.create_order("SO-2", 7, 100.0)
        # 两周 cap 之和只有 70。
        feasible = self.repo.trial(oid, [
            ("2026-11-30", 7, 30.0),
            ("2026-12-07", 7, 40.0),
        ])
        self.assertFalse(feasible)
        order = self.conn.execute(
            "SELECT * FROM split_trial_orders WHERE id=?", (oid,)).fetchone()
        self.assertEqual(order["status"], "trial_failed")
        self.assertEqual(order["total_gap_tons"], 30.0)
        with self.assertRaises(RuntimeError):
            self.repo.submit(oid)

    def test_retry_is_idempotent_no_duplicate_week_plans(self):
        oid = self.repo.create_order("SO-3", 7, 100.0)
        # 首轮失败：两周机台余量都被已批单压缩，cap 之和只有 30+50=80 < 100。
        self.conn.execute(
            "INSERT INTO quota_plans(machine_run_id,week_start,planned_tons,status) "
            "VALUES (7,'2026-11-30',70,'approved')")  # 首周余量 100-70=30
        self.conn.execute(
            "INSERT INTO quota_plans(machine_run_id,week_start,planned_tons,status) "
            "VALUES (7,'2026-12-07',50,'approved')")  # 次周余量 100-50=50
        self.repo.trial(oid, [("2026-11-30", 7, 60.0), ("2026-12-07", 7, 70.0)])
        self.assertEqual(
            self.conn.execute("SELECT status FROM split_trial_orders WHERE id=?", (oid,))
            .fetchone()["status"], "trial_failed")
        self.assertEqual(
            self.conn.execute("SELECT total_gap_tons FROM split_trial_orders WHERE id=?", (oid,))
            .fetchone()["total_gap_tons"], 20.0)

        # 释放占用后重试，多次重试也只保留两行周计划。
        self.conn.execute("DELETE FROM quota_plans WHERE machine_run_id=7")
        for _ in range(3):
            self.repo.trial(oid, self.specs)
        row_count = self.conn.execute(
            "SELECT COUNT(*) c FROM split_trial_weeks WHERE order_id=?", (oid,)).fetchone()["c"]
        self.assertEqual(row_count, 2)  # 不重复生成周计划
        order = self.conn.execute(
            "SELECT * FROM split_trial_orders WHERE id=?", (oid,)).fetchone()
        self.assertEqual(order["status"], "ready")
        self.assertEqual(order["trial_attempts"], 4)  # 1 次首轮 + 3 次重试

    def test_concurrent_approval_returns_whole_order_and_occupies_nothing(self):
        # 两张单都瞄准首周 2026-11-30 的机台容量。
        oid_a = self.repo.create_order("SO-A", 7, 60.0)
        self.repo.trial(oid_a, [
            ("2026-11-30", 7, 60.0),
            ("2026-12-07", 7, 60.0),
        ])
        self.repo.submit(oid_a)

        oid_b = self.repo.create_order("SO-B", 7, 60.0)
        # B 试算时机台尚空闲（A 未批准未占用），试算成功。
        self.repo.trial(oid_b, [
            ("2026-11-30", 7, 60.0),
            ("2026-12-07", 7, 60.0),
        ])
        self.repo.submit(oid_b)

        # A 先批准，占满首周 60 吨（机台容量 100，首周仅剩 40）。
        self.assertTrue(self.repo.approve(oid_a))
        # B 审批时首周余量 40 < 需求 60 → 整单退回。
        self.assertFalse(self.repo.approve(oid_b))

        b = self.conn.execute(
            "SELECT * FROM split_trial_orders WHERE id=?", (oid_b,)).fetchone()
        self.assertEqual(b["status"], "returned")
        # 两周都不占：B 没有任何 occupied 周行。
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM split_trial_weeks WHERE order_id=? AND occupied=1",
            (oid_b,)).fetchone()["c"], 0)
        # A 的占用不受影响。
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM split_trial_weeks WHERE order_id=? AND occupied=1",
            (oid_a,)).fetchone()["c"], 2)
        # 机台首周被占 60，余量计算仍为 40，B 未额外占用。
        self.assertEqual(self.repo._remaining(7, "2026-11-30"), 40.0)
        # 配额页面口径同样反映首周剩余 40。
        first_week = next(r for r in self.repo.quota_overview() if r["week_start"] == "2026-11-30")
        self.assertEqual(first_week["remaining"], 40.0)

    def test_quota_overview_reports_weekly_tons_and_gap(self):
        oid = self.repo.create_order("SO-4", 7, 100.0)
        self.repo.trial(oid, [
            ("2026-11-30", 7, 30.0),
            ("2026-12-07", 7, 40.0),
        ])  # 失败单：分周 30 / 40，订单级缺口 30
        overview = self.repo.quota_overview()
        self.assertEqual(len(overview), 2)
        first = overview[0]
        self.assertEqual(first["week_start"], "2026-11-30")
        self.assertEqual(first["alloc"], 30.0)
        self.assertEqual(first["gap"], 0.0)
        second = overview[1]
        self.assertEqual(second["alloc"], 40.0)
        self.assertEqual(second["gap"], 30.0)
        self.assertEqual(second["occ"], 0)  # 未批准不占余量
        self.assertEqual(second["remaining"], 100.0)  # 无已批占用时等于安全容量


if __name__ == "__main__":
    unittest.main()

"""整批拆周试算的纯函数口径。

业务规则
--------
计划员填写订单吨位，并在同一纸机上选择两个周次及各自工况。两周分别试算：

* 单周排量同时受两个上限约束：
    1. 该周工况预测下界 ``forecast_lower_tons``（预测带 10% 分位）；
    2. 该周机台余量 ``machine_remaining_tons``
       = 机台安全容量 − 该周已批单占用吨位。
* 首周尽量多交，即先把首周排到允许的最大值，再用第二周补齐订单剩余。
* 只有两周排量之和凑足订单吨位，整单才允许送审；否则试算失败，记录缺口。

本模块不触碰数据库与 Web 框架，所有金额/吨位使用 ``Decimal`` 以避免浮点误差，
方便用 SQLite / 纯 Python 单测直接验证。
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

_TONS = Decimal("0.01")


def _tons(value) -> Decimal:
    """统一转成两位小数的吨位 Decimal。"""
    return Decimal(str(value)).quantize(_TONS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class WeekInput:
    """单周试算输入。"""

    week_index: int
    week_start: str
    machine_run_id: int
    forecast_lower_tons: Decimal
    machine_remaining_tons: Decimal

    @classmethod
    def of(cls, week_index, week_start, machine_run_id, forecast_lower_tons, machine_remaining_tons):
        return cls(
            week_index=week_index,
            week_start=week_start,
            machine_run_id=machine_run_id,
            forecast_lower_tons=_tons(forecast_lower_tons),
            machine_remaining_tons=_tons(machine_remaining_tons),
        )

    @property
    def cap_tons(self) -> Decimal:
        """单周可排上限 = min(预测下界, 机台余量)。"""
        return min(self.forecast_lower_tons, self.machine_remaining_tons)


@dataclass
class WeekResult:
    """单周试算结果。"""

    week_index: int
    week_start: str
    machine_run_id: int
    forecast_lower_tons: Decimal
    machine_remaining_tons: Decimal
    cap_tons: Decimal
    allocated_tons: Decimal = Decimal("0.00")
    gap_tons: Decimal = Decimal("0.00")
    constrained_by: str = ""


@dataclass
class SplitTrialResult:
    """整单两周试算结果。"""

    order_tons: Decimal
    weeks: list = field(default_factory=list)
    total_allocated_tons: Decimal = Decimal("0.00")
    total_gap_tons: Decimal = Decimal("0.00")
    feasible: bool = False
    reason: str = ""

    def as_dict(self):
        return {
            "order_tons": float(self.order_tons),
            "feasible": self.feasible,
            "reason": self.reason,
            "total_allocated_tons": float(self.total_allocated_tons),
            "total_gap_tons": float(self.total_gap_tons),
            "weeks": [
                {
                    "week_index": w.week_index,
                    "week_start": w.week_start,
                    "machine_run_id": w.machine_run_id,
                    "forecast_lower_tons": float(w.forecast_lower_tons),
                    "machine_remaining_tons": float(w.machine_remaining_tons),
                    "cap_tons": float(w.cap_tons),
                    "allocated_tons": float(w.allocated_tons),
                    "gap_tons": float(w.gap_tons),
                    "constrained_by": w.constrained_by,
                }
                for w in self.weeks
            ],
        }


def _binding_reason(week: WeekInput) -> str:
    """标记该周排量被哪条边界卡住。"""
    if week.forecast_lower_tons <= week.machine_remaining_tons:
        return "forecast_lower"
    return "machine_remaining"


def run_split_trial(order_tons, weeks):
    """对同一纸机的两个周次执行整批拆周试算。

    参数
    ----
    order_tons:
        计划员填写的订单吨位。
    weeks:
        恰好两个 :class:`WeekInput`，顺序即交付顺序（首周在前，尽量多交）。

    返回
    ----
    :class:`SplitTrialResult`。首周先排到自身上限，第二周只补订单剩余；
    两周总和达到订单吨位才标记 ``feasible=True``，否则按周记录缺口。
    """
    if len(weeks) != 2:
        raise ValueError("拆周试算必须选择恰好两个周次")
    order = _tons(order_tons)
    if order <= 0:
        raise ValueError("订单吨位必须为正数")

    results = [
        WeekResult(
            week_index=w.week_index,
            week_start=w.week_start,
            machine_run_id=w.machine_run_id,
            forecast_lower_tons=w.forecast_lower_tons,
            machine_remaining_tons=w.machine_remaining_tons,
            cap_tons=w.cap_tons,
            constrained_by=_binding_reason(w),
        )
        for w in weeks
    ]

    remaining_demand = order
    for result in results:
        if remaining_demand <= 0:
            break
        take = min(remaining_demand, result.cap_tons)
        if take < 0:
            take = Decimal("0.00")
        result.allocated_tons = _tons(take)
        remaining_demand = _tons(remaining_demand - take)

    total_allocated = _tons(sum((w.allocated_tons for w in results), Decimal("0.00")))
    total_gap = _tons(order - total_allocated)
    # 试算按周顺序排产，前序周次都已排到自身上限，最终未满足的订单只可能
    # 留在最后一个周次，故订单级缺口整体计入末周，其余周记 0。
    # 这样各周缺口之和恒等于订单级缺口，便于配额页面直接汇总。
    if total_gap > 0:
        results[-1].gap_tons = total_gap

    feasible = total_gap <= 0
    if feasible:
        reason = "两周排量凑足订单吨位，可送审"
    else:
        reason = f"两周可排吨位不足，缺口 {total_gap} 吨，不能送审"

    return SplitTrialResult(
        order_tons=order,
        weeks=results,
        total_allocated_tons=total_allocated,
        total_gap_tons=total_gap,
        feasible=feasible,
        reason=reason,
    )

"""整批拆周试算纯函数单测，仅依赖标准库：python3 -m unittest。"""

import unittest
from decimal import Decimal

from src.split_allocation import WeekInput, run_split_trial


def week(idx, start, machine, lower, remaining):
    return WeekInput.of(idx, start, machine, lower, remaining)


class SplitTrialTests(unittest.TestCase):
    def test_first_week_maxes_out_second_fills_remainder(self):
        # 订单 100；首周下界/余量都很充足（cap=60），首周应尽量多交 60，第二周补 40。
        result = run_split_trial(
            100,
            [
                week(1, "2026-11-30", 7, 60, 80),   # cap = min(60,80)=60
                week(2, "2026-12-07", 7, 70, 80),   # cap = 70，只需补 40
            ],
        )
        self.assertTrue(result.feasible)
        self.assertEqual(result.weeks[0].allocated_tons, Decimal("60.00"))
        self.assertEqual(result.weeks[1].allocated_tons, Decimal("40.00"))
        self.assertEqual(result.total_allocated_tons, Decimal("100.00"))
        self.assertEqual(result.total_gap_tons, Decimal("0.00"))
        # 第二周只补订单剩余，不会因为自身 cap 大而超排。
        self.assertEqual(result.weeks[1].constrained_by, "forecast_lower")

    def test_machine_remaining_is_binding(self):
        # 首周预测下界 60，但机台余量只剩 45，排量被机台余量卡住。
        result = run_split_trial(
            100,
            [
                week(1, "2026-11-30", 7, 60, 45),   # cap = 45
                week(2, "2026-12-07", 7, 60, 80),   # cap = 60
            ],
        )
        self.assertTrue(result.feasible)
        self.assertEqual(result.weeks[0].allocated_tons, Decimal("45.00"))
        self.assertEqual(result.weeks[0].constrained_by, "machine_remaining")
        self.assertEqual(result.weeks[1].allocated_tons, Decimal("55.00"))

    def test_infeasible_records_gap(self):
        # 两周可排总和 30+40=70，订单 100，缺口 30，不能送审。
        result = run_split_trial(
            100,
            [
                week(1, "2026-11-30", 7, 30, 30),
                week(2, "2026-12-07", 7, 40, 40),
            ],
        )
        self.assertFalse(result.feasible)
        self.assertEqual(result.total_allocated_tons, Decimal("70.00"))
        self.assertEqual(result.total_gap_tons, Decimal("30.00"))
        # 首周满足 30（无缺口），缺口全部落在第二周。
        self.assertEqual(result.weeks[0].gap_tons, Decimal("0.00"))
        self.assertEqual(result.weeks[1].gap_tons, Decimal("30.00"))
        self.assertIn("缺口", result.reason)

    def test_first_week_shortfall_rolls_to_second(self):
        # 首周需求 100、cap 只有 20，缺口滚入第二周；第二周 cap 50 仍不够。
        result = run_split_trial(
            100,
            [
                week(1, "2026-11-30", 7, 20, 20),
                week(2, "2026-12-07", 7, 50, 50),
            ],
        )
        self.assertFalse(result.feasible)
        self.assertEqual(result.weeks[0].allocated_tons, Decimal("20.00"))
        self.assertEqual(result.weeks[0].gap_tons, Decimal("0.00"))  # 首周已排满
        self.assertEqual(result.weeks[1].allocated_tons, Decimal("50.00"))
        self.assertEqual(result.weeks[1].gap_tons, Decimal("30.00"))  # 缺口归末周
        # 各周缺口之和恒等于订单级缺口。
        self.assertEqual(result.total_gap_tons, Decimal("30.00"))
        self.assertEqual(
            sum((w.gap_tons for w in result.weeks), Decimal("0.00")),
            result.total_gap_tons,
        )

    def test_exact_capacity_is_feasible(self):
        result = run_split_trial(
            80,
            [
                week(1, "2026-11-30", 7, 40, 40),
                week(2, "2026-12-07", 7, 40, 40),
            ],
        )
        self.assertTrue(result.feasible)
        self.assertEqual(result.total_gap_tons, Decimal("0.00"))

    def test_requires_exactly_two_weeks(self):
        with self.assertRaises(ValueError):
            run_split_trial(100, [week(1, "2026-11-30", 7, 10, 10)])
        with self.assertRaises(ValueError):
            run_split_trial(100, [
                week(1, "2026-11-30", 7, 10, 10),
                week(2, "2026-12-07", 7, 10, 10),
                week(3, "2026-12-14", 7, 10, 10),
            ])

    def test_order_must_be_positive(self):
        with self.assertRaises(ValueError):
            run_split_trial(0, [
                week(1, "2026-11-30", 7, 10, 10),
                week(2, "2026-12-07", 7, 10, 10),
            ])

    def test_dict_serialization_uses_floats(self):
        result = run_split_trial(
            100,
            [
                week(1, "2026-11-30", 7, 60, 80),
                week(2, "2026-12-07", 7, 70, 80),
            ],
        )
        payload = result.as_dict()
        self.assertTrue(payload["feasible"])
        self.assertEqual(len(payload["weeks"]), 2)
        self.assertIsInstance(payload["weeks"][0]["allocated_tons"], float)


if __name__ == "__main__":
    unittest.main()

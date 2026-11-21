"""Business queries over authoritative project records."""

from .operations_reconciliation import RECONCILIATION_VIEWS

VIEWS = {
    'quota-exposure': (
        '机台安全余量',
        '比较预测区间上界与机台安全线，将需要拆分的配额交给计划员。',
        ['机台', '计划', '周起始', '上界 吨', '安全线 吨', '剩余 吨', '排程判定'],
        """
SELECT m.run_code,p.plan_code,p.week_start,p.upper_tons,p.safety_limit_tons,p.safety_limit_tons-p.upper_tons AS headroom,
       CASE WHEN p.upper_tons IS NULL THEN '等待预测' WHEN p.upper_tons>p.safety_limit_tons THEN '拆分计划' ELSE '安全线内' END
FROM quota_plans p JOIN machine_runs m ON m.id=p.machine_run_id ORDER BY headroom,p.plan_code
""",
    ),
    'material-balance': (
        '配额领用余额',
        '汇总每条计划下实际发料和待批领料，计算尚可安排的物料吨数。',
        ['配额计划', '状态', '计划 吨', '实发 吨', '未批及待发 吨', '可安排 吨'],
        """
SELECT p.plan_code,p.status,p.planned_tons,
       COALESCE(SUM(CASE WHEN i.issued_at IS NOT NULL THEN i.quantity_tons END),0),
       COALESCE(SUM(CASE WHEN i.issued_at IS NULL AND i.status NOT IN ('rejected','cancelled') THEN i.quantity_tons END),0),
       p.planned_tons-COALESCE(SUM(CASE WHEN i.status NOT IN ('rejected','cancelled') THEN i.quantity_tons END),0) AS available
FROM quota_plans p LEFT JOIN material_issues i ON i.quota_plan_id=p.id
GROUP BY p.id,p.plan_code,p.status,p.planned_tons ORDER BY available,p.plan_code
""",
    ),
    'blend-moisture': (
        '浆料水分对照',
        '将批次目标水分与当前纤维配比实测水分对照，超过 2 个百分点提示质检复核。',
        ['浆料批次', '等级', '配比', '目标 %', '实测 %', '偏差 百分点', '判定'],
        """
SELECT p.lot_code,p.grade,b.blend_code,p.target_moisture_pct,b.moisture_pct,b.moisture_pct-p.target_moisture_pct,
       CASE WHEN b.id IS NULL THEN '待关联配比' WHEN ABS(b.moisture_pct-p.target_moisture_pct)>2 THEN '核对批次水分' ELSE '常规检测' END
FROM pulp_lots p LEFT JOIN fiber_blends b ON b.id=p.blend_id ORDER BY p.received_at DESC,p.id
""",
    ),
    **RECONCILIATION_VIEWS,
}

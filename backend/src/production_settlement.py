"""Source records and completion rules for quota-settlement."""

PRODUCTION_SETTLEMENT = {
    'quota-settlement': {
        'title': '机台周量结算',
        'reference_label': '周排程结算单号',
        'outcomes': {
            'reconciled': ('已批准周量在机台限额内', ('safe',)),
            'replan': ('转生产计划拆分', None),
        },
        'sql': """
SELECT CONCAT(m.run_code,'/',p.week_start) AS source_key,CONCAT(m.run_code,' · ',p.week_start) AS subject,
       CONCAT('已批 ',COUNT(*),' 单 · 合计 ',COALESCE(SUM(p.planned_tons),0),' 吨 · 最低限额 ',MIN(p.safety_limit_tons),' 吨') AS source_summary,
       CASE WHEN COUNT(*)=COUNT(p.planned_tons) AND SUM(p.planned_tons)<=MIN(p.safety_limit_tons)
            THEN 'safe' ELSE 'overload' END AS readiness
FROM quota_plans p JOIN machine_runs m ON m.id=p.machine_run_id
WHERE p.status='approved' GROUP BY m.run_code,p.week_start
""",
    },
    'stock-handoff': {
        'title': '仓库发料凭证回收',
        'reference_label': '出库凭证号',
        'outcomes': {
            'posted': ('已登记出库并有独立签批', ('issued',)),
            'investigate': ('转仓库复核缺证', None),
        },
        'sql': """
SELECT i.issue_code AS source_key,CONCAT(i.issue_code,' · ',i.material_name) AS subject,
       CONCAT(p.plan_code,' · ',i.quantity_tons,' 吨 · ',i.status,' · 出库 ',COALESCE(i.issued_at::text,'未记账')) AS source_summary,
       CASE WHEN i.status='issued' AND i.issued_at IS NOT NULL AND i.approved_by<>i.requested_by
            THEN 'issued' ELSE 'pending' END AS readiness
FROM material_issues i JOIN quota_plans p ON p.id=i.quota_plan_id
""",
    },
}

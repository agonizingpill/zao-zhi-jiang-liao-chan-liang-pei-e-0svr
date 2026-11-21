"""Additional operational reconciliation rules for this project."""

RECONCILIATION_VIEWS = {
    'machine-week-consolidation': (
        '机台周配额并单核对',
        '按机台与周合并已批准配额，用同周批准单中的最低安全线核对并单结果。',
        ['机台', '周初', '已批单数', '未计吨单', '已批合计 吨', '最低安全线 吨', '排程结论'],
        """
SELECT m.run_code,p.week_start,COUNT(*),COUNT(*)-COUNT(p.planned_tons),SUM(p.planned_tons),MIN(p.safety_limit_tons),
       CASE WHEN COUNT(p.planned_tons)<COUNT(*) THEN '待补批准吨数'
            WHEN SUM(p.planned_tons)>MIN(p.safety_limit_tons) THEN '并单超出安全线'
            ELSE '并单容量内' END
FROM quota_plans p JOIN machine_runs m ON m.id=p.machine_run_id
WHERE p.status='approved' GROUP BY m.run_code,p.week_start ORDER BY p.week_start DESC,m.run_code
""",
    ),
    'issue-approval-ledger': (
        '发料签批链核销',
        '逐张核对领料申请、批准人和出库时点，标记出库凭证缺口与越权遗留。',
        ['领料单', '配额单', '物料', '数量 吨', '申请人', '批准人', '出库时间', '核销结论'],
        """
SELECT i.issue_code,p.plan_code,i.material_name,i.quantity_tons,requester.display_name,approver.display_name,i.issued_at,
       CASE WHEN i.status='rejected' THEN '已驳回'
            WHEN i.status='issued' AND (i.approved_by IS NULL OR i.issued_at IS NULL) THEN '出库证据待补'
            WHEN i.approved_by=i.requested_by THEN '申请与批准同人，待复核'
            WHEN i.status='issued' THEN '出库链齐备'
            WHEN i.status='approved' THEN '待实际出库'
            ELSE '待批准' END
FROM material_issues i JOIN quota_plans p ON p.id=i.quota_plan_id
JOIN users requester ON requester.id=i.requested_by LEFT JOIN users approver ON approver.id=i.approved_by
ORDER BY i.created_at DESC,i.id
""",
    ),
}

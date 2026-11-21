"""Join current source evidence to the accountable handling and review workflow."""

from fastapi import HTTPException

from . import casework_store
from .operations_evidence import actor_field, query
from .production_settlement import PRODUCTION_SETTLEMENT

WORKFLOWS = {
    **PRODUCTION_SETTLEMENT,
}


def workflow_for(code):
    if code not in WORKFLOWS:
        raise HTTPException(404, '该处置类型不存在')
    return WORKFLOWS[code]


def source_rows(workflow, source_key=None):
    template = workflow['sql']
    sql = f'SELECT source_key, subject, source_summary, readiness FROM ({template}) source_records'
    if source_key is not None:
        results = query(sql + ' WHERE source_key=%s', (source_key,))
    else:
        results = query(sql + ' ORDER BY readiness,source_key LIMIT 150')
    keys = ('source_key', 'subject', 'source_summary', 'readiness')
    return [dict(zip(keys, row, strict=True)) for row in results]


def case_view(code, source_key):
    workflow = workflow_for(code)
    rows = source_rows(workflow, source_key)
    past = casework_store.history(code, source_key)
    if not rows and not past:
        raise HTTPException(404, '找不到关联的业务记录')
    return {'source': rows[0] if rows else None, 'history': past, 'revision': past[0]['revision'] if past else 0}


def compact(value, limit):
    return ' '.join(value.split())[:limit]


def change(code, source_key, data, user):
    workflow = workflow_for(code)
    snapshot = case_view(code, source_key)
    history = snapshot['history']
    latest = history[0] if history else None
    if snapshot['revision'] != data.revision:
        raise HTTPException(409, '记录已有更新，请重新读取')
    actor = next(str(value) for key in ('username', 'email', 'name', 'id', 'sub')
                 if (value := actor_field(user, key)) is not None)
    role = actor_field(user, 'role')
    source = snapshot['source']
    summary = str(source['source_summary']) if source else '源记录不可用'
    note = compact(data.note, 500)
    if len(note) < 4:
        raise HTTPException(422, '请写明本次处置或复核的依据')
    if data.action == 'submit':
        if role not in {'operator', 'supervisor'}:
            raise HTTPException(403, '当前角色只能查看处置记录')
        if latest and latest['status'] != 'returned':
            raise HTTPException(409, '只有首次登记或退回后可以提交')
        if not source:
            raise HTTPException(409, '源记录不可用，请先恢复原业务记录')
        outcome = data.outcome
        reference = compact(data.reference, 120)
        if outcome not in workflow['outcomes'] or len(reference) < 3:
            raise HTTPException(422, '请选择处置结果并填写业务凭证编号')
        status = 'submitted'
    else:
        if role != 'supervisor':
            raise HTTPException(403, '只有主管可以复核处置单')
        if not latest or latest['status'] != 'submitted':
            raise HTTPException(409, '当前没有待复核的处置记录')
        if latest['actor'] == actor:
            raise HTTPException(409, '提交人需要由另一名主管复核')
        outcome, reference = latest['outcome'], latest['reference']
        label, required = workflow['outcomes'][outcome]
        if data.action == 'approve' and required and (not source or source['readiness'] not in required):
            raise HTTPException(409, f'当前源记录尚不满足「{label}」的验收条件')
        status = 'closed' if data.action == 'approve' else 'returned'
    revision = snapshot['revision'] + 1
    if not casework_store.append(code, source_key, revision, status, actor, outcome, reference, note, summary):
        raise HTTPException(409, '记录已有更新，请重新读取')
    return case_view(code, source_key)

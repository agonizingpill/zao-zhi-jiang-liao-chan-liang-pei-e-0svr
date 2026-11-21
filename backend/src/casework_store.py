"""Append-only handling records with optimistic concurrency per source record."""

from threading import Lock
from uuid import uuid4

from .operations_evidence import query

_schema_lock = Lock()
_schema_ready = False


def ensure_schema():
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        query("""
            CREATE TABLE IF NOT EXISTS operational_case_events (
                workflow VARCHAR(40) NOT NULL,
                source_key VARCHAR(100) NOT NULL,
                revision INTEGER NOT NULL,
                status VARCHAR(20) NOT NULL,
                actor VARCHAR(160) NOT NULL,
                outcome VARCHAR(40) NOT NULL,
                reference VARCHAR(120) NOT NULL,
                note VARCHAR(500) NOT NULL,
                source_summary TEXT NOT NULL,
                event_token CHAR(32) NOT NULL,
                event_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (workflow, source_key, revision)
            )
        """)
        _schema_ready = True


def history(workflow, key):
    ensure_schema()
    fields = ('revision', 'status', 'actor', 'outcome', 'reference', 'note', 'source_summary', 'event_at')
    rows = query("""
        SELECT revision, status, actor, outcome, reference, note, source_summary, event_at
        FROM operational_case_events WHERE workflow=%s AND source_key=%s ORDER BY revision DESC
    """, (workflow, key))
    return [dict(zip(fields, row, strict=True)) for row in rows]


def append(workflow, key, revision, status, actor, outcome, reference, note, source_summary):
    ensure_schema()
    token = uuid4().hex
    query("""
        INSERT INTO operational_case_events
            (workflow,source_key,revision,status,actor,outcome,reference,note,source_summary,event_token)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (workflow,source_key,revision) DO NOTHING
    """, (workflow, key, revision, status, actor, outcome, reference, note, source_summary, token))
    saved = query("""
        SELECT event_token FROM operational_case_events WHERE workflow=%s AND source_key=%s AND revision=%s
    """, (workflow, key, revision))
    return bool(saved) and saved[0][0] == token

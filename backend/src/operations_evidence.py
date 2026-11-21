"""Operational evidence views and supervisor acknowledgements."""

import hashlib
import json
from collections.abc import Mapping
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from .operations_queries import VIEWS
from .auth import current_user
from .db import connection

router = APIRouter(prefix="/operations-evidence", tags=["业务核验"])
_schema_lock = Lock()
_schema_ready = False


def query(sql, parameters=()):
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, parameters)
            if cursor.description is None:
                return []
            return [tuple(row.values()) if isinstance(row, Mapping) else tuple(row)
                    for row in cursor.fetchall()]


def ensure_receipt_table():
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        query("""
            CREATE TABLE IF NOT EXISTS operations_evidence_receipt (
                module VARCHAR(48) NOT NULL,
                row_key CHAR(64) NOT NULL,
                evidence TEXT NOT NULL,
                signer VARCHAR(160) NOT NULL,
                note VARCHAR(500) NOT NULL,
                signed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (module, row_key)
            )
        """)
        _schema_ready = True


def actor_field(user, field):
    return user.get(field) if isinstance(user, Mapping) else getattr(user, field, None)


def get_view(module):
    if module not in VIEWS:
        raise HTTPException(404, "核验模块不存在")
    return VIEWS[module]


def evidence_rows(sql):
    for row in query(sql + "\nLIMIT 500"):
        cells = jsonable_encoder(row)
        payload = json.dumps(cells, ensure_ascii=False, separators=(",", ":"))
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        yield fingerprint, cells, payload


class Acknowledgement(BaseModel):
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str = Field(min_length=3, max_length=500)


@router.get("")
def catalog(user=Depends(current_user)):
    return {
        "modules": [{"id": key, "name": view[0], "description": view[1]} for key, view in VIEWS.items()],
        "can_acknowledge": actor_field(user, "role") == "supervisor",
    }


@router.get("/{module}")
def view_rows(module: str, user=Depends(current_user)):
    title, description, columns, sql = get_view(module)
    ensure_receipt_table()
    receipts = {
        key: {"signer": signer, "note": note, "at": at}
        for key, signer, note, at in query(
            "SELECT row_key, signer, note, signed_at FROM operations_evidence_receipt WHERE module=%s",
            (module,),
        )
    }
    return {
        "title": title, "description": description, "columns": columns, "limit": 500,
        "rows": [{"key": key, "cells": cells, "receipt": receipts.get(key)}
                 for key, cells, _ in evidence_rows(sql)],
    }


@router.post("/{module}/acknowledgements", status_code=201)
def acknowledge(module: str, data: Acknowledgement, user=Depends(current_user)):
    if actor_field(user, "role") != "supervisor":
        raise HTTPException(403, "仅主管可以签认业务证据")
    note = data.note.strip()
    if len(note) < 3:
        raise HTTPException(422, "请填写至少 3 个有效字符的核验说明")
    sql = get_view(module)[3]
    current = next((payload for key, _, payload in evidence_rows(sql) if key == data.key), None)
    if current is None:
        raise HTTPException(409, "业务记录已变化，请刷新后核验")
    ensure_receipt_table()
    signer = next(str(value) for field in ("username", "email", "name", "id", "sub")
                  if (value := actor_field(user, field)) is not None)
    query("""
        INSERT INTO operations_evidence_receipt(module,row_key,evidence,signer,note)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (module,row_key) DO NOTHING
    """, (module, data.key, current, signer, note))
    result = query("""
        SELECT signer,note,signed_at FROM operations_evidence_receipt WHERE module=%s AND row_key=%s
    """, (module, data.key))[0]
    if result[:2] != (signer, note):
        raise HTTPException(409, "该证据已签认，请刷新查看")
    return {"key": data.key, "signer": result[0], "note": result[1], "at": result[2]}

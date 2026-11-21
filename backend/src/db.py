import time
from contextlib import contextmanager
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from .config import DATABASE_URL

@contextmanager
def connection():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def query(sql, params=(), one=False):
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return (dict(rows[0]) if rows else None) if one else [dict(row) for row in rows]

def execute(sql, params=()):
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

@contextmanager
def tx_cursor(one=False):
    """在单个连接/事务内打开游标，供需要行锁与多语句原子性的服务使用。

    提交/回滚由 :func:`connection` 上下文统一处理；游标使用 RealDictCursor，
    返回普通 dict 以与 :func:`query` 保持一致。
    """
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur

def wait_for_db():
    for _ in range(40):
        try:
            with connection():
                return
        except psycopg2.OperationalError:
            time.sleep(1)
    raise RuntimeError("database did not become ready")

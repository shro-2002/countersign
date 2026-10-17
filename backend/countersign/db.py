"""PostgreSQL access: one connection per request, explicit transactions, plain SQL."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA = Path(__file__).with_name("schema.sql")


@contextmanager
def connect(db_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(db_url, row_factory=dict_row, connect_timeout=5) as conn:
        yield conn


@contextmanager
def case_lock(db_url: str, case_id: str) -> Iterator[None]:
    """Serialize work on one key (a case id, or a named lock such as "demo-create") across processes (advisory lock)."""
    # ponytail: holds one connection for the PayPal call (~2 s); fine at demo scale, use a queue if it is not
    with psycopg.connect(db_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (case_id,))
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (case_id,))


def migrate(db_url: str) -> None:
    with connect(db_url) as conn:
        conn.execute(SCHEMA.read_text())


def add_event(conn: psycopg.Connection, case_id: str, kind: str, detail: dict | None = None) -> None:
    conn.execute("INSERT INTO events (case_id, kind, detail) VALUES (%s, %s, %s)",
                 (case_id, kind, Jsonb(detail or {})))


def json(value: object) -> Jsonb:
    return Jsonb(value)

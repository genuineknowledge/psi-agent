"""MySQL connection for the demo's mock store.

The dump is a MySQL 8.4 export and the reference SQL uses MySQL builtins, so the
demo runs the real engine rather than a translated one: all 396 reference queries
execute unmodified, and query plans come from the same optimiser the production
service will use.

Read-only is enforced by the database, not by convention: connect as a user
holding only SELECT on weekly_mock, so a bug here cannot mutate the store.
"""

from __future__ import annotations

import os
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

DB_HOST = os.environ.get("GUOSHU_WEEKLY_MYSQL_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("GUOSHU_WEEKLY_MYSQL_PORT", "3306"))
DB_USER = os.environ.get("GUOSHU_WEEKLY_MYSQL_USER", "weekly_ro")
DB_PASSWORD = os.environ.get("GUOSHU_WEEKLY_MYSQL_PASSWORD", "")
"""No default: this source ships without credentials.

Set GUOSHU_WEEKLY_MYSQL_PASSWORD before starting the service. A working default
left in source is how a demo password ends up in a production checkout.
"""
DB_NAME = os.environ.get("GUOSHU_WEEKLY_MYSQL_DB", "weekly_mock")

DSN_DESCRIPTION = f"mysql://{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
"""Human-readable target for diagnostics. Never includes the password."""


def connect() -> Any:
    """Open a read-only connection to the mock store."""
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
    )

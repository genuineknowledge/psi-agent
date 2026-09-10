"""Read-only PostgreSQL connection for the oa-weekly (ChatBI) data source.

The demo reads weekly_mock over MySQL (see ``_db.py``).  The formal ChatBI
source lives in O2OA's PostgreSQL (schema ``public``; 2026-09-08 field note):
this module opens a *read-only* connection only -- the session is forced into
read-only transactions, and the login role must hold SELECT only (granted by
the data owner with the SQL in the field note).

库名的实测结论(2026-09-10,只读账号直连复核):字段说明里写的 ``O2OA-DB`` 是
**业务叫法**,集群里没有这个 database;物理库名是 ``o2oa``(另见 ``oa_agent`` /
``oa_biz`` 两个库,后者是历史空表别用)。该库的 ``datacl`` 被显式改过
(``{=T/admin,admin=CTc/admin,...}``),PUBLIC 没有 CONNECT —— 所以必须由库 owner
显式 ``GRANT CONNECT ON DATABASE o2oa TO <只读账号>``,光靠角色默认权限连不上。

Environment (all optional; defaults are the demo/formal conventions):
    PGHOST / PGPORT / PGDATABASE / PGUSER / PGPASSWORD / PGSCHEMA

psycopg 3 is imported lazily so the demo stack never needs it.
"""

from __future__ import annotations

import os
from typing import Any

DB_HOST = os.environ.get("PGHOST", "127.0.0.1")
DB_PORT = int(os.environ.get("PGPORT", "5432"))
DB_NAME = os.environ.get("PGDATABASE", "o2oa")
DB_USER = os.environ.get("PGUSER", "chatbi_read")
DB_PASSWORD = os.environ.get("PGPASSWORD", "")
DB_SCHEMA = os.environ.get("PGSCHEMA", "public")

DB_CONNECT_TIMEOUT = int(os.environ.get("GUOSHU_PG_CONNECT_TIMEOUT", "10"))
"""建连超时(秒)。**必须有**:没有它时,库里不可达(网络黑洞、隧道半死)会让工具调用
一直挂着 —— agent 侧只看到"这一轮没返回",既不知道是查询慢还是库不可达,也拿不到
可判断的错误。10 秒足够覆盖正常建连,又把失败压成一条明确异常(``_guard`` 会把它包成
工具出口的信封)。

``GUOSHU_PG_CONNECT_TIMEOUT=0`` 可关掉(psycopg 语义:0 即不限时)。
"""

DB_STATEMENT_TIMEOUT_MS = int(os.environ.get("GUOSHU_PG_STATEMENT_TIMEOUT_MS", "30000"))
"""单条语句超时(毫秒),由连接参数下发。同步设它是因为:一个跑飞的查询与"库挂了"
对调用方是同一件事(都要等),而 30 秒后报 ``query_failed`` 至少是可行动的。
"""

try:
    import psycopg  # formal source only, kept top-level guarded
except ImportError:  # pragma: no cover
    # Rebind to None is intentional so the demo stack (no psycopg installed)
    # can still import this module; connect() raises a helpful error later.
    psycopg = None  # ty: ignore (module-typed name cannot be rebound)


def dsn() -> str:
    """Human-readable target. Never includes the password."""
    return f"pg://{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}?schema={DB_SCHEMA}"


def connect() -> Any:
    """Open a read-only connection to the formal source."""
    if psycopg is None:  # pragma: no cover
        raise RuntimeError(
            "psycopg is required for the o2oa data source; install with 'pip install \"psycopg[binary]>=3.2,<4\"'"
        )
    # -c options: read-only session + pinned schema + statement timeout.  The DB
    # role itself is granted SELECT only, so even a bug here cannot mutate the store.
    options = (
        "-c default_transaction_read_only=on "
        f"-c search_path={DB_SCHEMA} "
        f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}"
    )
    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD or None,
        autocommit=True,
        connect_timeout=DB_CONNECT_TIMEOUT or None,
        options=options,
    )

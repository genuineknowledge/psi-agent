"""Synthetic interface: ``haitun.review.requested`` (idle until wired).

Emit example::

    await ctx.emit({
        "payload": {
            "review_id": "r1",
            "subject": "供应商合同 v3",
            "kind": "contract",
            "assignee_open_id": "ou_legal",
            "requester_open_id": "ou_biz",
            "artifact_ref": "feishu_docx:…",
        },
        "routing": {"open_id": "ou_legal"},
        "idempotency_key": "haitun:review.requested:r1",
    })
"""

from __future__ import annotations

from typing import Any

import anyio


async def produce(ctx: Any) -> None:
    _ = ctx
    await anyio.sleep_forever()

"""feishu_pm_batch_send 结构钉 —— 状态行改标、工具参数校验。"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
from pathlib import Path


def test_apply_states_marks_sent_and_failed() -> None:
    f = importlib.import_module("_feishu_impl")
    lines = [
        "张三|格式违规|待补发",
        "李四|时间违规|待补发",
        "王五|粒度|已发",
    ]
    out = f._apply_states(lines, {"张三"}, {"李四": "230013 bot 不可达"})
    assert out == [
        "张三|格式违规|已发",
        "李四|时间违规|失败:230013 bot 不可达",
        "王五|粒度|已发",
    ]


def test_apply_states_keeps_unknown_rows() -> None:
    f = importlib.import_module("_feishu_impl")
    lines = ["张三|格式|待补发"]
    assert f._apply_states(lines, set(), {}) == ["张三|格式|待补发"]


def test_tool_rejects_bad_args() -> None:
    spec = importlib.util.spec_from_file_location("pm_tool", Path("agents/feishu/tools/feishu_pm_batch_send.py"))
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    async def run() -> None:
        for bad in ("not-json", "[]", '["x"]'):
            out = json.loads(await tool.feishu_pm_batch_send(bad))
            assert out["ok"] is False

    asyncio.run(run())

"""Generate a ``<group>-mcp`` skill from a committed MCP schema cache.

The argument documentation for a dispatched MCP group has to live somewhere the
model can read on demand. It must be **generated, not written**: these schemas
are authored upstream and do change (a recorded session calls
``browser_screenshot_visible``, which today is ``browser_take_screenshot``), so
a hand-maintained copy drifts silently and the model is told to pass arguments
that no longer exist.

Usage (from the workspace root)::

    python tools/_gen_mcp_skill.py canvas browser serper

Writes ``skills/<group>-mcp/SKILL.md``. Re-run after refreshing a cache.
"""

from __future__ import annotations

# RUF001: this module emits Chinese skill documentation, so full-width commas
# and parentheses in its string literals are the intended output, not typos.
# T201: this is a command-line generator — printing what it wrote is its output.
# ruff: noqa: RUF001, T201
import json
import sys
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
SKILLS = TOOLS.parent / "skills"

# One line per group: what the group is for, and when to reach for it. The rest
# of the skill is mechanical, but this sentence is judgement and stays by hand.
BLURB = {
    "canvas": (
        "在共享的 Excalidraw 画布上绘图与查看 —— 架构图、流程图、思维导图、线框图。需要空间布局而非文字或代码时用它。"
    ),
    "browser": (
        "用真实浏览器做交互 —— 点击、填表、滚动、读控制台/网络、处理弹窗、截图。只是取一个页面的文字用 fetch 更快。"
    ),
    "serper": "Google 系垂直搜索 —— 图片、地图、学术、专利、新闻、购物、评论等。",
}

# Which tools remain their own tool, so the skill can say so rather than
# sending the model through the dispatcher for something it can call directly.
KEPT = {
    "browser": (
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_tabs",
        "browser_take_screenshot",
        "browser_type",
    ),
    "serper": ("serper_google_search",),
    "canvas": (),
}


def type_of(ps: dict[str, Any]) -> str:
    t = ps.get("type")
    if isinstance(t, list):
        t = "/".join(x for x in t if x != "null") or "string"
    if not isinstance(t, str):
        t = "object" if "properties" in ps else "string"
    if enum := ps.get("enum"):
        return f"{t} ({' | '.join(map(str, enum))})"
    if t == "array":
        items = ps.get("items")
        if isinstance(items, dict):
            return f"array[{type_of(items)}]"
    return t


def render(group: str, prefix: str | None, schemas: dict[str, dict[str, Any]]) -> str:
    pfx = f"{group}_" if prefix is None else prefix
    kept = KEPT.get(group, ())
    names = sorted(schemas)

    desc = (
        f"{BLURB.get(group, group)} 经 `{group}_call(tool, args_json)` 调用，"
        f"本文档载有全部 {len(schemas)} 个工具的参数表。"
        f"Use when the task needs {group} capabilities beyond"
        + (f" the always-loaded {', '.join(f'`{k}`' for k in kept)}." if kept else " prose or code.")
    )
    out = [
        "---",
        f"name: {group}-mcp",
        f'description: "{desc}"',
        "category: integration",
        "generated_by: tools/_gen_mcp_skill.py",
        "---",
        "",
        f"# {group} 工具参数表",
        "",
        "这些工具由上游 MCP 服务器提供，schema 由它定义。**本文件是生成的** —— 改了缓存请重跑",
        f"`python tools/_gen_mcp_skill.py {group}`，不要手改（上游改过名，手维护必然漂移）。",
        "",
        "回复用中文，除非用户明显在用其他语言。",
        "",
        "## 怎么调",
        "",
        f'```\n{group}_call(tool="<下表的工具名>", args_json=\'{{"参数": 值}}\')\n```',
        "",
        "`args_json` 是 JSON **对象**字符串；不吃参数的工具可省略或传 `{}`。",
        # Playwright's own names already carry ``browser_``, so its prefix is the
        # empty string and there is no second spelling to mention.
        (
            f"工具名写全名（带 `{pfx}` 前缀）或裸名都认。名字写错会被本地拒绝并列出可用名，不会真发请求。"
            if pfx
            else "名字写错会被本地拒绝并列出可用名，不会真发请求。"
        ),
        "",
    ]
    if kept:
        out += [
            "## 这几个是独立工具，直接调，别走 " + f"`{group}_call`",
            "",
            *[f"- `{k}`" for k in kept],
            "",
        ]
    out += ["## 工具参数表", ""]

    for n in names:
        sch = schemas[n]
        full = pfx + n if not n.startswith(pfx) else n
        inp = sch.get("inputSchema") or {}
        props: dict[str, Any] = inp.get("properties") or {}
        req = set(inp.get("required") or [])
        out.append(f"### `{full}`" + ("  ← 独立工具" if full in kept else ""))
        out.append("")
        if d := (sch.get("description") or "").strip():
            out.append(d)
            out.append("")
        if not props:
            out += ["无参数。", ""]
            continue
        out += ["| 参数 | 类型 | 必填 | 说明 |", "|---|---|---|---|"]
        for pn, ps in props.items():
            if not isinstance(ps, dict):
                ps = {}
            note = " ".join((ps.get("description") or "").split()).replace("|", "\\|")
            out.append(f"| `{pn}` | {type_of(ps)} | {'是' if pn in req else ''} | {note} |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    groups = sys.argv[1:] or ["canvas", "browser", "serper"]
    for group in groups:
        cache = TOOLS / ".mcp_cache" / f"{group}.json"
        if not cache.is_file():
            print(f"! no cache for {group!r} at {cache} — skipped")
            continue
        data = json.loads(cache.read_text(encoding="utf-8"))
        schemas = data.get("schemas") or {}
        text = render(group, data.get("prefix"), schemas)
        d = SKILLS / f"{group}-mcp"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(text, encoding="utf-8")
        print(f"wrote {d / 'SKILL.md'} — {len(schemas)} tools, {len(text)} chars")


if __name__ == "__main__":
    main()

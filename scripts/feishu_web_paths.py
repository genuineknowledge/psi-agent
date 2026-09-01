#!/usr/bin/env python3
"""feishu-web 前端实际会打的后端路径 —— 提取、核对、探测。

## 为什么有这个脚本

云上拓扑是 Caddy → `oauth-proxy.py`(**白名单**反代) → gateway 容器。白名单少一条,
那条路径就静默 404, 而前端只显示一个笼统的「加载失败」。本地直连 gateway 时这些路径
全通, 所以**本地测得再全也碰不到这类失败** —— 差异面本身没有任何东西守着。

人手抄一份清单挡不住: 前端加一个端点没人会想起来更新它, 而漂移的表现恰好就是云上 404。
所以清单从**源码提取**, 并由 `tests/psi_agent/gateway/test_feishu_web_api_paths.py`
双向钉住(源码多一条 → 红; 清单少一条 → 红)。

## 三个用法

    python scripts/feishu_web_paths.py --check          # 清单与源码是否一致
    python scripts/feishu_web_paths.py --regenerate      # 源码变了, 重新生成清单
    python scripts/feishu_web_paths.py --probe http://127.0.0.1:8765   # 逐条打, 找 404
    python scripts/feishu_web_paths.py --print-shell     # 生成部署机上跑的 curl 核对脚本

## 刻意的边界

**本脚本不改白名单、不碰云上任何东西。** 公网暴露面必须先由负责人拍方案(`/sessions/{id}/chat`
能驱动 agent 执行工具), 这里只产出核对工具与清单, 不做暴露决策。

清单是**描述现状**的, 不是规范现状的 —— 前端调用路径该长什么样不由它说话。

只依赖标准库, 且避开 3.10+ 的运行时语法(`from __future__ import annotations` 让注解全
变字符串), 这样部署机上任意 3.7+ 解释器都能跑, 不必与仓库的 `requires-python` 对齐。
"""

# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FEISHU_WEB = _REPO_ROOT / "src" / "psi_agent" / "gateway" / "feishu" / "feishu-web"

#: 清单落在前端目录里 —— 它描述的是前端的行为, 跟着前端源码一起被 review。
MANIFEST = _FEISHU_WEB / "api-paths.json"

#: 发 HTTP 的两个文件。**不是**「所有 ts 文件」: 见 `http_call_sites()`, 那条守着
#: 「有没有第三个文件开始发请求」, 比在这里放宽范围更抓得住漂移。
SOURCE_FILES = ("src/api.ts", "src/services/chatStream.ts")

#: 探测参数化路径时替换 `{param}` 的哨兵值。取一个真实 session 绝不会用的形状 ——
#: 命中真实会话会改动状态(`DELETE /sessions/{id}`), 那不是核对该干的事。
PROBE_SENTINEL = "__psi_path_probe__"

#: 发 HTTP 的构造。`http_call_sites()` 拿它扫全部前端源码, 出现在 `SOURCE_FILES`
#: 之外就说明清单的取材范围已经不够了。
_HTTP_CONSTRUCTS = ("fetch", "EventSource", "XMLHttpRequest", "sendBeacon", "axios")

_CALLEE_RE = re.compile(r"\b(fetch|requestJson)\b")


@dataclass(frozen=True)
class PathEntry:
    """一条前端会打的路径。

    `path` 里的路径参数一律写成 `{param}` —— 前端那侧是 `${encodeURIComponent(id)}`,
    没有名字可取。与 aiohttp 的 canonical(`/sessions/{session_id}`)比对时两边都归一
    成 `{}`, 见测试里的 `_normalize`。
    """

    method: str
    path: str
    source: str

    def probe_path(self) -> str:
        return re.sub(r"\{[^}]*\}", PROBE_SENTINEL, self.path)

    def as_json(self) -> dict[str, str]:
        return {"method": self.method, "path": self.path, "source": self.source}

    @classmethod
    def from_json(cls, row: dict[str, str]) -> PathEntry:
        return cls(method=row["method"], path=row["path"], source=row["source"])


# ---- 源码提取 -----------------------------------------------------------


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _skip_generic(text: str, i: int) -> int:
    """跳过 `requestJson<...>` 的类型参数, 返回 `>` 之后的位置; 没有类型参数则原样返回。

    **必须数尖括号深度**, 不能用 `<[^>]*>`: `requestJson<Record<string, string>>` 的
    类型参数自己带一层 `>`, 非贪婪写法会停在里层那个 `>` 上, 于是 `/feishu/titles` 与
    `/feishu/summaries` 两条被静默漏掉 —— 漏掉的表现是「清单看起来是从代码来的、但就是
    少两条」, 恰好最难发现。实测踩过, 由 `test_extractor_handles_nested_generics` 钉住。
    """
    if i >= len(text) or text[i] != "<":
        return i
    depth = 0
    while i < len(text):
        if text[i] == "<":
            depth += 1
        elif text[i] == ">":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _scan_string(text: str, i: int) -> tuple[str, int]:
    """从引号处读一个字符串/模板字面量, 返回(归一后的内容, 结束位置)。

    模板里的 `${...}` 归一成 `{param}` —— 嵌套的 `}`(如 `${f({a:1})}`)要靠数括号,
    否则会在第一个 `}` 处提前收尾。
    """
    quote = text[i]
    i += 1
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            out.append(text[i : i + 2])
            i += 2
            continue
        if ch == quote:
            return "".join(out), i + 1
        if quote == "`" and text.startswith("${", i):
            depth = 0
            i += 1  # 停在 `{` 上
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            out.append("{param}")
            continue
        out.append(ch)
        i += 1
    return "".join(out), i


def _scan_args(text: str, i: int) -> tuple[str, int]:
    """从 `(` 处读完整实参区, 返回(区内文本, `)` 之后的位置)。跳过字符串内的括号。"""
    assert text[i] == "("
    depth = 0
    start = i
    while i < len(text):
        ch = text[i]
        if ch in "\"'`":
            _, i = _scan_string(text, i)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return text[start + 1 :], i


def _method_of(args: str) -> str:
    """从实参区判 HTTP 方法。`jsonPost(...)` 是 `api.ts` 里的 POST 快捷式。"""
    explicit = re.search(r"method\s*:\s*[\"'](\w+)[\"']", args)
    if explicit:
        return explicit.group(1).upper()
    if "jsonPost(" in args:
        return "POST"
    return "GET"


@dataclass(frozen=True)
class CallSite:
    """一处 HTTP 调用点。`entry` 为 None 表示首参不是字面量(如 `requestJson` 自己的
    `fetch(url, init)`) —— 那不是一条路径, 但也不该被当成「没找到」而悄悄跳过。"""

    file: str
    line: int
    callee: str
    entry: PathEntry | None


def _call_sites_in(text: str, source: str) -> Iterator[CallSite]:
    for match in _CALLEE_RE.finditer(text):
        i = _skip_ws(text, match.end())
        i = _skip_generic(text, i)
        i = _skip_ws(text, i)
        if i >= len(text) or text[i] != "(":
            continue  # 不是调用(如 import 里的标识符)
        args, _ = _scan_args(text, i)
        line = text.count("\n", 0, match.start()) + 1
        arg_start = _skip_ws(args, 0)
        if arg_start >= len(args) or args[arg_start] not in "\"'`":
            yield CallSite(source, line, match.group(1), None)
            continue
        raw, after = _scan_string(args, arg_start)
        if not raw.startswith("/"):
            yield CallSite(source, line, match.group(1), None)
            continue
        path = raw.split("?", 1)[0]  # 查询串不是路由的一部分
        entry = PathEntry(method=_method_of(args[after:]), path=path, source=source)
        yield CallSite(source, line, match.group(1), entry)


def call_sites(root: Path | None = None) -> list[CallSite]:
    base = root or _FEISHU_WEB
    found = []
    for rel in SOURCE_FILES:
        path = base / rel
        if not path.is_file():
            raise SystemExit(f"找不到前端源码 {path} —— 清单的取材范围已失效")
        found.extend(_call_sites_in(path.read_text(encoding="utf-8"), rel))
    return found


def extract_paths(root: Path | None = None) -> list[PathEntry]:
    """从源码提取路径清单, 按 (path, method) 排序去重。"""
    entries = {site.entry for site in call_sites(root) if site.entry is not None}
    return sorted(entries, key=lambda e: (e.path, e.method))


def http_call_sites(root: Path | None = None) -> list[tuple[str, int, str]]:
    """扫**全部**前端源码里发 HTTP 的构造, 返回 (文件, 行号, 构造名)。

    `extract_paths` 只读 `SOURCE_FILES` 两个文件。有人在第三个文件里直接 `fetch(`,
    或换用 `EventSource` / `axios`, 提取器不会报错 —— 它只是少提一条, 于是清单齐全、
    测试全绿、云上照旧 404。这个函数是那一层的判据取材。
    """
    base = root or _FEISHU_WEB
    src = base / "src"
    pattern = re.compile(r"\b(" + "|".join(_HTTP_CONSTRUCTS) + r")\b")
    hits = []
    for path in sorted(src.rglob("*")):
        if path.suffix not in (".ts", ".tsx") or not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//", 1)[0]
            if code.lstrip().startswith("*"):
                continue  # 块注释正文
            for match in pattern.finditer(code):
                hits.append((rel, lineno, match.group(1)))
    return hits


# ---- 清单读写 -----------------------------------------------------------

_MANIFEST_HEADER = (
    "本文件由 scripts/feishu_web_paths.py --regenerate 生成, 不要手改。"
    "它是 feishu-web 前端实际会打的后端路径, 供两处消费: "
    "(1) 本地判据 tests/psi_agent/gateway/test_feishu_web_api_paths.py; "
    "(2) 云上 oauth-proxy 白名单核对(--print-shell 生成 curl 脚本)。"
)


def load_manifest(path: Path | None = None) -> list[PathEntry]:
    target = path or MANIFEST
    if not target.is_file():
        raise SystemExit(f"清单不存在: {target}\n跑 --regenerate 生成它。")
    data = json.loads(target.read_text(encoding="utf-8"))
    return [PathEntry.from_json(row) for row in data["paths"]]


def dump_manifest(entries: Sequence[PathEntry], path: Path | None = None) -> None:
    target = path or MANIFEST
    body = {
        "_comment": _MANIFEST_HEADER,
        "generated_by": "scripts/feishu_web_paths.py --regenerate",
        "paths": [e.as_json() for e in entries],
    }
    target.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def diff_manifest(root: Path | None = None, path: Path | None = None) -> tuple[list[PathEntry], list[PathEntry]]:
    """返回 (源码有而清单没有, 清单有而源码没有)。两个方向都要, 见模块头。"""
    from_source = set(extract_paths(root))
    from_manifest = set(load_manifest(path))
    missing = sorted(from_source - from_manifest, key=lambda e: (e.path, e.method))
    stale = sorted(from_manifest - from_source, key=lambda e: (e.path, e.method))
    return missing, stale


# ---- 探测 ---------------------------------------------------------------

#: 路由不存在时 aiohttp 回的是 `text/plain` 的 `404: Not Found`; 而 handler 自己判出的
#: 404(会话不存在)走 `_error()`, 是 `application/json`。判据是**路由存在性**, 所以两者
#: 必须分开 —— 否则拿哨兵 id 打 `/sessions/{id}/todos` 会假红。
_ROUTER_404_BODY = "404: Not Found"


def classify(status: int, content_type: str, body: str) -> str:
    """`missing`(路由不存在, 是 bug) / `present`(路由在, 状态码随身份与参数而定)。"""
    if status != 404:
        return "present"
    if content_type.startswith("application/json"):
        return "present"
    if body.strip().startswith(_ROUTER_404_BODY):
        return "missing"
    return "missing"


def probe(base_url: str, entries: Iterable[PathEntry], timeout: float = 10.0) -> list[dict[str, object]]:
    """逐条打一遍, 返回每条的状态码与判定。只读标准库, 不引 aiohttp。"""
    base = base_url.rstrip("/")
    results: list[dict[str, object]] = []
    for entry in entries:
        url = base + entry.probe_path()
        req = urllib.request.Request(url, method=entry.method)
        if entry.method in ("POST", "PUT", "PATCH"):
            req.add_header("Content-Type", "application/json")
            req.data = b"{}"
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                ctype = resp.headers.get_content_type()
                body = resp.read(512).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            # 4xx/5xx 走这支 —— 对本脚本它们**不是错误**: 401/400/405 都说明路由在。
            status = exc.code
            ctype = exc.headers.get_content_type() if exc.headers else ""
            body = exc.read(512).decode("utf-8", "replace")
        except OSError as exc:
            # 连不上(服务没起、端口写错)与「路由不存在」是两件事, 单独报, 否则会被
            # 当成 19 条全 missing, 读起来像白名单全丢。
            results.append(
                {
                    "method": entry.method,
                    "path": entry.path,
                    "status": 0,
                    "verdict": "unreachable",
                    "detail": str(exc),
                }
            )
            continue
        results.append(
            {
                "method": entry.method,
                "path": entry.path,
                "status": status,
                "verdict": classify(status, ctype or "", body),
                "detail": body.strip()[:80],
            }
        )
    return results


# ---- 部署机上的核对脚本 -------------------------------------------------


def shell_script(entries: Sequence[PathEntry]) -> str:
    """生成部署机上直接跑的 curl 核对脚本 —— 路径**内联**, 不需要 jq 或 python。

    判据与部署卡一致: 放行路径 200/4xx 都算通过(401 是未登录的正常回应), **404 即失败** ——
    但 404 要分两种, 见下面 `content_type` 那段。这个脚本只**读**, 不改白名单 ——
    该不该放行是负责人的决定。
    """
    lines = [
        "#!/usr/bin/env bash",
        "# 由 scripts/feishu_web_paths.py --print-shell 生成, 不要手改。",
        "# 用法: bash check-feishu-web-paths.sh [BASE_URL]",
        "#   BASE_URL 默认 http://127.0.0.1:8090 —— 云上 oauth-proxy 的监听地址,",
        "#   也就是 Caddy 反代过去的那一跳。想直接量 gateway 容器就把它指到容器端口。",
        "# 判据是**路由存在性**, 不是状态码为 200:",
        "#   * 401/400/405/500 → 路由到得了(未登录、缺参、方法不符) → 通过",
        "#   * 404 且 text/plain → 路由到不了(白名单少一条, 或本就没这条路由) → 失败",
        "#   * 404 且 application/json → handler 自己判出的「会话不存在」(哨兵 id 本就不存在)",
        "#     → 通过。**不能只看状态码**: 那样 /sessions/<id>/* 一族会全报假 FAIL, 实测过。",
        "# 本脚本只读不改: 该不该放行由负责人拍, 不在这里改 ALLOWED_PATHS。",
        "set -u",
        'BASE="${1:-http://127.0.0.1:8090}"',
        "fail=0",
        "check() {",
        '  out=$(curl -s -o /dev/null -w "%{http_code} %{content_type}" -X "$1" \\',
        '    -H "Content-Type: application/json" "$BASE$2")',
        "  code=${out%% *}",
        "  ctype=${out#* }",
        '  if [ "$code" = "404" ] && [ "${ctype#application/json}" = "$ctype" ]; then',
        '    printf "FAIL  %-6s %-52s 404 (%s)\\n" "$1" "$2" "$ctype"',
        "    fail=$((fail+1))",
        "  else",
        '    printf "ok    %-6s %-52s %s\\n" "$1" "$2" "$code"',
        "  fi",
        "}",
        "",
    ]
    for entry in entries:
        lines.append(f'check {entry.method} "{entry.probe_path()}"')
    lines.extend(
        [
            "",
            'echo "---"',
            'if [ "$fail" -ne 0 ]; then',
            '  echo "$fail 条路径 404 —— 前端会拿到笼统的加载失败。"',
            '  echo "对照 oauth-proxy.py 的 ALLOWED_PATHS 逐条比对上面 FAIL 的行。"',
            "  exit 1",
            "fi",
            'echo "全部 ' + str(len(entries)) + ' 条路径可达。"',
        ]
    )
    return "\n".join(lines) + "\n"


# ---- CLI ---------------------------------------------------------------


def _force_utf8_stdout() -> None:
    """把 stdout 切成 UTF-8。**本脚本所有输出都是中文, 不切会直接崩。**

    Windows 控制台默认 cp1252 编不出中文, `--check` 于是在**清单其实一致**时也抛
    `UnicodeEncodeError`、以退出码 1 失败 —— 把「同步守卫」变成「Windows 上必红」。
    本仓开发机就是 Windows, 所以修在脚本里而不是靠调用方设 `PYTHONIOENCODING`。
    与 `scripts/gen_legal_html.py` 同款做法(那边踩过 CI)。

    `reconfigure` 在 stdout 被替换成非 `TextIOWrapper` 时可能不存在(某些捕获实现),
    所以先探再调。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="核对清单与源码是否一致")
    group.add_argument("--regenerate", action="store_true", help="按源码重新生成清单")
    group.add_argument("--list", action="store_true", help="打印清单")
    group.add_argument("--probe", metavar="BASE_URL", help="逐条打一遍并报状态码")
    group.add_argument("--print-shell", action="store_true", help="生成部署机上跑的 curl 核对脚本")
    args = parser.parse_args(argv)

    if args.regenerate:
        entries = extract_paths()
        dump_manifest(entries)
        print(f"已写入 {MANIFEST} ({len(entries)} 条)")
        return 0

    if args.check:
        missing, stale = diff_manifest()
        for entry in missing:
            print(f"源码有、清单没有: {entry.method} {entry.path} ({entry.source})")
        for entry in stale:
            print(f"清单有、源码没有: {entry.method} {entry.path}")
        if missing or stale:
            print("跑 scripts/feishu_web_paths.py --regenerate 同步。")
            return 1
        print(f"清单与源码一致 ({len(load_manifest())} 条)。")
        return 0

    if args.list:
        for entry in load_manifest():
            print(f"{entry.method:6} {entry.path}")
        return 0

    if args.print_shell:
        sys.stdout.write(shell_script(load_manifest()))
        return 0

    results = probe(args.probe, load_manifest())
    bad = [r for r in results if r["verdict"] != "present"]
    for row in results:
        flag = "ok   " if row["verdict"] == "present" else "FAIL "
        print(f"{flag} {row['method']:6} {row['path']:48} {row['status']} {row['verdict']}")
    print("---")
    if bad:
        print(f"{len(bad)} 条路由到不了。")
        return 1
    print(f"全部 {len(results)} 条路由可达。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

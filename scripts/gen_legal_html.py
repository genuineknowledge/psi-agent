"""把法务提供的两份协议 md 生成为 HTML, 供安装器与产品内共用同一份产物。

两份源文件没有任何 Markdown 结构(0 个 `#`, 0 个 `|`), 标题是「一、定义」这样的中文序号裸行,
表格是 Tab 分隔的裸行, 因此不能用通用 md 渲染器 -- 解析规则见 `_render_body`。
唯一保留的 Markdown 语法是 `**加粗**`: 加粗属法律判断, 写在 md 源里(diff 可审), 本脚本只做透传。

用法:
    python scripts/gen_legal_html.py            # 生成
    python scripts/gen_legal_html.py --check    # 校验库内产物是否与 md 源一致(CI 用)

设计文档: docs/superpowers/specs/2026-08-15-installer-tos-consent-design.md
"""
# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。

import argparse
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPA_V2 = REPO_ROOT / "src" / "psi_agent" / "gateway" / "desktop" / "spa-v2"
# ** 源不放 docs/ **: 这两份 md 不是给开发者读的文档, 是发给用户的协议正文 ——
# 改它等于改产品内容。放在产物目录隔壁, 源与产物的关系一眼可见;
# 而 docs/ 下只放 superpowers 的 spec 与 plan。
SRC_DIR = _SPA_V2 / "legal"
OUT_DIR = _SPA_V2 / "public"

# 生成物同时被两处消费, 改路径要同步:
#   - vite 把 public/* 拷进 dist/, PyInstaller 再打包 dist → 产品内登录面板的协议链接
#   - .github/inno-setup/haitun.iss 以 dontcopy 引同一路径 → 安装期协议页
_H1_LINE = 0
_H2_RE = re.compile(r"^[一二三四五六七八九十]+、")
_H3_RE = re.compile(r"^\d+\.\d+ ")
_META_RE = re.compile(r"^(更新日期|生效日期)：")  # noqa: RUF001  全角冒号是源文件里的字面量, 不能换成半角
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_UNNUMBERED_H2 = ("导言",)
# 「3.1 我们可能通过以下几种方式收集用户个人信息：」是正文引出语而非小标题, 靠尾字符区分。  # noqa: RUF003
_NOT_HEADING_TAIL = ("：", "。", "，", "、", "）")  # noqa: RUF001  同上, 这些是要匹配的正文字面量
_H3_MAX_LEN = 30
# 目录块判定阈值: 连续 N 行以上「一、」式标题且中间无正文夹杂, 判为目录。
# 隐私政策 :12-23 是与正文逐字重复的 12 行目录, 照 h2 规则处理会产出重复标题并撞 id;
# 许可协议无此块(:12 起即正文, 每个标题后紧跟正文), 同一规则对它是空操作。
_TOC_MIN_RUN = 3


@dataclass(frozen=True)
class LegalDoc:
    """一份协议: md 源 → HTML 产物。"""

    src: Path
    out: Path
    browser_title: str


DOCS_TO_BUILD: tuple[LegalDoc, ...] = (
    LegalDoc(SRC_DIR / "Haitun_软件许可及服务协议_1.0.md", OUT_DIR / "terms.html", "软件许可及服务协议"),
    LegalDoc(SRC_DIR / "Haitun_隐私保护政策_1.0.md", OUT_DIR / "privacy.html", "隐私保护政策"),
)

_PUBLISHER = "合肥真知人工智能应用软件有限公司"


def _inline(text: str) -> str:
    """转义 HTML 后透传 `**加粗**`。先转义再替换, 否则 escape 会把生成的标签一起转掉。"""
    return _BOLD_RE.sub(r"<strong>\1</strong>", html.escape(text))


def _is_heading(line: str) -> bool:
    return bool(_H2_RE.match(line)) or _is_h3(line) or line in _UNNUMBERED_H2


def _is_h3(line: str) -> bool:
    return bool(_H3_RE.match(line)) and len(line) <= _H3_MAX_LEN and not line.endswith(_NOT_HEADING_TAIL)


def _find_toc_range(lines: list[str]) -> tuple[int, int] | None:
    """找出目录块的行区间 [start, end)。没有则返回 None。

    **重复标题即目录结束**。隐私政策的目录末项是「十二、联系我们」, 紧接着正文第一个标题
    「一、定义」——它同样匹配 `_H2_RE`, 只数「连续」会把它一起吞进目录, 正文就少一个标题。
    目录列的是互不相同的标题, 因此某标题第二次出现就是正文开始。
    """
    run_start: int | None = None
    seen: set[str] = set()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if _H2_RE.match(line) and line not in seen:
            if run_start is None:
                run_start = i
                seen = set()
            seen.add(line)
            continue
        if run_start is not None and i - run_start >= _TOC_MIN_RUN:
            return (run_start, i)
        run_start = None
        seen = set()
    if run_start is not None and len(lines) - run_start >= _TOC_MIN_RUN:
        return (run_start, len(lines))
    return None


def _heading_ids(lines: list[str], toc: tuple[int, int] | None) -> dict[str, str]:
    """预扫一遍正文标题 → 锚点 id, 让目录能链到正文(目录本身不参与编号)。"""
    ids: dict[str, str] = {}
    section = 0
    for i, raw in enumerate(lines):
        line = raw.strip()
        if i == _H1_LINE or (toc and toc[0] <= i < toc[1]) or "\t" in line:
            continue
        if _H2_RE.match(line) or line in _UNNUMBERED_H2:
            section += 1
            ids.setdefault(line, f"sec-{section}")
    return ids


def _render_table(rows: list[str]) -> list[str]:
    """Tab 分隔行块 → table, 首行为表头。"""
    out = ["<table>", "<thead><tr>"]
    out += [f"<th>{_inline(c)}</th>" for c in rows[0].split("\t")]
    out += ["</tr></thead>", "<tbody>"]
    for row in rows[1:]:
        cells = "".join(f"<td>{_inline(c)}</td>" for c in row.split("\t"))
        out.append(f"<tr>{cells}</tr>")
    out += ["</tbody>", "</table>"]
    return out


def _render_body(lines: list[str]) -> tuple[str, list[str]]:
    """按解析规则渲染正文, 返回 (HTML, 元信息行)。"""
    toc = _find_toc_range(lines)
    ids = _heading_ids(lines, toc)
    body: list[str] = []
    meta: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or i == _H1_LINE:  # h1 由调用方从首行单独渲染
            i += 1
            continue
        if _META_RE.match(line):
            meta.append(line)
            i += 1
            continue
        if toc and toc[0] <= i < toc[1]:
            if i == toc[0]:
                body.append('<nav class="toc"><ul>')
            anchor = ids.get(line)
            item = f'<a href="#{anchor}">{_inline(line)}</a>' if anchor else _inline(line)
            body.append(f"<li>{item}</li>")
            if i == toc[1] - 1:
                body.append("</ul></nav>")
            i += 1
            continue
        if "\t" in line:
            block = []
            while i < len(lines) and "\t" in lines[i]:
                block.append(lines[i].strip())
                i += 1
            body += _render_table(block)
            continue
        if _H2_RE.match(line) or line in _UNNUMBERED_H2:
            body.append(f'<h2 id="{ids[line]}">{_inline(line)}</h2>')
        elif _is_h3(line):
            body.append(f"<h3>{_inline(line)}</h3>")
        else:
            body.append(f"<p>{_inline(line)}</p>")
        i += 1
    return "\n".join(body), meta


def render(doc: LegalDoc) -> str:
    """生成一份协议的完整 HTML。"""
    lines = doc.src.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"{doc.src} 是空文件")
    title = lines[_H1_LINE].strip()
    body, meta = _render_body(lines)
    meta_html = f'<p class="meta">{_inline(" · ".join(meta))}</p>' if meta else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(doc.browser_title)} · HaiTun Agent</title>
<link rel="stylesheet" href="legal.css" />
<!-- 本文件由 scripts/gen_legal_html.py 从 docs/ 下的 md 源生成, 请勿手改。 -->
</head>
<body>
<main>
  <h1>{_inline(title)}</h1>
  {meta_html}
{body}
</main>
<footer>{html.escape(_PUBLISHER)}</footer>
</body>
</html>
"""


def _normalize(text: str) -> str:
    """统一换行, 让 --check 不受检出时的 CRLF 转换影响。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _force_utf8_stdout() -> None:
    """把 stdout 切成 UTF-8。**本脚本所有输出都是中文, 不切会直接崩。**

    Windows 控制台默认 cp1252, 编不出中文 —— GitHub 的 windows-latest 上
    ``print("产物与 md 源一致。")`` 抛 ``UnicodeEncodeError``, 于是 CI 的
    ``--check`` 步在**产物其实是一致的**时候仍然以退出码 1 失败。这比漏检更坏:
    它把「同步守卫」变成了「Windows 上必红」。

    修在脚本里而不是给 workflow 加 ``PYTHONIOENCODING`` —— 本仓开发机就是
    Windows, 人在 cmd.exe 里跑会撞同一个坑, 只修 CI 等于把坑留给人。

    ``reconfigure`` 在 stdout 被替换成非 ``TextIOWrapper`` 时可能不存在
    (某些捕获实现), 所以先探再调; 探不到就维持原样, 不为了日志把主流程搞挂。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验不写入: 库内产物与 md 源不一致时返回 1 (改了 md 忘了重生成会在 CI 里失败)",
    )
    args = parser.parse_args(argv)

    stale: list[Path] = []
    for doc in DOCS_TO_BUILD:
        generated = render(doc)
        if args.check:
            # 比对前统一换行。仓库 core.autocrlf=true 且无 .gitattributes, Windows 检出会把
            # 库内的 LF 变成 CRLF —— 直接比字节会让 CI(windows-latest)在干净检出上就判过期。
            current = _normalize(doc.out.read_text(encoding="utf-8")) if doc.out.exists() else ""
            if current != _normalize(generated):
                stale.append(doc.out)
                print(f"过期: {doc.out.relative_to(REPO_ROOT)}")
            continue
        # newline="\n": 仓库里两份产物在 Windows 上生成也要保持 LF, 否则每次换机器都产生全文件 diff。
        doc.out.write_text(generated, encoding="utf-8", newline="\n")
        print(f"已生成 {doc.out.relative_to(REPO_ROOT)} ({len(generated):,} 字节)")

    if args.check:
        if stale:
            print(f"\n{len(stale)} 个产物与 md 源不一致, 请运行: python scripts/gen_legal_html.py")
            return 1
        print("产物与 md 源一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

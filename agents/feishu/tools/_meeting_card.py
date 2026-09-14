"""Meeting summary card: deterministic card payload + idempotent card delivery.

会议投递的卡片形态(格式形状归 ``skills/card-dsl/templates/meeting-summary-card.xml``,
本模块只做确定性组装):
  - 把 pipeline 的三段分析文本(meeting_summary / analysis_text /
    positive_negative_overview)解析、裁剪并渲染成模板 values;
  - ``render_meeting_summary_card`` 经 ``_card_dsl.render_template`` 走与
    review/todo 卡相同的模板渲染链路,返回 card 2.0 JSON;
  - ``notify_meeting_card`` 是文本 ``meeting_session_notify`` 的卡片孪生:
    同一收据文件与幂等 key (record_file_id + identity),同一 already_sent 语义,
    单发(不分块),群聊 oc_ 与私聊 ou_ 均由 send_card_impl 按前缀自动判型。
文本通知保持原样;本模块与文本通知写同一收据,同 record 只会有一种载荷成功发出。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any

import _card_dsl
import _feishu_impl as _f
from _meeting_automation import meeting_artifact_root
from meeting_session_notify import _read_receipts, _receipt_lock, _resolve_recipient, _write_receipts

from psi_agent._appdata import resolve_appdata_root

# 卡片**不再按固定字符数截断正文**(2026-09-12): 长段落进折叠面板(默认收起、可展开),
# 首屏照旧干净, 但一个字都不丢。真正的约束是下面那张卡片的**字节预算** ——
# 飞书卡片硬上限约 30KB(错误码 230025, 见 ``_feishu/message.py``), 超了整张卡会
# 发送失败, 所以超预算时按"最长的一段先降级"收敛(见 ``_fit_card_to_budget``)。
#
# 这两条仍是**单条/条数**层面的上限, 与卡片体积无关, 属于"读得动"的取舍:
_EVIDENCE_LIMIT = 160
_CANDIDATE_LIMIT = 12
#: 卡片 JSON 的字节预算: 30KB 硬上限留出余量给飞书侧包装与转义膨胀。
CARD_BYTE_BUDGET = 26_000
#: 收缩地板: 再砍下去这段话就没信息量了, 交给调用方/告警处理, 不无限砍。
_SECTION_FLOOR = 240
#: 可收缩的段落(按"谁长先动谁"排序, 与优先级无关)。
_SHRINKABLE_KEYS = ("key_points", "summary", "positives", "negatives")
_BUDGET_PASSES = 12
_ELLIPSIS = "\n\n…(内容较长, 已截断, 完整文本见会议存档)"

_CANDIDATE_KEYS = ("positive_candidates", "negative_candidates", "positives", "negatives")
#: 候选条目的正文键 —— 模型实际用过 ``candidate`` / ``event`` / ``text`` 三种写法,
#: 只认其中一种会把整栏候选静默丢光(实测 1100 日会: 15 条正面 + 14 条负面全被丢弃,
#: 卡片上只剩「另有 N 条候选」)。
_CANDIDATE_TEXT_KEYS = ("candidate", "event", "text", "title", "summary", "item")
#: 候选条目的分类/分层信息(有就附在要点括号里, 便于 mentor 判断性质)。
_CANDIDATE_META_KEYS = ("scope", "axis", "fact_layer", "evidence_kind", "kind")

# 「写在代码/规则里的声明」不该出现在给人看的卡片正文里 —— 模型会把系统侧口径复述进
# 输出(实测: 【用途与边界】本输出仅为候选观察, 不写入正式负面总表、不计分、不进入绩效)。
# 这里做确定性剥离: 不依赖模型自觉, 也保证历史 record 重渲染时同样干净。
_DECLARATION_SECTION_RE = re.compile(
    r"【[^】]{0,20}(?:用途|边界|声明|口径|免责|合规)[^】]{0,20}】[\s\S]*?"
    r"(?=【|\n\s*\n|\n\s*#{1,6}\s|$)"
)
_DECLARATION_HINTS = (
    "不写入正式",
    "不计分",
    "不进入绩效",
    "不排名",
    "不自动认定红线",
    "候选观察",
    "本输出仅",
    "仅为候选",
    "不作为证据",
    "active:false",
    "active: false",
)
# 代码生成的两类元信息也不该出现在卡片正文(卡片首行已经有会议与日期):
#   「合并口径: 本分析由三段分块分析合并去重…」整段, 与「【合并后分析|会议 ...】」横幅行。
_MERGE_DECLARATION_RE = re.compile(r"(?m)^\s*合并口径[:\uff1a][^\n]*(?:\n+|$)")
_META_BANNER_RE = re.compile(r"(?m)^\s*【合并后分析[^\n]*】\s*(?:\n+|$)")
# 一条要点的目标长度, 也是"要不要拆"的阈值: 超过它就按句号/逗号把短分句攒成多条要点。
# 数值刻意小 —— 60~140 字符的段落此前原样成行, 那正是"看起来还是一大段"的来源。
_BULLET_TARGET = 48
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。\uff1b!?\uff01\uff1f])")
# 全角标点一律写成 \uffXX 转义(与上面的句子切分同一处理): RUF001 会把字面全角标点
# 判为 ambiguous unicode, 而这里必须按全角切分中文。
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[\uff0c,\u3001:\uff1a;\uff1b])")
_LEADING_BULLET_RE = re.compile(r"^\s*(?:[-\*\u2022\u00b7]|\d+[.\u3001)\uff09]|[\uff08(]\d+[)\uff09])\s*")
_BRACKET_TITLE_RE = re.compile(r"【([^】]{1,24})】")
_MD_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s*(.+?)\s*$")
_CN_NUMBERED_HEADING_RE = re.compile(r"(?m)^\s*([一二三四五六七八九十]{1,3}、)\s*(.+?)\s*$")
# overview 未结构化时, 按【正面候选…】/【负面候选…】这类小标题分栏。
_POSITIVE_HEADING_RE = re.compile(r"【[^】]{0,12}(?:正面|正向|优点|亮点)[^】]{0,12}】")
_NEGATIVE_HEADING_RE = re.compile(r"【[^】]{0,12}(?:负面|负向|问题|风险)[^】]{0,12}】")


def _strip_declarations(text: str) -> str:
    """去掉口径/边界声明段落与行(系统侧约束不该复述给收件人)。"""
    cleaned = _DECLARATION_SECTION_RE.sub("", str(text or ""))
    cleaned = _MERGE_DECLARATION_RE.sub("", cleaned)
    cleaned = _META_BANNER_RE.sub("", cleaned)
    kept = [line for line in cleaned.splitlines() if not any(hint in line for hint in _DECLARATION_HINTS)]
    return "\n".join(kept).strip()


def _bullet_lines(line: str, *, strip_marker: bool) -> list[str]:
    """把一行整理成若干条 ``- `` 要点; 短行不拆, 长句按标点再切。"""
    body = _LEADING_BULLET_RE.sub("", line).strip() if strip_marker else line.strip()
    if not body:
        return []
    if len(body) <= _BULLET_TARGET:
        return [f"- {body}"]
    pieces: list[str] = []
    for sentence in (piece.strip() for piece in _SENTENCE_SPLIT_RE.split(body)):
        if not sentence:
            continue
        if len(sentence) <= _BULLET_TARGET:
            pieces.append(sentence)
            continue
        # 句子本身还太长: 按逗号/分号把短分句攒到目标长度, 别让整句挤成一行。
        current = ""
        for clause in (part for part in _CLAUSE_SPLIT_RE.split(sentence) if part):
            if current and len(current) + len(clause) > _BULLET_TARGET:
                pieces.append(current)
                current = clause
            else:
                current += clause
        if current.strip():
            pieces.append(current)
    return [f"- {piece.strip()}" for piece in pieces if piece.strip()]


def _structure(text: str) -> str:
    """把正文整理成**一句一行**的要点结构。

    卡片是要"扫"的, 所以: 标题独立成行、每行都是 ``- `` 要点、尽量短。此前只对超过
    140 字符的段落做切分, 模型输出的中等长度段落(60~140 字符)原样成行, 整屏看过去
    仍是一坨 —— 阈值下调到 60, 并把仍偏长的句子按逗号再切一刀。
    """
    body = str(text or "").strip()
    if not body:
        return ""
    body = _MD_HEADING_RE.sub(lambda match: f"\n\n**{match.group(1).strip()}**\n", body)
    body = _CN_NUMBERED_HEADING_RE.sub(lambda match: f"\n\n**{match.group(1)}{match.group(2)}**\n", body)
    body = _BRACKET_TITLE_RE.sub(lambda match: f"\n\n**{match.group(1).strip()}**\n", body)
    blocks: list[str] = []
    for raw_paragraph in body.split("\n"):
        paragraph = raw_paragraph.strip()
        if not paragraph:
            continue
        # 标题行/表格行/引用行原样保留(标题拆成要点就不像标题了)
        if paragraph.startswith(("**", "|", ">")):
            blocks.append(paragraph)
            continue
        blocks.extend(_bullet_lines(paragraph, strip_marker=paragraph.startswith(("-", "*", "•", "·"))))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(blocks)).strip()


def _truncate(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + _ELLIPSIS


def _parse_overview(text: str) -> dict[str, object] | None:
    """把 positive_negative_overview 解析成 dict。

    模型可能给 JSON 或 Python repr(dict); 两种都试, 都失败返回 None
    (调用方按"未结构化"处理, 不抛错)。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    for loader in (json.loads, ast.literal_eval):
        try:
            value = loader(raw)
        except ValueError, SyntaxError:
            continue
        return value if isinstance(value, dict) else None
    return None


def _candidate_lines(candidates: object) -> str:
    """把一栏候选渲染成要点: 一行一条, 证据缩进一级。

    正文键不写死一种: 模型实际用过 ``candidate`` / ``event`` / ``text`` 三种写法
    (实测 1100 日会 15 条正面 + 14 条负面因为只认 ``candidate`` 而**全被丢光**,
    卡片上只剩「另有 N 条候选」)。字符串条目也直接当正文, 不再静默跳过。
    """
    lines: list[str] = []
    items = candidates if isinstance(candidates, list) else []
    for item in items[:_CANDIDATE_LIMIT]:
        text = ""
        meta: list[str] = []
        evidence = ""
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            for key in _CANDIDATE_TEXT_KEYS:
                value = str(item.get(key) or "").strip()
                if value:
                    text = value
                    break
            item_id = str(item.get("id") or "").strip()
            if item_id:
                meta.append(item_id)
            for key in _CANDIDATE_META_KEYS:
                value = str(item.get(key) or "").strip()
                if value:
                    meta.append(_truncate(value, 32))
            evidence = str(item.get("evidence") or "").strip()
        if not text:
            continue
        # 候选正文本身可能是一整句长话: 与正文同样切成短要点, 别让一条候选占满一行。
        lines.extend(_bullet_lines(text, strip_marker=False))
        if meta:
            lines.append(f"  - 分类: {' · '.join(meta)}")
        for index, piece in enumerate(_bullet_lines(_truncate(evidence, _EVIDENCE_LIMIT), strip_marker=False)):
            # 证据常常是一整段原话: 同样切短, 别让一条证据占满一行(实测 150 字符)。
            fragment = piece[2:] if piece.startswith("- ") else piece
            lines.append(f"  - 证据: {fragment}" if index == 0 else f"    - {fragment}")
    if len(items) > _CANDIDATE_LIMIT:
        lines.append(f"\n…另有 {len(items) - _CANDIDATE_LIMIT} 条候选, 完整清单见会议存档")
    return "\n".join(lines)


def _unstructured_sections(text: str) -> tuple[str, str, str]:
    """不是 JSON 的 overview: 按【正面候选…】/【负面候选…】小标题分到两栏。

    实测有一场会(周中对齐会 09-09)的 overview 就是这种带小标题的散文 —— 以前整段被
    截断塞进 footer, 正文两栏空着, 收件人看到的是一整段谁也读不进去的话。
    条数按 ``_CANDIDATE_LIMIT`` 收口: 卡片只列前若干条, 其余指向会议存档
    (无上限时实测出现过 113 行的负面候选, 从"读不动"变成"翻不完")。
    """
    body = _strip_declarations(text)
    if not body:
        return "", "", ""
    marks = list(re.finditer(r"【([^】]{1,24})】", body))
    if not marks:
        return "", "", body
    note = _truncate(body[: marks[0].start()].strip(), 200)
    positives: list[tuple[str, str]] = []
    negatives: list[tuple[str, str]] = []
    neutral = 0
    for index, mark in enumerate(marks):
        title = mark.group(1).strip()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        chunk = body[mark.end() : end].strip()
        if not chunk:
            continue
        target = f"【{title}】"
        if _NEGATIVE_HEADING_RE.search(target):
            negatives.append((title, chunk))
        elif _POSITIVE_HEADING_RE.search(target):
            positives.append((title, chunk))
        else:
            neutral += 1

    def _render(entries: list[tuple[str, str]]) -> str:
        blocks = [f"**{title}**\n{_structure(chunk)}" for title, chunk in entries[:_CANDIDATE_LIMIT]]
        extra = len(entries) - len(blocks)
        if extra > 0:
            blocks.append(f"…另有 {extra} 条, 完整清单见会议存档")
        return "\n\n".join(blocks)

    if neutral:
        suffix = f"…另有 {neutral} 条中性/待补充证据条目, 完整清单见会议存档"
        note = f"{note}\n{suffix}" if note else suffix
    return _render(positives), _render(negatives), note


def _overview_sections(overview_text: str) -> tuple[str, str, str]:
    """从 overview 文本分出 (positive_md, negative_md, declaration_note)。

    declaration 等说明文字放进 note, 由调用方拼进 footer。
    """
    parsed = _parse_overview(overview_text)
    if parsed is None:
        return _unstructured_sections(overview_text)
    declaration = str(parsed.get("declaration") or parsed.get("disclaimer") or "").strip()
    note = _truncate(declaration, 240) if declaration else ""
    positives = ""
    negatives = ""
    for key in _CANDIDATE_KEYS:
        if key in ("positive_candidates", "positives"):
            if not positives and parsed.get(key) is not None:
                positives = _candidate_lines(parsed.get(key))
        else:
            if not negatives and parsed.get(key) is not None:
                negatives = _candidate_lines(parsed.get(key))
    return positives, negatives, note


def _card_bytes(card: dict[str, Any]) -> int:
    """卡片 JSON 的实际字节数(中文按 UTF-8 3 字节计 —— 飞书的 30KB 上限是字节数)。"""
    return len(json.dumps(card, ensure_ascii=False).encode("utf-8"))


def _render_values(values: dict[str, str]) -> dict[str, Any]:
    return _card_dsl.render_template("meeting-summary-card", values_json=json.dumps(values, ensure_ascii=False))


def _fit_card_to_budget(values: dict[str, str], rendered: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """把卡片收敛进字节预算: 只降级**最长的那一段**, 其余内容保持完整。

    截断说明会写进被砍的那一段, 所以砍最长的那段才对症 —— 砍短的既丢内容又解决不了
    体积。每轮至少砍掉超出预算的部分(中文约 3 字节/字), 并设轮数与地板, 不会死循环。
    """
    working = dict(values)
    for _ in range(_BUDGET_PASSES):
        if not rendered.get("ok"):
            return working, rendered
        over = _card_bytes(rendered["card"]) - CARD_BYTE_BUDGET
        if over <= 0:
            return working, rendered
        key = max(_SHRINKABLE_KEYS, key=lambda name: len(str(working.get(name) or "")))
        current = str(working.get(key) or "")
        if len(current) <= _SECTION_FLOOR:
            return working, rendered
        cut = max(over // 3 + 1, len(current) // 8)
        working[key] = _truncate(current, max(_SECTION_FLOOR, len(current) - cut))
        rendered = _render_values(working)
    return working, rendered


def render_meeting_summary_card(
    meeting_title: str,
    meeting_code: str,
    meeting_date: str,
    analysis: dict[str, str],
) -> dict[str, object]:
    """组装会议总结卡: 模板 + values → card 2.0 JSON。

    Returns ``{"ok": True, "card": {...}, "handlers": {...}, "values": {...}}``
    or ``{"ok": False, "error": ...}`` — the same shape the render tool uses.
    """
    summary = str(analysis.get("meeting_summary") or "").strip()
    key_points = str(analysis.get("analysis_text") or "").strip()
    overview_text = str(analysis.get("positive_negative_overview") or "").strip()
    positives, negatives, overview_note = _overview_sections(overview_text)
    footer = f"⚠️ 候选观察: 不进入正式正负面总表 · 不计分 · 不进入绩效\n会议号 {meeting_code}"
    if overview_note:
        footer = f"{overview_note}\n\n{footer}"
    values = {
        "meeting_line": f"{meeting_title} · {meeting_date}",
        # 不再按固定字符数截断: 摘要在首屏, 其余长段落进折叠面板(模板里是 <collapse>)。
        "summary": _structure(_strip_declarations(summary)),
        "key_points": _structure(_strip_declarations(key_points)),
        "positives": positives,
        "negatives": negatives,
        "footer": footer,
    }
    values, rendered = _fit_card_to_budget(values, _render_values(values))
    if not rendered.get("ok"):
        return {"ok": False, "error": str(rendered.get("error") or "card render failed")}
    return {"ok": True, "card": rendered["card"], "handlers": rendered.get("handlers", {}), "values": values}


async def notify_meeting_card(
    meeting_name: str,
    recipient: str,
    card_json: str,
    record_file_id: str = "",
    user_key: str = "",
    appdata_root: str = "",
) -> str:
    """向固定个人或群聊收件人发送会议总结卡, 并记录幂等回执(与文本通知同文件)。"""
    try:
        if not meeting_name.strip() or not recipient.strip() or not card_json.strip():
            raise ValueError("meeting_name, recipient, and card_json are required")
        base = await resolve_appdata_root(appdata_root)
        artifact = meeting_artifact_root(base, meeting_name)
        artifact.mkdir(parents=True, exist_ok=True)
        identity, display_name, receive_id_type = await _resolve_recipient(recipient, user_key)
        if not identity:
            return json.dumps(
                {"ok": False, "status": "recipient_unresolved", "recipient": recipient, "error": display_name},
                ensure_ascii=False,
            )
        receipt_path = artifact / "notification_receipts.json"
        receipt_key = hashlib.sha256(f"{record_file_id}\n{identity}".encode()).hexdigest()
        async with await _receipt_lock(str(receipt_path) + ":" + receipt_key):
            receipts = await _read_receipts(receipt_path)
            previous = receipts.get(receipt_key)
            if isinstance(previous, dict) and previous.get("ok"):
                return json.dumps(
                    {
                        "ok": True,
                        "status": "already_sent",
                        "recipient": display_name,
                        "message_id": previous.get("message_id", ""),
                    },
                    ensure_ascii=False,
                )
            delivery = "chat" if receive_id_type == "chat_id" else "direct"
            content_hash = hashlib.sha256(card_json.encode()).hexdigest()
            if isinstance(previous, dict) and previous.get("text_sha256") not in (None, "", content_hash):
                return json.dumps(
                    {
                        "ok": False,
                        "status": "receipt_content_mismatch",
                        "recipient": display_name,
                        "delivery": delivery,
                        "recipient_id": identity,
                        "message_ids": previous.get("message_ids", []),
                        "error": "同 record 已有不同内容的通知, 拒绝覆盖发送",
                    },
                    ensure_ascii=False,
                )
            try:
                # 类型由解析结果给出: 租户级 user_id 没有前缀, 写死 "open_id" 会被判 230001。
                sent = await _f.send_card_impl(identity, card_json, receive_id_type, user_key or None)
            except Exception as exc:
                sent = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
            if not isinstance(sent, dict) or not sent.get("ok"):
                error = str((sent or {}).get("message") or "card send failed")
                receipt = {
                    "ok": False,
                    "status": "send_failed",
                    "recipient": display_name,
                    "delivery": delivery,
                    "recipient_id": identity,
                    "message_id": "",
                    "message_ids": [],
                    "text_sha256": content_hash,
                    "error": error,
                }
                receipts[receipt_key] = receipt
                await _write_receipts(receipt_path, receipts)
                return json.dumps(receipt, ensure_ascii=False)
            message_id = str(sent.get("message_id") or "")
            receipt = {
                "ok": True,
                "status": "sent",
                "recipient": display_name,
                "delivery": delivery,
                "recipient_id": identity,
                "message_id": message_id,
                "message_ids": [message_id] if message_id else [],
                "text_sha256": content_hash,
                "error": "",
            }
            receipts[receipt_key] = receipt
            await _write_receipts(receipt_path, receipts)
            return json.dumps(receipt, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return json.dumps(
            {"ok": False, "status": "meeting_card_notification_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["notify_meeting_card", "render_meeting_summary_card"]

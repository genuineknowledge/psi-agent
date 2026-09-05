# ruff: noqa: RUF001, RUF002, RUF003  # 中文全角标点是刻意排版
"""正负面清单「判断卡」MVP v2 的共享核心（确定性、无大模型）。

依据文档: 《正负面清单 · MVP v2 优化方案》(2026-09-03 王炜博拍板, 扩源优先 +
卡片化闭环)。一种「判断卡」模板、四种场景; P0 = 会议纪要候选链路 +
场景① 记录确认 + 场景② 判断复核; P1 = 场景③ 结果反馈(判断通过后本人
补充说明 / 开始复盘) + 场景④ 周小结(本周记录汇总 / 查看 / 复盘)。

本模块只做确定性的事:
- 状态存储: 候选批次 / 判断复核 / 结果反馈 / 周小结 各自一个 JSON 文件,
  落在隔离 AppData ``pn-state/``(与 member-feedback 同根, 跨会话共享; conftest 已把
  PSI_APPDATA 指到 tmp, 测试天然隔离)。
- 卡片渲染: 场景① 候选确认卡 = schema 2.0 multi_use 逐行动作(确认记录/修改内容/
  不做记录); 场景② 判断复核卡 = schema 2.0 单卡三动作(同意判断/调整判断/退回补证);
  两个取文本的表单卡(改→修改候选 / 调整判断→覆写判断) = legacy form(与 handbook /
  assignment_feedback 同一套已被验证的 input+form_submit 形态)。
- 行动作幂等: 已终态的行再点返回 ok+unchanged, 不重复落账、不重复改卡。
- 记录管道: ``commit_record()`` —— 确认的候选走"与手写同一条记录管道"。
  真正台账(多维表格)定位前, 默认本地落账 ``pn-state/records.json``;
  接入正式台账只需替换这一个函数(或用 ledger 参数走 bitable 追加)。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import anyio

from psi_agent._appdata import resolve_appdata_root

# 单卡候选上限: 一次会后批量确认, 10 条内一屏看完; 超出让海豚分批发。
MAX_CANDIDATES = 10
MAX_TEXT = 200
MAX_VERDICT = 200
MAX_ADVICE = 200

_SIGN = "判断卡 · 海豚三号 · MVP v2"

_POLARITY_CN = {"positive": "正面", "negative": "负面"}
_TEMPLATE = {"positive": "green", "negative": "red"}

# 场景① 行终态(不再响应任何按钮)
_FINAL_ROW = {"kept", "dropped", "kept_edited"}
# 场景② 复核终态(整卡不再响应任何按钮)
_FINAL_JUDGE = {"approved", "returned", "overridden"}

_ACTION_CONFIRM = "pn_keep_{i}"
_ACTION_DROP = "pn_drop_{i}"
_ACTION_EDIT = "pn_edit_{i}"
_ACTION_EDIT_SUBMIT = "pn_confirm_edit_submit"
_ACTION_JUDGE_AGREE = "pn_judge_agree"
_ACTION_JUDGE_OVERRIDE = "pn_judge_override"
_ACTION_JUDGE_RETURN = "pn_judge_return"
_ACTION_OVERRIDE_SUBMIT = "pn_judge_override_submit"


# ── 小工具 ──────────────────────────────────────────────────────────────────


def _esc(text: Any) -> str:
    return (str(text)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _now() -> str:
    import datetime  # noqa: PLC0415  # 保持与 reminder 卡一致: 本地时间即卡面时间

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _pn_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _stars(score: int) -> str:
    score = max(1, min(5, int(score or 0)))
    return "★" * score + "☆" * (5 - score)


def _clamp(text: Any, limit: int = MAX_TEXT) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _fmt_cell(text: str, color: str = "#1F2329") -> dict:
    """schema 2.0 的一个 markdown 段落, 统一着色入口。"""
    return {"tag": "markdown", "content": f"<font color='{color}'>{text}</font>"}


def _ledger_link_url(ledger: dict) -> str:
    """把 ledger 坐标拼成可点的台账表格链接(feishu.cn 别名域, 与手发链接同款)。

    app_token 必给才出链接; 有 table_id 则深链到具体数据表(?table=)。空 → 无链接。
    """
    app_token = str((ledger or {}).get("app_token") or "").strip()
    if not app_token:
        return ""
    url = f"https://feishu.cn/base/{app_token}"
    table_id = str((ledger or {}).get("table_id") or "").strip()
    if table_id:
        url += f"?table={table_id}"
    return url


def _ledger_link_cell(holder: dict, label: str = "打开台账表格") -> dict | None:
    """卡片带台账坐标时, 页脚给一行该表格的可点链接(记录写进/读到哪张表, 一眼可跳)。

    未接台账(无 ledger / 无 app_token) → None, 卡面与旧版一致、不留空行。
    """
    url = _ledger_link_url(holder.get("ledger"))
    if not url:
        return None
    return _fmt_cell(f"<font color='#8F959E'>📊 台账: [{label} ↗]({url})</font>")


# 形如 open_id / union_id / chat_id / app_id / 消息 id 的内部 id（带已知前缀，
# 或纯 ASCII 的超长串）。判定口径：ou_/on_/oc_/cli_/om_/rec_ 等前缀 → 内部 id；
# 无前缀但纯 ASCII 且 ≥20 位 → 也当 id。
_ID_PREFIXES = (
    "ou_",
    "on_",
    "oc_",
    "om_",
    "cli_",
    "user_",
    "u_",
    "app_",
    "rec_",
    "tbl_",
    "judge_",
    "cand_",
    "batch_",
    "cn_",
)
_LONG_ASCII = re.compile(r"^[0-9A-Za-z_\-]{20,}$")
_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _display_name(value: Any, fallback: str = "") -> str:
    """卡面人名：open_id / 内部 id 绝不上卡（曾把 ou_xxx 原样印在终态行上）。

    decided_by 等留痕字段存的是操作者 open_id（便于审计），展示时发现是内部 id
    就换成可读姓名（默认复核人/收卡人）；已解析好的中文姓名原样展示。
    """
    s = str(value or "").strip()
    if not s:
        return fallback
    if _CJK.search(s):
        return s  # 含中文/日文/韩文 → 已解析的人名，直接展示
    low = s.lower()
    if low.startswith(_ID_PREFIXES) or _LONG_ASCII.match(s):
        return fallback or "复核人"
    return s


# ── 状态存储(AppData pn-state/, 与 member-feedback 同根) ───────────────────


async def _state_dir() -> anyio.Path:
    root = await resolve_appdata_root("")
    d = anyio.Path(root) / "pn-state"
    await d.mkdir(parents=True, exist_ok=True)
    return d


async def _read_json(path: anyio.Path) -> dict | None:
    try:
        raw = await path.read_text(encoding="utf-8")
    except OSError, ValueError, UnicodeDecodeError:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


async def _write_json(path: anyio.Path, obj: dict) -> None:
    await path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


async def save_batch(batch: dict) -> dict:
    d = await _state_dir()
    await _write_json(d / f"cand_{batch['batch_id']}.json", batch)
    return batch


async def load_batch(batch_id: str) -> dict | None:
    d = await _state_dir()
    return await _read_json(d / f"cand_{batch_id}.json")


async def save_judge(judge: dict) -> dict:
    d = await _state_dir()
    await _write_json(d / f"judge_{judge['judge_id']}.json", judge)
    return judge


async def load_judge(judge_id: str) -> dict | None:
    d = await _state_dir()
    return await _read_json(d / f"judge_{judge_id}.json")


async def commit_record(record: dict) -> dict:
    """记录管道: 把一条已确认记录写进本地台账暂存(真正台账接好后替换本函数)。

    追加到 ``pn-state/records.json``, 每次成功一条; 并发安全由文件级 append
    (读-改-写) 保障 —— 卡片按钮经 Channel 逐 action 消费一次, 不会并发同一条。
    """
    d = await _state_dir()
    path = d / "records.json"
    rec = {"id": _pn_id("rec"), "committed_at": _now(), **record}
    rows: list[dict] = []
    existing = await _read_json(path)
    if isinstance(existing, dict) and isinstance(existing.get("records"), list):
        rows = existing["records"]
    rows.append(rec)
    await _write_json(path, {"kind": "pn_records", "records": rows})
    return {"ok": True, "record_id": rec["id"], "total": len(rows)}


# ── 场景① 候选确认卡 ─────────────────────────────────────────────────────


def _build_candidate_batch(
    person_open_id: str,
    person_name: str,
    source_label: str,
    meeting_date: str,
    candidates: list[dict],
    ledger: dict | None = None,
) -> dict:
    rows = [
        {
            "index": i,
            "text": _clamp(c["text"]),
            "note": _clamp(c.get("note") or "", 80),
            "status": "pending",
            "edited_text": "",
            "decided_at": "",
        }
        for i, c in enumerate(candidates)
    ]
    return {
        "kind": "candidate_confirm",
        "batch_id": _pn_id("cand"),
        "person_open_id": person_open_id,
        "person_name": person_name,
        "source_label": source_label,
        "meeting_date": meeting_date,
        "created_at": _now(),
        "message_id": "",
        "ledger": dict(ledger or {}),
        "rows": rows,
    }


def _row_final_markdown(row: dict) -> dict:
    """终态行渲染: 记入/忽略/修改中/已记入(改)。"""
    status = row["status"]
    idx = row["index"] + 1
    if status == "kept":
        return _fmt_cell(f"✅ <font color='#34C724'>**{idx}. {_esc(row['text'])}**</font>　—已记入")
    if status == "kept_edited":
        new_text = _esc(row.get("edited_text") or row["text"])
        return _fmt_cell(f"✅ <font color='#34C724'>**{idx}. {new_text}**</font>　—已记入(修改后)")
    if status == "dropped":
        return _fmt_cell(f"➖ <font color='#8F959E'>{idx}. {_esc(row['text'])}　—未记录</font>")
    return _fmt_cell(f"✏️ <font color='#FA8C16'>{idx}. {_esc(row['text'])}　—修改中…</font>")


def _row_buttons(batch: dict, row: dict) -> list[dict]:
    """pending 行的一排三个按钮: 确认记录 / 修改内容 / 不做记录。"""
    i = row["index"]
    base = {
        "pn_id": batch["batch_id"],
        "batch_id": batch["batch_id"],
        "person_open_id": batch["person_open_id"],
    }

    def _btn(label: str, action: str, typ: str) -> dict:
        return {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "elements": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": label},
                    "type": typ,
                    "behaviors": [{"type": "callback", "value": {**base, "action": action}}],
                }
            ],
        }

    return [
        _btn("确认记录", _ACTION_CONFIRM.format(i=i), "primary"),
        _btn("修改内容", _ACTION_EDIT.format(i=i), "default"),
        _btn("不做记录", _ACTION_DROP.format(i=i), "default"),
    ]


def render_confirm_card(batch: dict) -> dict:
    """场景① 候选确认卡(schema 2.0, multi_use 逐行)。"""
    rows = batch.get("rows") or []
    pending = [r for r in rows if r["status"] == "pending"]
    elements: list[dict] = [
        _fmt_cell(
            f"<font color='#8F959E'>🕐 {_now()} · 来源 {_esc(batch.get('source_label') or '')}"
            f" · {_esc(batch.get('meeting_date') or '')} · 只整理 {_esc(batch.get('person_name') or '你')}"
            " 自己的候选</font>"
        ),
        {"tag": "hr"},
        _fmt_cell(
            f"<font color='#1F2329'>**🎯 {len(pending)}/{len(rows)} 待确认**</font>"
            ' · 从会议纪要抽出的"像行为事实"的句子, 确认一条记一条'
        ),
    ]
    for row in rows:
        if row["status"] == "pending":
            elements.append(_fmt_cell(f"**{row['index'] + 1}. {_esc(row['text'])}**"))
            if row.get("note"):
                elements.append(_fmt_cell(f"<font color='#8F959E'>　{_esc(row['note'])}</font>"))
            elements.append(
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_spacing": "8px",
                    "columns": _row_buttons(batch, row),
                }
            )
        else:
            elements.append(_row_final_markdown(row))
    elements.append({"tag": "hr"})
    link = _ledger_link_cell(batch, "打开记录写入的表格")
    if link:
        elements.append(link)
    elements.append(_fmt_cell(f"<font color='#B0B6BF'>确认即入库 · 记录不删 · 判断可进化 · {_SIGN}</font>"))
    return {
        "schema": "2.0",
        "config": {"width_mode": "regular"},
        "header": {
            "title": {"tag": "plain_text", "content": "🎯 正负面 · 候选确认"},
            "template": "blue" if pending else "grey",
        },
        "body": {"elements": elements},
    }


def confirm_card_handlers(batch: dict) -> dict:
    """场景① 整卡动作 → handler(全指向同一个工具, 工具按 action 分发)。"""
    handlers: dict[str, str] = {}
    for row in batch.get("rows") or []:
        if row["status"] in _FINAL_ROW:
            continue
        i = row["index"]
        handlers[_ACTION_CONFIRM.format(i=i)] = "feishu_pn_confirm_card"
        handlers[_ACTION_DROP.format(i=i)] = "feishu_pn_confirm_card"
        handlers[_ACTION_EDIT.format(i=i)] = "feishu_pn_confirm_card"
    return handlers


# 场景① 的"改"→ 修改表单卡(legacy form + form_submit, 与 handbook 同形态)


def render_confirm_edit_card(batch: dict, index: int) -> dict:
    """把第 index 条候选改成自己想要的文字后确认(单次卡)。"""
    row = batch["rows"][index]
    original = _esc(row["text"])
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "✏️ 修改候选 · 确认后记入"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"原句: {original}\n\n请改成你认为**准确的行为事实**后提交(会按新文字记入清单)。",
                },
            },
            {
                "tag": "form",
                "name": "pn_confirm_edit_form",
                "elements": [
                    {
                        "tag": "input",
                        "name": "pn_edit_text",
                        "required": True,
                        "placeholder": {"tag": "plain_text", "content": "修改后的行为事实"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "确认修改并记入"},
                        "type": "primary",
                        "name": "submit_edit",
                        "action_type": "form_submit",
                        "value": {
                            "action": _ACTION_EDIT_SUBMIT,
                            "batch_id": batch["batch_id"],
                            "row_index": index,
                            "person_open_id": batch["person_open_id"],
                        },
                    },
                ],
            },
        ],
    }


# ── 场景② 判断复核卡 ─────────────────────────────────────────────────────


def build_judge(
    receiver_open_id: str,
    receiver_name: str,
    judgment: dict,
    ledger: dict | None = None,
    person_open_id: str = "",
    person_name: str = "",
) -> dict:
    """把一份海豚判断(锐评三要)包装成待复核记录。

    ledger 可选: {app_token, table_id, record_id[, status_field]} —— 发卡时带了
    台账坐标, 复核决定(同意/调整/退回)就会把该行「状态」列从待复核刷成对应终态。
    person_open_id/person_name 可选: 被判断的行为对象本人 —— 判断生效(同意/改判)
    后海豚会把结果反馈卡(场景③)发给这个人。
    """
    return {
        "kind": "judge_review",
        "judge_id": _pn_id("judge"),
        "receiver_open_id": receiver_open_id,
        "receiver_name": receiver_name,
        "person_open_id": person_open_id,
        "person_name": person_name,
        "judgment": dict(judgment),
        "ledger": dict(ledger or {}),
        "created_at": _now(),
        "message_id": "",
        "decided": "pending",
        "decided_by": "",
        "decided_at": "",
        "override": {},
    }


# 复核决定 → 台账「状态」列取值(单选选项, 与真实台账 status_options 对齐)。
_JUDGE_DECIDED_STATUS = {"approved": "已通过", "overridden": "已调整", "returned": "已退回"}
_JUDGE_STATUS_FIELD = "状态"


async def apply_judge_ledger_status(judge: dict, decided: str, user_key: str) -> dict:
    """把复核决定写回台账「状态」列(单选), 走用户身份(prefer=user)。

    纪律与场景①同源: **没写进台账的状态迁移不算数** —— 调用方拿到 ok=False 就
    必须保持判断 pending、不许刷终态卡, 否则就是"卡面已闭环、表里还是待复核"
    的假闭环。未接台账(无 ledger / 无 record_id / 无映射) → ok=True + skipped,
    行为与旧版一致。
    """
    ledger = judge.get("ledger") or {}
    app_token = str(ledger.get("app_token") or "").strip()
    table_id = str(ledger.get("table_id") or "").strip()
    record_id = str(ledger.get("record_id") or "").strip()
    if not (app_token and table_id and record_id):
        return {"ok": True, "skipped": "no ledger record wired"}
    status = _JUDGE_DECIDED_STATUS.get(decided)
    if not status:
        return {"ok": True, "skipped": f"no status mapping for decided={decided!r}"}
    field = str(ledger.get("status_field") or _JUDGE_STATUS_FIELD).strip() or _JUDGE_STATUS_FIELD
    import _feishu_impl as _core  # noqa: PLC0415  # 运行时导入, 保持与 confirm 同风格

    try:
        res = await _core.update_bitable_record_impl(
            app_token,
            table_id,
            record_id,
            json.dumps({field: status}, ensure_ascii=False),
            user_key=user_key,
            identity="user",
            validate_fields=True,
        )
    except Exception as e:  # 如实回传, 由调用方决定是否阻止终态
        return {"ok": False, "error": f"{e!r}"}
    if not res.get("ok"):
        return {"ok": False, "error": str(res.get("message") or res.get("error") or res)}
    return {"ok": True, "status": status, "field": field, "record_id": record_id}


def _judge_header(judge: dict) -> dict:
    j = judge["judgment"]
    return {
        "tag": "plain_text",
        "content": (
            f"⚖️ 判断复核 · {_display_name(judge.get('receiver_name'), '成员')}"
            f" · {_POLARITY_CN.get(j.get('polarity'), '')}"
        ),
    }


def _judge_summary_block(judge: dict, *, decided: bool = False) -> list[dict]:
    """判断卡中段: 判断(锐评★) > 证据 > 建议; 负面附「正确做法」行。"""
    j = judge["judgment"]
    polarity = j.get("polarity")
    negative = polarity == "negative"
    color = "red" if negative else "#34C724"
    label = _esc(j.get("label") or (_POLARITY_CN.get(polarity) or "行为"))
    block: list[dict] = [
        _fmt_cell(f"<font color='#1F2329'>**判断**</font>　{label}"),
        _fmt_cell(
            f"<font color='{color}'>**{_esc(j.get('verdict') or '')}**"
            f"　<font color='#FA8C16'>{_stars(int(j.get('score') or 0))}</font></font>"
        ),
    ]
    _src_note = " ".join(x for x in (j.get("source") or "", j.get("source_time") or "") if x)
    _evidence = f"<font color='#1F2329'>**证据**</font>　{_esc(j.get('behavior') or '')}"
    if _src_note:
        _evidence += f"<font color='#8F959E'> · {_esc(_src_note)}</font>"
    block.append(_fmt_cell(_evidence))
    advice = j.get("advice") or ""
    if negative:
        block.append(
            _fmt_cell(f"<font color='#1F2329'>**正确做法 · 建议**</font>　<font color='#FA8C16'>{_esc(advice)}</font>")
        )
    elif advice:
        block.append(_fmt_cell(f"<font color='#1F2329'>**建议**</font>　{_esc(advice)}"))
    if not decided:
        note = (
            "负面结论先经 mentor 复核再通知本人 · 每条都可以被挑战"
            if negative
            else "复核通过后反馈本人 · 每条都可以被挑战"
        )
        block.append(_fmt_cell(f"<font color='#B0B6BF'>{note}</font>"))
    return block


def _judge_action_row(judge: dict) -> dict:
    base = {
        "judge_id": judge["judge_id"],
        "person_open_id": judge.get("receiver_open_id") or "",
    }

    def _btn(label: str, action: str, typ: str) -> dict:
        return {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "elements": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": label},
                    "type": typ,
                    "behaviors": [{"type": "callback", "value": {**base, "action": action}}],
                }
            ],
        }

    return {
        "tag": "column_set",
        "flex_mode": "none",
        "horizontal_spacing": "8px",
        "columns": [
            _btn("同意判断", _ACTION_JUDGE_AGREE, "primary"),
            _btn("调整判断", _ACTION_JUDGE_OVERRIDE, "default"),
            _btn("退回补证", _ACTION_JUDGE_RETURN, "danger"),
        ],
    }


def _judge_decided_block(judge: dict) -> list[dict]:
    """复核终态: 谁在什么时候做了什么决定; 整卡不再带任何按钮。"""
    d = judge["decided"]
    # decided_by 留痕存 open_id, 展示必须换成可读姓名（id 印上卡就是"一串英文"）。
    who = _display_name(judge.get("decided_by"), judge.get("receiver_name") or "复核人")
    when = judge.get("decided_at") or ""
    override = judge.get("override") or {}
    if d == "approved":
        head = (
            "✅ <font color='#34C724'>**已同意判断**</font>"
            f" · {_esc(who)} {when}<font color='#8F959E'> — 判断成立, 生效</font>"
        )
    elif d == "returned":
        head = (
            "↩️ <font color='#F53F3F'>**已退回补证**</font>"
            f" · {_esc(who)} {when}<font color='#8F959E'> — 判断退回, 待补证后重新判断</font>"
        )
    else:
        head = (
            "✍️ <font color='#3370FF'>**已调整判断**</font>"
            f" · {_esc(who)} {when}<font color='#8F959E'> — 以调整后的判断为准</font>"
        )
    block = [_fmt_cell(head)]
    if d == "overridden" and override.get("verdict"):
        block.append(
            _fmt_cell(
                f"<font color='#3370FF'>**调整后判断: {_esc(override['verdict'])}**"
                f"　{_stars(int(override.get('score') or 0))}</font>"
            )
        )
    return block


def render_judge_card(judge: dict) -> dict:
    """场景② 判断复核卡(schema 2.0)。pending 带三动作, 终态无按钮。"""
    elements: list[dict] = [
        _fmt_cell(f"<font color='#8F959E'>🕐 {_now()} · 待 mentor 复核</font>"),
        {"tag": "hr"},
    ]
    if judge["decided"] in _FINAL_JUDGE:
        elements += _judge_decided_block(judge)
        elements.append({"tag": "hr"})
        elements += _judge_summary_block(judge, decided=True)
        template = "green" if judge["decided"] == "approved" else "grey"
    else:
        elements += _judge_summary_block(judge)
        elements.append({"tag": "hr"})
        elements.append(_judge_action_row(judge))
        template = "red" if judge["judgment"].get("polarity") == "negative" else "green"
    elements.append({"tag": "hr"})
    link = _ledger_link_cell(judge, "打开台账表格")
    if link:
        elements.append(link)
    elements.append(_fmt_cell(f"<font color='#B0B6BF'>{_SIGN}</font>"))
    return {
        "schema": "2.0",
        "config": {"width_mode": "regular"},
        "header": {"title": _judge_header(judge), "template": template},
        "body": {"elements": elements},
    }


def judge_card_handlers(judge: dict) -> dict:
    if judge["decided"] in _FINAL_JUDGE:
        return {}
    return {
        _ACTION_JUDGE_AGREE: "feishu_pn_judge_card",
        _ACTION_JUDGE_OVERRIDE: "feishu_pn_judge_card",
        _ACTION_JUDGE_RETURN: "feishu_pn_judge_card",
    }


# 场景② 的"改判"→ 覆写表单卡(legacy form, 海豚原判断亮在卡上)


def render_judge_override_card(judge: dict) -> dict:
    """让 mentor 写下自己的判断(一句话 + 可选分数), 提交即改判。"""
    j = judge["judgment"]
    original = _esc(j.get("verdict") or "")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "✍️ 调整判断 · 写下你的判断"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"海豚原判断: {original}　{_stars(int(j.get('score') or 0))}\n\n"
                        "请按**锐评三要**写下你的判断(下结论、指名行为、带 1 个可执行动作), "
                        '分数 1–5 可写进句子里(如 "3分 这次复盘缺结论…")。'
                    ),
                },
            },
            {
                "tag": "form",
                "name": "pn_judge_override_form",
                "elements": [
                    {
                        "tag": "input",
                        "name": "pn_override_text",
                        "required": True,
                        "placeholder": {"tag": "plain_text", "content": "你的判断(一句话, 可含分数)"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "提交调整判断"},
                        "type": "primary",
                        "name": "submit_override",
                        "action_type": "form_submit",
                        "value": {
                            "action": _ACTION_OVERRIDE_SUBMIT,
                            "judge_id": judge["judge_id"],
                        },
                    },
                ],
            },
        ],
    }


# ── 回调 payload 解析(与 member_todo_card 同一信封) ──────────────────────


def parse_action(card_action_json: str) -> dict:
    """把 <feishu_card_action> 解成 {action, form_value, value..., _message_id, _operator}。"""
    try:
        payload = json.loads(card_action_json) if card_action_json.strip() else {}
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    action = payload.get("action")
    if not isinstance(action, dict):
        return {}
    value = action.get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        out.update(value)
    form_value = action.get("form_value")
    if isinstance(form_value, dict):
        out["_form_value"] = form_value
    out["_message_id"] = str(payload.get("message_id") or "").strip()
    operator = payload.get("operator")
    out["_operator"] = str(operator.get("open_id") or "").strip() if isinstance(operator, dict) else ""
    return out


def row_index_of(action: str) -> int | None:
    """从 pn_keep_3 / pn_drop_3 / pn_edit_3 里解出行号。"""
    try:
        return int(str(action).rsplit("_", 1)[1])
    except ValueError, IndexError:
        return None


# ── 场景③ 结果反馈卡（判断通过 → 反馈给行为对象本人）──────────────────────

# ③ 结果反馈: 判断经 mentor 复核通过(approved / overridden)后, 海豚把结果
# 反馈给行为对象本人。卡面两动作(用户定稿): 开始复盘 / 补充说明。本人可对
# 这条记录补写说明 —— 写回台账「备注」列留痕(mentor/海豚下次可见), 或开启
# 一对一复盘(工具只负责留痕+终态, 复盘对话由 agent 后续轮次驱动)。

_FINAL_FEEDBACK = {"reviewed", "noted"}
_FEEDBACK_NOTE_PENDING = "note_pending"
_MAX_NOTE_TEXT = 400  # 本人补充说明上限(比行为事实宽一些, 允许还原现场)
_ACTION_FB_REVIEW = "pn_fb_review"
_ACTION_FB_NOTE = "pn_fb_note"
_ACTION_FB_NOTE_SUBMIT = "pn_fb_note_submit"
_FEEDBACK_NOTE_FIELD = "备注"


def build_feedback(
    person_open_id: str,
    person_name: str,
    judgment: dict,
    ledger: dict | None = None,
    source_judge_id: str = "",
) -> dict:
    """把一条已通过复核的判断包装成待本人处理的结果反馈记录。"""
    return {
        "kind": "pn_feedback",
        "feedback_id": _pn_id("fb"),
        "person_open_id": person_open_id,
        "person_name": person_name,
        "judgment": dict(judgment),
        "ledger": dict(ledger or {}),
        "source_judge_id": source_judge_id,
        "created_at": _now(),
        "message_id": "",
        "decided": "pending",  # pending | note_pending | noted | reviewed
        "decided_at": "",
        "note_text": "",
    }


async def save_feedback(fb: dict) -> dict:
    d = await _state_dir()
    await _write_json(d / f"feedback_{fb['feedback_id']}.json", fb)
    return fb


async def load_feedback(feedback_id: str) -> dict | None:
    d = await _state_dir()
    return await _read_json(d / f"feedback_{feedback_id}.json")


def _fb_action_row(fb: dict) -> dict:
    base = {
        "feedback_id": fb["feedback_id"],
        "person_open_id": fb.get("person_open_id") or "",
    }

    def _btn(label: str, action: str, typ: str) -> dict:
        return {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "elements": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": label},
                    "type": typ,
                    "behaviors": [{"type": "callback", "value": {**base, "action": action}}],
                }
            ],
        }

    return {
        "tag": "column_set",
        "flex_mode": "none",
        "horizontal_spacing": "8px",
        "columns": [
            _btn("开始复盘", _ACTION_FB_REVIEW, "primary"),
            _btn("补充说明", _ACTION_FB_NOTE, "default"),
        ],
    }


def render_feedback_card(fb: dict) -> dict:
    """场景③ 结果反馈卡(schema 2.0)。pending 带 [开始复盘][补充说明], 终态无按钮。"""
    j = fb["judgment"]
    negative = j.get("polarity") == "negative"
    name = _display_name(fb.get("person_name"), "成员")
    elements: list[dict] = [
        _fmt_cell(f"<font color='#8F959E'>🕐 {_now()} · 结果反馈 · 判断已通过复核 · 只发给本人</font>"),
        {"tag": "hr"},
    ]
    decided = fb["decided"]
    if decided == "reviewed":
        who = _display_name(fb.get("decided_by"), name)
        when = fb.get("decided_at") or ""
        elements.append(_fmt_cell(f"🎯 <font color='#3370FF'>**已开始复盘**</font> · {_esc(who)} {when}"))
        elements.append({"tag": "hr"})
    elif decided == "noted":
        who = _display_name(fb.get("decided_by"), name)
        when = fb.get("decided_at") or ""
        note = _esc(fb.get("note_text") or "")
        elements.append(
            _fmt_cell(
                f"📝 <font color='#34C724'>**已补充说明**</font> · {_esc(who)} {when}"
                "<font color='#8F959E'> — 说明已写回台账备注，mentor/海豚下次可见</font>"
            )
        )
        elements.append({"tag": "hr"})
        elements.append(_fmt_cell(f"<font color='#1F2329'>{note}</font>"))
        elements.append({"tag": "hr"})
    elif decided == _FEEDBACK_NOTE_PENDING:
        elements.append(_fmt_cell("✏️ <font color='#FA8C16'>补充说明填写中…（提交后写回台账备注）</font>"))
        elements.append({"tag": "hr"})
    # 判断核心复用复核卡同款渲染（judgment 结构一致；decided=True 不带"待复核"提示）
    elements += _judge_summary_block({"judgment": j, "receiver_name": name}, decided=True)
    if decided == "pending":
        elements.append({"tag": "hr"})
        elements.append(_fb_action_row(fb))
        tail = (
            "判断已生效 · 可以补充背景或辩解，也可以和我复盘这条，看下次怎么做得更好"
            if not negative
            else "负面判断已生效 · 可以补充说明还原现场，或和我复盘：承认-归因-下一步"
        )
        elements.append(_fmt_cell(f"<font color='#B0B6BF'>{tail}</font>"))
    elements.append({"tag": "hr"})
    link = _ledger_link_cell(fb, "打开备注写回的表格")
    if link:
        elements.append(link)
    elements.append(_fmt_cell(f"<font color='#B0B6BF'>{_SIGN}</font>"))
    template = "red" if (negative and decided == "pending") else ("green" if decided == "noted" else "grey")
    return {
        "schema": "2.0",
        "config": {"width_mode": "regular"},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"🗣️ 结果反馈 · {name} · {_POLARITY_CN.get(j.get('polarity'), '')}",
            },
            "template": template,
        },
        "body": {"elements": elements},
    }


def feedback_card_handlers(fb: dict) -> dict:
    if fb["decided"] != "pending":
        return {}
    return {
        _ACTION_FB_REVIEW: "feishu_pn_feedback_card",
        _ACTION_FB_NOTE: "feishu_pn_feedback_card",
    }


def render_feedback_note_card(fb: dict) -> dict:
    """本人补充说明表单卡(legacy form, 与修改候选同形态)。"""
    j = fb["judgment"]
    original = _esc(j.get("behavior") or j.get("verdict") or "")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📝 补充说明 · 这条记录的你的视角"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"记录: {original}\n\n写下你想补充的背景/辩解/后续动作，"
                        "提交后会**写回台账备注**留痕，mentor 与海豚下次都看得到。"
                    ),
                },
            },
            {
                "tag": "form",
                "name": "pn_feedback_note_form",
                "elements": [
                    {
                        "tag": "input",
                        "name": "pn_fb_note_text",
                        "required": True,
                        "placeholder": {"tag": "plain_text", "content": "你的补充说明"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "提交补充说明"},
                        "type": "primary",
                        "name": "submit_note",
                        "action_type": "form_submit",
                        "value": {
                            "action": _ACTION_FB_NOTE_SUBMIT,
                            "feedback_id": fb["feedback_id"],
                            "person_open_id": fb.get("person_open_id") or "",
                        },
                    },
                ],
            },
        ],
    }


def _field_text(value: Any) -> str:
    """bitable 文本列的值可能是字符串或富文本数组 [{text,type}...]，摊平。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    if value is None:
        return ""
    return str(value)


async def append_feedback_note_to_ledger(fb: dict, note: str, user_key: str) -> dict:
    """把本人补充说明写回台账该行「备注」列(读-拼-写, 保留历史备注), prefer=user。

    纪律与复核回写同源: **没写进台账就不算补充完成** —— 调用方拿到 ok=False 必须
    保持 note_pending、不许刷终态, 否则就是"卡面已补充、表里没留痕"的假闭环。
    未接台账(无 ledger / 无 record_id) → ok=True + skipped, 只落本地反馈状态。
    """
    ledger = fb.get("ledger") or {}
    app_token = str(ledger.get("app_token") or "").strip()
    table_id = str(ledger.get("table_id") or "").strip()
    record_id = str(ledger.get("record_id") or "").strip()
    if not (app_token and table_id and record_id):
        return {"ok": True, "skipped": "no ledger record wired"}
    import _feishu_api_impl as _api  # noqa: PLC0415
    import _feishu_impl as _core  # noqa: PLC0415

    paths = json.dumps(
        {"app_token": app_token, "table_id": table_id, "record_id": record_id},
        ensure_ascii=False,
    )
    try:
        get = await _api.call_api_impl(
            "GET",
            "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id",
            paths_json=paths,
            prefer="user",
            user_key=user_key,
        )
    except Exception as e:  # 如实回传, 由调用方决定是否阻止终态
        return {"ok": False, "error": f"read record failed: {e!r}"}
    if not get.get("ok"):
        return {"ok": False, "error": str(get.get("message") or get.get("error") or get)}
    data = get.get("data") or {}
    record = data.get("record") or {}
    fields = record.get("fields") or {}
    old_note = _field_text(fields.get(_FEEDBACK_NOTE_FIELD))
    who = _display_name(fb.get("person_name"), "本人")
    stamp = f"[补充 · {who} {_now()}] {note}"
    new_note = f"{old_note}\n{stamp}" if old_note.strip() else stamp
    try:
        upd = await _core.update_bitable_record_impl(
            app_token,
            table_id,
            record_id,
            json.dumps({_FEEDBACK_NOTE_FIELD: new_note}, ensure_ascii=False),
            user_key=user_key,
            identity="user",
            validate_fields=True,
        )
    except Exception as e:
        return {"ok": False, "error": f"update record failed: {e!r}"}
    if not upd.get("ok"):
        return {"ok": False, "error": str(upd.get("message") or upd.get("error") or upd)}
    return {"ok": True, "field": _FEEDBACK_NOTE_FIELD, "record_id": record_id, "note": stamp}


async def send_feedback_card(fb: dict, user_key: str = "") -> dict:
    """把已 build+save 的结果反馈卡发出去(发卡 + 回调 handler 注册 + 记 message_id)。

    供场景③ 主工具发卡与 judge approved 联动共用 —— 联动时由 judge 侧构造 fb
    后直接调本函数, 避免把 send_card 封装逻辑复制两份。
    """
    import _feishu_impl as _core  # noqa: PLC0415

    handlers = feedback_card_handlers(fb)
    if not handlers:
        return {"ok": False, "error": "nothing to act on (feedback already final)"}
    try:
        res = await _core.send_card_impl(
            receive_id=fb.get("person_open_id") or "",
            card_json=json.dumps(render_feedback_card(fb), ensure_ascii=False),
            receive_id_type="open_id",
            user_key=user_key,
            business_context_json=json.dumps(
                {"kind": "pn_feedback", "feedback_id": fb["feedback_id"], "person": fb.get("person_name") or ""},
                ensure_ascii=False,
            ),
            action_handlers_json=json.dumps(handlers, ensure_ascii=False),
            multi_use=True,
        )
    except Exception as e:
        return {"ok": False, "error": f"{e!r}"}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("message") or res.get("error") or res}
    fb["message_id"] = res.get("message_id", "")
    await save_feedback(fb)
    return {"ok": True, "message_id": fb["message_id"], "feedback_id": fb["feedback_id"]}


async def maybe_send_feedback_after_judge(judge: dict, user_key: str = "") -> dict:
    """判断生效(approved/overridden)后 → 给行为对象本人发结果反馈卡(场景③)。

    由场景② 的同意/改判两个提交点调用。judge 没带 person_open_id(旧数据/未指定
    行为对象) → skipped，不硬发; 发送失败 → ok False, 调用方仅 warn 不阻塞判断
    本身(判断已生效落账, 反馈可稍后手动补发)。
    """
    person = str(judge.get("person_open_id") or "").strip()
    if not person:
        return {"ok": True, "skipped": "judge carries no person, feedback not sent"}
    if judge.get("decided") not in ("approved", "overridden"):
        return {"ok": True, "skipped": f"decided={judge.get('decided')!r} not a final-positive state"}
    j = dict(judge.get("judgment") or {})
    override = judge.get("override") or {}
    if judge["decided"] == "overridden" and override.get("verdict"):
        # 改判生效 → 反馈 mentor 调整后的判断(不是海豚原判)
        j = {**j, "verdict": override["verdict"], "score": int(override.get("score") or j.get("score") or 0)}
    fb = build_feedback(
        person_open_id=person,
        person_name=str(judge.get("person_name") or "").strip(),
        judgment=j,
        ledger=judge.get("ledger") or {},
        source_judge_id=str(judge.get("judge_id") or "").strip(),
    )
    await save_feedback(fb)
    try:
        return await send_feedback_card(fb, user_key=user_key)
    except Exception as e:  # send_feedback_card 自身兜底, 这里只做最终保险
        return {"ok": False, "error": f"{e!r}"}


# ── 场景④ 周小结卡（本周记录汇总 / 查看 / 复盘）───────────────────────────

# ④ 周小结: 统计某人在本周(周一~今天)真台账里已登记的行为记录, 给本人一张
# 小结卡。两动作(用户定稿): 查看本周记录(展开全文明细) / 开始复盘(对本周整体
# 开启复盘对话并留痕)。台账无极性/分数列 —— 周小结只展示台账真有的维度
# (条数/状态分布/逐条), 不编造台账里没有的分数。

_FINAL_WEEKLY = {"reviewed"}
_ACTION_WEEK_VIEW = "pn_week_view"
_ACTION_WEEK_REVIEW = "pn_week_review"
_WEEK_STATUSES = ("待复核", "已通过", "已调整", "已退回")

_MMDD = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})(?!\d)")


def _mmdd(text: str) -> tuple[int, int] | None:
    m = _MMDD.search(str(text or ""))
    if not m:
        return None
    mon, day = int(m.group(1)), int(m.group(2))
    if 1 <= mon <= 12 and 1 <= day <= 31:
        return mon, day
    return None


def _this_week_window() -> tuple[str, set[tuple[int, int]]]:
    """本周窗口: 周一~今天(含)。返回 (标签如 08-31 ~ 09-05, 窗口内的 (月,日) 集合)。"""
    import datetime  # noqa: PLC0415

    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    days: set[tuple[int, int]] = set()
    d = monday
    while d <= today:
        days.add((d.month, d.day))
        d += datetime.timedelta(days=1)
    label = f"{monday.month:02d}-{monday.day:02d} ~ {today.month:02d}-{today.day:02d}"
    return label, days


def build_weekly(
    person_open_id: str,
    person_name: str,
    week_label: str,
    records: list[dict],
    ledger: dict | None = None,
) -> dict:
    """包装本周小结。records 为快照行 {record_id,text,status,date,note}。"""
    return {
        "kind": "pn_weekly",
        "weekly_id": _pn_id("week"),
        "person_open_id": person_open_id,
        "person_name": person_name,
        "week_label": week_label,
        "records": list(records),
        "ledger": dict(ledger or {}),
        "created_at": _now(),
        "message_id": "",
        "decided": "pending",  # pending | reviewed
        "under_view": False,  # True = 明细已展开(查看本周记录已点)
        "decided_at": "",
    }


async def save_weekly(weekly: dict) -> dict:
    d = await _state_dir()
    await _write_json(d / f"weekly_{weekly['weekly_id']}.json", weekly)
    return weekly


async def load_weekly(weekly_id: str) -> dict | None:
    d = await _state_dir()
    return await _read_json(d / f"weekly_{weekly_id}.json")


async def fetch_person_week_records(
    app_token: str, table_id: str, person_name: str, user_key: str
) -> tuple[list[dict] | None, str]:
    """真台账里对象==person_name 且会议日期(文本 MM-DD)在本周的行。

    Returns: (rows | None, err)。rows 每项 {record_id,text,status,date,note}，
    按会议日期倒序(新在前)。读取失败 → (None, err)，调用方不出卡、如实报错。
    """
    import _feishu_impl as _core  # noqa: PLC0415

    try:
        res = await _core.search_bitable_records_impl(
            app_token,
            table_id,
            filter_json=json.dumps(
                {
                    "conjunction": "and",
                    "conditions": [{"field_name": "对象", "operator": "is", "value": [person_name]}],
                },
                ensure_ascii=False,
            ),
            field_names=json.dumps(["记录", "状态", "备注", "会议日期"], ensure_ascii=False),
            page_size=500,
            user_key=user_key,
        )
    except Exception as e:
        return None, f"search ledger failed: {e!r}"
    if not res.get("ok"):
        return None, str(res.get("message") or res.get("error") or res)
    week_label, window = _this_week_window()
    rows: list[dict] = []
    for rec in res.get("records") or []:
        fields = rec.get("fields") or {}
        date_md = _mmdd(_field_text(fields.get("会议日期")))
        if not date_md or date_md not in window:
            continue
        rows.append(
            {
                "record_id": str(rec.get("record_id") or ""),
                "text": _field_text(fields.get("记录")),
                "status": str(fields.get("状态") or "待复核").strip(),
                "date": _field_text(fields.get("会议日期")),
                "note": _field_text(fields.get("备注")),
            }
        )

    def _sort_key(row: dict) -> tuple[int, int, str]:
        md = _mmdd(row["date"]) or (0, 0)
        return (md[0], md[1], row["record_id"])

    rows.sort(key=_sort_key, reverse=True)
    return rows, week_label


def _weekly_status_line(weekly: dict) -> list[dict]:
    counts = dict.fromkeys(_WEEK_STATUSES, 0)
    for r in weekly.get("records") or []:
        s = str(r.get("status") or "").strip()
        counts[s] = counts.get(s, 0) + 1
    parts = []
    for s in _WEEK_STATUSES:
        if counts.get(s):
            parts.append(f"{s} {counts[s]}")
    line = "<font color='#1F2329'>**状态分布**</font>　" + " · ".join(parts)
    return _fmt_cell(line) if parts else None


def _weekly_rows_block(weekly: dict) -> list[dict]:
    """记录列表: 默认一行摘要(截 44 字 + 状态tag); 展开后全文+备注+日期。"""
    records = weekly.get("records") or []
    if not records:
        return [_fmt_cell("<font color='#B0B6BF'>本周台账还没有你的已登记记录。</font>")]
    out: list[dict] = []
    expanded = bool(weekly.get("under_view"))
    for i, r in enumerate(records, 1):
        status = str(r.get("status") or "").strip()
        tag_color = {"待复核": "#FA8C16", "已通过": "#34C724", "已调整": "#3370FF", "已退回": "#F53F3F"}.get(
            status, "#8F959E"
        )
        text = str(r.get("text") or "").strip()
        if expanded:
            head = f"{i}. {_esc(text)}"
            line = f"<font color='#1F2329'>{head}</font>　"
            line += f"<font color='{tag_color}'>[{_esc(status)}]</font>"
            out.append(_fmt_cell(line))
            sub = " ".join(x for x in (str(r.get("date") or ""), str(r.get("note") or "")) if x)
            if sub:
                out.append(_fmt_cell(f"<font color='#8F959E'>　{_esc(sub)}</font>"))
        else:
            snippet = text if len(text) <= 44 else text[:43] + "…"
            out.append(_fmt_cell(f"{i}. {_esc(snippet)}　<font color='{tag_color}'>[{_esc(status)}]</font>"))
    return out


def _weekly_action_row(weekly: dict) -> dict:
    base = {
        "weekly_id": weekly["weekly_id"],
        "person_open_id": weekly.get("person_open_id") or "",
    }

    def _btn(label: str, action: str, typ: str) -> dict:
        return {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "elements": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": label},
                    "type": typ,
                    "behaviors": [{"type": "callback", "value": {**base, "action": action}}],
                }
            ],
        }

    columns = [_btn("开始复盘", _ACTION_WEEK_REVIEW, "primary")]
    if not weekly.get("under_view"):
        columns.insert(0, _btn("查看本周记录", _ACTION_WEEK_VIEW, "default"))
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "horizontal_spacing": "8px",
        "columns": columns,
    }


def render_weekly_card(weekly: dict) -> dict:
    """场景④ 周小结卡(schema 2.0)。pending 带 [查看本周记录][开始复盘]，终态无按钮。"""
    name = _display_name(weekly.get("person_name"), "成员")
    records = weekly.get("records") or []
    decided = weekly["decided"]
    elements: list[dict] = [
        _fmt_cell(f"<font color='#8F959E'>🕐 {_now()} · {_esc(weekly.get('week_label') or '')} · 只发给本人</font>"),
        {"tag": "hr"},
    ]
    if decided == "reviewed":
        who = _display_name(weekly.get("decided_by"), name)
        when = weekly.get("decided_at") or ""
        elements.append(_fmt_cell(f"🎯 <font color='#3370FF'>**已开始本周复盘**</font> · {_esc(who)} {when}"))
        elements.append({"tag": "hr"})
    n_kept = sum(1 for r in records if str(r.get("status") or "").strip() != "待复核")
    head = f"**📊 本周登记 {len(records)} 条**" + (f" · 已确认 {n_kept} 条" if n_kept else "")
    elements.append(_fmt_cell(f"<font color='#1F2329'>{head}</font>"))
    status_line = _weekly_status_line(weekly)
    if status_line:
        elements.append(status_line)
    if records:
        elements.append(_fmt_cell("<font color='#8F959E'>━━ 记录 ━━</font>"))
        elements += _weekly_rows_block(weekly)
    elif decided != "reviewed":
        elements.append(_fmt_cell("<font color='#B0B6BF'>本周台账还没有你的已登记记录。</font>"))
    if decided == "pending":
        elements.append({"tag": "hr"})
        elements.append(_weekly_action_row(weekly))
        tail = "<font color='#B0B6BF'>复盘 = 和我一起把这周的几条过一遍：发生了什么 / 影响 / 下次怎么做更好</font>"
        elements.append(_fmt_cell(tail))
    elements.append({"tag": "hr"})
    link = _ledger_link_cell(weekly, "打开本周记录所在表格")
    if link:
        elements.append(link)
    elements.append(_fmt_cell(f"<font color='#B0B6BF'>{_SIGN}</font>"))
    return {
        "schema": "2.0",
        "config": {"width_mode": "regular"},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"📊 周小结 · {name} · {_esc(weekly.get('week_label') or '')}",
            },
            "template": "blue" if decided == "pending" else "grey",
        },
        "body": {"elements": elements},
    }


def weekly_card_handlers(weekly: dict) -> dict:
    if weekly["decided"] != "pending":
        return {}
    handlers = {_ACTION_WEEK_REVIEW: "feishu_pn_weekly_card"}
    if not weekly.get("under_view"):
        handlers[_ACTION_WEEK_VIEW] = "feishu_pn_weekly_card"
    return handlers

"""saving_read v1: 把**页面**确定性地读成事实。

放在链路里的位置(承 `saving_login`):

    查登录态 -> 登录 -> 导航到读定义的 url -> **本工具读成事实** -> 交给本体判定

本工具只负责最后一步。**不导航、不点击、不重试** —— 导航由 agent 用 `browser_*` 完成:
它看得见页面, 也要在登录墙 / 验证码前停下(那时走 `saving_login(action="blocked")`)。

## 一条不能破的线: "读不到" != "没有"

同一段选择器读到 0 张券, 有两种成因, 而它们在计数上完全一样:

- 券包**确实是空的** —— 页面的模块容器还在, 只是里面没有券。这是**事实**。
- 页面**结构变了 / 根本不是那一页** —— 连模块容器都找不到。这是**未知**。

区分它们只能靠 `markers`(页面必须存在的容器选择器)。所以本工具的结果是这样分的:

| 情况 | 返回 |
|---|---|
| 身份对 + markers 在 + 有券 | `ok=true`, 券清单(事实) |
| 身份对 + markers 在 + 0 张 | `ok=true, empty=true`(事实: 券包为空) |
| 身份对 + markers **不在** | `ok=false, reason=page_shape_changed` |
| 落到登录域 | `ok=false, reason=logged_out`(附 gate 与下一步) |
| 是别的地址 | `ok=false, reason=wrong_page`(附该去的 url) |
| 窗口被用户关 | `ok=false, reason=browser_closed`(要停下告知用户) |
| MCP 不通 / JS 抛错 | `ok=false, reason=browser_unavailable` |
| 拿回的文本不是 JSON | `ok=false, reason=unparsable_extract` |

`ok=false` 的返回**不带** `coupons` / `count` 字段。这是刻意的, 与本仓 facts 契约的四态
语义同源: `MISSING` 绝不能被下游当成 `false`。少了字段只是少一个信息; 多了个 `count: 0`
就是一个被当真的假事实。

## 事实与判定分开

本工具只交**页面上写着的东西**(面额/门槛/有效期/适用范围/券编号)。能不能用在这单上、
和国补怎么叠加、最终到手多少, 全是**本体**的活 —— 这里一律不推断, 也不给"建议"。
返回里的 `note` 会把这句话再说一遍, 因为越权推断最容易发生在刚拿到原始数据的那一刻。

## 配置在哪

读定义跟着平台走, 放 `<agent>/platforms/<key>.yaml` 的 `reads:` 下(与 `gate` 同源):
出厂内容, 加平台 / 加页面 = 加数据。平台注册表的加载直接复用 `saving_login`, 不另起一份。
"""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _browser_eval
import saving_login as _login

# 页面上按声明抽字段的固定运行时。`__SPEC__` 由 _build_js 换成一个 JSON 字面量
# (JSON 是 JS 的子集, 所以选择器里的引号由 json.dumps 负责转义, 不用手写拼串)。
_JS_TEMPLATE = """(() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const run = (spec) => {
    const markers = {};
    for (const m of spec.markers) markers[m] = document.querySelectorAll(m).length;
    const els = Array.from(document.querySelectorAll(spec.item));
    const coupons = els.map((el) => {
      const out = {};
      for (const name of Object.keys(spec.fields)) {
        const f = spec.fields[name];
        if (f.many) {
          out[name] = Array.from(el.querySelectorAll(f.sel)).map((node) => {
            if (!f.kv) return norm(node.textContent);
            const lab = node.querySelector(f.kv[0]);
            const val = node.querySelector(f.kv[1]);
            return { label: norm(lab ? lab.textContent : ''), value: norm(val ? val.textContent : '') };
          });
        } else if (f.presence) {
          out[name] = !!el.querySelector(f.sel);
        } else if (f.attr) {
          const node = el.querySelector(f.sel);
          out[name] = node ? (node.getAttribute(f.attr) || '') : '';
        } else {
          const node = el.querySelector(f.sel);
          out[name] = norm(node ? node.textContent : '');
        }
      }
      return out;
    });
    return { url: location.href, title: document.title, markers: markers, count: els.length, coupons: coupons };
  };
  return JSON.stringify(run(__SPEC__));
})()"""


def _fail(reason: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": False, "reason": reason}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _platform_ref(key: str, name: str) -> dict[str, str]:
    return {"key": key, "name": name}


# --------------------------------------------------------------------------- #
# 读定义 -> JS
# --------------------------------------------------------------------------- #


def _spec(read: dict[str, Any]) -> dict[str, Any] | None:
    """把 yaml 里的读定义收成一个干净的 spec; 不完整就返回 None(配置错, 不是页面错)。"""
    raw_fields = read.get("fields")
    fields: dict[str, dict[str, Any]] = {}
    if isinstance(raw_fields, dict):
        for raw_name, raw_field in raw_fields.items():
            if not isinstance(raw_field, dict):
                continue
            sel = str(raw_field.get("sel") or "").strip()
            if not sel:
                continue
            fields[str(raw_name)] = {k: v for k, v in raw_field.items() if k != "note"}
    item = str(read.get("item") or "").strip()
    markers = [str(m).strip() for m in read.get("markers") or [] if str(m).strip()]
    if not item or not markers or not fields:
        return None
    return {"item": item, "markers": markers, "fields": fields}


def _build_js(spec: dict[str, Any]) -> str:
    return _JS_TEMPLATE.replace("__SPEC__", json.dumps(spec, ensure_ascii=False))


def _parse_payload(text: str) -> dict[str, Any] | None:
    """从 MCP 返回的文本里取出那段 JSON。

    宽松是刻意的: `browser_evaluate` 可能带 `### Result` 前缀或把结果包在代码块里, 而这些
    外形随上游版本变。取第一个 `{` 到最后一个 `}` 足够稳, 且不会把非 JSON 认成 JSON。
    """
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- #
# 页面身份判定
# --------------------------------------------------------------------------- #


def _verdict(actual_url: Any, definition: dict[str, Any], read: dict[str, Any]) -> tuple[str, str]:
    """判定"现在这一页是不是读定义要的那一页" -> ``(verdict, basis)``。

    verdict ∈ ``on_page`` / ``logged_out`` / ``wrong_page`` / ``no_page``。
    判不准就算 `wrong_page`(保守): 宁可让 agent 再导航一次, 也不要从别的页面上读事实。
    """
    text = str(actual_url or "").strip()
    if not text:
        return "no_page", "empty_url"
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return "no_page", "unparsable_url"
    host = (parsed.hostname or "").lower()
    if not host:
        return "no_page", "no_host"

    login_hosts = [str(h).strip().lower() for h in definition.get("login_hosts") or [] if str(h).strip()]
    if any(host == h or host.endswith("." + h) for h in login_hosts):
        return "logged_out", f"landed_on_login_host:{host}"

    want_host = str(read.get("host") or "").strip().lower()
    if want_host and not (host == want_host or host.endswith("." + want_host)):
        return "wrong_page", f"host:{host}!={want_host}"
    prefix = str(read.get("path_prefix") or "").strip()
    if prefix and not (parsed.path or "/").startswith(prefix):
        return "wrong_page", f"path:{parsed.path}!~{prefix}"
    return "on_page", "matched"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


async def saving_read(platform: str = "", target: str = "", return_json: bool = True) -> str:
    """把浏览器**当前页面**按平台读定义读成结构化事实(目前: 京东已领券清单)。

    platform: 平台, 可给 key(jd) 或名称(京东)。
    target:   读哪一类页面, 见读定义。留空 = 列出这个平台有哪些可读页面(含该去的 url)。
    return_json: 默认返回 JSON 文本。

    用法: 先用 `browser_navigate` 打开列表里给的 url(需要登录时先走 `saving_login`),
    再调本工具。**本工具不导航** —— 导航要在你能看见登录墙/验证码的前提下做。

    返回的 `ok=true` 才是事实: `empty=true` 表示券包确实为空(模块容器在、里面没券),
    这与"读不到"是两回事。`ok=false` 一律**不带** facts 字段, 按 reason 处理:
    先看那个页面的实际地址, 再决定是去登录、去导航, 还是停下告诉用户。

    **不要在这里推断**: 券能不能用在这单上、和国补怎么叠加、到手多少, 都是本体的活。
    本工具只交页面上写着的东西。
    """
    # 平台注册表只有一份: 直接复用 saving_login 的加载与归一, 不在这里另起一套 ——
    # 两份平台定义迟早会对不上, 而对不上的表现就是"登录说登了, 读却说不是那一页"。
    registry = await _login._load_platforms()
    if not registry:
        return _fail(f"没有平台定义; 往 {_login._platforms_dir()} 放 <key>.yaml 即可加平台")

    key = _login._resolve_platform(registry, platform)
    if key is None:
        known = sorted(f"{k}({v.get('name') or k})" for k, v in registry.items())
        return _fail(f"未知平台: {platform!r}", known=known)
    definition = registry[key]
    name = str(definition.get("name") or key)

    raw_reads = definition.get("reads")
    reads: dict[str, Any] = raw_reads if isinstance(raw_reads, dict) else {}
    if not reads:
        return _fail(
            f"{name} 还没有可读页面定义(reads:)",
            platform=_platform_ref(key, name),
            note=f"往 {_login._platforms_dir() / (key + '.yaml')} 的 reads: 下加一条即可, 不用改代码。",
        )

    # -- 不指定 target: 列出可读页面 ------------------------------------------
    target_key = (target or "").strip()
    if not target_key:
        rows = [
            {
                "target": str(t),
                "title": str(d.get("title") or t) if isinstance(d, dict) else str(t),
                "url": str(d.get("url") or "") if isinstance(d, dict) else "",
                "needs_login": bool(definition.get("gate")) if isinstance(d, dict) else False,
                "verified_at": str(d.get("verified_at") or "") if isinstance(d, dict) else "",
                "gives": sorted((d.get("fields") or {}).keys()) if isinstance(d, dict) else [],
            }
            for t, d in sorted(reads.items())
        ]
        payload = {
            "ok": True,
            "action": "list",
            "platform": _platform_ref(key, name),
            "targets": rows,
            "gate": str(definition.get("gate") or definition.get("home") or ""),
            "note": (
                "先 browser_navigate 到某个 target 的 url(要登录就先按 gate 走 saving_login), "
                "再带上 target 调一次本工具读事实。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)

    raw_read = reads.get(target_key)
    if not isinstance(raw_read, dict):
        known_targets = sorted(str(t) for t in reads)
        return _fail(
            f"{name} 没有名为 {target_key!r} 的读定义",
            platform=_platform_ref(key, name),
            known_targets=known_targets,
        )
    read: dict[str, Any] = raw_read

    spec = _spec(read)
    if spec is None:
        return _fail(
            f"读定义 {key}.{target_key} 不完整(需要 item / markers / fields)",
            platform=_platform_ref(key, name),
            note="这是配置问题, 不是页面问题: 改平台 yaml, 别去重试页面。",
        )

    expected = {
        "url": str(read.get("url") or ""),
        "host": str(read.get("host") or ""),
        "markers": spec["markers"],
        "item": spec["item"],
    }
    verified_at = str(read.get("verified_at") or "")
    source = f"saving_read:{key}:{target_key}"

    # -- 读页面 ---------------------------------------------------------------
    try:
        text = await _browser_eval.evaluate(_build_js(spec))
    except _browser_eval.BrowserGoneError as exc:
        return _fail(
            "browser_closed",
            platform=_platform_ref(key, name),
            target=target_key,
            message=str(exc),
            note=("**停下, 把窗口被关这件事告诉用户, 不要擅自重开。** 用户回复继续之后再重新导航、重新读。"),
        )
    except _browser_eval.BrowserEvalError as exc:
        return _fail(
            "browser_unavailable",
            platform=_platform_ref(key, name),
            target=target_key,
            detail=str(exc),
            note='浏览器/驱动这一层没读成。这不是"页面上没有", 别据此下结论; 稍后重试或让用户看窗口状态。',
        )

    data = _parse_payload(text)
    if data is None:
        return _fail(
            "unparsable_extract",
            platform=_platform_ref(key, name),
            target=target_key,
            raw=text[:500],
            note="页面上没拿回可解析的结果(可能页面在读取途中跳走了)。重新导航后再读一次。",
        )

    page = {"url": str(data.get("url") or ""), "title": str(data.get("title") or "")}
    verdict, basis = _verdict(page["url"], definition, read)

    if verdict == "logged_out":
        gate = str(definition.get("gate") or definition.get("home") or "")
        return _fail(
            "logged_out",
            platform=_platform_ref(key, name),
            target=target_key,
            basis=basis,
            page=page,
            gate=gate,
            note=(
                "页面被跳到登录域了, 现在读不到券。先按 saving_login 打开 gate 让用户登录, "
                "登录后再导航回 " + expected["url"] + " 重读。"
            ),
        )
    if verdict != "on_page":
        return _fail(
            "wrong_page",
            platform=_platform_ref(key, name),
            target=target_key,
            basis=basis,
            page=page,
            expected=expected,
            note="当前地址不是读定义里的那一页。先 browser_navigate 到 expected.url, 再调一次本工具。",
        )

    # 身份对了。markers 决定"读到 0 张"能不能当事实。
    markers = data.get("markers")
    marker_counts: dict[str, Any] = markers if isinstance(markers, dict) else {}
    missing = [m for m in spec["markers"] if not marker_counts.get(m)]
    if missing:
        return _fail(
            "page_shape_changed",
            platform=_platform_ref(key, name),
            target=target_key,
            basis="missing_markers",
            page=page,
            expected=expected,
            missing_markers=missing,
            note=(
                '页面在了, 但**应当存在的模块容器找不到** —— 这是"我读不到", 不是"没有券"。'
                "不要据此告诉用户券包是空的。请把这一页截图给维护者核(页面结构可能改了), "
                "或先让用户自己看一眼券包。"
            ),
        )

    raw_coupons = data.get("coupons")
    coupons = [c for c in raw_coupons if isinstance(c, dict)] if isinstance(raw_coupons, list) else []
    count = len(coupons)

    payload = {
        "ok": True,
        "target": target_key,
        "title": str(read.get("title") or target_key),
        "platform": _platform_ref(key, name),
        "page": page,
        "read_at": _now_iso(),
        "source": source,
        "verified_at": verified_at,
        "empty": count == 0,
        "count": count,
        "coupons": coupons,
        "note": (
            "券包确实是空的(模块容器在, 里面没有券)。这是一条事实, 不是读取失败。"
            if count == 0
            else (
                "以上是页面上的原始事实。能不能用在这单上、和国补怎么叠加、到手多少, 都交给本体判定 —— 不要在这里推断。"
            )
        ),
    }
    return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)

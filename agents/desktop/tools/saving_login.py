"""saving_login v1: 省钱场景的**平台授权层**(登录态 / 一次授权 / 可撤销)。

定位: 「agent 以用户身份读账户」这条链路的第一环。读账户的完整形状是

    判断需要读账户 -> 查登录态 -+- 已登录 -> 读页面 -> 事实
                              \\- 未登录/未知 -> 打开登录页 -> 用户登录 -> 续跑

本工具只负责**登录态**这一格。它不驱动浏览器(那是 `browser_*` 的活), 也不读页面。

## 两个数据面, 刻意分开

| 面 | 在哪 | 为什么 |
|---|---|---|
| **平台怎么进** | `<agent>/platforms/<key>.yaml` | 出厂内容; 加平台 = 加文件, 不改代码 |
| **授权记录** | `{appdata}/saving/platforms.json` | 运行期状态; 不是出厂内容, 不该随包发 |

授权记录与 todos / history 同区(记忆区), 因为它记的是**这台机器上这个用户**核过什么,
不是产品的一部分。

## 判定的可信度分层(**保守优先**)

`report` 按平台定义判定, 三类结果的可信度**不同**, 所以返回值带 `basis`:

- 落到 `login_hosts` -> `logged_out`。**可信** —— 跳登录域只有一个原因。
- 到达 `gate` 页本身 -> `logged_in`。**依赖 `gate` 配置正确** —— 若 gate 其实不需要
  登录, 这一条会误判成已登录。所以配置里带 `verified_at`, 由维护者负责。
- 其它一律 `unknown`。**判不准就说判不准**, 不猜。这条与本仓 facts 契约的四态语义同源:
  把"认不出"说成结论, 比说"确认不了"坏得多。

## 一次授权覆盖后续(已拍板)

记录**不自动失效** —— 用户授权一次, 后续访问不再重复询问。但每次查询都返回
`checked_at` / `age_days` / `stale`, 超期只**提示**不改变状态; 要不要重核由调用方决定。
`forget` 用于撤销。
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
import yaml
from loguru import logger

from psi_agent._appdata import resolve_appdata_root

_PLATFORMS_DIRNAME = "platforms"
_RECORD_PARTS = ("saving", "platforms.json")
_RECORD_VERSION = 1

# 超过这个天数只做提示, 不改变状态(尊重「一次授权覆盖后续」)。
_STALE_AFTER_DAYS = 7

_ACTIONS = ("list", "status", "report", "confirm", "blocked", "forget")

# 平台要求人工验证/限流时的**规范话术**。刻意做成常量而不是让每处各写一遍:
# 这条消息要同时说清三件事 —— 发生了什么、为什么我停下、你可以怎么办(含降级出口)。
RATE_LIMIT_MESSAGE = (
    "这个平台要求人工验证了(可能是访问过于频繁, 也可能是它识别到了自动访问)。"
    "为了不让你的账号有风险, 我没有继续尝试, 也不会自动重试。你可以: "
    "① 在弹出的浏览器里手动完成验证, 然后回复「继续」, 我再接着读; "
    "② 或者直接发我一张券页/结算页的截图, 我用截图继续, 不再走浏览器。"
)

# 整个注册表的缓存: 全部平台文件的 (路径, mtime_ns, size) -> 解析结果
_registry_cache: tuple[tuple[tuple[str, int, int], ...], dict[str, dict[str, Any]]] | None = None


# --------------------------------------------------------------------------- #
# 平台定义(出厂内容)
# --------------------------------------------------------------------------- #


def _platforms_dir() -> Path:
    """``<agent>/platforms`` —— 与 ``_fact_cards`` 同源: 本文件所在的那个包。"""
    return Path(__file__).resolve().parents[1] / _PLATFORMS_DIRNAME


async def _load_platforms() -> dict[str, dict[str, Any]]:
    """读全部平台定义, 按 (路径, mtime, size) 失效缓存。

    加平台 = 往 ``platforms/`` 放一个 yaml, 不改代码(扩场景=加数据, 不改能力)。
    """
    global _registry_cache
    directory = _platforms_dir()
    entries: list[tuple[Path, Any]] = []
    try:
        async for path in anyio.Path(str(directory)).glob("*.yaml"):
            real = Path(str(path))
            try:
                stat = await anyio.Path(str(real)).stat()
            except OSError:
                continue
            entries.append((real, stat))
    except OSError:
        entries = []

    signature = tuple(sorted((str(p), s.st_mtime_ns, s.st_size) for p, s in entries))
    if _registry_cache is not None and _registry_cache[0] == signature:
        return _registry_cache[1]

    registry: dict[str, dict[str, Any]] = {}
    for path, _stat in entries:
        try:
            data = yaml.safe_load(await anyio.Path(str(path)).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            logger.warning(f"Skipping platform definition {path}: {exc!r}")
            continue
        if not isinstance(data, dict):
            logger.warning(f"Skipping platform definition {path}: 顶层不是 mapping")
            continue
        key = str(data.get("key") or path.stem).strip()
        if not key:
            logger.warning(f"Skipping platform definition {path}: 缺 key")
            continue
        registry[key] = data
        logger.debug(f"Platform definition loaded: {key} <- {path.name}")
    _registry_cache = (signature, registry)
    return registry


def _resolve_platform(registry: dict[str, dict[str, Any]], token: str) -> str | None:
    """把调用方给的说法(``jd`` / ``京东`` / ``JD``)归一成注册表里的 key。"""
    text = (token or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in registry:
        return lowered
    if text in registry:
        return text
    for key, definition in registry.items():
        if str(definition.get("name") or "").strip() == text:
            return key
    return None


# --------------------------------------------------------------------------- #
# 授权记录(运行期状态)
# --------------------------------------------------------------------------- #


async def _record_path() -> anyio.Path:
    root = await resolve_appdata_root()
    path = anyio.Path(root)
    for part in _RECORD_PARTS[:-1]:
        path = path / part
    return path / _RECORD_PARTS[-1]


async def _read_record(path: anyio.Path) -> dict[str, Any]:
    """读授权记录; 文件缺失或损坏都当空 —— 丢了记录只是让用户被再问一次, 可恢复。"""
    try:
        data = json.loads(await path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {"version": _RECORD_VERSION, "platforms": {}}
    if not isinstance(data, dict) or not isinstance(data.get("platforms"), dict):
        return {"version": _RECORD_VERSION, "platforms": {}}
    return data


async def _write_record(path: anyio.Path, record: dict[str, Any]) -> None:
    """原子写: 先写 tmp 再 rename, 免得多进程/断电留半截 JSON。"""
    await path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    await tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if await path.exists():
        await path.unlink()
    await tmp.rename(path)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _age_days(checked_at: Any) -> float | None:
    if not isinstance(checked_at, str) or not checked_at.strip():
        return None
    try:
        moment = datetime.fromisoformat(checked_at)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return round((datetime.now().astimezone() - moment).total_seconds() / 86400, 2)


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #


def _judge(url: str, definition: dict[str, Any]) -> tuple[str, str]:
    """按平台定义判定 ``(status, basis)``。**判不准就 unknown, 不猜。**

    子域也算命中 ``login_hosts``(``passport.jd.com`` 与 ``x.passport.jd.com`` 同级看待)。
    """
    text = (url or "").strip()
    if not text:
        return "unknown", "no_url"
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return "unknown", "unparsable_url"
    host = (parsed.hostname or "").lower()
    if not host:
        return "unknown", "no_host"

    login_hosts = [str(h).strip().lower() for h in definition.get("login_hosts") or [] if str(h).strip()]
    if any(host == h or host.endswith("." + h) for h in login_hosts):
        return "logged_out", "landed_on_login_host"

    gate = str(definition.get("gate") or "").strip()
    if gate:
        gate_parsed = urllib.parse.urlparse(gate)
        if (gate_parsed.hostname or "").lower() == host:
            # 到达 gate 所在站点还要比路径: gate 失效返回 404 时路径不同, 不能当成功。
            if parsed.path.rstrip("/") == (gate_parsed.path or "").rstrip("/"):
                return "logged_in", "reached_gate"
            return "unknown", "gate_host_but_other_path"
    return "unknown", "unrecognized_url"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _fail(reason: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": False, "reason": reason}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


async def saving_login(
    platform: str = "",
    action: str = "status",
    url: str = "",
    return_json: bool = True,
) -> str:
    """省钱场景的平台授权: 查登录态 / 报告浏览器当前地址 / 确认已登录 / 报告被平台拦 / 列出 / 撤销。

    platform: 平台, 可给 key(jd) 或名称(京东); action=list 时留空表示"列出全部"。
    action: list / status / report / confirm / blocked / forget。
    url: 仅 action=report 用 —— 浏览器**当前地址栏**的地址(不是你要打开的地址)。

    action 语义:
    - list    -> 列出所有平台及各自授权状态(用户要看 agent 记住了哪些)
    - status  -> 查某平台状态。返回 gate 地址供你打开登录; 状态只来自记录, **不探测**
    - report  -> 你把浏览器当前 URL 报进来, 按平台定义判定并写入记录
    - confirm -> 拿不到 URL 时, 由用户明确确认已登录 -> 写入记录
    - blocked -> **你在浏览器里看到平台要求验证码 / 人机校验 / 访问过于频繁时调用**。
                 返回一段规范话术(含"改发截图"的降级出口)并要求你停下 —— 不要自动重试,
                 也不要换个入口再试。用户手动过了验证后照常继续即可。
    - forget  -> 撤销授权(删记录), 之后会重新询问

    判定是保守的: 落到登录域 -> logged_out; 到达 gate 页本身 -> logged_in;
    其它一律 unknown(判不准就说判不准)。记录不自动失效, 但返回 checked_at / stale。

    **风控的检测靠你**(你看得见页面), 工具不做页面特征猜测 —— 猜错会让 agent 在不必停的
    时候停下。工具负责的是规范响应: 每次说的话一样, 且必然带上降级出口。
    """
    action_key = (action or "status").strip().lower()
    if action_key not in _ACTIONS:
        return _fail(f"未知 action: {action!r}", supported=list(_ACTIONS))

    registry = await _load_platforms()
    if not registry:
        return _fail(f"没有平台定义; 往 {_platforms_dir()} 放 <key>.yaml 即可加平台")

    path = await _record_path()
    record = await _read_record(path)
    stored: dict[str, Any] = record["platforms"]

    # -- list: 全部平台的授权状态摘要 -----------------------------------------
    if action_key == "list":
        rows = []
        for key, definition in sorted(registry.items()):
            raw_entry = stored.get(key)
            entry: dict[str, Any] = raw_entry if isinstance(raw_entry, dict) else {}
            age = _age_days(entry.get("checked_at"))
            rows.append(
                {
                    "key": key,
                    "name": definition.get("name") or key,
                    "status": entry.get("status") or "unknown",
                    "checked_at": entry.get("checked_at"),
                    "age_days": age,
                    "stale": bool(age is not None and age > _STALE_AFTER_DAYS),
                }
            )
        payload = {"ok": True, "action": "list", "platforms": rows, "record_path": str(path)}
        return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)

    # -- 其余动作都需要一个具体平台 -------------------------------------------
    key = _resolve_platform(registry, platform)
    if key is None:
        known = sorted(f"{k}({v.get('name') or k})" for k, v in registry.items())
        return _fail(f"未知平台: {platform!r}", known=known)
    definition = registry[key]
    raw_entry = stored.get(key)
    entry: dict[str, Any] = raw_entry if isinstance(raw_entry, dict) else {}

    if action_key == "forget":
        stored.pop(key, None)
        record["version"] = _RECORD_VERSION
        await _write_record(path, record)
        logger.info(f"Saving platform authorization revoked: {key}")
        payload = {
            "ok": True,
            "action": "forget",
            "platform": {"key": key, "name": definition.get("name") or key},
            "status": "unknown",
            "note": "已撤销。下次要读这个平台的账户页时会重新询问。",
        }
        return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)

    if action_key == "report":
        status, basis = _judge(url, definition)
        if status == "unknown":
            # 判不准**不写记录** —— 别让一次认不出的 URL 把状态固化成结论。
            payload = {
                "ok": True,
                "action": "report",
                "platform": {"key": key, "name": definition.get("name") or key},
                "status": "unknown",
                "basis": basis,
                "url": url,
                "note": (
                    "这个地址认不出登录态(既没落到登录域, 也不是 gate 页)。"
                    "请打开 gate 地址后, 把**地址栏**的地址再报一次; 或让用户确认后走 confirm。"
                ),
                "gate": definition.get("gate") or definition.get("home") or "",
            }
            return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)
        entry = {"status": status, "source": f"report:{basis}", "checked_at": _now_iso()}
        stored[key] = entry
        record["version"] = _RECORD_VERSION
        await _write_record(path, record)
        logger.info(f"Saving platform login state recorded: {key}={status} (basis={basis})")
        payload = {
            "ok": True,
            "action": "report",
            "platform": {"key": key, "name": definition.get("name") or key},
            "status": status,
            "basis": basis,
            "checked_at": entry["checked_at"],
            "stale": False,
        }
        return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)

    if action_key == "confirm":
        entry = {"status": "logged_in", "source": "user-confirmed", "checked_at": _now_iso()}
        stored[key] = entry
        record["version"] = _RECORD_VERSION
        await _write_record(path, record)
        logger.info(f"Saving platform login state confirmed by user: {key}")
        payload = {
            "ok": True,
            "action": "confirm",
            "platform": {"key": key, "name": definition.get("name") or key},
            "status": "logged_in",
            "basis": "user-confirmed",
            "checked_at": entry["checked_at"],
            "stale": False,
        }
        return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)

    if action_key == "blocked":
        # 检测**交给正在看着页面的模型**(它看得见验证码/限流页), 工具不做特征猜测 ——
        # 猜 per-platform 的页面特征会误报, 而误报的代价是"让 agent 在不必停的时候停下"。
        # 工具负责的是**规范响应**: 每次说的都一样, 带降级出口, 且明确不自动重试。
        entry = {"status": "blocked", "source": "model-observed", "checked_at": _now_iso()}
        stored[key] = entry
        record["version"] = _RECORD_VERSION
        await _write_record(path, record)
        logger.info(f"Saving platform reported blocked: {key}")
        payload = {
            "ok": True,
            "action": "blocked",
            "platform": {"key": key, "name": definition.get("name") or key},
            "status": "blocked",
            "checked_at": entry["checked_at"],
            "message": RATE_LIMIT_MESSAGE,
            "note": (
                "**停下, 不要自动重试, 不要换个入口再试。** 把 message 原样告诉用户, 然后等他的选择。"
                "用户手动过了验证或改用截图之后, 正常流程会覆盖这条记录 —— 它不是锁。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)

    # -- status: 只读记录, 不探测(尊重「一次授权覆盖后续」) --------------------
    checked_at = entry.get("checked_at")
    age = _age_days(checked_at)
    stale = bool(age is not None and age > _STALE_AFTER_DAYS)
    status = entry.get("status") or "unknown"
    if status == "blocked":
        # 上一条记录是"被平台拦了": 明说不要重试, 并把规范话术再给一次 —— 这是跨调用
        # 仍然有效的那半分, 免得下一轮有人换个入口又试一遍。
        note = (
            "上次访问这个平台时它要求人工验证。**不要自动重试, 也不要换个入口再试。** "
            "请把 rate_limit_message 告诉用户并等他的选择; 用户处理完后正常流程会覆盖这条记录。"
        )
    elif stale:
        note = "记录较旧, 建议重新核实(打开 gate 地址后把地址栏的地址用 report 报回来)。"
    else:
        note = (
            "状态来自记录, 未重新探测。要读账户页就打开 gate 地址; 若发现要登录, "
            "说明记录已过时, 请让用户登录后用 report/confirm 更新。"
        )
    payload = {
        "ok": True,
        "action": "status",
        "platform": {"key": key, "name": definition.get("name") or key},
        "status": status,
        "source": entry.get("source"),
        "checked_at": checked_at,
        "age_days": age,
        "stale": stale,
        "rate_limit_message": RATE_LIMIT_MESSAGE if status == "blocked" else "",
        "gate": definition.get("gate") or definition.get("home") or "",
        "login_hosts": list(definition.get("login_hosts") or []),
        "platform_verified_at": definition.get("verified_at"),
        "note": note,
    }
    return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)

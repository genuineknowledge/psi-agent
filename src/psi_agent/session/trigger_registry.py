"""Event triggers — load TRIGGER.md, match envelopes, fire like schedules.

Parallel to ``ScheduleRegistry``: cron/sleep is replaced by ``POST /events``
push + ``event``/``filter`` match. Fire semantics reuse ``fire=tool|prompt``.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections import OrderedDict
from collections.abc import Callable, Sequence
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
from loguru import logger

from psi_agent._yaml import parse_yaml_header
from psi_agent.session import layer_probe
from psi_agent.session.content_roots import ContentRoot
from psi_agent.session.content_tombstone import is_tombstone
from psi_agent.session.event_protocol import (
    MATCH_ALL,
    EventEnvelope,
    filter_matches,
)
from psi_agent.session.history_display import (
    KIND_TRIGGER_DISPLAY,
    KIND_TRIGGER_SILENT,
    with_kind,
)
from psi_agent.session.protocol import AgentChunk
from psi_agent.session.schedule_registry import FIRE_PROMPT, FIRE_TOOL

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent

_IDEMPOTENCY_MAX = 2048

__all__ = [
    "MATCH_ALL",
    "Trigger",
    "TriggerEntry",
    "TriggerRegistry",
    "merge_event_tool_args",
    "tool_result_is_noop",
]

# Result keys that count as "something actually changed" when non-zero.
# Only ``fire=tool`` results are inspected, and only when the tool reports in
# this shape; anything unrecognised is treated as a change (write it).
_CHANGE_COUNT_KEYS = frozenset(
    {
        "read_advanced",
        "card_updates",
        "updated",
        "created",
        "deleted",
        "sent",
        "advanced",
        "changed",
    }
)


def tool_result_is_noop(result: str) -> bool:
    """True when a ``fire=tool`` result says **nothing changed**.

    件二A 写入准入: a periodic trigger writes 2 history rows per fire whether or
    not it did anything. Those rows carry no information, yet every later turn
    pays to assemble them. So a fire that changed nothing writes nothing.

    Deliberately conservative — it returns ``True`` only for a JSON object that
    (a) reports ``ok: true``, (b) carries at least one recognised change
    counter, (c) has every recognised counter at zero/false, and (d) reports no
    ``errors``. A non-JSON result, an unrecognised shape, a failure, or any
    error entry all mean "write it": swallowing a *failure* would trade a cheap
    history row for an invisible outage.
    """
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError, TypeError:
        return False
    if not isinstance(parsed, dict):
        return False
    if parsed.get("ok") is not True:
        return False
    errors = parsed.get("errors")
    if errors:
        return False
    counters = [key for key in parsed if key in _CHANGE_COUNT_KEYS]
    if not counters:
        return False
    return all(not parsed[key] for key in counters)


def merge_event_tool_args(
    func: Callable[..., Any],
    tool_args: dict[str, Any],
    envelope: EventEnvelope,
) -> dict[str, Any]:
    """Fill envelope fields into tool kwargs when the callable accepts them.

    Intentional: ``fire=tool`` ``tool_args`` in TRIGGER.md are static and cannot
    hard-code each hire's ``open_id``. If the tool declares any of the parameters
    below and YAML left them empty, inject from the envelope so tools like
    ``handbook_onboarding_send_welcome`` can address ``payload.open_id``:

    - ``event_payload_json`` — ``json.dumps(payload)``
    - ``event_name`` — ``envelope.event``
    - ``raw_event`` — ``envelope.raw_event``
    - ``event_source`` — ``envelope.source``
    """
    merged = dict(tool_args)
    try:
        params = inspect.signature(func).parameters
    except TypeError, ValueError:
        return merged
    injections: dict[str, Any] = {
        "event_payload_json": json.dumps(envelope.payload, ensure_ascii=False),
        "event_name": envelope.event,
        "raw_event": envelope.raw_event or "",
        "event_source": envelope.source,
    }
    for key, value in injections.items():
        if key not in params:
            continue
        existing = merged.get(key)
        if existing is None or (isinstance(existing, str) and not existing.strip()):
            merged[key] = value
    return merged


@dataclass
class Trigger:
    """One trigger loaded from agent/triggers/*/TRIGGER.md."""

    name: str
    event: str
    filter: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    task_content: str = ""
    visibility: str = "silent"
    fire: str = FIRE_PROMPT
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    run_once: bool = False
    task_path: str = ""
    # Optional Feishu (or other) native type — matched if normalized ``event`` misses.
    raw_event: str = ""
    raw_filter: dict[str, Any] = field(default_factory=dict)


@dataclass
class TriggerEntry:
    file_hash: str
    trigger: Trigger
    fresh: bool = False
    #: 墓碑标记(B-2, 见 ``content_tombstone``)。墓碑**是**一个已加载的条目而不是被跳过
    #: 的文件: 它要参与跨层合并才能让更远那层的同名 trigger 落选。但它绝不能触发, 所以
    #: ``triggers`` 把它滤掉 —— 一个"停用标记"自己跑起来是最坏的形状。
    tombstone: bool = False


class TriggerRegistry:
    """Owns trigger configs under ``agent/triggers/`` (no cron runners)."""

    def __init__(
        self,
        *,
        files: dict[str, TriggerEntry] | None = None,
        work_dir: Path | None = None,
    ) -> None:
        self._files: dict[str, TriggerEntry] = dict(files or {})
        self._work_dir = work_dir
        # idempotency_key → True; OrderedDict for FIFO eviction
        self._seen_keys: OrderedDict[str, bool] = OrderedDict()

    @property
    def triggers(self) -> list[Trigger]:
        """The triggers that can fire — tombstones excluded.

        Tombstones live in ``_files`` (they have to, so the layered merge can let
        one shadow a lower root's same-named trigger by name), but they are
        *absences*: a "this is deleted" marker that fires is worse than a failed
        delete, because it fires with an empty body on whatever event the marker
        happened to carry.
        """
        return [e.trigger for e in self._files.values() if not e.tombstone]

    @classmethod
    async def load(cls, triggers_dir: Path) -> TriggerRegistry:
        """Load from *triggers_dir* alone — the single-root entry point.

        Kept as-is for every caller that has one directory and no notion of
        layers (tests, ``run_once``, tools). ``load_content_roots`` is the
        layered entry point ``SessionAgent`` reaches for.
        """
        files = await cls._load_from_dir(triggers_dir)
        seen: list[tuple[str, int]] = []
        if await anyio.Path(str(triggers_dir)).is_dir():
            seen.append((layer_probe.root_name(triggers_dir), len(files)))
        layer_probe.report("triggers", roots_declared=1, per_root=seen)
        return cls(files=files, work_dir=triggers_dir)

    @classmethod
    async def load_content_roots(cls, roots: Sequence[ContentRoot]) -> TriggerRegistry:
        """Load ``<root>/triggers`` across *roots*, nearest-wins by trigger name.

        Merge semantics, matching skills: a trigger in a nearer root **replaces**
        the same-named one from a lower root entirely — no field-level merge.
        Merging fields would synthesise a third trigger nobody wrote, and a
        trigger carries a ``filter`` and a ``fire`` target, so a half-overridden
        one fires the wrong thing on the wrong events.

        Identity is the trigger **name**, not the file path. Paths are unique per
        root by construction, so keying on them (as ``_files`` does) would let
        both copies live and fire twice — the override would silently *add* a
        firing rather than replace one.

        *roots* is ascending in priority (see ``content_roots``); the highest
        root is the one ``refresh()`` reloads and the one ``work_dir`` points at,
        because it is the only one a user can write to.
        """
        ordered = sorted(roots, key=lambda root: root.priority)
        by_name: dict[str, tuple[str, TriggerEntry]] = {}
        per_root: list[tuple[str, int]] = []
        for root in ordered:
            triggers_dir = root.path / "triggers"
            loaded = await cls._load_from_dir(triggers_dir)
            # 只报**存在**的层: "这层没有 triggers 目录"与"有目录但空"是两件事,
            # 压成同一个 0 就是 B-0 探针存在的那个坑(见 ``layer_probe``)。
            if await anyio.Path(str(triggers_dir)).is_dir():
                per_root.append((root.name, len(loaded)))
            for path, entry in loaded.items():
                by_name[entry.trigger.name] = (path, entry)
        files = dict(by_name.values())
        # per_root 报各层**贡献**(去重前), 与合并后的 total 不等即说明发生了同名
        # 覆盖 —— 覆盖行为得看得见, 否则"近的赢了"和"远的没被读到"同形。
        layer_probe.report("triggers", roots_declared=len(ordered), per_root=per_root)
        work_dir = ordered[-1].path / "triggers" if ordered else None
        return cls(files=files, work_dir=work_dir)

    async def refresh(self) -> dict[str, str]:
        try:
            return await self._do_refresh()
        except Exception:
            logger.warning("Failed to refresh triggers")
            return {}

    async def _do_refresh(self) -> dict[str, str]:
        if self._work_dir is None:
            logger.warning("No work_dir set, cannot refresh triggers")
            return {}
        logger.debug("Starting trigger refresh")
        new_files = await self._load_from_dir(self._work_dir, self._files)
        result: dict[str, str] = {}
        for path in list(self._files):
            if path not in new_files:
                name = self._files[path].trigger.name
                result[name] = "removed"
                del self._files[path]
        for path, new_entry in new_files.items():
            old = self._files.get(path)
            name = new_entry.trigger.name
            if old is None:
                result[name] = "added"
                self._files[path] = new_entry
            elif not new_entry.fresh:
                result[name] = "skipped"
            else:
                result[name] = "updated"
                self._files[path] = new_entry
        logger.info(f"Trigger refresh complete: {result or 'no changes'}")
        return result

    def match(self, envelope: EventEnvelope) -> list[Trigger]:
        """Return triggers matching *envelope*, stable-sorted by name.

        Matching order per trigger (二者兼得):
        1. Normalized: ``trigger.event`` == ``envelope.event`` + ``filter`` vs ``payload``
        2. Else raw: ``trigger.raw_event`` == ``envelope.raw_event`` + ``raw_filter``
           vs ``raw_payload`` (falls back to ``payload`` when raw_payload empty)

        **The raw path cannot be wider than the normalized one** (刻意为之).
        An empty filter no longer matches everything (see
        ``event_protocol.filter_matches``), so a trigger that narrows ``filter``
        but leaves ``raw_filter`` empty can no longer fall through the raw path
        and match every event — the exact 2026-09-02 production failure.
        """
        hits: list[Trigger] = []
        for trigger in self.triggers:
            if trigger.source and trigger.source != envelope.source:
                continue
            matched = False
            if trigger.event and trigger.event == envelope.event and filter_matches(envelope.payload, trigger.filter):
                matched = True
            if not matched and trigger.raw_event:
                env_raw = (envelope.raw_event or "").strip()
                if trigger.raw_event == env_raw and env_raw:
                    raw_body = envelope.raw_payload if envelope.raw_payload else envelope.payload
                    if filter_matches(raw_body, trigger.raw_filter):
                        matched = True
            if matched:
                hits.append(trigger)
        hits.sort(key=lambda t: t.name)
        return hits

    def remember_idempotency(self, key: str) -> bool:
        """Return True if *key* is new; False if already seen (duplicate)."""
        if not key:
            return True
        if key in self._seen_keys:
            return False
        self._seen_keys[key] = True
        while len(self._seen_keys) > _IDEMPOTENCY_MAX:
            self._seen_keys.popitem(last=False)
        return True

    async def dispatch(self, envelope: EventEnvelope, agent: SessionAgent) -> list[str]:
        """Match and fire all hits under *agent*'s lock (caller may already hold it).

        Returns names of triggers that fired.
        """
        await self.refresh()
        if envelope.idempotency_key and not self.remember_idempotency(envelope.idempotency_key):
            logger.info(f"Duplicate event idempotency_key={envelope.idempotency_key!r}; skipping")
            return []

        hits = self.match(envelope)
        fired: list[str] = []
        for trigger in hits:
            response_kind = KIND_TRIGGER_DISPLAY if trigger.visibility == "display" else KIND_TRIGGER_SILENT
            try:
                if trigger.fire == FIRE_TOOL:
                    await TriggerRegistry._fire_tool(trigger, agent, response_kind, envelope)
                else:
                    await TriggerRegistry._fire_prompt(trigger, agent, response_kind, envelope)
                fired.append(trigger.name)
                logger.info(f"Trigger {trigger.name!r} fired (event={envelope.event!r}, fire={trigger.fire!r})")
                if trigger.run_once:
                    await TriggerRegistry._consume_run_once(trigger, self)
            except Exception as e:
                logger.error(f"Trigger {trigger.name!r} failed: {e!r}")
        return fired

    @staticmethod
    async def _fire_prompt(
        trigger: Trigger,
        agent: SessionAgent,
        response_kind: str,
        envelope: EventEnvelope,
    ) -> list[AgentChunk]:
        body = trigger.task_content.strip() or (
            f"[trigger] {trigger.name}\nevent={envelope.event}\n"
            f"payload={json.dumps(envelope.payload, ensure_ascii=False)}"
        )
        user_msg = with_kind({"role": "user", "content": body}, KIND_TRIGGER_SILENT)
        pending: list[AgentChunk] = []
        async with aclosing(agent.run(user_msg, response_kind=response_kind)) as chunks:
            async for chunk in chunks:
                pending.append(chunk)
        if trigger.visibility == "display" and pending:
            agent.set_pending_schedule_chunks(pending)
        return pending

    @staticmethod
    async def _fire_tool(
        trigger: Trigger,
        agent: SessionAgent,
        response_kind: str,
        envelope: EventEnvelope,
    ) -> list[AgentChunk]:
        await agent.reload_tools()
        tool_name = trigger.tool_name.strip()
        args = dict(trigger.tool_args)
        logger.info(f"Trigger tool fire: {trigger.name!r} → {tool_name!r}({args!r}) event={envelope.event!r}")

        chunks: list[AgentChunk] = []
        # 刻意为之: the tool runs **before** anything is written. The old order
        # committed the user row first (a crash-recovery baseline, as in
        # ``agent.run``), but a ``fire=tool`` trigger has no LLM turn to resume
        # and its rows are pure record-keeping — and the baseline forced a write
        # even when the tool turned out to change nothing. Trade-off: a crash
        # mid-tool now leaves no trace in history (the fire is still logged).
        func = agent._tool_registry.get(tool_name) if tool_name else None
        if func is None:
            result = f"Error: Tool {tool_name!r} not found"
            logger.error(f"Trigger {trigger.name!r}: {result}")
        else:
            args = merge_event_tool_args(func, args, envelope)
            try:
                raw = await func(**args)
                result = str(raw)
                logger.info(f"Trigger tool result ({tool_name!r}): {result[:1000]!r}")
            except Exception as e:
                result = f"Error executing tool {tool_name!r}: {e}"
                logger.error(f"Trigger {trigger.name!r} tool error: {e!r}")

        chunks.append(AgentChunk(reasoning=f"[Tool Call: {tool_name}({json.dumps(args, ensure_ascii=False)})]"))
        chunks.append(AgentChunk(reasoning=f"[Tool Result: {result[:1000]}]"))
        if trigger.visibility == "display":
            chunks.append(AgentChunk(content=result[:2000]))

        if tool_result_is_noop(result):
            # 件二A 写入准入: nothing changed → nothing written. Skipping both
            # rows keeps the history prefix byte-identical, so the next turn
            # still hits the upstream prefix cache.
            logger.info(f"Trigger {trigger.name!r} tool {tool_name!r} reported no changes; skipping history write")
        else:
            async with agent._conversation:
                agent._conversation.add(
                    with_kind(
                        {
                            "role": "user",
                            "content": (
                                f"[trigger tool] {trigger.name}: call {tool_name}\n"
                                f"event={envelope.event} "
                                f"payload={json.dumps(envelope.payload, ensure_ascii=False)}"
                                + (f"\n{trigger.task_content}" if trigger.task_content else "")
                            ),
                        },
                        KIND_TRIGGER_SILENT,
                    )
                )
                agent._conversation.add(
                    with_kind(
                        {
                            "role": "assistant",
                            "content": (
                                f"[trigger tool {tool_name}] {result[:3500]}"
                                if trigger.visibility == "display"
                                else f"[trigger tool {tool_name}] ok"
                            ),
                        },
                        response_kind,
                    )
                )
                await agent._conversation.commit()

        if trigger.visibility == "display" and chunks:
            agent.set_pending_schedule_chunks(chunks)
        return chunks

    @staticmethod
    async def _consume_run_once(trigger: Trigger, registry: TriggerRegistry) -> None:
        path_str = trigger.task_path
        if not path_str:
            logger.warning(f"run_once trigger {trigger.name!r} has no task_path; cannot delete")
            return
        path = anyio.Path(path_str)
        with anyio.CancelScope(shield=True):
            try:
                if await path.exists():
                    await path.unlink()
                    parent = path.parent
                    with suppress(OSError):
                        await parent.rmdir()
                    logger.info(f"run_once trigger {trigger.name!r} removed {path_str!r}")
            except Exception as e:
                logger.error(f"run_once cleanup failed for trigger {trigger.name!r}: {e!r}")
            finally:
                registry._files.pop(path_str, None)

    @staticmethod
    async def _load_from_dir(
        triggers_dir: Path,
        old_files: dict[str, TriggerEntry] | None = None,
    ) -> dict[str, TriggerEntry]:
        files: dict[str, TriggerEntry] = {}
        root = anyio.Path(str(triggers_dir))
        try:
            if not await root.is_dir():
                logger.debug(f"Triggers directory not found: {triggers_dir!r}")
                return files
        except Exception as e:
            logger.warning(f"Cannot access triggers directory {triggers_dir!r}: {e!r}")
            return files

        async for task_dir in root.iterdir():
            try:
                dir_path = anyio.Path(str(task_dir))
                if not await dir_path.is_dir():
                    continue
                task_file = dir_path / "TRIGGER.md"
                if not await task_file.is_file():
                    continue
                str_path = str(task_file)
                content = await task_file.read_text(encoding="utf-8")
                file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if old_files is not None:
                    old = old_files.get(str_path)
                    if old is not None and old.file_hash == file_hash:
                        files[str_path] = TriggerEntry(file_hash=file_hash, trigger=old.trigger, fresh=False)
                        continue

                header, body = parse_yaml_header(content)
                if header is None:
                    logger.error(f"No YAML header in {task_file!r}; skipping")
                    continue
                name = str(header.get("name") or dir_path.name).strip()
                # 墓碑要在 ``event`` 检查**之前**认出来: 它本来就没有 event(它不代表一个
                # 会触发的东西), 走到下面那条 continue 就会被当成损坏文件跳过 —— 于是它
                # 不进 ``_files``, 也就遮不住更远那层的同名 trigger, 删除静默失效。
                if is_tombstone(header):
                    files[str_path] = TriggerEntry(
                        file_hash=file_hash,
                        trigger=Trigger(name=name, event=""),
                        fresh=True,
                        tombstone=True,
                    )
                    logger.debug(f"Loaded tombstone for trigger {name!r} from {task_file!r}")
                    continue
                event = str(header.get("event") or "").strip()
                if not event:
                    logger.error(f"Missing event in {task_file!r}; skipping")
                    continue

                raw_source = header.get("source", "")
                source = str(raw_source).strip().casefold() if isinstance(raw_source, str) else ""

                raw_filter = header.get("filter", {})
                filt: dict[str, Any] = dict(raw_filter) if isinstance(raw_filter, dict) else {}

                platform_raw_event = str(header.get("raw_event") or "").strip()
                raw_filter_hdr = header.get("raw_filter", {})
                raw_filt: dict[str, Any] = dict(raw_filter_hdr) if isinstance(raw_filter_hdr, dict) else {}

                visibility = str(header.get("visibility") or "silent").strip().casefold()
                if visibility not in {"display", "silent"}:
                    visibility = "silent"

                raw_fire = header.get("fire", FIRE_PROMPT)
                fire = str(raw_fire).strip().casefold() if isinstance(raw_fire, str) else FIRE_PROMPT
                if fire not in {FIRE_PROMPT, FIRE_TOOL}:
                    fire = FIRE_PROMPT

                tool_name = ""
                tool_args: dict[str, Any] = {}
                if fire == FIRE_TOOL:
                    tool_name = str(header.get("tool") or "").strip()
                    raw_args = header.get("tool_args", {})
                    if isinstance(raw_args, str):
                        try:
                            parsed = json.loads(raw_args)
                        except json.JSONDecodeError as e:
                            logger.error(f"Invalid tool_args JSON in {task_file!r}: {e!r}")
                            continue
                        if not isinstance(parsed, dict):
                            logger.error(f"tool_args in {task_file!r} must be an object")
                            continue
                        tool_args = parsed
                    elif isinstance(raw_args, dict):
                        tool_args = dict(raw_args)
                    if not tool_name:
                        logger.error(f"fire=tool trigger {name!r} missing tool; skipping")
                        continue

                raw_once = header.get("run_once", False)
                if isinstance(raw_once, str):
                    run_once = raw_once.strip().casefold() in {"1", "true", "yes", "on"}
                else:
                    run_once = bool(raw_once)

                trigger = Trigger(
                    name=name,
                    event=event,
                    filter=filt,
                    source=source,
                    task_content=body.strip(),
                    visibility=visibility,
                    fire=fire,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    run_once=run_once,
                    task_path=str_path,
                    raw_event=platform_raw_event,
                    raw_filter=raw_filt,
                )
                files[str_path] = TriggerEntry(file_hash=file_hash, trigger=trigger, fresh=True)
                logger.debug(f"Loaded trigger: {name!r} (event={event!r}, fire={fire!r}, filter={filt!r})")
            except Exception as e:
                logger.error(f"Failed to load trigger from {task_dir!r}: {e!r}")
        return files

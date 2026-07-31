"""FusionFlow 运行时的运行目录、绑定与恢复支持。"""

from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from hashlib import sha256
from importlib import import_module
from os import PathLike
from secrets import choice
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import anyio
from anyio.lowlevel import checkpoint_if_cancelled
from loguru import logger

from .model import (
    ExecutionTrace,
    RunResult,
    SessionRunner,
    TraceKind,
    TraceStatus,
    aggregate_tokens,
    assert_safe_name,
)

if TYPE_CHECKING:
    from .flow import Flow

type Program = Callable[[RunContext], Awaitable[object]]
type PathValue = str | PathLike[str] | anyio.Path

_CURRENT_RUN: ContextVar[RunContext | None] = ContextVar(
    "fusion_flow_current_run",
    default=None,
)
_CURRENT_TRACE: ContextVar[ExecutionTrace | None] = ContextVar(
    "fusion_flow_current_trace",
    default=None,
)


def _now_iso() -> str:
    """返回 UTC 的 ISO 8601 时间戳。"""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _make_run_id() -> str:
    """生成便于排序且带随机后缀的运行标识。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "".join(choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(6))
    return f"{stamp}-{suffix}"


def _error_text(error: BaseException) -> str:
    """提取异常的非空可读文本。"""
    text = str(error)
    return text or error.__class__.__name__


def stable_payload_hash(value: object) -> str:
    """为可 JSON 序列化值生成稳定的 SHA-256 摘要。"""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


async def _atomic_write_bytes(path: anyio.Path, value: bytes) -> None:
    """以原子替换方式写入字节。"""
    # 临时文件与目标文件同目录. 确保替换可保持原子性。
    temporary = anyio.Path(path.parent, f".{path.name}.tmp-{uuid4().hex}")
    try:
        await temporary.write_bytes(value)
        # 完整写入后再原子替换目标文件。
        await temporary.replace(path)
    finally:
        with anyio.CancelScope(shield=True):
            # 清理未被替换的临时文件。
            try:
                if await temporary.exists():
                    await temporary.unlink()
            except Exception as cleanup_error:
                logger.warning(
                    f'Failed to clean temporary FusionFlow file "{temporary}": {cleanup_error}',
                )


async def _atomic_write_text(path: anyio.Path, value: str) -> None:
    """以原子替换方式写入 UTF-8 文本文件。"""
    await _atomic_write_bytes(path, value.encode("utf-8"))


async def _atomic_write_json(
    path: anyio.Path,
    value: Mapping[str, object],
) -> None:
    """以格式化 JSON 原子写入映射数据。"""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    await _atomic_write_text(path, f"{payload}\n")


async def _remove_tree(path: anyio.Path) -> None:
    """递归删除目录树. 但不跟随符号链接。"""
    if await path.is_symlink():
        await path.unlink()
        return
    if await path.is_junction():
        await path.rmdir()
        return
    async for child in path.iterdir():
        if await child.is_symlink():
            await child.unlink()
        elif not await child.is_dir():
            try:
                await child.unlink()
            except PermissionError:
                await child.chmod(0o700)
                await child.unlink()
        elif await child.is_junction():
            await child.rmdir()
        else:
            await _remove_tree(child)
    await path.rmdir()


async def _resolve_direct_child(
    path: anyio.Path,
    parent: anyio.Path,
    *,
    label: str,
) -> anyio.Path:
    """验证路径是父目录下的非链接直接子项。"""
    if await path.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link")
    resolved = await path.resolve()
    if resolved != anyio.Path(parent, path.name):
        raise ValueError(f"{label} escapes its parent directory")
    return resolved


async def _ensure_run_subdirectory(
    run_dir: anyio.Path,
    name: str,
) -> anyio.Path:
    """确保运行目录的指定直接子目录存在且安全。"""
    path = anyio.Path(run_dir, name)
    if await path.exists():
        if not await path.is_dir():
            raise ValueError(f'run path "{name}" must be a directory')
    else:
        await path.mkdir()
    return await _resolve_direct_child(
        path,
        run_dir,
        label=f'run path "{name}"',
    )


async def _validate_existing_run_subdirectory(
    run_dir: anyio.Path,
    name: str,
) -> None:
    """只读验证已有运行子目录, 缺失目录留到预检完成后创建。"""
    path = anyio.Path(run_dir, name)
    if not await path.exists():
        return
    if not await path.is_dir():
        raise ValueError(f'run path "{name}" must be a directory')
    await _resolve_direct_child(
        path,
        run_dir,
        label=f'run path "{name}"',
    )


class RunContext:
    """单次 ``run()`` 生命周期内有效的可变运行状态。

    绑定名称只能单次赋值。名称预留和 trace 子节点等共享状态由 ``_lock`` 保护。
    """

    def __init__(
        self,
        *,
        run_id: str,
        run_dir: anyio.Path,
        inputs: Mapping[str, str],
        runner: SessionRunner | None,
        root_trace: ExecutionTrace,
        resumed: bool,
        resume_bindings: Mapping[str, str],
    ) -> None:
        """使用已创建的运行目录和恢复状态初始化上下文。"""
        self.run_id = run_id
        self.run_dir = str(run_dir)
        self.runner = runner
        self.root_trace = root_trace
        self.resumed = resumed
        self._path = run_dir
        self._inputs = dict(inputs)
        self._resume_bindings = dict(resume_bindings)
        self._resume_metadata: dict[str, dict[str, object]] = {}
        self._services: dict[str, object] = {}
        self._blocks: dict[str, object] = {}
        self._input_names: set[str] = set()
        # Resume bindings are cache inputs, not writes performed by this run.
        # A cache miss may replace one once; a second current-run write still
        # fails through this set.
        self._binding_names: set[str] = set()
        self._binding_reservations: set[str] = set()
        self._call_ordinals: dict[str, set[int]] = {}
        self._call_ordinal_reservations: set[tuple[str, int]] = set()
        self._session_call_counts: dict[str, int] = {}
        self._service_call_counts: dict[str, int] = {}
        self._progress_started: set[str] = set()
        self._progress_finished: set[str] = set()
        self._lock = anyio.Lock()
        self._sealed = False

    async def input(self, name: str, default_value: str) -> str:
        """读取并持久化一个可被运行注入值覆盖的具名输入。"""
        self._ensure_open()
        normalized = assert_safe_name(name)
        async with self._trace("input", normalized) as trace:
            value = await self._read_input(normalized, default_value)
            trace.output_summary = value
            return value

    async def save(self, name: str, value: str) -> None:
        """通过单赋值路径持久化一个具名绑定。"""
        self._ensure_open()
        normalized = assert_safe_name(name)
        await self._commit_binding(
            normalized,
            value,
            metadata=self._binding_metadata(
                normalized,
                produced_by="flow.output",
                operation="output",
            ),
        )

    @property
    def services(self) -> dict[str, object]:
        """返回当前运行注册的服务表。"""
        return self._services

    @property
    def blocks(self) -> dict[str, object]:
        """返回当前运行注册的块表。"""
        return self._blocks

    @property
    def flow(self) -> Flow:
        """返回包级 ``flow`` API, 与作为参数传入的 context 绑定到同一运行。"""
        # 延迟解析避免 runtime 与 flow 在模块初始化阶段形成循环依赖。
        module = import_module(".flow", __package__)
        return cast("Flow", module.flow)

    def _ensure_open(self) -> None:
        """确认上下文尚未封存。"""
        if self._sealed:
            raise RuntimeError("run context is sealed")

    def _binding_metadata(
        self,
        name: str,
        *,
        produced_by: str,
        tokens: Mapping[str, int | None] | None = None,
        **details: object,
    ) -> dict[str, object]:
        """构建绑定持久化和恢复校验所需的元数据。"""
        trace = _CURRENT_TRACE.get() or self.root_trace
        produced_at = _now_iso()
        metadata: dict[str, object] = {
            "name": name,
            "produced_by": produced_by,
            "produced_at": produced_at,
            "source_node": trace.trace_id,
        }
        if tokens is not None:
            metadata["tokens"] = dict(tokens)
        metadata.update(details)
        return metadata

    async def _read_input(self, name: str, default_value: str) -> str:
        """读取一次输入并在成功后写入运行目录。"""
        normalized = assert_safe_name(name)
        if not isinstance(default_value, str):
            raise TypeError("input default_value must be a string")
        async with self._lock:
            self._ensure_open()
            if normalized in self._input_names:
                raise ValueError(f'input "{normalized}" was already read')
            self._input_names.add(normalized)

        value = self._inputs.get(normalized, default_value)
        if not isinstance(value, str):
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self._input_names.discard(normalized)
            raise TypeError(f'input "{normalized}" must be a string')
        try:
            await _atomic_write_text(
                anyio.Path(self._path, "input", f"{normalized}.md"),
                value,
            )
        except BaseException:
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self._input_names.discard(normalized)
            raise
        return value

    async def _reserve_binding(self, name: str) -> str:
        """预留一个尚未存在的绑定名称。"""
        normalized = assert_safe_name(name)
        async with self._lock:
            self._ensure_open()
            if normalized in self._binding_names or normalized in self._binding_reservations:
                raise ValueError(f'binding "{normalized}" already exists')
            # 锁内预留可避免并发调用获得同名绑定。
            self._binding_reservations.add(normalized)
        return normalized

    async def _release_binding(
        self,
        name: str,
        *,
        call_base: str | None = None,
        call_count: int | None = None,
    ) -> None:
        """释放未提交的名称和可选调用序号预留, 以便后续重试。"""
        with anyio.CancelScope(shield=True):
            async with self._lock:
                self._binding_reservations.discard(name)
                if call_base is not None and call_count is not None:
                    reservation = (call_base, call_count)
                    if reservation in self._call_ordinal_reservations:
                        self._call_ordinal_reservations.remove(reservation)
                        self._call_ordinals[call_base].discard(call_count)

    async def _reserve_auto_binding(self, base: str) -> str:
        """预留基名或带递增后缀的第一个可用绑定名称。"""
        normalized = assert_safe_name(base)
        suffix = 1
        while True:
            candidate = normalized if suffix == 1 else f"{normalized}.{suffix}"
            try:
                return await self._reserve_binding(candidate)
            except ValueError:
                suffix += 1

    async def _reserve_call_binding(
        self,
        base: str,
        binding_name: str | None,
        *,
        ordinal_base: str | None = None,
    ) -> tuple[str, str, int]:
        """预留调用 binding, 但把序号推进留到缓存命中或成功落盘时。"""
        normalized = assert_safe_name(base)
        ordinal = assert_safe_name(ordinal_base or base)
        explicit = assert_safe_name(binding_name) if binding_name is not None else None
        async with self._lock:
            self._ensure_open()
            ordinals = self._call_ordinals.setdefault(ordinal, set())
            count = 1
            while True:
                if count in ordinals:
                    count += 1
                    continue
                candidate = explicit or (normalized if count == 1 else f"{normalized}.{count}")
                if candidate not in self._binding_names and candidate not in self._binding_reservations:
                    break
                if explicit is not None:
                    raise ValueError(f'binding "{candidate}" already exists')
                count += 1
            # 名称先锁定, 但序号仍是临时值; 失败重试会再次取得同一序号。
            self._binding_reservations.add(candidate)
            ordinals.add(count)
            self._call_ordinal_reservations.add((ordinal, count))
        return candidate, ordinal, count

    async def _commit_reserved_call(
        self,
        name: str,
        base: str,
        count: int,
        *,
        call_owner: str | None = None,
        service_call: bool = False,
    ) -> None:
        """提交一次缓存命中的调用身份, 不重复写已有 binding 文件。"""
        if service_call and call_owner is None:
            raise RuntimeError("service_call requires call_owner")
        with anyio.CancelScope(shield=True):
            async with self._lock:
                self._ensure_open()
                reservation = (base, count)
                if reservation not in self._call_ordinal_reservations:
                    raise RuntimeError(f'call ordinal for "{base}" is not reserved')
                self._call_ordinal_reservations.remove(reservation)
                self._binding_reservations.remove(name)
                self._binding_names.add(name)
                if call_owner is not None:
                    self._increment_call_count(
                        call_owner,
                        service=service_call,
                    )

    def _increment_call_count(
        self,
        owner: str,
        *,
        service: bool,
    ) -> None:
        """在持有运行锁时记录一次成功或缓存命中的调用。"""
        counts = self._service_call_counts if service else self._session_call_counts
        counts[owner] = counts.get(owner, 0) + 1

    async def _commit_reserved_binding(
        self,
        name: str,
        value: str,
        *,
        metadata: Mapping[str, object] | None = None,
        call_base: str | None = None,
        call_count: int | None = None,
        call_owner: str | None = None,
        service_call: bool = False,
    ) -> None:
        """将已预留的绑定和元数据全部落盘后提交。"""
        if (call_base is None) != (call_count is None):
            await self._release_binding(name)
            raise RuntimeError("call_base and call_count must be provided together")
        if service_call and call_owner is None:
            await self._release_binding(
                name,
                call_base=call_base,
                call_count=call_count,
            )
            raise RuntimeError("service_call requires call_owner")
        if not isinstance(value, str):
            await self._release_binding(
                name,
                call_base=call_base,
                call_count=call_count,
            )
            raise TypeError("binding value must be a string")

        metadata_payload = dict(metadata or {})
        trace = _CURRENT_TRACE.get() or self.root_trace
        metadata_payload.setdefault("name", name)
        metadata_payload.setdefault(
            "produced_by",
            str(metadata_payload.get("operation", "run")),
        )
        metadata_payload.setdefault("produced_at", _now_iso())
        metadata_payload.setdefault("source_node", trace.trace_id)
        try:
            json.dumps(metadata_payload)
        except (TypeError, ValueError) as error:
            await self._release_binding(
                name,
                call_base=call_base,
                call_count=call_count,
            )
            raise TypeError("binding metadata must be JSON serializable") from error

        binding_path = anyio.Path(self._path, "bindings", f"{name}.md")
        metadata_path = anyio.Path(self._path, "bindings", f"{name}.meta.json")
        previous_value = self._resume_bindings.get(name)
        previous_metadata = self._resume_metadata.get(name)
        try:
            self._ensure_open()
            await _atomic_write_text(binding_path, value)
            # Metadata is the commit marker: it must never describe content
            # that has not reached its binding file yet.
            await _atomic_write_json(
                metadata_path,
                metadata_payload,
            )

            async with self._lock:
                self._ensure_open()
                if call_base is not None and call_count is not None:
                    reservation = (call_base, call_count)
                    if reservation not in self._call_ordinal_reservations:
                        raise RuntimeError(
                            f'call ordinal for "{call_base}" is not reserved',
                        )
                    self._call_ordinal_reservations.remove(reservation)
                # 内容与元数据全部落盘后, 才提交名称、序号和新的恢复快照。
                self._binding_reservations.remove(name)
                self._binding_names.add(name)
                self._resume_bindings[name] = value
                self._resume_metadata[name] = metadata_payload
                if call_owner is not None:
                    self._increment_call_count(
                        call_owner,
                        service=service_call,
                    )
        except BaseException:
            with anyio.CancelScope(shield=True):
                # Every rollback step is attempted, but none may replace the
                # original write error or cancellation.
                binding_restored = False
                try:
                    if previous_value is None:
                        if await binding_path.exists():
                            await binding_path.unlink()
                    else:
                        await _atomic_write_text(binding_path, previous_value)
                    binding_restored = True
                except Exception as rollback_error:
                    logger.error(f'Failed to restore binding "{name}": {rollback_error}')
                try:
                    if not binding_restored or previous_metadata is None:
                        if await metadata_path.exists():
                            await metadata_path.unlink()
                    else:
                        await _atomic_write_json(metadata_path, previous_metadata)
                except Exception as rollback_error:
                    logger.error(f'Failed to restore metadata for binding "{name}": {rollback_error}')
                    try:
                        if await metadata_path.exists():
                            await metadata_path.unlink()
                    except Exception as cleanup_error:
                        logger.error(
                            f'Failed to remove metadata for binding "{name}": {cleanup_error}',
                        )
                try:
                    await self._release_binding(
                        name,
                        call_base=call_base,
                        call_count=call_count,
                    )
                except Exception as rollback_error:
                    logger.error(f'Failed to release binding "{name}": {rollback_error}')
            raise

    async def _commit_binding(
        self,
        name: str,
        value: str,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """预留名称并通过统一的提交路径保存绑定。"""
        normalized = await self._reserve_binding(name)
        await self._commit_reserved_binding(
            normalized,
            value,
            metadata=metadata,
        )

    def _resume_binding(self, name: str) -> str | None:
        """返回恢复目录中同名绑定的内容。"""
        return self._resume_bindings.get(name)

    def _resume_binding_metadata(self, name: str) -> dict[str, object] | None:
        """返回恢复目录中同名绑定元数据的副本。"""
        payload = self._resume_metadata.get(name)
        if payload is None:
            return None
        return dict(payload)

    def _resume_lookup(
        self,
        binding_name: str,
        *,
        cache_key: str | None,
        operation: str,
    ) -> str | None:
        """按操作和可选缓存键查找可复用的恢复绑定。"""
        # 自动绑定只解决命名冲突. 恢复查找还须匹配操作和缓存键。
        cached = self._resume_binding(binding_name)
        if cached is None:
            return None
        metadata = self._resume_binding_metadata(binding_name)
        if metadata is None:
            return None
        stored_operation = metadata.get("operation")
        if stored_operation is not None and stored_operation != operation:
            return None
        if cache_key is not None:
            stored_cache_key = metadata.get("cache_key")
            if stored_cache_key != cache_key:
                return None
        return cached

    def _register(
        self,
        registry: dict[str, object],
        name: str,
        value: object,
        *,
        kind: str,
    ) -> str:
        """向注册表加入唯一的安全名称和值。"""
        normalized = assert_safe_name(name)
        self._ensure_open()
        if normalized in registry:
            raise ValueError(f'{kind} "{normalized}" is already defined')
        registry[normalized] = value
        return normalized

    async def _append_child(
        self,
        parent: ExecutionTrace,
        child: ExecutionTrace,
    ) -> None:
        """在锁内把子 trace 接到父 trace, 保持执行图结构一致。"""
        async with self._lock:
            self._ensure_open()
            parent.children = (*parent.children, child)

    async def _record_progress(
        self,
        trace: ExecutionTrace,
        event: str,
    ) -> None:
        """追加与参考实现兼容的 node_start/node_end 进度事件。"""
        record: dict[str, object] = {
            "ts": _now_iso(),
            "event": event,
            "id": trace.trace_id,
            "type": trace.kind,
            "label": trace.label,
        }
        if event == "node_end":
            record["status"] = trace.status
            record["durationMs"] = trace.duration_ms
        line = json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        async with self._lock:
            with anyio.CancelScope(shield=True):
                stream = await anyio.Path(
                    self._path,
                    "progress.jsonl",
                ).open("a", encoding="utf-8")
                async with stream:
                    await stream.write(f"{line}\n")
                if event == "node_start":
                    self._progress_started.add(trace.trace_id)
                elif event == "node_end":
                    self._progress_started.discard(trace.trace_id)
                    self._progress_finished.add(trace.trace_id)
        await checkpoint_if_cancelled()

    @asynccontextmanager
    async def _trace(
        self,
        kind: TraceKind,
        label: str,
        *,
        input_summary: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AsyncIterator[ExecutionTrace]:
        """在当前 trace 下记录一个子操作的生命周期。"""
        # 子 trace 始终绑定到进入上下文时的父 trace, 而非依赖调用方手工关联。
        parent = _CURRENT_TRACE.get() or self.root_trace
        trace = ExecutionTrace(
            trace_id=f"{kind}-{uuid4().hex[:12]}",
            kind=kind,
            label=label,
            started_at=_now_iso(),
            input_summary=input_summary,
            metadata=dict(metadata or {}),
        )
        started = time.perf_counter()
        token = None
        try:
            await self._append_child(parent, trace)
            try:
                await self._record_progress(trace, "node_start")
            except Exception as progress_error:
                # progress.jsonl 是尽力而为的观测信号, 不能阻断业务节点。
                logger.error(
                    f"Failed to persist start event for trace {trace.trace_id}: {progress_error}",
                )
            # token 使嵌套 flow 操作自动继承此 trace, 并在退出时恢复父上下文。
            token = _CURRENT_TRACE.set(trace)
            yield trace
            trace.status = "ok"
            trace.finished_at = _now_iso()
            trace.duration_ms = (time.perf_counter() - started) * 1_000
            if trace.trace_id not in self._progress_started:
                try:
                    await self._record_progress(trace, "node_start")
                except Exception as progress_error:
                    logger.error(
                        f"Failed to persist start event for completed trace {trace.trace_id}: {progress_error}",
                    )
            try:
                await self._record_progress(trace, "node_end")
            except Exception as progress_error:
                logger.error(
                    f"Failed to persist completed trace {trace.trace_id}: {progress_error}",
                )
        except BaseException as error:
            if not any(child is trace for child in parent.children):
                raise
            if trace.trace_id in self._progress_finished:
                # The node_end write completed; a cancellation checkpoint after
                # it must not rewrite the same terminal event as cancelled.
                raise
            cancelled = isinstance(error, anyio.get_cancelled_exc_class())
            # 取消也要写下终态; shield 防止取消信号打断这次诊断持久化。
            with anyio.CancelScope(shield=cancelled):
                # 异常状态写入 trace, 供最终封存的执行图保留失败原因。
                trace.status = "cancelled" if cancelled else "error"
                trace.error = _error_text(error)
                trace.finished_at = _now_iso()
                trace.duration_ms = (time.perf_counter() - started) * 1_000
                if trace.trace_id not in self._progress_started:
                    try:
                        await self._record_progress(trace, "node_start")
                    except Exception as progress_error:
                        logger.error(
                            f"Failed to persist start event for trace "
                            f"{trace.trace_id} while handling "
                            f"{error.__class__.__name__}: {progress_error}",
                        )
                try:
                    await self._record_progress(trace, "node_end")
                except Exception as progress_error:
                    logger.error(
                        f"Failed to persist trace {trace.trace_id} while handling "
                        f"{error.__class__.__name__}: {progress_error}",
                    )
            raise
        finally:
            # 无论成功, 失败还是取消, 都不能把子 trace 泄漏给后续操作。
            if token is not None:
                _CURRENT_TRACE.reset(token)

    async def _seal(self) -> None:
        """封存 context, 阻止最终状态开始写入后继续注册新内容。"""
        async with self._lock:
            self._sealed = True

    async def _write_trace_file(
        self,
        name: str,
        trace: ExecutionTrace,
    ) -> None:
        """将命名 trace 写为独立的诊断文件, 失败只记录日志。"""
        # progress.jsonl 是运行中快照; 命名 trace 文件是单个已完成操作的独立诊断记录。
        try:
            await _atomic_write_json(
                anyio.Path(self._path, "trace", f"{assert_safe_name(name)}.json"),
                trace.to_dict(),
            )
        except Exception as error:
            logger.error(f'Failed to persist diagnostic trace "{name}": {error}')


def current_run_context() -> RunContext:
    """返回当前 ContextVar 中的运行上下文, 缺失时明确报错。"""
    context = _CURRENT_RUN.get()
    if context is None:
        raise RuntimeError("flow operation requires an active run() context")
    return context


async def _validate_resume_inputs(run_dir: anyio.Path) -> None:
    """验证 resume run 中已有输入产物的名称与位置。"""
    names: set[str] = set()
    directory = anyio.Path(run_dir, "input")
    if not await directory.exists():
        return
    if not await directory.is_dir():
        raise ValueError('resume path "input" must be a directory')
    directory = await _resolve_direct_child(
        directory,
        run_dir,
        label='resume path "input"',
    )
    try:
        paths = [path async for path in directory.iterdir()]
    except OSError as error:
        logger.warning(f'Ignoring unreadable resume input directory "{directory}": {error}')
        return
    for path in paths:
        if not path.name.endswith(".md"):
            continue
        if await path.is_symlink():
            raise ValueError(f'resume input "{path.name}" must not be a symbolic link')
        if not await path.is_file():
            continue
        path = await _resolve_direct_child(
            path,
            directory,
            label=f'resume input "{path.name}"',
        )
        name = path.name.removesuffix(".md")
        try:
            normalized = assert_safe_name(name)
        except ValueError:
            continue
        if normalized != name:
            raise ValueError(
                f'resume input name "{name}" must use NFC normalization',
            )
        if normalized in names:
            raise ValueError(
                f'duplicate resume input after NFC normalization: "{normalized}"',
            )
        names.add(normalized)


async def _load_resume_bindings(run_dir: anyio.Path) -> dict[str, str]:
    """加载 resume run 的直接 bindings 子目录中的安全 Markdown 绑定。"""
    bindings: dict[str, str] = {}
    names: set[str] = set()
    directory = anyio.Path(run_dir, "bindings")
    if not await directory.exists():
        return bindings
    if not await directory.is_dir():
        raise ValueError('resume path "bindings" must be a directory')
    directory = await _resolve_direct_child(
        directory,
        run_dir,
        label='resume path "bindings"',
    )
    try:
        paths = [path async for path in directory.iterdir()]
    except OSError as error:
        logger.warning(f'Ignoring unreadable resume bindings directory "{directory}": {error}')
        return bindings
    for path in paths:
        if not path.name.endswith(".md"):
            continue
        if await path.is_symlink():
            raise ValueError(f'resume binding "{path.name}" must not be a symbolic link')
        if not await path.is_file():
            continue
        path = await _resolve_direct_child(
            path,
            directory,
            label=f'resume binding "{path.name}"',
        )
        # 只接受 bindings 的直接普通文件, 解析后仍须留在该目录内。
        name = path.name.removesuffix(".md")
        try:
            normalized = assert_safe_name(name)
        except ValueError:
            continue
        if normalized != name:
            raise ValueError(
                f'resume binding name "{name}" must use NFC normalization',
            )
        if normalized in names:
            raise ValueError(
                f'duplicate resume binding after NFC normalization: "{normalized}"',
            )
        names.add(normalized)
        try:
            bindings[normalized] = await path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            logger.warning(f'Ignoring unreadable resume binding "{path.name}": {error}')
    return bindings


async def _load_resume_metadata(
    run_dir: anyio.Path,
    binding_names: set[str],
) -> dict[str, dict[str, object]]:
    """加载 resume bindings 对应的直接元数据文件。"""
    payloads: dict[str, dict[str, object]] = {}
    names: set[str] = set()
    directory = anyio.Path(run_dir, "bindings")
    if not await directory.exists():
        return payloads
    if not await directory.is_dir():
        raise ValueError('resume path "bindings" must be a directory')
    directory = await _resolve_direct_child(
        directory,
        run_dir,
        label='resume path "bindings"',
    )
    try:
        paths = [path async for path in directory.iterdir()]
    except OSError as error:
        logger.warning(f'Ignoring unreadable resume metadata directory "{directory}": {error}')
        return payloads
    for path in paths:
        if not path.name.endswith(".meta.json"):
            continue
        if await path.is_symlink():
            raise ValueError(
                f'resume binding metadata "{path.name}" must not be a symbolic link',
            )
        if not await path.is_file():
            continue
        path = await _resolve_direct_child(
            path,
            directory,
            label=f'resume binding metadata "{path.name}"',
        )
        # 元数据与绑定分别加载; 名称经 NFC 规范化后必须各自唯一。
        name = path.name.removesuffix(".meta.json")
        try:
            normalized = assert_safe_name(name)
        except ValueError:
            continue
        if normalized != name:
            raise ValueError(
                f'resume binding metadata name "{name}" must use NFC normalization',
            )
        if normalized in names:
            raise ValueError(
                f'duplicate resume metadata after NFC normalization: "{normalized}"',
            )
        names.add(normalized)
        if normalized not in binding_names:
            continue
        try:
            raw = json.loads(await path.read_text(encoding="utf-8"))
        except Exception as error:
            logger.warning(f'Ignoring corrupt resume metadata "{path.name}": {error}')
            continue
        if not isinstance(raw, dict):
            logger.warning(
                f'Ignoring corrupt resume metadata "{path.name}": expected a JSON object',
            )
            continue
        payloads[normalized] = dict(raw)
    return payloads


async def _persist_final_state(
    context: RunContext,
    *,
    status: TraceStatus,
    started_at: str,
    started: float,
    error: BaseException | None,
    resume_from_run_id: str | None,
    program_snapshot: str | None,
) -> None:
    """封存根 trace, 并写入完成后的执行图和 run 元数据。"""
    finished_at = _now_iso()
    duration_ms = (time.perf_counter() - started) * 1_000
    context.root_trace.status = status
    context.root_trace.finished_at = finished_at
    context.root_trace.duration_ms = duration_ms
    if error is not None:
        context.root_trace.error = _error_text(error)
    tokens = aggregate_tokens(context.root_trace)
    session_calls = {name: count for name, count in context._session_call_counts.items() if not name.startswith("__")}
    evaluator_calls = {name: count for name, count in context._session_call_counts.items() if name.startswith("__")}

    # progress.jsonl 只是过程快照; 最终 execution graph 和 meta 才是封存记录。
    await _atomic_write_json(
        anyio.Path(context._path, "execution-graph.json"),
        {
            "run_id": context.run_id,
            "root": context.root_trace.to_dict(),
        },
    )
    await _atomic_write_json(
        anyio.Path(context._path, "meta.json"),
        {
            "run_id": context.run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "status": status,
            "error": _error_text(error) if error is not None else None,
            "resumed": resume_from_run_id is not None,
            "resume_from_run_id": resume_from_run_id,
            "program_snapshot": program_snapshot,
            "session_calls": session_calls,
            "evaluator_calls": evaluator_calls,
            "service_calls": dict(context._service_call_counts),
            "total_tokens": {
                "input": tokens.input,
                "output": tokens.output,
            },
            "llm_calls": tokens.calls,
            "user_tokens": {
                "input": tokens.user.input,
                "output": tokens.user.output,
            },
            "user_llm_calls": tokens.user.calls,
            "evaluator_tokens": {
                "input": tokens.internal.input,
                "output": tokens.internal.output,
            },
            "evaluator_llm_calls": tokens.internal.calls,
            "tokens": {
                "user": {
                    "calls": tokens.user.calls,
                    "input": tokens.user.input,
                    "output": tokens.user.output,
                },
                "internal": {
                    "calls": tokens.internal.calls,
                    "input": tokens.internal.input,
                    "output": tokens.internal.output,
                },
                "calls": tokens.calls,
                "input": tokens.input,
                "output": tokens.output,
            },
        },
    )


def _validate_gc_retention(
    keep_count: int,
    keep_days: int | float,
) -> None:
    """验证自动清理保留参数。"""
    if isinstance(keep_count, bool) or not isinstance(keep_count, int):
        raise TypeError("keep_count must be an integer")
    if isinstance(keep_days, bool) or not isinstance(keep_days, int | float):
        raise TypeError("keep_days must be a number")
    try:
        finite = math.isfinite(keep_days)
    except OverflowError as error:
        raise ValueError("keep_days must be finite") from error
    if not finite:
        raise ValueError("keep_days must be finite")
    if keep_count < 0 or keep_days < 0:
        raise ValueError("keep_count and keep_days must be non-negative")


async def run(
    program: Program,
    *,
    runs_dir: PathValue = "runs",
    inputs: Mapping[str, str] | None = None,
    runner: SessionRunner | None = None,
    run_id: str | None = None,
    resume_from_run_id: str | None = None,
    throw_on_error: bool = False,
    program_path: PathValue | None = None,
    keep_count: int = 50,
    keep_days: int | float = 7,
) -> RunResult:
    """执行一个 FusionFlow program, 并封存其动态 trace。"""

    _validate_gc_retention(keep_count, keep_days)
    if run_id is not None and resume_from_run_id is not None:
        raise ValueError("run_id and resume_from_run_id are mutually exclusive")
    if run_id == "last":
        raise ValueError('run_id "last" is reserved for resume_from_run_id')
    root = anyio.Path(runs_dir)
    if resume_from_run_id == "last":
        latest: str | None = None
        try:
            async for child in root.iterdir():
                try:
                    is_directory = await child.is_dir()
                except OSError as error:
                    logger.warning(
                        f'Failed to inspect FusionFlow run "{child.name}": {error}',
                    )
                    continue
                if is_directory and (latest is None or child.name > latest):
                    latest = child.name
        except OSError as error:
            raise FileNotFoundError(
                f'resume run "last" could not read {root}',
            ) from error
        if latest is None:
            raise FileNotFoundError(f'resume run "last" found no runs in {root}')
        resume_from_run_id = latest
    selected_id = assert_safe_name(
        resume_from_run_id if resume_from_run_id is not None else run_id if run_id is not None else _make_run_id(),
    )
    resumed = resume_from_run_id is not None

    normalized_inputs: dict[str, str] = {}
    for name, value in (inputs or {}).items():
        normalized = assert_safe_name(name)
        if not isinstance(value, str):
            raise TypeError(f'input "{normalized}" must be a string')
        if normalized in normalized_inputs:
            raise ValueError(
                f'duplicate input after NFC normalization: "{normalized}"',
            )
        normalized_inputs[normalized] = value

    created_run = False
    resume_bindings: dict[str, str] = {}
    resume_metadata: dict[str, dict[str, object]] = {}
    try:
        # 第一阶段: 验证既有 resume run, 或创建并约束新的直接 run 子目录。
        if resumed:
            if not await root.exists():
                raise FileNotFoundError(
                    f'resume run "{selected_id}" does not exist in {root}',
                )
            root_resolved = await root.resolve()
            candidate = anyio.Path(root, selected_id)
            if await candidate.is_symlink():
                raise ValueError(f'resume run "{selected_id}" must not be a symbolic link')
            if not await candidate.is_dir():
                raise FileNotFoundError(
                    f'resume run "{selected_id}" does not exist in {root}',
                )
            run_path = await _resolve_direct_child(
                candidate,
                root_resolved,
                label=f'resume run "{selected_id}"',
            )
            for directory in ("input", "bindings", "trace"):
                await _validate_existing_run_subdirectory(run_path, directory)
            await _validate_resume_inputs(run_path)
            resume_bindings = await _load_resume_bindings(run_path)
            resume_metadata = await _load_resume_metadata(
                run_path,
                set(resume_bindings),
            )
        else:
            await root.mkdir(parents=True, exist_ok=True)
            root_resolved = await root.resolve()
            run_path = anyio.Path(root_resolved, selected_id)
            with anyio.CancelScope(shield=True):
                await run_path.mkdir()
                created_run = True
            await checkpoint_if_cancelled()
            run_path = await _resolve_direct_child(
                run_path,
                root_resolved,
                label=f'run "{selected_id}"',
            )

        for directory in ("input", "bindings", "trace"):
            await _ensure_run_subdirectory(run_path, directory)

        if not resumed:
            try:
                await gc_runs(
                    root_resolved,
                    keep_count=keep_count,
                    keep_days=keep_days,
                    exclude_run_id=selected_id,
                )
            except Exception as cleanup_error:
                logger.warning(
                    f"Automatic FusionFlow run cleanup failed: {cleanup_error}",
                )

        snapshot_status: str | None = None
        source = anyio.Path(program_path) if program_path is not None else anyio.Path(sys.argv[0]) if sys.argv else None
        if source is not None:
            # Python 的入口脚本对应 TypeScript 的 process.argv[1]。无论路径
            # 来自显式参数还是宿主环境, 程序快照都只做尽力而为。
            try:
                if await source.is_file():
                    await _atomic_write_bytes(
                        anyio.Path(run_path, "program.py"),
                        await source.read_bytes(),
                    )
                    snapshot_status = str(source)
                else:
                    snapshot_status = f"unavailable: {source}"
                    logger.warning(
                        f"Failed to snapshot FusionFlow program: {source} is not a file",
                    )
            except Exception as snapshot_error:
                snapshot_status = f"unavailable: {source}"
                logger.warning(
                    f"Failed to snapshot FusionFlow program: {snapshot_error}",
                )

        started_at = _now_iso()
        started = time.perf_counter()
        root_trace = ExecutionTrace(
            trace_id="run-root",
            kind="run",
            label=selected_id,
            started_at=started_at,
        )
        context = RunContext(
            run_id=selected_id,
            run_dir=run_path,
            inputs=normalized_inputs,
            runner=runner,
            root_trace=root_trace,
            resumed=resumed,
            resume_bindings=resume_bindings,
        )
        # 第二阶段: 构造 context, 并注入预检阶段加载的可复用绑定和元数据。
        if resumed:
            context._resume_metadata = resume_metadata
    except BaseException:
        if created_run:
            with anyio.CancelScope(shield=True):
                try:
                    if await run_path.exists():
                        await _remove_tree(run_path)
                except Exception as cleanup_error:
                    logger.error(
                        f'Failed to clean incomplete run "{selected_id}": {cleanup_error}',
                    )
        raise

    run_token = _CURRENT_RUN.set(context)
    trace_token = _CURRENT_TRACE.set(root_trace)
    status: TraceStatus = "ok"
    caught: BaseException | None = None
    persistence_error: BaseException | None = None
    try:
        try:
            # 第三阶段: 在 run ContextVar 中执行 program, 并记录成功或失败终态。
            await program(context)
        except BaseException as error:
            caught = error
            status = "cancelled" if isinstance(error, anyio.get_cancelled_exc_class()) else "error"
        # 第四阶段: 即使外层取消, 也完成 graph/meta 的封存和 context 的 seal。
        with anyio.CancelScope(shield=True):
            try:
                await context._seal()
                await _persist_final_state(
                    context,
                    status=status,
                    started_at=started_at,
                    started=started,
                    error=caught,
                    resume_from_run_id=resume_from_run_id,
                    program_snapshot=snapshot_status,
                )
            except BaseException as error:
                persistence_error = error
    finally:
        # 最终持久化之后恢复调用方的 ContextVar, 避免 run 状态跨调用泄漏。
        _CURRENT_TRACE.reset(trace_token)
        _CURRENT_RUN.reset(run_token)

    cancelled = caught is not None and isinstance(caught, anyio.get_cancelled_exc_class())
    if persistence_error is not None and cancelled:
        logger.error(
            f"Failed to persist cancelled FusionFlow run {selected_id}: {persistence_error}",
        )

    # Cancellation may arrive while final persistence is shielded. Propagate it
    # before this otherwise synchronous tail can return a successful result.
    try:
        await checkpoint_if_cancelled()
    except anyio.get_cancelled_exc_class():
        if persistence_error is not None and not cancelled:
            logger.error(
                f"Failed to persist cancelled FusionFlow run {selected_id}: {persistence_error}",
            )
        raise

    if persistence_error is not None and not cancelled:
        if caught is not None:
            logger.error(
                f"FusionFlow run {selected_id} also failed before final-state "
                f"persistence failed: {_error_text(caught)}",
            )
        raise persistence_error

    duration_ms = context.root_trace.duration_ms
    assert duration_ms is not None
    logger.info(
        f"FusionFlow run {selected_id} finished with status={status} in {duration_ms:.1f}ms",
    )
    if caught is not None and (
        isinstance(caught, anyio.get_cancelled_exc_class()) or not isinstance(caught, Exception) or throw_on_error
    ):
        raise caught
    return RunResult(
        run_id=selected_id,
        run_dir=str(run_path),
        status="error" if status == "error" else "ok",
    )


async def gc_runs(
    runs_dir: PathValue,
    *,
    keep_count: int = 50,
    keep_days: int | float = 7,
    exclude_run_id: str | None = None,
) -> tuple[str, ...]:
    """按数量和日期保留规则清理 runs 目录的直接子 run 目录。"""

    _validate_gc_retention(keep_count, keep_days)
    if exclude_run_id is not None:
        exclude_run_id = assert_safe_name(exclude_run_id)
    if keep_count == 0 and keep_days == 0:
        return ()

    root = anyio.Path(runs_dir)
    try:
        if not await root.exists():
            return ()
        root_resolved = await root.resolve()
        children = [child async for child in root.iterdir()]
    except OSError as error:
        logger.warning(f"Failed to read FusionFlow runs directory {root}: {error}")
        return ()
    candidates: list[tuple[str, float, anyio.Path]] = []
    for child in children:
        try:
            normalized_name = assert_safe_name(child.name)
        except ValueError:
            continue
        if normalized_name == exclude_run_id:
            continue
        try:
            if await child.is_symlink() or not await child.is_dir():
                continue
            resolved = await child.resolve()
            if resolved.parent != root_resolved:
                continue
            stat = await child.stat(follow_symlinks=False)
        except Exception as error:
            logger.warning(
                f'Failed to inspect FusionFlow run "{child.name}": {error}',
            )
            continue
        candidates.append((child.name, stat.st_mtime, child))

    candidates.sort(key=lambda item: item[0], reverse=True)
    keep: set[str] = set()
    if keep_count > 0:
        keep.update(name for name, _, _ in candidates[:keep_count])
    if keep_days > 0:
        # keep_days 与 keep_count 取并集, 满足任一保留条件即可留下。
        cutoff = time.time() - keep_days * 24 * 60 * 60
        keep.update(name for name, mtime, _ in candidates if mtime >= cutoff)

    deleted: list[str] = []
    for name, _, path in candidates:
        if name in keep:
            continue
        await checkpoint_if_cancelled()
        try:
            with anyio.CancelScope(shield=True):
                await _remove_tree(path)
        except Exception as error:
            logger.warning(f'Failed to remove FusionFlow run "{name}": {error}')
        else:
            deleted.append(name)
        await checkpoint_if_cancelled()
    return tuple(deleted)

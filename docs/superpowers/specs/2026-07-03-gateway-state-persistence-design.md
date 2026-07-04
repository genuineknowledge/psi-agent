# Gateway State Persistence Design

## Problem

Gateway 重启后，AI、Session、Title 三者的状态全部丢失——AIManager 和 SessionManager 的 `_entries` 是内存中的 `dict`，TitleManager 的 `_titles` 同理。用户必须手动重新创建所有 AI 和 Session。

## Goal

Gateway 维护 `state/latest.json`（相对 CWD），在每次状态变更时自动持久化，启动时自动恢复。

## Architecture

新增 `_state.py` 模块，定义 `GatewayState` dataclass 负责 JSON 文件的 load/save。三个有状态 manager 各自增加 `_persist` 回调参数（语义明确：仅用于落盘），在 mutate 后触发异步保存。

```
GatewayState (新模块)
     ▲  persist(ais, sessions, titles)
     │
     ├── AIManager._persist ──── create/delete ──► save
     ├── SessionManager._persist ─ create/delete ─► save
     └── TitleManager._persist ─── set/generate ──► save

Gateway.run():
  1. state.load()                     ← 文件不存在返回空 dict
  2. for each ai → aim.create()       ← 失败 skip
  3. for each session → sm.create()   ← 失败 skip
  4. for each title → tm.set()
  5. 创建 persist 闭包 → 注入三个 manager
```

## JSON Format

```json
{
  "ais": [
    {"id": "<ai_id>", "provider": "openai", "model": "gpt-4o", "api_key": "sk-...", "base_url": "http://..."}
  ],
  "sessions": [
    {"id": "<session_id>", "ai_id": "<ai_id>", "workspace": "/home/..."}
  ],
  "titles": [
    {"id": "<session_id>", "title": "My Chat"}
  ]
}
```

- API key 明文存储（设计决策：Gateway 仅 listen 127.0.0.1，文件权限由用户自行控制）
- 不存 `verbose`、`max_tool_rounds` 等调试/调优参数

## Changes per File

### New: `gateway/_state.py`

```python
@dataclass
class GatewayState:
    _path: anyio.Path          # Path("state/latest.json")

    async def load(self) -> dict[str, dict]:
        """读取 JSON，文件不存在返回 {"ais": {}, "sessions": {}, "titles": {}}"""

    async def save(self, ais: list[dict[str, str]], sessions: list[dict[str, str]], titles: list[dict[str, str]]) -> None:
        """写入 JSON。失败 log warning，不抛异常"""
```

### `_ai_manager.py`

- 新增 `_persist: Callable[[], Awaitable[None]] | None` 字段（构造函数参数，默认 `None`）
- `create()`: `return AiInfo(...)` 前 `if self._persist: await self._persist()`
- `delete()`: `logger.info(...)` 前 `if self._persist: await self._persist()`
- Crash 清理 (`_run_ai` except): `self._entries.pop(...)` 后 `if self._persist: await self._persist()`
- `get_socket(ai_id)`: 不存在时用 `_socket_path()` 计算路径返回，**不抛 LookupError**

### `_session_manager.py`

- 新增 `_persist: Callable[[], Awaitable[None]] | None` 字段（构造函数参数，默认 `None`）
- `create()`: 去掉 `self._aim.has(ai_id)` 检查；`ai_socket` 通过 `self._aim.get_socket(ai_id)` 获取（支持 AI 尚未创建的场景）
- `create()`: `return SessionInfo(...)` 前 `if self._persist: await self._persist()`
- `delete()`: `logger.info(...)` 前 `if self._persist: await self._persist()`
- Crash 清理 (`_run_session` except): `self._entries.pop(...)` 后 `if self._persist: await self._persist()`

### `_title_manager.py`

- 新增 `_persist: Callable[[], Awaitable[None]] | None` 字段（`__init__` 参数，默认 `None`）
- `set()`: 改为 `async def set()`，`self._titles[sid] = title` 后 `if self._persist: await self._persist()`
- `generate()`: `self._titles[session_id] = title` 后 `if self._persist: await self._persist()`

### `__init__.py` (Gateway.run)

启动流程变更为：

```
setup_logging(verbose)
→ state = GatewayState()
→ snapshot = await state.load()
→ async with anyio.create_task_group() as tg:
    → aim = AIManager(...) + sm = SessionManager(...)
    → 恢复（_persist 未设置，不触发 save）:
        for ai in snapshot.ais → aim.create(..., id=ai_id)
        for sess in snapshot.sessions → sm.create(..., id=sess_id)
    → app = await create_app(aim, sm, ...)
        ← server.py 内部: tm = TitleManager(...)
    → 创建 _do_persist 闭包（tm 已存在，闭包捕获引用）:
        async def _do_persist():
            await state.save(
                ais=await aim.list_all(),
                sessions=await sm.list_all(),
                titles=tm.get_all(),
            )
    → 注入 _persist:
        aim._persist = sm._persist = tm._persist = _do_persist
    → 恢复标题:
        for sid, title in snapshot.titles.items():
            await tm.set(sid, title)
    → 调用一次 _do_persist() 持久化恢复后的初始状态
    → ... 后续不变
```

重建失败处理：log warning，不阻塞其他条目的恢复。

### `server.py`

- `_set_title`: `await tm.set(...)` （原来是同步调用）
- `_generate_title`: `tm.generate()` 成功后内部已调用 `_persist`，无需额外处理
- `create_app()` 签名不变 —— `tm._persist` 由 `Gateway.run()` 在 `create_app()` 返回后外部注入

## Edge Cases

1. **文件不存在 → 空启动**: `load()` 返回空 dict，Gateway 正常启动
2. **写入失败**: `save()` catch 所有异常，log warning，不影响当前操作
3. **Crash 自动清理触发保存**: AI/Session crash 后 `_entries.pop()` 也会触发 save
4. **重启后 socket 路径复用相同 UUID**: 旧 socket 文件已在进程退出时失效，新进程绑定到同一路径
5. **并发安全**: `save()` 在 manager 的锁外调用，可能读到中间状态但无数据损坏风险（dict 快照 + 单线程写入）

## Testing

- 单元测试: `GatewayState` 的 load/save 往返
- 集成测试: Gateway 进程 → 创建 AI/Session → 设置 title → 确认 `latest.json` 内容正确 → 停止 → 重启 → 确认 AI/Session/Title 恢复

## Notes

- `state/` 目录在 `save()` 首次写入时自动创建（`anyio.Path.mkdir(parents=True)`）
- 不自动添加 `.gitignore`（`state/` 是 Gateway 内部状态，不属于 workspace 范畴）
- `_persist` 为 `None` 时（CLI 上下文直接使用 manager），不会尝试保存

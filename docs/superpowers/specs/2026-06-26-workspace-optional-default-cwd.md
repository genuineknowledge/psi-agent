# Session Workspace 可选化设计规格

**日期**: 2026-06-26
**状态**: 已审批

---

## 1. 概述

当前 `Session` dataclass 的 `workspace: str` 是必填字段。本设计将其改为可选：未提供时默认使用进程当前工作目录（`Path.cwd()`）作为 workspace。

**设计原则**：
- 向后兼容——传入 `workspace` 的现有行为完全不变
- 零侵入——workspace 解析逻辑仅在 `Session.run()` 中一处改动
- 所有 workspace 子目录（tools/、schedules/、systems/、histories/）的容错逻辑不变：目录缺失时静默降级为空

---

## 2. 默认值方案

**方案 B（选定）**: `workspace: str = ""`

- 空字符串作为标记值，表示"使用 CWD"
- 类型签名清晰（保持不变仍是 `str`）
- `tyro` CLI 将 `--workspace ""` 变为可选（空字符串是 dataclass 默认值，tyro 自动将必填变为可选）

`run()` 中的 workspace 路径解析：

```python
workspace_path = Path.cwd() if self.workspace == "" else Path(str(await anyio.Path(self.workspace).resolve()))
```

`Path.cwd()` 返回绝对路径，与原来 `resolve()` 的产出一致（语义上不需要 `anyio.Path` 包装，`Path.cwd()` 是纯路径操作，不涉及 IO）。

---

## 3. Session 变更

### `workspace` 字段

```python
workspace: str = ""
"""Path to the workspace directory.  Defaults to current working directory."""
```

### `run()` 方法

```python
async def run(self) -> None:
    setup_logging(verbose=self.verbose)

    workspace_path = (
        Path.cwd()
        if self.workspace == ""
        else Path(str(await anyio.Path(self.workspace).resolve()))
    )
    logger.info(f"Loading workspace from {workspace_path}")
    ...
```

其余代码不变。`SessionAgent.create()` 仍然接收 `workspace_path: Path`，行为不变。

---

## 4. `_run.py` YAML 配置

`type: session` 的 `workspace` 字段变为可选。省略时使用配置文件的所在目录（`yaml` 所在目录为 CWD，因为进程从那里启动）或实际的 CWD。

docstring 示例更新——workspace 标记为 optional：

```yaml
    - type: session
      # workspace: ./my-workspace    # 可选，默认当前目录
      channel_socket: ./channel.sock
      ai_socket: ./ai.sock
```

代码层面无需改动——`Session(**item)` 中 `workspace` 不在 `item` 时自动取默认值 `""`。

---

## 5. CLI 行为

`tyro` 在生成 CLI 参数时，有默认值的字段自动变为 `--workspace WORKSPACE`（可选，方括号标记）。

```
psi-agent session --workspace ""          # 显式使用 CWD（与不传等价）
psi-agent session                          # workspace 默认为 CWD
psi-agent session --workspace ./my-dir    # 现有行为保持不变
```

---

## 6. 边缘情况

| 场景 | 行为 |
|------|------|
| `workspace=""` + CWD 无 `tools/` 目录 | `load_tools_from_workspace()` 返回空 dict（WARNING 日志），不影响运行 |
| `workspace=""` + CWD 无 `systems/` 目录 | `_load_system_prompt_builder()` 返回 None（WARNING 日志），无 system prompt |
| `workspace=""` + CWD 无 `schedules/` 目录 | `load_schedules_from_workspace()` 返回空列表（WARNING 日志） |
| `workspace=""` + CWD 无 `histories/` 目录 | `_init_history()` 自动创建目录 + `.gitignore`（首次时） |
| `Session(**item)` 中 `workspace` 缺失 | dataclass 默认值 `""` 自动生效 |

---

## 7. 测试策略

### 单元测试

1. `test_workspace_empty_string_uses_cwd` — `workspace=""` → `workspace_path` 等于 `Path.cwd()`
2. `test_workspace_explicit_path_still_works` — `workspace="./some/path"` → 行为不变

### 集成测试

3. `test_yaml_session_without_workspace` — `_run.py` 配置中 session 不含 `workspace` → Session 正常启动

---

## 8. 文档更新

- `README.md` / `README_en.md`: `--workspace` 标记为可选，说明默认 CWD
- `AGENTS.md`: 更新 workspace 启动流程描述
- `src/psi_agent/session/AGENTS.md`: 更新 Session 层启动流程 + workspace 字段描述

---

## 9. 版本历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-26 | v0.5.0-draft | Session workspace 可选，默认 CWD |

# Feishu 群聊 @ 触发实施计划

**日期**: 2026-07-20
**分支**: add-feishu-tools4
**目标**: 飞书机器人在群里被 @ 时才用 Haitun Agent 读取消息并回复；单聊照常回复。策略可配置。

---

## 背景与根因

底层 `lark_channel` 库已内置该能力，且默认开启：

- `PolicyGate`（`channel/safety/policy_gate.py`）默认 `require_mention=True`。
- 群聊（`chat_type` 为 `group`/`topic`）里，只有 @机器人（`mentioned_bot=True`）的消息才通过；否则被 `policy_no_mention` 拒绝，**不触发 `on("message")`**，也就不调用 Haitun Agent。
- 单聊（p2p）默认 `dm_policy="open"`，照常全部响应。
- 安全管线 `_safety` 在 `_ensure_bg_loop` 里无条件构建，用 `self._config.policy`。

**根因（"群里 @ 了也不回复"）**：policy gate 判断"是否 @ 机器人"完全依赖 `bot_open_id`
（`policy_gate.py:142-147`）。该 id 靠 `FeishuChannel` 启动时调 `/bot/v3/info` 自动拉取。
若拉取失败（网络抖动 / 飞书后台未开启"机器人"能力），`bot_open_id` 恒为 `None`
→ 群里每条消息 `mentioned_bot=False` → 全被 `require_mention` 以 `policy_no_mention` 拒掉。
psi-agent 侧 `run_feishu` 对此完全无感知、无日志、无兜底，且从未传 `policy`（只吃库默认值）。

---

## 改动范围

只动 psi-agent feishu channel 层，不碰工具、不改 `lark_channel` 库、不合入 main。

- `src/psi_agent/channel/feishu/__init__.py`
- `src/psi_agent/channel/feishu/client.py`
- `tests/psi_agent/channel/feishu/test_feishu.py`
- `docs/superpowers/specs/2026-06-25-feishu-channel.md`（补策略与 @ 触发一节）

---

## 详细设计

### 1. `ChannelFeishu` 暴露策略开关（`__init__.py`）

新增两个字段（仅 @ 相关，粒度按用户确认）：

```python
require_mention: bool = True
"""群聊仅在 @机器人时回复；单聊不受影响。"""

respond_to_mention_all: bool = False
"""是否把 @所有人 视为有效 @（默认否）。"""
```

`run()` 中将这两个值透传给 `run_feishu(...)`。

### 2. `run_feishu` 构建并传入 `PolicyConfig`（`client.py`）

- 新增参数 `require_mention: bool = True`、`respond_to_mention_all: bool = False`。
- 从 `lark_channel.channel.config`（或包顶层）import `PolicyConfig`，构造后经
  `FeishuChannel(app_id=..., app_secret=..., policy=PolicyConfig(...))` 传入。
  （现状：完全没传 policy，只吃库默认值。）

### 3. 修复 + 可诊断 bot 身份（`client.py` `run_feishu`）

`start_background()` 之后：

- 若 `channel.bot_identity is None`，`await channel.resolve_bot_identity()` 兜底重试一次。
- 成功 → `logger.info` 打出 open_id / name。
- 仍失败 → `logger.warning`：明确提示"群聊 @ 检测将不可用，请确认飞书后台已开启机器人能力"。
- 注册 `channel.on("reject", ...)` 回调：把被策略拒绝的消息按原因（`policy_no_mention` 等）
  记 DEBUG 日志，方便日后"@ 了不回复"排查。回调需容错（其自身异常不得冒泡）。

reject 事件对象来自 `lark_channel`（`RejectEvent`，含 `message_id` / `reason`）——按 duck-typing
读取 `getattr`，不硬 import 具体类型以降低耦合。

### 4. 测试（`test_feishu.py`）

- `ChannelFeishu` 新字段默认值（`require_mention=True`、`respond_to_mention_all=False`）与显式赋值透传。
- `run_feishu` 用 monkeypatch 替换 `FeishuChannel`，断言 `policy` kwarg 是 `PolicyConfig`
  且 `require_mention` / `respond_to_mention_all` 取值正确。
- bot 身份未解析（`bot_identity` 为 None）时触发 `resolve_bot_identity` 兜底；
  解析仍失败时发出 warning（可用 caplog 或 mock 校验调用）。
- `on("reject")` 回调被注册（断言 `channel.on` 以 "reject" 被调用）。

沿用现有测试里 `_patch_feishu` / `_fake_channel` 的 mock 风格。

### 5. 文档

`docs/superpowers/specs/2026-06-25-feishu-channel.md` 增补：
- 「群聊 @ 触发与准入策略」小节：说明 `require_mention` / `respond_to_mention_all` 语义、
  单聊 vs 群聊差异、bot 身份解析对 @ 检测的前置依赖及排查提示。

---

## 验证

- `ruff check` + `ruff format --check`（CI 两个都跑）
- `pytest tests/psi_agent/channel/feishu/`

## 完成后

- commit + push 功能分支 `add-feishu-tools4`（不合 main）。
- 更新 memory。

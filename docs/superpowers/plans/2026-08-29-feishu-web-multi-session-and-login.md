# 飞书网页应用: 私聊多会话 + 标准免登 + 身份隔离 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让飞书用户在网页应用里开多个私聊会话(各自独立 jsonl、共享同一个 workspace), 用官方 JSSDK 免登拿到真实身份, 并让会话列表/历史按身份隔离。

**Architecture:** 前端(`feishu-web`)走官方 `tt.requestAccess` 拿 code → 后端新增 `POST /auth/feishu` 用 app_id/app_secret 换 `user_access_token` 再换 `open_id`, 签一个 HttpOnly cookie 会话态。会话隔离**不改骨架 `_list_sessions` 的语义**: 在 `gateway/feishu/_routes.py` 里新增 `/feishu/sessions` 一族按身份过滤的路由, ToC 的 spa-v2 走的骨架路由逐字节不动。多会话靠 `POST /sessions` 不传 `id` 拿新 uuid, workspace 一律复用 `FeishuManager._workspace_for()` 派生。

**Tech Stack:** Python 3.11 / aiohttp / anyio / loguru / pytest(anyio mode) / TypeScript / React 19 / Vite

## 本计划的事实基础(已逐条读代码核实, 与 spec 的差异已标出)

计划编写时在本 worktree 核实过的结论, 实施者可直接依赖:

- `_feishu_manager.py:97-106` `_session_id()`: 私聊 `feishu-<open_id>` 且 `-`→`_`; 群聊 `feishu-chat-<chat_id>`。`_workspace_for()`(:108-123) 私聊 `<root>/<open_id>`(同样转义)、群聊 `<root>/chat-<chat_id>`、`PSI_PRIVATE_OPEN_IDS` 白名单走 `.private/<open_id>`。
- `route()` 的 adopt 分支在 `:174-178`: 路由表未命中但 `sm.has(sid)` 为真就复用。所以私聊永远只有一个 session。
- `_session_manager.py:120-121`: `session_id = id or _new_uuid()`; `workspace = workspace.strip() or self._default_workspace or os.getcwd()`。**不传 id 即得新 uuid, 这条是多会话的地基。**
- `gateway/server.py:302-327` `_create_session` 只读 `backend_type/backend_id/ai_id/id/workspace/agent` —— **确认不读 `open_id`**。
- `gateway/server.py:351-353 / 356-358 / 418-420 / 473-482`: `_list_sessions`/`_list_titles`/`_list_summaries`/`_get_history` **零身份过滤**, `_get_history` 只在 session 不存在时 404。
- `session/agent.py:234` `self._lock = anyio.Lock()`, `:364` `handle_request` 内 `async with self._lock:` —— 一个 Session 一把锁, 网页与 IM 并发是排队。**读代码得出, 任务 8 必须实测复核。**
- `session/conversation.py:75,83`: 新写一律落 `{appdata}/histories/{session_id}.jsonl`。
- `desktop/_auth_store.py` 模块头第 1 条取舍: **token 不进 `state/latest.json`**; `_state.py:167-172` 确实把 AI 的 `api_key` 明文写进快照(已核实)。本计划的登录凭证不落 `state`。
- **与 spec 的差异 1**: 本 worktree 已被重做过(commit b2f2d20c)。`index.html:13` 已有 `h5-js-sdk-1.5.35.js` 的同步 script 标签; `api.ts` 模块头明确写了「没有任何登录相关函数, 归任务 5fef7」。所以**没有 `window.h5.getAuthCode` 与 `loginDev` 硬编码 open_id 要删** —— 那是 PR 755 的形态, 本 worktree 不存在。`grep -rn "loginDev\|getAuthCode\|ou_b23dbe79" src/psi_agent/gateway/feishu/feishu-web` 应为零命中, 实施第一步先跑一遍确认。
- **与 spec 的差异 2**: 官方文档 `client-docs/h5/api/requestaccess` 写明 `requestAccess` 返回的 code **有效期 3 分钟**(不是 5 分钟; 5 分钟是 SSO 授权码), 只能用一次。
- **与 spec 的差异 3**: Gateway 侧目前**刻意不知道 app_secret** —— `_oauth_manager.py:8` 原文「Gateway 侧刻意**不碰 token 交换**: 不知道 app_secret」。`PSI_FEISHU_APP_ID`/`PSI_FEISHU_APP_SECRET` 只在 channel 进程读(`channel/feishu/__init__.py:71-76`)。`/auth/feishu` 必须换 token, 所以这是一次**有意的架构变更**: Gateway 从此持有 app_secret。任务 4 的 AGENTS.md 与 `_oauth_manager` 模块头都要把这条差异写清, 否则下一个人会以为是违规。
- 官方免登链路(取自 `open.feishu.cn` 的 `.md` 版官方文档, 非博客): `tt.requestAccess({appID, scopeList, success, fail})`, `scopeList: []` 表示只授予「获取用户凭证信息」; 失败时 `errno === 103` 表示**客户端版本过低**, 要退回 `tt.requestAuthCode({appId, success, fail})` —— 注意两者 App ID 参数**大小写不同**(`appID` vs `appId`), 且 `window.tt.requestAccess` 不存在时也要退回。
- 换 token: `POST https://open.feishu.cn/open-apis/authen/v2/oauth/token`, body `{grant_type: "authorization_code", client_id, client_secret, code}`, 返回 `{code, access_token, expires_in, ...}`, `code == 0` 才算成功。取身份: `GET https://open.feishu.cn/open-apis/authen/v1/user_info` 带 `Authorization: Bearer <user_access_token>`, 返回 `{code, msg, data: {name, open_id, ...}}`。**两个接口失败时 HTTP 仍是 200, 靠 body 里的 `code != 0` 判失败** —— 这是把伪造 code 映射成 4xx 的关键。v2 端点官方标注为「历史版本」, 推荐 v3 `https://accounts.feishu.cn/oauth/v3/token`(请求/响应结构与 v2 一致); 本计划用 v2 端点并把 URL 收进一个常量, 迁 v3 时改一处。

## Global Constraints

- **Python 版本**: 3.11+(`pyproject.toml` 的 requires-python 为准), 全部新文件首行 `from __future__ import annotations`。
- **禁止新增第三方依赖**: HTTP 客户端用已在依赖里的 `aiohttp`; 不引 `lark-oapi`、不引 `httpx`。
- **不改骨架路由语义**: `gateway/server.py` 的 `_list_sessions` / `_get_history` / `_list_titles` / `_list_summaries` **函数体一行都不改**。ToC 的 `desktop/spa-v2` 用的就是这几条。
- **不在骨架层新增飞书路由**: 所有新路由落 `gateway/feishu/_routes.py`, 依赖方向只能是 产品 → 骨架。
- **workspace / session_id 派生只有一个来源**: 一律调 `FeishuManager` 的 `_workspace_for()` / `_session_id()`, 禁止在前端或另一处重拼。理由见 `_feishu_manager.py:100-102` 与 `_feishu_routing.py:11-12`: 不转义 `-` 时 open_id 恰为 `chat-oc_x` 的人会与群 `oc_x` 派生出逐字节相同的 id, 是隐私事故。
- **凭证不落 `state/latest.json`**: 遵守 `desktop/_auth_store.py` 模块头第 1 条。本计划的登录态只在内存 + HttpOnly cookie。
- **`dev_open_id` 旁路默认关闭**: 由环境变量 `PSI_FEISHU_DEV_OPEN_ID` 控制, 未设置即不可用, 设置时每次登录打 `logger.warning`。
- **ruff 全角字符禁令**: 中文注释里不用全角 `，（）：×`, 一律半角 `, ( ) :` 和 `x`; `。`、`——`、`「」`、`→` 可用(RUF001/002/003)。
- **测试目录镜像 src**: 每层都要有 `__init__.py`(漏掉会触发 import file mismatch)。异步测试加 `@pytest.mark.anyio`。
- **跑测试必须带 PYTHONPATH**: 本 worktree 里 `PYTHONPATH=src uv run pytest ...`, 否则测的是主 checkout 的 src。跑子树要 `-o testpaths=` 且写在路径**之前**。
- **前端构建判据**: `npm run build`(内含 `tsc --noEmit`) 必须零错。`vite.config.ts` 的 `base` 与后端 `add_static` 前缀同为 `/feishu-web/`。
- **appID 不写死在前端**: 从后端接口取, 见任务 3。

---

## File Structure

**后端(新建)**

- `src/psi_agent/gateway/feishu/_auth.py` —— 免登核心。`FeishuAuth` 持 app_id/app_secret, 负责 code → `user_access_token` → `open_id`/`name`, 以及登录态(token→身份)的内存表。**只有本文件出网**, 便于测试里替换。
- `src/psi_agent/gateway/feishu/_identity.py` —— 「当前请求属于谁」与「某 session 属不属于他」两个纯函数级判定。被 `_routes.py` 的过滤路由与 `/auth/feishu` 共用, 单独一文件是因为它是安全判定, 要能被单测密集覆盖。

**后端(修改)**

- `src/psi_agent/gateway/feishu/_routes.py` —— 注册 `POST /auth/feishu`、`GET /auth/me`、`POST /auth/logout`、`GET /feishu/app-id`, 以及按身份过滤的 `GET /feishu/sessions`、`GET /feishu/sessions/{id}/history`、`GET /feishu/titles`、`GET /feishu/summaries`、`POST /feishu/sessions`。
- `src/psi_agent/gateway/__init__.py` —— 新增 `feishu_app_id` / `feishu_app_secret` 两个字段并透传给 `register_feishu_routes`。
- `src/psi_agent/gateway/feishu/_feishu_manager.py` —— 把 `_session_id` / `_workspace_for` 暴露成公开方法(`session_id_for` / `workspace_for`), 供新路由复用同一份派生逻辑。旧私有名保留为薄封装, 免得动 5 处调用点。

**前端(新建)**

- `src/services/feishuAuth.ts` —— JSSDK 免登: 等 `h5sdk.ready` → `requestAccess`(带 `errno===103` 与「方法不存在」两条退路到 `requestAuthCode`) → 打 `/auth/feishu`。
- `src/hooks/useAuth.ts` —— 登录态 hook: `{status, me, error, retry}`, 失败时给可见重试入口。

**前端(修改)**

- `src/api.ts` —— 补 `login/getMe/logout/getFeishuAppId`, 并把会话一族从骨架路由切到 `/feishu/*` 过滤路由。
- `src/hooks/useSessions.ts` —— `create()` 走新的 `POST /feishu/sessions`(不传 id), 建完 `setTitle`; 列表过滤只滤群聊。
- `src/App.tsx` —— 未登录时渲染登录/重试屏; 「新会话」入口; 老 session 的「来自飞书对话」角标与上下文提示。

**测试(新建)**

- `tests/psi_agent/gateway/test_feishu_auth.py` —— code 换 token、伪造 code、缺 code、`dev_open_id` 默认不可用。
- `tests/psi_agent/gateway/test_feishu_identity.py` —— 归属判定与群聊过滤的纯函数用例。
- `tests/integration/test_feishu_web_sessions.py` —— A 看不到 B 的会话; 直取 B 的 history 被拒; 多会话各自独立 jsonl 且共享 workspace。

---

### Task 1: 把 workspace / session_id 派生变成可复用的公开方法

新路由要按「同一个人的多个会话共享同一个 workspace」建 session, 必须用与机器人侧**逐字节相同**的派生逻辑。现在两个函数是私有的(`_session_id` / `_workspace_for`), 直接跨模块调私有名会让下一个人以为可以自己重拼。

**Files:**
- Modify: `src/psi_agent/gateway/feishu/_feishu_manager.py:97-123`
- Test: `tests/psi_agent/gateway/test_feishu_manager.py`

**Interfaces:**
- Consumes: 无(第一个任务)
- Produces:
  - `FeishuManager.session_id_for(key: str) -> str` —— 私聊传裸 `open_id`, 群聊传 `chat:<chat_id>`
  - `FeishuManager.workspace_for(key: str) -> str`
  - 私有名 `_session_id` / `_workspace_for` 继续存在且行为不变

- [ ] **Step 1: 先确认 spec 里提到的 PR 755 遗留在本 worktree 确实不存在**

```bash
cd src/psi_agent/gateway/feishu/feishu-web
grep -rn "loginDev\|getAuthCode\|ou_b23dbe79e4c5e98516e26ce937cb7976" src index.html || echo "CLEAN: 无 PR755 遗留"
```

预期: 打印 `CLEAN`。若有命中, 说明 worktree 状态与本计划的前提不一致, **停下来先报告**, 不要自行删改。

- [ ] **Step 2: 写失败测试**

追加到 `tests/psi_agent/gateway/test_feishu_manager.py` 末尾:

```python
def test_public_derivation_escapes_dash(tmp_path: str) -> None:
    """私聊侧 ``-`` 必须转义: 否则 open_id 为 ``chat-oc_x`` 的人与群 ``oc_x`` 撞同一个 id。"""
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    assert fm.session_id_for("chat-oc_x") == "feishu-chat_oc_x"
    assert fm.session_id_for("chat:oc_x") == "feishu-chat-oc_x"
    assert fm.session_id_for("chat-oc_x") != fm.session_id_for("chat:oc_x")
    assert fm.workspace_for("chat-oc_x") != fm.workspace_for("chat:oc_x")
```

- [ ] **Step 3: 运行测试确认失败**

```bash
PYTHONPATH=src uv run pytest -o testpaths= tests/psi_agent/gateway/test_feishu_manager.py -k public_derivation -v -p no:cacheprovider --no-cov
```

预期: FAIL, `AttributeError: 'FeishuManager' object has no attribute 'session_id_for'`。

- [ ] **Step 4: 实现**

在 `_feishu_manager.py` 里, 把现有 `_session_id` 的实现体整段搬到新的公开方法 `session_id_for`, 并让 `_session_id` 变成薄封装(保留 5 处既有调用点不动)。同样处理 `_workspace_for`:

```python
    def session_id_for(self, key: str) -> str:
        """派生确定性 session_id, 加 ``feishu-`` 前缀与 SPA 手建 session 命名空间隔离。

        群聊键 ``chat:<chat_id>`` → ``feishu-chat-<chat_id>``; 私聊 → ``feishu-<open_id>``。
        私聊侧把 ``-`` 转义成 ``_``, 否则某人 open_id 恰为 ``chat-oc_x`` 时会与群 ``oc_x`` 撞成
        同一个 session (陌生人共享上下文的隐私事故)。飞书真实 open_id 不含 ``-``, 这只是防御层。

        **公开**是因为网页应用侧要按同一份逻辑建 session/workspace: 重实现一次就会漏掉上面
        那条转义。派生只能有一份, 故对外只暴露本方法, 不暴露拼接细节。
        """
        if key.startswith("chat:"):
            return f"feishu-chat-{_sanitize_open_id(key.removeprefix('chat:'))}"
        return f"feishu-{_sanitize_open_id(key).replace('-', '_')}"

    def _session_id(self, key: str) -> str:
        """内部别名 —— 既有 5 处调用点不动, 实现见 ``session_id_for``。"""
        return self.session_id_for(key)

    def workspace_for(self, key: str) -> str:
        """每个路由键得到独立子目录 (root 空则以 cwd 为父)。

        群聊 → ``<root>/chat-<chat_id>``, 私聊 → ``<root>/<open_id>`` (``-`` 同样转义,
        与 ``session_id_for`` 一致, 免得两个键指到同一个 workspace 目录)。

        ``PSI_PRIVATE_OPEN_IDS`` 白名单里的人 → ``<root>/.private/<open_id>``, 工具层
        据此拒绝其他 session 访问 (见 ``psi_agent._private_space``)。群聊不进私密区 ——
        群是多人共用上下文, 放私密区等于把私密资料摊给全群。

        网页应用侧「一个人的多个会话共享一个 workspace」正是靠调本方法实现: 同一个
        ``open_id`` 无论开几个 session, 都落这一个目录。
        """
        root = self._workspace_root or os.getcwd()
        if key.startswith("chat:"):
            return os.path.join(root, f"chat-{_sanitize_open_id(key.removeprefix('chat:'))}")
        if _private_space.is_private_user(key):
            return _private_space.private_dir(root, _sanitize_open_id(key))
        return os.path.join(root, _sanitize_open_id(key).replace("-", "_"))

    def _workspace_for(self, key: str) -> str:
        """内部别名 —— 既有调用点不动, 实现见 ``workspace_for``。"""
        return self.workspace_for(key)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
PYTHONPATH=src uv run pytest -o testpaths= tests/psi_agent/gateway/test_feishu_manager.py -v -p no:cacheprovider --no-cov
```

预期: 全部 PASS(含既有用例, 证明薄封装没改行为)。

- [ ] **Step 6: 提交**

```bash
git add src/psi_agent/gateway/feishu/_feishu_manager.py tests/psi_agent/gateway/test_feishu_manager.py
git commit -m "refactor(feishu): workspace/session_id 派生转公开方法, 供网页应用复用同一份逻辑"
```

---

### Task 2: 身份归属判定 `_identity.py`

「这个 session 属不属于这个 open_id」是本任务里唯一的安全判定, 单独一个文件、纯函数、无 I/O, 便于密集单测。判定要覆盖三类 session: 机器人派生的私聊(`feishu-<open_id>`)、网页新建的 uuid session、别人的东西。

网页新建的 session 是随机 uuid, 光看 id 认不出主人 —— 靠 **workspace 等于该 open_id 的 workspace** 来认。这也是「决定一: 共享 workspace」带来的好处: 归属判定有了唯一依据。

**Files:**
- Create: `src/psi_agent/gateway/feishu/_identity.py`
- Test: `tests/psi_agent/gateway/test_feishu_identity.py`

**Interfaces:**
- Consumes: `FeishuManager.session_id_for()` / `workspace_for()`(Task 1)
- Produces:
  - `is_group_session(session_id: str) -> bool`
  - `owns_session(open_id: str, session_id: str, workspace: str, fm: FeishuManager) -> bool`
  - `visible_sessions(open_id: str, sessions: Sequence[SessionLike], fm: FeishuManager) -> list[SessionLike]`
  - `class SessionLike(Protocol)`: 有 `id: str` 与 `workspace: str` 两个属性
  - 常量 `GROUP_SESSION_PREFIX = "feishu-chat-"`

- [ ] **Step 1: 写失败测试**

新建 `tests/psi_agent/gateway/test_feishu_identity.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from psi_agent.gateway.feishu._feishu_manager import FeishuManager
from psi_agent.gateway.feishu._identity import (
    is_group_session,
    owns_session,
    visible_sessions,
)
from psi_agent.runtime._session_manager import SessionManager

_NO_SM = cast(SessionManager, None)


@dataclass
class _S:
    """最小 SessionLike 替身 —— 判定只看 id 与 workspace。"""

    id: str
    workspace: str


def test_is_group_session() -> None:
    assert is_group_session("feishu-chat-oc_room") is True
    # 私聊不能被当成群聊: 转义后是 ``feishu-chat_oc_x``, 只差一个字符。
    assert is_group_session("feishu-chat_oc_x") is False
    assert is_group_session("feishu-ou_alice") is False
    assert is_group_session("3f2a1b0c-uuid") is False


def test_owns_own_bot_session(tmp_path: str) -> None:
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws = fm.workspace_for("ou_alice")
    assert owns_session("ou_alice", "feishu-ou_alice", ws, fm) is True


def test_does_not_own_others_bot_session(tmp_path: str) -> None:
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws_bob = fm.workspace_for("ou_bob")
    assert owns_session("ou_alice", "feishu-ou_bob", ws_bob, fm) is False


def test_owns_web_uuid_session_by_workspace(tmp_path: str) -> None:
    """网页新建的 uuid session 认不出主人, 靠 workspace 归属认。"""
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws = fm.workspace_for("ou_alice")
    assert owns_session("ou_alice", "3f2a1b0c-uuid", ws, fm) is True
    assert owns_session("ou_bob", "3f2a1b0c-uuid", ws, fm) is False


def test_group_session_never_owned(tmp_path: str) -> None:
    """群聊第一版不显示 —— 即便 workspace 在自己名下也不算自己的。"""
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws = fm.workspace_for("chat:oc_room")
    assert owns_session("ou_alice", "feishu-chat-oc_room", ws, fm) is False


def test_empty_open_id_owns_nothing(tmp_path: str) -> None:
    """未登录(空身份)不得命中任何东西 —— 否则空 open_id 会变成万能钥匙。"""
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    assert owns_session("", "feishu-ou_alice", fm.workspace_for("ou_alice"), fm) is False
    assert owns_session("", "", "", fm) is False


def test_visible_sessions_filters(tmp_path: str) -> None:
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws_a, ws_b = fm.workspace_for("ou_alice"), fm.workspace_for("ou_bob")
    ws_room = fm.workspace_for("chat:oc_room")
    rows = [
        _S("feishu-ou_alice", ws_a),
        _S("uuid-1", ws_a),
        _S("feishu-ou_bob", ws_b),
        _S("feishu-chat-oc_room", ws_room),
    ]
    got = [s.id for s in visible_sessions("ou_alice", rows, fm)]
    assert got == ["feishu-ou_alice", "uuid-1"]


def test_path_comparison_is_normalized(tmp_path: str) -> None:
    """workspace 比对必须归一化: 尾斜杠/大小写(Windows)/相对段不该改变归属。"""
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws = fm.workspace_for("ou_alice")
    assert owns_session("ou_alice", "uuid-1", ws + "/", fm) is True
    assert owns_session("ou_alice", "uuid-1", ws + "/./", fm) is True
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH=src uv run pytest -o testpaths= tests/psi_agent/gateway/test_feishu_identity.py -v -p no:cacheprovider --no-cov
```

预期: FAIL, `ModuleNotFoundError: No module named 'psi_agent.gateway.feishu._identity'`。

- [ ] **Step 3: 实现**

新建 `src/psi_agent/gateway/feishu/_identity.py`:

```python
"""会话归属判定 —— 「这个 session 是不是这个飞书用户的」。

单独一个文件而非塞进 ``_routes.py``: 这是本包里唯一的**安全**判定, 判错的后果是
陌生人互相看见对话内容。纯函数 + 零 I/O, 于是能被单测密集覆盖(见
``tests/psi_agent/gateway/test_feishu_identity.py``)。

两类 session 各有判据:

* **机器人派生的私聊** ``feishu-<open_id>`` —— id 本身就是身份, 直接与
  ``FeishuManager.session_id_for(open_id)`` 比对。
* **网页新建的会话** —— id 是随机 uuid, 认不出主人; 靠 **workspace 等于该 open_id 的
  workspace** 认。这是「同一个人的多个会话共享同一个 workspace」这条产品决定的直接
  收益: 归属有了唯一依据, 不必再另存一张 session→owner 的表(那张表要落盘、要跟
  session 生命周期对齐, 而 workspace 本来就跟着 session 走)。

群聊 (``feishu-chat-*``) 一律判为「不属于任何人」: 第一版网页只做私聊。判据用
``session_id_for('chat:'+id)`` 的前缀而非 ``startswith('feishu-')`` —— 后者会把私聊
一起滤掉, 而私聊必须可见(它就是 IM 里那条)。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol, TypeVar

from psi_agent.gateway.feishu._feishu_manager import FeishuManager

GROUP_SESSION_PREFIX = "feishu-chat-"
"""群聊 session_id 前缀 —— 与 ``FeishuManager.session_id_for('chat:x')`` 同一套派生。

注意与私聊的一字之差: 私聊侧 ``-`` 被转义, open_id 为 ``chat-oc_x`` 的人得到的是
``feishu-chat_oc_x`` (下划线), 不会误判成群聊。
"""


class SessionLike(Protocol):
    """判定只需要 id 与 workspace 两个属性 —— 故不依赖 ``SessionInfo`` 具体类型。"""

    id: str
    workspace: str


_S = TypeVar("_S", bound=SessionLike)


def is_group_session(session_id: str) -> bool:
    """是否群聊派生的 session。第一版网页不显示它们。"""
    return session_id.startswith(GROUP_SESSION_PREFIX)


def _same_path(a: str, b: str) -> bool:
    """路径归一化比对: 尾斜杠、``.`` 段、Windows 大小写都不该改变归属结论。

    用 ``normcase(normpath(abspath(...)))`` 而非 ``samefile``: 后者要求路径**存在**,
    而列表里可能有已被删掉 workspace 的历史 session, 那时 ``samefile`` 抛 OSError。
    """
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(os.path.abspath(a))) == os.path.normcase(
        os.path.normpath(os.path.abspath(b))
    )


def owns_session(open_id: str, session_id: str, workspace: str, fm: FeishuManager) -> bool:
    """*open_id* 是否有权看 *session_id*。

    空 *open_id* (未登录) 恒为假 —— 否则空身份会变成万能钥匙。
    """
    if not open_id or not session_id:
        return False
    if is_group_session(session_id):
        return False
    if session_id == fm.session_id_for(open_id):
        return True
    # 网页新建的 uuid session: 落在本人 workspace 下即为本人所有。
    return _same_path(workspace, fm.workspace_for(open_id))


def visible_sessions(open_id: str, sessions: Sequence[_S], fm: FeishuManager) -> list[_S]:
    """从全量 session 里筛出 *open_id* 可见的那些, 保持入参顺序。"""
    return [s for s in sessions if owns_session(open_id, s.id, s.workspace or "", fm)]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
PYTHONPATH=src uv run pytest -o testpaths= tests/psi_agent/gateway/test_feishu_identity.py -v -p no:cacheprovider --no-cov
```

预期: 8 passed。

- [ ] **Step 5: 跑 lint**

```bash
uv run ruff check src/psi_agent/gateway/feishu/_identity.py tests/psi_agent/gateway/test_feishu_identity.py
uv run ruff format --check src/psi_agent/gateway/feishu/_identity.py tests/psi_agent/gateway/test_feishu_identity.py
```

预期: 全过。RUF001 若报全角字符, 把注释里的全角标点改半角。

- [ ] **Step 6: 提交**

```bash
git add src/psi_agent/gateway/feishu/_identity.py tests/psi_agent/gateway/test_feishu_identity.py
git commit -m "feat(feishu): 会话归属判定, 私聊可见群聊不可见, 8 条用例覆盖空身份与路径归一化"
```

---

### Task 3: `_auth.py` —— code 换身份 + 登录态

**open_id 必须由后端从飞书换回来, 绝不能信前端传的值。** 这是本任务的全部要点。

两个官方接口(已核官方 `.md` 文档, 非博客):

1. `POST https://open.feishu.cn/open-apis/authen/v2/oauth/token`, JSON body `{grant_type: "authorization_code", client_id: <app_id>, client_secret: <app_secret>, code: <code>}` → `{code, access_token, expires_in, ...}`。
2. `GET https://open.feishu.cn/open-apis/authen/v1/user_info`, 头 `Authorization: Bearer <access_token>` → `{code, msg, data: {name, open_id, ...}}`。

**关键陷阱**: 这两个接口失败时 **HTTP 状态码仍然是 200**, 靠 body 里的 `code != 0` 判失败(官方文档的错误码表就是 `200 | 20005 | ...` 这种形状)。只看 HTTP 状态会把「伪造 code」当成成功, 然后拿 `data` 里不存在的 `open_id`(空串)当身份 —— 空 open_id 在 Task 2 里恒为假, 表面上安全, 但会返回 500 而非 4xx, 违反验收 7。

`user_access_token` 拿到 `open_id` 后**即可丢弃**: 本产品不需要以用户身份调 OpenAPI。不存 token 就不必操心加密落盘, 也自然满足「凭证不进 `state/latest.json`」。登录态只是一张 `sid → (open_id, name, 过期时间)` 的内存表。

**Files:**
- Create: `src/psi_agent/gateway/feishu/_auth.py`
- Test: `tests/psi_agent/gateway/test_feishu_auth.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `@dataclass class Identity`: 字段 `open_id: str`、`name: str`
  - `@dataclass class FeishuAuth`: 字段 `app_id: str = ""`、`app_secret: str = ""`、`_sessions: dict[str, tuple[Identity, float]]`、`_ttl: float = 8 * 3600`
  - `FeishuAuth.configured -> bool` (property)
  - `async FeishuAuth.identity_from_code(code: str) -> Identity` —— 失败抛 `AuthError`
  - `FeishuAuth.issue(identity: Identity) -> str` —— 返回高熵 sid
  - `FeishuAuth.lookup(sid: str) -> Identity | None` —— 过期即删并返回 None
  - `FeishuAuth.revoke(sid: str) -> None`
  - `class AuthError(Exception)` —— 语义是「入参/上游拒绝」, 路由层映射成 4xx
  - `dev_open_id() -> str` —— 读 `PSI_FEISHU_DEV_OPEN_ID`, 未设置返回空串
  - 模块常量 `TOKEN_URL`、`USER_INFO_URL`、`DEV_OPEN_ID_ENV = "PSI_FEISHU_DEV_OPEN_ID"`

- [ ] **Step 1: 写失败测试**

新建 `tests/psi_agent/gateway/test_feishu_auth.py`:

```python
from __future__ import annotations

import time

import pytest

from psi_agent.gateway.feishu._auth import (
    DEV_OPEN_ID_ENV,
    AuthError,
    FeishuAuth,
    Identity,
    dev_open_id,
)


def test_not_configured_without_secret() -> None:
    assert FeishuAuth().configured is False
    assert FeishuAuth(app_id="cli_x").configured is False
    assert FeishuAuth(app_id="cli_x", app_secret="s").configured is True


@pytest.mark.anyio
async def test_empty_code_rejected_before_network() -> None:
    """空 code 不该打网络 —— 未配 app_secret 时也必须是 AuthError 而非连接错误。"""
    auth = FeishuAuth(app_id="cli_x", app_secret="s")
    with pytest.raises(AuthError):
        await auth.identity_from_code("")


@pytest.mark.anyio
async def test_unconfigured_raises_auth_error() -> None:
    with pytest.raises(AuthError):
        await FeishuAuth().identity_from_code("some-code")


def test_issue_lookup_revoke() -> None:
    auth = FeishuAuth(app_id="cli_x", app_secret="s")
    sid = auth.issue(Identity(open_id="ou_alice", name="Alice"))
    assert len(sid) >= 32  # 高熵, 不可猜
    got = auth.lookup(sid)
    assert got is not None
    assert (got.open_id, got.name) == ("ou_alice", "Alice")
    auth.revoke(sid)
    assert auth.lookup(sid) is None


def test_issue_returns_distinct_sids() -> None:
    auth = FeishuAuth(app_id="cli_x", app_secret="s")
    ident = Identity(open_id="ou_alice", name="Alice")
    assert auth.issue(ident) != auth.issue(ident)


def test_lookup_unknown_sid() -> None:
    assert FeishuAuth().lookup("nope") is None
    assert FeishuAuth().lookup("") is None


def test_expired_session_is_dropped() -> None:
    auth = FeishuAuth(app_id="cli_x", app_secret="s", _ttl=-1.0)
    sid = auth.issue(Identity(open_id="ou_alice", name="Alice"))
    assert auth.lookup(sid) is None
    assert sid not in auth._sessions  # 过期即清, 不留垃圾


def test_ttl_boundary_still_valid() -> None:
    auth = FeishuAuth(app_id="cli_x", app_secret="s", _ttl=60.0)
    sid = auth.issue(Identity(open_id="ou_alice", name="Alice"))
    assert auth._sessions[sid][1] > time.time()
    assert auth.lookup(sid) is not None


def test_dev_open_id_absent_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认配置下旁路不可用 —— 这条守的是验收 7。"""
    monkeypatch.delenv(DEV_OPEN_ID_ENV, raising=False)
    assert dev_open_id() == ""


def test_dev_open_id_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEV_OPEN_ID_ENV, "ou_dev")
    assert dev_open_id() == "ou_dev"


def test_dev_open_id_blank_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """设成空串/空白等于没设, 免得 ``PSI_FEISHU_DEV_OPEN_ID=`` 变成空身份旁路。"""
    monkeypatch.setenv(DEV_OPEN_ID_ENV, "   ")
    assert dev_open_id() == ""
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH=src uv run pytest -o testpaths= tests/psi_agent/gateway/test_feishu_auth.py -v -p no:cacheprovider --no-cov
```

预期: FAIL, `ModuleNotFoundError: No module named 'psi_agent.gateway.feishu._auth'`。

- [ ] **Step 3: 实现(上半: 模块头与数据结构)**

新建 `src/psi_agent/gateway/feishu/_auth.py`:

```python
"""飞书网页应用免登 —— 前端交来的 ``code`` 换成**后端认定**的身份。

安全前提只有一条: **open_id 由后端向飞书换回来, 绝不采信前端传的值。** 前端可以伪造
任何 body 字段, 唯一不能伪造的是「飞书认这个 code 属于谁」。因此本模块的输入只有
``code``, 输出是 ``Identity``, 中间不接受任何调用方给的身份提示。

``user_access_token`` 用完即弃
------------------------------
换到 token 后只用它调一次 ``user_info`` 取 ``open_id``/``name``, 随后丢掉, 既不存内存也
不落盘。本产品不需要以用户身份调 OpenAPI (要那个得走增量授权链路, 是另一件事)。这条
取舍顺带满足 ``desktop/_auth_store.py`` 模块头的第 1 条约定: 凭证不进
``state/latest.json`` —— 我们压根没有长期凭证可存。

登录态是一张内存表 ``sid -> (Identity, 过期时刻)``, 不落盘: Gateway 重启后大家重新免登
即可 (在飞书客户端里是无感的一次 JSAPI 调用), 用不着为此引入加密存储。

上游失败判据
------------
飞书这两个接口**失败时 HTTP 仍是 200**, 错误在 body 的 ``code`` 字段 (官方错误码表形如
``200 | 20005 | The user access token passed is invalid``)。只看 HTTP 状态会把伪造的 code
当成功、拿到空 ``open_id``, 于是错误在下游以 500 的形式冒出来。故本模块一律以
``code == 0`` 为成功判据, 其余抛 ``AuthError``, 由路由层映射成 4xx。

``PSI_FEISHU_DEV_OPEN_ID`` 旁路
-------------------------------
本机开发时飞书客户端外没有 JSAPI, 于是留一个环境变量旁路。**默认不设置即完全不可用**,
且启用时每次登录打 WARNING —— PR 755 的教训是一个写死的真实 open_id 上云后让所有访问者
变成同一个人。旁路只能由部署者显式打开, 代码里不留任何默认身份。
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
from loguru import logger

TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
"""换 ``user_access_token``。官方已把 v2 标为历史版本, 推荐 v3
``https://accounts.feishu.cn/oauth/v3/token`` (请求/响应结构与 v2 一致, 差别在 PKCE 校验
更严)。这里用 v2 是因为本流程不启用 PKCE, 两者行为等价; 迁 v3 时只改这一个常量。
"""

USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
"""拿 ``open_id`` / ``name``。只需要这两个字段, 故不申请手机号/邮箱等敏感字段权限。"""

DEV_OPEN_ID_ENV = "PSI_FEISHU_DEV_OPEN_ID"

_HTTP_TIMEOUT = ClientTimeout(total=10)


class AuthError(Exception):
    """入参无效或上游拒绝 —— 路由层一律映射成 4xx, 不是 500。"""


@dataclass
class Identity:
    """后端认定的身份。只有这两个字段是业务需要的。"""

    open_id: str
    name: str


def dev_open_id() -> str:
    """读开发旁路的 open_id; 未设置或全空白 → 空串 (即旁路不可用)。

    每次调用都重读环境变量而不缓存: 与 ``external_sessions()`` 同一个理由 —— 换来
    「改了 env 重启进程即生效」这条最简单的运维语义。
    """
    return (os.environ.get(DEV_OPEN_ID_ENV, "") or "").strip()
```

- [ ] **Step 4: 实现(下半: `FeishuAuth`)**

追加到同一文件末尾:

```python
@dataclass
class FeishuAuth:
    """免登的全部状态: 应用凭证 + 一张内存登录态表。

    ``_ttl`` 默认 8 小时 —— 一个工作日。到期后前端在飞书客户端内重新免登是无感的,
    所以不做 refresh_token 那一套 (那要求申请 ``offline_access`` 权限并加密存长期凭证)。
    """

    app_id: str = ""
    app_secret: str = ""
    _sessions: dict[str, tuple[Identity, float]] = field(default_factory=dict)
    _ttl: float = 8 * 3600

    @property
    def configured(self) -> bool:
        """两个凭证都齐才算配好 —— 缺一个就换不到 token。"""
        return bool(self.app_id and self.app_secret)

    async def identity_from_code(self, code: str) -> Identity:
        """``code`` → ``Identity``。任何失败都抛 ``AuthError``。"""
        if not self.configured:
            raise AuthError("Feishu app credentials are not configured on the Gateway")
        if not code or not code.strip():
            raise AuthError("missing code")
        async with ClientSession(timeout=_HTTP_TIMEOUT) as http:
            token = await self._exchange_token(http, code.strip())
            return await self._fetch_user_info(http, token)

    async def _exchange_token(self, http: ClientSession, code: str) -> str:
        body = {
            "grant_type": "authorization_code",
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "code": code,
        }
        data = await self._post_json(http, TOKEN_URL, body)
        token = str(data.get("access_token") or "")
        if not token:
            raise AuthError("Feishu returned no access_token")
        return token

    async def _fetch_user_info(self, http: ClientSession, token: str) -> Identity:
        try:
            async with http.get(USER_INFO_URL, headers={"Authorization": f"Bearer {token}"}) as resp:
                payload = await resp.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as e:
            raise AuthError(f"Feishu user_info request failed: {e}") from e
        data = self._unwrap(payload, what="user_info").get("data") or {}
        open_id = str(data.get("open_id") or "")
        if not open_id:
            # 上游说成功却没给 open_id: 宁可当失败, 也不让空身份流进归属判定。
            raise AuthError("Feishu user_info returned no open_id")
        return Identity(open_id=open_id, name=str(data.get("name") or ""))

    async def _post_json(self, http: ClientSession, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with http.post(
                url,
                json=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
            ) as resp:
                payload = await resp.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as e:
            raise AuthError(f"Feishu token request failed: {e}") from e
        return self._unwrap(payload, what="token")

    @staticmethod
    def _unwrap(payload: object, *, what: str) -> dict[str, Any]:
        """校验 ``code == 0``。**失败时飞书的 HTTP 状态仍是 200**, 判据只能是 body。

        错误信息里不回显 ``app_secret`` / token, 只带上游的 code 与 msg: 这些响应会进
        日志与 4xx 响应体。
        """
        if not isinstance(payload, dict):
            raise AuthError(f"Feishu {what} response is not a JSON object")
        code = payload.get("code")
        if code not in (0, None):
            msg = payload.get("msg") or payload.get("error_description") or payload.get("error") or ""
            raise AuthError(f"Feishu {what} rejected (code={code}): {msg}")
        # v2 token 接口失败时给 ``error``/``error_description`` 而不带 ``code``。
        if code is None and payload.get("error"):
            raise AuthError(f"Feishu {what} rejected: {payload.get('error_description') or payload['error']}")
        return payload

    def issue(self, identity: Identity) -> str:
        """签发登录态, 返回高熵 sid (放 HttpOnly cookie, 不可猜)。"""
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = (identity, time.time() + self._ttl)
        return sid

    def lookup(self, sid: str) -> Identity | None:
        """取身份; 过期即删并返回 None (顺手回收, 免得表只增不减)。"""
        if not sid:
            return None
        entry = self._sessions.get(sid)
        if entry is None:
            return None
        identity, expires_at = entry
        if expires_at <= time.time():
            del self._sessions[sid]
            logger.debug("FeishuAuth: login session expired, dropped")
            return None
        return identity

    def revoke(self, sid: str) -> None:
        self._sessions.pop(sid, None)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
PYTHONPATH=src uv run pytest -o testpaths= tests/psi_agent/gateway/test_feishu_auth.py -v -p no:cacheprovider --no-cov
```

预期: 11 passed。注意 `test_empty_code_rejected_before_network` 与 `test_unconfigured_raises_auth_error` 都**不打网络**, 所以离线环境也能跑。

- [ ] **Step 6: 跑 lint 与类型检查**

```bash
uv run ruff check src/psi_agent/gateway/feishu/_auth.py tests/psi_agent/gateway/test_feishu_auth.py
uv run ruff format --check src/psi_agent/gateway/feishu/_auth.py tests/psi_agent/gateway/test_feishu_auth.py
```

- [ ] **Step 7: 提交**

```bash
git add src/psi_agent/gateway/feishu/_auth.py tests/psi_agent/gateway/test_feishu_auth.py
git commit -m "feat(feishu): code 换身份, open_id 只认后端换回的值, 上游 code!=0 一律 AuthError"
```

---

### Task 4: 挂 `POST /auth/feishu` 等四条登录路由 + Gateway 透传凭证

路由落 `gateway/feishu/_routes.py`(产品线路由归本包)。cookie 名 `psi_feishu_sid`, `HttpOnly` + `SameSite=Lax` + `Path=/`。

**这里有一条要写进文档的架构变更**: `_oauth_manager.py` 模块头原话是「Gateway 侧刻意**不碰 token 交换**: 不知道 app_secret」。免登必须换 token, 所以 Gateway 从此持有 app_secret。这不是违规而是有意变更, 但**必须在两处写清**, 否则下一个人读到那句模块头会以为本任务写错了。

**Files:**
- Modify: `src/psi_agent/gateway/feishu/_routes.py`(imports、handler、`register_feishu_routes` 签名与 `app.router.add_*`)
- Modify: `src/psi_agent/gateway/__init__.py:69-78` 附近加两个字段, `:264-268` 透传
- Modify: `src/psi_agent/gateway/feishu/_oauth_manager.py` 模块头(补一句指向 `_auth.py`)
- Test: `tests/psi_agent/gateway/test_feishu_auth_routes.py`(新建)

**Interfaces:**
- Consumes: `FeishuAuth` / `Identity` / `AuthError` / `dev_open_id`(Task 3)
- Produces:
  - `app["feishu_auth"]: FeishuAuth`
  - `SID_COOKIE = "psi_feishu_sid"`(定义在 `_routes.py`)
  - `current_identity(request: web.Request) -> Identity | None` —— 供 Task 5 的过滤路由复用
  - `POST /auth/feishu` body `{code}` → `200 {open_id, name}` + `Set-Cookie`
  - `GET /auth/me` → `200 {open_id, name}` 或 `401 {error}`
  - `POST /auth/logout` → `200 {status: "ok"}`
  - `GET /feishu/app-id` → `200 {app_id}`(未配时 `app_id` 为空串, 仍是 200)
  - `Gateway.feishu_app_id` / `Gateway.feishu_app_secret` 两个字段
  - `register_feishu_routes(..., feishu_app_id: str = "", feishu_app_secret: str = "")`

- [ ] **Step 1: 写失败测试**

新建 `tests/psi_agent/gateway/test_feishu_auth_routes.py`。用 aiohttp 自带的 test utils, 不起真端口, 也**不打飞书网络**(未配凭证 → 400; 配了假凭证但 code 为空 → 400):

```python
from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from psi_agent.gateway.feishu._auth import DEV_OPEN_ID_ENV, FeishuAuth, Identity
from psi_agent.gateway.feishu._routes import SID_COOKIE, register_auth_routes


async def _client(app: web.Application) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _app(auth: FeishuAuth) -> web.Application:
    """只贴登录三条路由 —— 不需要 SessionManager, 故不造 task group。"""
    app = web.Application()
    app["feishu_auth"] = auth
    register_auth_routes(app)
    return app


@pytest.mark.anyio
async def test_missing_code_is_400_not_500() -> None:
    client = await _client(_app(FeishuAuth(app_id="cli_x", app_secret="s")))
    try:
        resp = await client.post("/auth/feishu", json={})
        assert resp.status == 400
        assert "error" in await resp.json()
    finally:
        await client.close()


@pytest.mark.anyio
async def test_non_object_body_is_400() -> None:
    client = await _client(_app(FeishuAuth(app_id="cli_x", app_secret="s")))
    try:
        resp = await client.post("/auth/feishu", data="not-json")
        assert resp.status == 400
    finally:
        await client.close()


@pytest.mark.anyio
async def test_unconfigured_gateway_is_400() -> None:
    """未配 app_secret → 4xx 而非 500。"""
    client = await _client(_app(FeishuAuth()))
    try:
        resp = await client.post("/auth/feishu", json={"code": "whatever"})
        assert resp.status == 400
    finally:
        await client.close()


@pytest.mark.anyio
async def test_client_supplied_open_id_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """前端塞 open_id 不该起任何作用 —— 未配凭证时照旧 400, 不会认下这个身份。"""
    monkeypatch.delenv(DEV_OPEN_ID_ENV, raising=False)
    client = await _client(_app(FeishuAuth()))
    try:
        resp = await client.post("/auth/feishu", json={"open_id": "ou_victim"})
        assert resp.status == 400
    finally:
        await client.close()


@pytest.mark.anyio
async def test_dev_bypass_unavailable_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """守验收 7: 默认配置下旁路不可用。"""
    monkeypatch.delenv(DEV_OPEN_ID_ENV, raising=False)
    client = await _client(_app(FeishuAuth()))
    try:
        resp = await client.post("/auth/feishu", json={"dev": True})
        assert resp.status == 400
        resp = await client.get("/auth/me")
        assert resp.status == 401
    finally:
        await client.close()


@pytest.mark.anyio
async def test_dev_bypass_works_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEV_OPEN_ID_ENV, "ou_dev")
    client = await _client(_app(FeishuAuth()))
    try:
        resp = await client.post("/auth/feishu", json={})
        assert resp.status == 200
        assert (await resp.json())["open_id"] == "ou_dev"
        resp = await client.get("/auth/me")
        assert resp.status == 200
        assert (await resp.json())["open_id"] == "ou_dev"
    finally:
        await client.close()


@pytest.mark.anyio
async def test_me_and_logout_with_issued_cookie() -> None:
    auth = FeishuAuth(app_id="cli_x", app_secret="s")
    sid = auth.issue(Identity(open_id="ou_alice", name="Alice"))
    client = await _client(_app(auth))
    try:
        resp = await client.get("/auth/me", cookies={SID_COOKIE: sid})
        assert resp.status == 200
        assert await resp.json() == {"open_id": "ou_alice", "name": "Alice"}

        resp = await client.post("/auth/logout", cookies={SID_COOKIE: sid})
        assert resp.status == 200
        assert auth.lookup(sid) is None
    finally:
        await client.close()


@pytest.mark.anyio
async def test_me_rejects_forged_cookie() -> None:
    client = await _client(_app(FeishuAuth(app_id="cli_x", app_secret="s")))
    try:
        resp = await client.get("/auth/me", cookies={SID_COOKIE: "forged-sid"})
        assert resp.status == 401
    finally:
        await client.close()


@pytest.mark.anyio
async def test_app_id_endpoint_never_leaks_secret() -> None:
    client = await _client(_app(FeishuAuth(app_id="cli_x", app_secret="super-secret")))
    try:
        resp = await client.get("/feishu/app-id")
        assert resp.status == 200
        body = await resp.json()
        assert body == {"app_id": "cli_x"}
        assert "super-secret" not in str(body)
    finally:
        await client.close()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH=src uv run pytest -o testpaths= tests/psi_agent/gateway/test_feishu_auth_routes.py -v -p no:cacheprovider --no-cov
```

预期: FAIL, `ImportError: cannot import name 'SID_COOKIE'`。

- [ ] **Step 3: 实现登录 handler**

在 `_routes.py` 的 import 区补:

```python
from psi_agent.gateway.feishu._auth import AuthError, FeishuAuth, Identity, dev_open_id
```

在 `_list_feishu_routes` 之后插入:

```python
SID_COOKIE = "psi_feishu_sid"
"""登录态 cookie 名。``HttpOnly`` 是要点: 页面脚本读不到它, XSS 也偷不走登录态。"""


def current_identity(request: web.Request) -> Identity | None:
    """当前请求的身份, 未登录返回 None。

    唯一来源是 ``HttpOnly`` cookie 里的 sid —— **不读 body/query 里的 open_id**。
    前端能伪造任何字段, 但伪造不出一个签发过的高熵 sid。会话过滤路由 (见下) 全部
    经由本函数取身份, 于是「谁在问」只有一个判据。
    """
    auth: FeishuAuth = request.app["feishu_auth"]
    return auth.lookup(request.cookies.get(SID_COOKIE, ""))


async def _auth_feishu(request: web.Request) -> web.Response:
    """``POST /auth/feishu`` —— body ``{code}`` → ``{open_id, name}`` + 登录 cookie。

    **body 里的 ``open_id`` 一律忽略**: 身份只能是 ``code`` 换回来的。前端传了也不看,
    这是本端点的安全前提。

    ``PSI_FEISHU_DEV_OPEN_ID`` 设了才有开发旁路, 且每次打 WARNING。默认不设置 → 无 code
    就是 400。
    """
    auth: FeishuAuth = request.app["feishu_auth"]
    body = await _read_json(request)
    code = ""
    if isinstance(body, dict):
        code = str(body.get("code") or "")

    if not code:
        # dev_open_id() 自己就打 WARNING (Task 3 已实现), 这里不要再打第二遍:
        # 同一次旁路登录刷两条同义告警, 只会让真正的告警更难被看见。
        bypass = dev_open_id()
        if bypass:
            return _issue_login(Identity(open_id=bypass, name=bypass), auth)
        return _error("missing code", status=400)

    try:
        identity = await auth.identity_from_code(code)
    except AuthError as e:
        # 伪造/过期 code, 或 Gateway 未配凭证 —— 都是 4xx, 不是 500。
        logger.info(f"Feishu login rejected: {e}")
        return _error(str(e), status=400)
    except Exception as e:
        logger.error(f"Unexpected error during Feishu login: {e!r}")
        return _error("login failed", status=500)
    return _issue_login(identity, auth)


def _issue_login(identity: Identity, auth: FeishuAuth) -> web.Response:
    """签发登录 cookie 并回身份。

    ``auth`` 必填而非可选: 两个调用点 (正常登录与开发旁路) 都必须签 cookie, 漏签的表现
    是登录看着成功、下一秒 ``/auth/me`` 401 —— 可选参数只会让这种漏法静默通过。
    """
    resp = _json({"open_id": identity.open_id, "name": identity.name})
    resp.set_cookie(
        SID_COOKIE,
        auth.issue(identity),
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return resp


async def _auth_me(request: web.Request) -> web.Response:
    identity = current_identity(request)
    if identity is None:
        return _error("not logged in", status=401)
    return _json({"open_id": identity.open_id, "name": identity.name})


async def _auth_logout(request: web.Request) -> web.Response:
    auth: FeishuAuth = request.app["feishu_auth"]
    auth.revoke(request.cookies.get(SID_COOKIE, ""))
    resp = _json({"status": "ok"})
    resp.del_cookie(SID_COOKIE, path="/")
    return resp


async def _feishu_app_id(request: web.Request) -> web.Response:
    """前端免登要的 appID —— **只给 app_id, 永不给 app_secret**。

    前端因此不必写死 appID (PR 755 把它连同一个真实 open_id 一起硬编码在前端, 上云后
    所有访问者都变成同一个人)。未配置时返回空串而非 404: 前端据此显示「未配置免登」
    这条可读的提示, 而不是撞一个语义不明的 404。
    """
    auth: FeishuAuth = request.app["feishu_auth"]
    return _json({"app_id": auth.app_id})


def register_auth_routes(app: web.Application) -> web.Application:
    """把登录四条路由贴到 *app*。

    与 ``register_feishu_routes`` 分开是为了让单测能只贴这几条 —— 那边会建
    ``FeishuManager``, 要求一个真的 ``SessionManager`` 与 task group。
    """
    app.router.add_post("/auth/feishu", _auth_feishu)
    app.router.add_get("/auth/me", _auth_me)
    app.router.add_post("/auth/logout", _auth_logout)
    app.router.add_get("/feishu/app-id", _feishu_app_id)
    return app
```

- [ ] **Step 4: 补全 import**

`_auth_feishu` 用到骨架的 `_read_json`。Step 3 开头那行 import 补成:

```python
from psi_agent.gateway.feishu._auth import AuthError, FeishuAuth, Identity, dev_open_id
from psi_agent.gateway.server import _error, _json, _read_json
```

**注意不要 import `DEV_OPEN_ID_ENV`**: 旁路的 WARNING 由 `dev_open_id()` 自己打(Task 3 已实现, 见 `_auth.py:68-83`), 路由层不再打第二遍, 所以这个常量在 `_routes.py` 里没有消费者, import 了就是 ruff F401。测试文件里仍然要 import 它 —— `monkeypatch.delenv/setenv` 用得到。

`_read_json`(`gateway/server.py:95`)在 body 不是 JSON 对象时返回 None, 正好让 `test_non_object_body_is_400` 落到 `missing code` 分支。

- [ ] **Step 5: 在 `register_feishu_routes` 里建 `FeishuAuth` 并调 `register_auth_routes`**

修改 `_routes.py:142-162` 的签名与函数体:

```python
def register_feishu_routes(
    app: web.Application,
    *,
    feishu_ai_id: str = "",
    feishu_workspace_root: str = "",
    feishu_app_id: str = "",
    feishu_app_secret: str = "",
) -> web.Application:
```

在 `app["oauth"] = OAuthRelay()` 之后加:

```python
    # 网页应用免登。**Gateway 从此持有 app_secret** —— 与 ``_oauth_manager`` 模块头那句
    # 「Gateway 侧刻意不碰 token 交换: 不知道 app_secret」是一次有意的变更, 不是疏漏:
    # 免登必须由后端拿 code 去换 token, 换的动作只能发生在知道 secret 的一侧, 而这一侧
    # 必须是服务端 (放前端等于公开 secret)。OAuthRelay 那条路径**照旧不碰 token**,
    # 两者互不影响。
    app["feishu_auth"] = FeishuAuth(app_id=feishu_app_id, app_secret=feishu_app_secret)
    register_auth_routes(app)
```

- [ ] **Step 6: Gateway 透传两个凭证**

`gateway/__init__.py` 在 `feishu_workspace_root` 字段之后加:

```python
    feishu_app_id: str = ""
    """飞书自建应用的 App ID (CLI 参数 > ``PSI_FEISHU_APP_ID`` 环境变量)。

    网页应用免登要用它: 前端经 ``GET /feishu/app-id`` 取, 再传给 ``tt.requestAccess``;
    后端拿它 + secret 把 code 换成 ``user_access_token``。空 = 未配, ``/auth/feishu``
    返回 400 (而非 500), 前端显示「未配置免登」。
    """

    feishu_app_secret: str = ""
    """飞书自建应用的 App Secret (CLI 参数 > ``PSI_FEISHU_APP_SECRET`` 环境变量)。

    **只在服务端使用, 永不下发前端** —— ``GET /feishu/app-id`` 只回 app_id。与 channel
    侧读的是同一对凭证 (同一个自建应用), 但两个进程各自读环境变量, 不互相传递。
    """
```

在 `:264-268` 的 `register_feishu_routes(` 调用里补两个实参, 并按「CLI > env」取值:

```python
            register_feishu_routes(
                app,
                feishu_ai_id=self.feishu_ai_id,
                feishu_workspace_root=self.feishu_workspace_root,
                feishu_app_id=self.feishu_app_id or os.environ.get("PSI_FEISHU_APP_ID", ""),
                feishu_app_secret=self.feishu_app_secret or os.environ.get("PSI_FEISHU_APP_SECRET", ""),
            )
```

先确认 `gateway/__init__.py` 已 `import os`; 没有就补。取值口径与 `channel/feishu/__init__.py:71-72` 一致(CLI 优先, 回落同名环境变量), 差别是这里**不抛异常**: 免登未配时 Gateway 其余功能照常, 只有 `/auth/feishu` 返 400。

- [ ] **Step 7: 补一句到 `_oauth_manager.py` 模块头**

把「Gateway 侧刻意**不碰 token 交换**: 不知道 app_secret」那段之后加一句, 免得读者以为 `_auth.py` 违规:

```
本模块的这条性质**只描述 OAuth 中继这条路径**。网页应用免登 (``_auth.py``) 是另一条路径,
那里 Gateway 确实持有 app_secret 并亲自换 token —— 因为免登的 code 只能由服务端换 (放前端
等于公开 secret), 而中继搬运的 code 属于发起方, Gateway 没有理由知道那边的 secret。
```

- [ ] **Step 8: 运行测试确认通过**

```bash
PYTHONPATH=src uv run pytest -o testpaths= tests/psi_agent/gateway/test_feishu_auth_routes.py -v -p no:cacheprovider --no-cov
PYTHONPATH=src uv run pytest -o testpaths= tests/psi_agent/gateway tests/integration/test_gateway.py -v -p no:cacheprovider --no-cov
```

预期: 新增 9 条全 PASS; 既有 gateway 测试不回归(`register_feishu_routes` 新参数都有默认值, 老调用点不受影响)。

- [ ] **Step 9: 提交**

```bash
git add src/psi_agent/gateway/feishu/_routes.py src/psi_agent/gateway/feishu/_oauth_manager.py \
        src/psi_agent/gateway/__init__.py tests/psi_agent/gateway/test_feishu_auth_routes.py
git commit -m "feat(feishu): /auth/feishu 三条登录路由, 缺 code 与伪造 code 都是 400, dev 旁路默认关"
```

---

### Task 5: 按身份过滤的会话路由 `/feishu/sessions` 一族

spec 要求「先把两条路各自会动到谁写清, 别直接改骨架 `_list_sessions` 的语义」。结论与理由:

| 方案 | 动到谁 | 取舍 |
|---|---|---|
| 给骨架路由加可选身份参数 | `gateway/server.py` 的 4 个 handler + ToC 的 `desktop/spa-v2`(同一批路由的另一个消费者) + `desktop/_routes.py` | 骨架从此认识「飞书身份」这个概念, 违反「骨架不认识产品线」。ToC 侧没有 open_id, 得为它想一套「无身份 = 看全部」的语义, 而那正是今天的漏洞形状 —— 一个漏传参数就退回不过滤。 |
| **在飞书这条链上单独包一层(选它)** | 只动 `gateway/feishu/_routes.py` 与 `feishu-web` 前端 | 骨架逐字节不动, ToC 零影响。新路由**默认拒绝**(未登录 401), 漏传的后果是拒而不是放行。代价是多 4 条路由, 且 `/sessions` 等骨架路由在 ToB 进程里依然存在 —— 见下面「残留敞口」。 |

**残留敞口(必须写进 PR 正文)**: 本任务只让前端不再用骨架路由, 骨架的 `GET /sessions` / `GET /sessions/{id}/history` 在 ToB 进程里**仍然无鉴权可达**。真正封堵要么在 Gateway 前面的反代(Caddy/oauth-proxy)上挡, 要么给骨架加中间件, 两者都超出本任务范围。所以本任务的验收 8 判据是「过滤路由正确」+「前端不再走裸路由」, 不是「裸路由已封」。**不要在 PR 里写成后者。**

**Files:**
- Modify: `src/psi_agent/gateway/feishu/_routes.py`
- Test: `tests/integration/test_feishu_web_sessions.py`(新建)

**Interfaces:**
- Consumes: `current_identity()`(Task 4)、`visible_sessions()`/`owns_session()`(Task 2)、`FeishuManager.workspace_for()`(Task 1)
- Produces:
  - `GET /feishu/sessions` → `200 [{id, backend_type, backend_id, workspace, agent, from_im}]` / `401`
  - `POST /feishu/sessions` body `{backend_id, agent?}` → `201 {id, ..., from_im: false}` / `401`
  - `GET /feishu/sessions/{id}/history` → `200 [...]` / `401` / `403`
  - `GET /feishu/titles`、`GET /feishu/summaries` → 只含自己会话的键
  - 响应新增字段 `from_im: bool` —— 前端据此打「来自飞书对话」角标

- [ ] **Step 1: 写失败测试**

新建 `tests/integration/test_feishu_web_sessions.py`。这条是集成测试(要真的 `SessionManager` 才能建 session), 结构照 `tests/integration/test_gateway.py:258` 的 `test_gateway_feishu_route`:

```python
from __future__ import annotations

import os

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout

from psi_agent.gateway.feishu._auth import FeishuAuth, Identity
from psi_agent.gateway.feishu._routes import SID_COOKIE, register_feishu_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager
from tests.integration.test_gateway import _start_app_on_free_port


@pytest.mark.anyio
async def test_feishu_web_sessions_are_isolated_per_identity(tmp_path: str) -> None:
    """A 看不到 B 的会话; 直取 B 的 history 被拒; 多会话共享 workspace 而各有 jsonl。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    # ``appdata`` 走 create_core_app 的关键字参数 —— 历史 jsonl 落在这个根下, 用 tmp_path
    # 隔开才不会写到开发者真实的 AppData 里。
    app = register_feishu_routes(
        await create_core_app(
            aim,
            sm,
            TitleManager(),
            appdata=os.path.join(str(tmp_path), "appdata"),
        ),
        feishu_ai_id="ai1",
        feishu_workspace_root=os.path.join(str(tmp_path), "ws"),
    )
    auth: FeishuAuth = app["feishu_auth"]
    sid_a = auth.issue(Identity(open_id="ou_alice", name="Alice"))
    sid_b = auth.issue(Identity(open_id="ou_bob", name="Bob"))

    base_url, runner = await _start_app_on_free_port(app)
    created: list[str] = []
    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as http:
            async with http.post(
                f"{base_url}/ais",
                json={
                    "provider": "openai",
                    "model": "gpt-4o",
                    "api_key": "sk-test",
                    "base_url": "https://api.example.com",
                    "id": "ai1",
                },
            ) as resp:
                assert resp.status == 201

            # 未登录 → 401, 不是「看到全部」。
            async with http.get(f"{base_url}/feishu/sessions") as resp:
                assert resp.status == 401
            async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}) as resp:
                assert resp.status == 401

            ck_a = {SID_COOKIE: sid_a}
            ck_b = {SID_COOKIE: sid_b}

            # A 连开 3 个会话 → 3 个不同 id, 同一个 workspace。
            workspaces: set[str] = set()
            for _ in range(3):
                async with http.post(
                    f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=ck_a
                ) as resp:
                    assert resp.status == 201
                    data = await resp.json()
                    created.append(data["id"])
                    workspaces.add(data["workspace"])
                    assert data["from_im"] is False
            assert len(set(created)) == 3
            assert len(workspaces) == 1  # 决定一: 共享 workspace

            # 机器人那条私聊 session (IM 侧建的) 走 /feishu/route。
            async with http.post(
                f"{base_url}/feishu/route", json={"open_id": "ou_alice", "ai_id": "ai1"}
            ) as resp:
                assert resp.status == 201
                bot_sid = (await resp.json())["session_id"]
            created.append(bot_sid)

            # 群聊 session 也建一个 —— 它必须**不出现**在私聊列表里。
            async with http.post(
                f"{base_url}/feishu/route",
                json={"open_id": "ou_alice", "chat_id": "oc_room", "chat_type": "group", "ai_id": "ai1"},
            ) as resp:
                assert resp.status == 201
                group_sid = (await resp.json())["session_id"]
            created.append(group_sid)

            # B 也建一个自己的。
            async with http.post(
                f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=ck_b
            ) as resp:
                assert resp.status == 201
                b_sid = (await resp.json())["id"]
            created.append(b_sid)

            # A 的列表: 3 个自建 + 机器人那条(带角标), 无群聊, 无 B 的。
            async with http.get(f"{base_url}/feishu/sessions", cookies=ck_a) as resp:
                assert resp.status == 200
                rows = await resp.json()
            ids = {r["id"] for r in rows}
            assert ids == {*created[:3], bot_sid}
            assert group_sid not in ids
            assert b_sid not in ids
            assert [r["from_im"] for r in rows if r["id"] == bot_sid] == [True]

            # B 的列表里只有 B 自己那条。
            async with http.get(f"{base_url}/feishu/sessions", cookies=ck_b) as resp:
                assert {r["id"] for r in await resp.json()} == {b_sid}

            # 直取别人的 history → 403, 不是内容。
            async with http.get(
                f"{base_url}/feishu/sessions/{b_sid}/history", cookies=ck_a
            ) as resp:
                assert resp.status == 403
            # 群聊的也不行。
            async with http.get(
                f"{base_url}/feishu/sessions/{group_sid}/history", cookies=ck_a
            ) as resp:
                assert resp.status == 403
            # 自己的可以。
            async with http.get(
                f"{base_url}/feishu/sessions/{created[0]}/history", cookies=ck_a
            ) as resp:
                assert resp.status == 200
            # 不存在的 → 404。
            async with http.get(
                f"{base_url}/feishu/sessions/no-such-session/history", cookies=ck_a
            ) as resp:
                assert resp.status == 404

            # titles/summaries 也按身份过滤。
            async with http.post(
                f"{base_url}/titles", json={"id": b_sid, "title": "B 的秘密"}
            ) as resp:
                assert resp.status == 200
            async with http.get(f"{base_url}/feishu/titles", cookies=ck_a) as resp:
                assert b_sid not in await resp.json()
            async with http.get(f"{base_url}/feishu/titles", cookies=ck_b) as resp:
                assert (await resp.json())[b_sid] == "B 的秘密"
    finally:
        await runner.cleanup()
        for sid in created:
            with anyio.CancelScope(shield=True):
                await sm.delete(sid)
        await aim.delete("ai1")
        await tg.__aexit__(None, None, None)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH=src uv run pytest -o testpaths= tests/integration/test_feishu_web_sessions.py -v -p no:cacheprovider --no-cov
```

预期: FAIL, `/feishu/sessions` 返回 404(路由不存在)。

- [ ] **Step 3: 实现过滤路由**

在 `_routes.py` 里 import 区补:

```python
from typing import Any

from psi_agent.gateway.feishu._identity import owns_session, visible_sessions
from psi_agent.gateway.server import _error, _json, _read_json, _session_data
from psi_agent.runtime._history_manager import HistoryManager
from psi_agent.runtime._session_manager import SessionInfo, SessionManager
from psi_agent.runtime._summary_manager import SummaryManager
from psi_agent.runtime._title_manager import TitleManager
```

`_session_data` 在 `gateway/server.py:112`, 复用它保证响应形状与骨架一致(它会补 `ai_id` / `scheduler` / `active_schedules` 三个派生字段, 自己拼会漏)。`_routes.py` 已 import 过 `SessionManager`, 合并到同一行即可; 其余几个 import 路径与 `gateway/server.py:22-27` 逐字一致。

在 `_feishu_app_id` 之后插入:

```python
def _require_identity(request: web.Request) -> Identity:
    """取当前身份, 未登录抛 ``PermissionError`` (由各 handler 映射成 401)。

    **默认拒绝**是本组路由与骨架 ``/sessions`` 的关键差别: 骨架那条无身份即返回全量,
    于是漏传身份的后果是「泄漏」; 这里漏传的后果是 401。
    """
    identity = current_identity(request)
    if identity is None:
        raise PermissionError("not logged in")
    return identity


def _web_session_data(info: SessionInfo, *, from_im: bool) -> dict[str, Any]:
    """骨架的 ``_session_data`` 再加一个 ``from_im`` —— 前端据此打「来自飞书对话」角标。

    角标本身是产品决定二: IM 里那条 session 在网页里正常显示、可续聊, 但用户要能看出
    它与 IM 共通 (在里面发言 IM 侧也看得到)。
    """
    data = _session_data(info)
    data["from_im"] = from_im
    return data


async def _web_list_sessions(request: web.Request) -> web.Response:
    """``GET /feishu/sessions`` —— 只回当前身份可见的私聊会话。

    与骨架 ``GET /sessions`` 的关系: 骨架那条**语义一行不改**(ToC 的 spa-v2 在用), 本条
    是飞书链上单独包的一层。过滤在**服务端**做 —— PR 755 在浏览器里 filter, 那只是显示
    过滤, 谁都能直接打裸路由拿全量。
    """
    try:
        identity = _require_identity(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    bot_sid = fm.session_id_for(identity.open_id)
    rows = visible_sessions(identity.open_id, await sm.list_all(), fm)
    return _json([_web_session_data(r, from_im=r.id == bot_sid) for r in rows])


async def _web_create_session(request: web.Request) -> web.Response:
    """``POST /feishu/sessions`` —— 开一个**全新**会话: 新 uuid + 新 jsonl。

    两条产品决定都落在这里:

    * **不传 ``id``** 给 ``SessionManager.create`` → 它走 ``id or _new_uuid()`` 发新 uuid,
      于是历史落到一个**新的** ``{appdata}/histories/<uuid>.jsonl``。这正是「飞书机器人
      开不了新会话、上下文一直往同一个文件里长」的解法。
    * **workspace 由 ``fm.workspace_for(open_id)`` 派生** → 同一个人的多个会话落**同一个**
      目录 (决定一)。不这么做的话每开一个会话就多一个空目录、交付物散落。派生绝不在此
      处重拼: 私聊侧 ``-`` 转义漏掉会让 open_id 为 ``chat-oc_x`` 的人与群 ``oc_x`` 撞进
      同一个目录。
    """
    try:
        identity = _require_identity(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    schedm: SchedulerManager = request.app["schedm"]
    body = await _read_json(request) or {}
    backend_id = str(body.get("backend_id") or body.get("ai_id") or "")
    try:
        info = await sm.create(
            backend_type="ai",
            backend_id=backend_id,
            workspace=fm.workspace_for(identity.open_id),
            agent=str(body.get("agent") or ""),
        )
        await schedm.ensure(info.workspace, ai_id=info.backend_id, agent=info.agent)
    except (TypeError, ValueError, KeyError) as e:
        return _error(str(e), status=400)
    except LookupError as e:
        return _error(str(e), status=404)
    return _json(_web_session_data(info, from_im=False), status=201)


async def _web_get_history(request: web.Request) -> web.Response:
    """``GET /feishu/sessions/{id}/history`` —— 只给自己的会话。

    别人的/群聊的 → 403 而非内容; 不存在的 → 404。先查存在性再判归属: 反过来会让
    「不存在」与「不属于你」都返回 403, 前端分不出「会话被删了」和「越权」。
    """
    try:
        identity = _require_identity(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    hm: HistoryManager = request.app["hm"]
    session_id = request.match_info["session_id"]
    try:
        workspace = sm.get_workspace(session_id)
    except LookupError:
        return _error(f"Session '{session_id}' not found", status=404)
    if not owns_session(identity.open_id, session_id, workspace, fm):
        return _error("forbidden", status=403)
    messages = await hm.get(workspace, session_id, appdata=str(request.app.get("appdata") or ""))
    return _json(messages)


async def _web_owned_ids(request: web.Request) -> set[str]:
    """当前身份可见的 session id 集合 —— titles/summaries 过滤共用。"""
    identity = _require_identity(request)
    fm: FeishuManager = request.app["fm"]
    sm: SessionManager = request.app["sm"]
    return {s.id for s in visible_sessions(identity.open_id, await sm.list_all(), fm)}


async def _web_list_titles(request: web.Request) -> web.Response:
    """``GET /feishu/titles`` —— 标题表里只留自己会话的键。

    不过滤的话标题本身就是泄漏: 它是首句 prompt 派生的, 等于把别人问了什么摊出来。
    """
    try:
        owned = await _web_owned_ids(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    tm: TitleManager = request.app["tm"]
    return _json({k: v for k, v in tm.get_all().items() if k in owned})


async def _web_list_summaries(request: web.Request) -> web.Response:
    try:
        owned = await _web_owned_ids(request)
    except PermissionError as e:
        return _error(str(e), status=401)
    sum_m: SummaryManager = request.app["sum_m"]
    return _json({k: v for k, v in sum_m.get_all().items() if k in owned})
```

`is_group_session` 刻意**不**在本文件 import: 群聊过滤已由 `visible_sessions` / `owns_session` 内部完成, 多引一个符号只会让人以为这里还要再滤一次(ruff F401 也会报未使用)。

- [ ] **Step 4: 注册这 5 条路由**

在 `register_feishu_routes` 里 `app.router.add_get("/feishu/routes", _list_feishu_routes)` 之后加:

```python
    # 按身份过滤的会话一族。**骨架 ``/sessions`` 一族不动** —— ToC 的 spa-v2 用的是那批,
    # 改它的语义会波及一条不相干的产品线。这里是飞书链上单独的一层, 默认拒绝(401)。
    app.router.add_get("/feishu/sessions", _web_list_sessions)
    app.router.add_post("/feishu/sessions", _web_create_session)
    app.router.add_get("/feishu/sessions/{session_id}/history", _web_get_history)
    app.router.add_get("/feishu/titles", _web_list_titles)
    app.router.add_get("/feishu/summaries", _web_list_summaries)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
PYTHONPATH=src uv run pytest -o testpaths= tests/integration/test_feishu_web_sessions.py -v -p no:cacheprovider --no-cov
```

预期: PASS。若 `_start_app_on_free_port` 从 `tests.integration.test_gateway` import 失败, 说明缺 `__init__.py`, 按 AGENTS.md 那条补齐。

- [ ] **Step 6: 确认骨架没被动过**

```bash
git diff --stat src/psi_agent/gateway/server.py
```

预期: **无输出**(骨架零改动)。有输出就是走错了方案, 回退。

- [ ] **Step 7: 提交**

```bash
git add src/psi_agent/gateway/feishu/_routes.py tests/integration/test_feishu_web_sessions.py
git commit -m "feat(feishu): /feishu/sessions 一族按身份过滤, 骨架路由零改动, A 看不到 B 有测试守"
```

---

### Task 6: 前端标准免登 `feishuAuth.ts` + `useAuth.ts`

**照官方文档实现, 不照博客。** 已核准的官方形态(取自 `open.feishu.cn` 的 `.md` 版文档 `client-docs/h5/development-guide/step-3` 与 `uYjL24iN/uUzMuUzMuUzM/requestaccess`):

- 主路径 `window.tt.requestAccess({appID, scopeList, success, fail})` —— 注意 **`appID` 大写 ID**, 网页应用必传; `scopeList: []` 表示只授予「获取用户凭证信息」权限, 正好够我们取 `open_id`/`name`。返回 `code`, **有效期 3 分钟, 只能用一次**。
- 退路 `window.tt.requestAuthCode({appId, success, fail})` —— 注意 **`appId` 小写 d**, 与上面**不是同一个拼法**。两个触发条件: (1) `window.tt.requestAccess` 不存在(JSSDK 版本过低); (2) `requestAccess` 的 `fail` 回调里 `errno === 103`(飞书客户端版本过低)。
- `index.html:13` 已同步引入 `h5-js-sdk-1.5.35.js`(本 worktree 已有, 无需改动)。已实测该 bundle 里 `requestAccess` 与 `requestAuthCode` 两个符号都在。
- `window.h5sdk.ready(cb)` 用来等 SDK 就绪; 在飞书客户端外(普通浏览器)`window.h5sdk` 不存在 —— 那时**不静默回退到任何假身份**, 而是报「请在飞书客户端内打开」并给重试按钮。

**Files:**
- Create: `src/psi_agent/gateway/feishu/feishu-web/src/services/feishuAuth.ts`
- Create: `src/psi_agent/gateway/feishu/feishu-web/src/hooks/useAuth.ts`
- Modify: `src/psi_agent/gateway/feishu/feishu-web/src/api.ts`
- Modify: `src/psi_agent/gateway/feishu/feishu-web/src/vite-env.d.ts`(声明 `window.h5sdk` / `window.tt`)

**Interfaces:**
- Consumes: `GET /feishu/app-id`、`POST /auth/feishu`、`GET /auth/me`(Task 4)
- Produces:
  - `api.ts`: `getFeishuAppId(): Promise<string>`、`login(code: string): Promise<Me>`、`loginDevBypass(): Promise<Me>`、`getMe(): Promise<Me>`、`logout(): Promise<void>`、`interface Me { open_id: string; name: string }`
  - `feishuAuth.ts`: `requestFeishuCode(appId: string): Promise<string>`、`class FeishuAuthUnavailable extends Error`
  - `useAuth.ts`: `useAuth(): { status: "loading" | "ready" | "failed"; me: Me | null; error: string; retry: () => void }`

- [ ] **Step 1: 声明 SDK 全局类型**

`src/vite-env.d.ts` 追加(现有内容保留):

```typescript
/**
 * 飞书 JSSDK 的全局对象 —— 由 ``index.html`` 里那个同步 script 注入。
 *
 * 两个 App ID 参数**拼法不同**, 是官方文档里就有的不一致, 不是笔误:
 * ``requestAccess`` 用 ``appID``, ``requestAuthCode`` 用 ``appId``。写错的表现是
 * 「网页应用必传 appID」类报错, 而非静默失败。
 */
interface FeishuRequestAccessArgs {
  appID: string;
  scopeList: string[];
  success: (res: { code: string }) => void;
  fail: (err: { errno?: number; errString?: string }) => void;
}

interface FeishuRequestAuthCodeArgs {
  appId: string;
  success: (res: { code: string }) => void;
  fail: (err: { errno?: number; errString?: string }) => void;
}

interface Window {
  h5sdk?: {
    ready: (cb: () => void) => void;
    error?: (cb: (err: unknown) => void) => void;
  };
  tt?: {
    requestAccess?: (args: FeishuRequestAccessArgs) => void;
    requestAuthCode?: (args: FeishuRequestAuthCodeArgs) => void;
  };
}
```

- [ ] **Step 2: 写 `feishuAuth.ts`**

```typescript
/**
 * 飞书网页应用免登 —— 拿一次性 ``code`` 交给后端。
 *
 * 严格照官方文档 ``client-docs/h5/development-guide/step-3`` 的兼容示例实现, 两级退路:
 *
 * 1. ``window.tt.requestAccess`` 不存在 → JSSDK 版本过低, 用 ``requestAuthCode``。
 * 2. ``requestAccess`` 的 ``fail`` 里 ``errno === 103`` → 飞书客户端版本过低, 同样退到
 *    ``requestAuthCode``; 其余 errno 是用户拒绝或真失败, 直接报错。
 *
 * **不做静默回退到假身份。** 在飞书客户端外 ``window.h5sdk`` 不存在, 这时抛
 * ``FeishuAuthUnavailable``, 由 UI 显示「请在飞书客户端内打开」+ 重试按钮。历史教训:
 * PR 755 的免登分支永不执行(它引的 SDK 不存在), 每次都掉进写死了一个真实 open_id 的
 * fallback, 上云后所有访问者都是同一个人。
 *
 * ``code`` 有效期 3 分钟且只能用一次 —— 所以每次登录都重新取, 绝不缓存。
 */

/** SDK 不在(不在飞书客户端内)。与「取 code 失败」分开, UI 的提示文案不同。 */
export class FeishuAuthUnavailable extends Error {}

const READY_TIMEOUT_MS = 10_000;

/** 等 ``h5sdk.ready``; 超时也算不可用 —— 否则页面会永远停在 loading。 */
function sdkReady(): Promise<void> {
  const sdk = window.h5sdk;
  if (!sdk) {
    return Promise.reject(new FeishuAuthUnavailable("window.h5sdk 不存在, 请在飞书客户端内打开"));
  }
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(
      () => reject(new FeishuAuthUnavailable("飞书 JSSDK 初始化超时")),
      READY_TIMEOUT_MS,
    );
    sdk.ready(() => {
      window.clearTimeout(timer);
      resolve();
    });
  });
}

function viaRequestAuthCode(appId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const fn = window.tt?.requestAuthCode;
    if (!fn) {
      reject(new FeishuAuthUnavailable("JSSDK 不支持 requestAuthCode"));
      return;
    }
    // 注意: 这里是小写 ``appId``, 与 requestAccess 的 ``appID`` 不同。
    fn({
      appId,
      success: (res) => resolve(res.code),
      fail: (err) => reject(new Error(`requestAuthCode 失败 (errno=${err.errno ?? "?"}): ${err.errString ?? ""}`)),
    });
  });
}

/** 取一次性登录 code。*appId* 由后端 ``GET /feishu/app-id`` 提供, 不写死。 */
export async function requestFeishuCode(appId: string): Promise<string> {
  if (!appId) throw new Error("后端未配置飞书 App ID, 无法免登");
  await sdkReady();
  const requestAccess = window.tt?.requestAccess;
  if (!requestAccess) return viaRequestAuthCode(appId); // JSSDK 版本过低

  return new Promise<string>((resolve, reject) => {
    // 注意: 这里是大写 ``appID``, 网页应用必传。空 scopeList = 只要用户凭证信息。
    requestAccess({
      appID: appId,
      scopeList: [],
      success: (res) => resolve(res.code),
      fail: (err) => {
        if (err.errno === 103) {
          // 客户端版本过低, 不支持 requestAccess。
          viaRequestAuthCode(appId).then(resolve, reject);
          return;
        }
        reject(new Error(`requestAccess 失败 (errno=${err.errno ?? "?"}): ${err.errString ?? ""}`));
      },
    });
  });
}
```

- [ ] **Step 3: 在 `api.ts` 里补登录一族**

追加到 `api.ts`(放在 `listAis` 之前, 与文件现有分节风格一致):

```typescript
// ---- 免登 / 身份 -------------------------------------------------------

export interface Me {
  open_id: string;
  name: string;
}

/** appID 从后端取, 不写死在前端 —— 换应用/换租户只改部署参数。 */
export async function getFeishuAppId(): Promise<string> {
  const data = await requestJson<{ app_id?: string }>("/feishu/app-id");
  return data.app_id || "";
}

export async function login(code: string): Promise<Me> {
  return requestJson<Me>("/auth/feishu", jsonPost({ code }));
}

/**
 * 无 code 登录 —— 只有后端设了 ``PSI_FEISHU_DEV_OPEN_ID`` 才会成功, 否则 400。
 *
 * 身份由**后端**的环境变量决定, 前端不传也不能传 open_id。这与 PR 755 那个前端写死
 * 真实 open_id 的做法是两件事: 这里前端没有任何身份信息可伪造。
 */
export async function loginDevBypass(): Promise<Me> {
  return requestJson<Me>("/auth/feishu", jsonPost({}));
}

export async function getMe(): Promise<Me> {
  return requestJson<Me>("/auth/me");
}

export async function logout(): Promise<void> {
  await requestJson<unknown>("/auth/logout", jsonPost({}));
}
```

并把会话一族的 URL 从骨架路由切到过滤路由 —— 改这 4 处(其余函数不动):

```typescript
export async function listSessions(): Promise<SessionInfo[]> {
  // 过滤路由: 只回当前身份的私聊会话。裸 ``/sessions`` 不按身份过滤, 前端不再用它。
  return asList(await requestJson<SessionInfo[] | { value?: SessionInfo[] }>("/feishu/sessions"));
}

export async function createSession(backendId: string): Promise<SessionInfo> {
  // **不传 id** → 后端发新 uuid → 新 jsonl。workspace 由后端按 open_id 派生, 前端不传。
  return requestJson<SessionInfo>("/feishu/sessions", jsonPost({ backend_id: backendId }));
}

export async function getSessionHistory(id: string): Promise<HistoryMessage[]> {
  const data = await requestJson<HistoryMessage[] | { value?: HistoryMessage[] }>(
    `/feishu/sessions/${encodeURIComponent(id)}/history`,
  );
  return asList(data);
}

export async function listTitles(): Promise<Record<string, string>> {
  return requestJson<Record<string, string>>("/feishu/titles");
}

export async function listSummaries(): Promise<Record<string, string>> {
  return requestJson<Record<string, string>>("/feishu/summaries");
}
```

`SessionInfo` 接口加一个字段:

```typescript
  /** 是否 IM 里那条会话(``feishu-<open_id>``) —— 列表上打「来自飞书对话」角标。 */
  from_im?: boolean;
```

`api.ts` 的模块头那段「所以这里没有任何登录相关函数……落地后在这里补 login 一族即可」现在已不成立, 改成:

```
 * 飞书免登已落地(任务 5fef7): ``login`` / ``getMe`` / ``logout`` 打的是
 * ``gateway/feishu/_routes.py`` 里的 ``/auth/*``。会话一族走 ``/feishu/sessions`` 而非裸
 * ``/sessions``: 后者不按身份过滤, 在浏览器侧 filter 只是显示过滤, 谁都能直接打裸路由
 * 拿全量。
```

- [ ] **Step 4: 写 `useAuth.ts`**

```typescript
import { useCallback, useEffect, useState } from "react";
import { getFeishuAppId, getMe, login, loginDevBypass, type Me } from "../api";
import { FeishuAuthUnavailable, requestFeishuCode } from "../services/feishuAuth";

type Status = "loading" | "ready" | "failed";

/**
 * 登录态。顺序: 先问 ``/auth/me``(已有 cookie 就免了一次 JSAPI) → 否则走免登。
 *
 * 失败**一定**留一个可见的 ``retry`` —— code 只活 3 分钟, 用户从后台切回来时上一个 code
 * 大概率已过期, 没有重试入口就只能刷页面。
 */
export function useAuth() {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    void (async () => {
      setStatus("loading");
      setError("");
      try {
        const already = await getMe().catch(() => null);
        if (already) {
          if (!alive) return;
          setMe(already);
          setStatus("ready");
          return;
        }
        const appId = await getFeishuAppId();
        const code = await requestFeishuCode(appId);
        const who = await login(code);
        if (!alive) return;
        setMe(who);
        setStatus("ready");
      } catch (err) {
        if (!alive) return;
        // 不在飞书客户端内: 试一次后端的开发旁路。它只有在后端显式设了
        // PSI_FEISHU_DEV_OPEN_ID 时才会成功, 默认配置下照样失败 —— 所以这不是
        // 「静默冒充身份」, 身份完全由后端决定。
        if (err instanceof FeishuAuthUnavailable) {
          const dev = await loginDevBypass().catch(() => null);
          if (dev && alive) {
            setMe(dev);
            setStatus("ready");
            return;
          }
        }
        setError(err instanceof Error ? err.message : String(err));
        setStatus("failed");
      }
    })();
    return () => {
      alive = false;
    };
  }, [attempt]);

  return { status, me, error, retry };
}
```

- [ ] **Step 5: 构建确认零错**

```bash
cd src/psi_agent/gateway/feishu/feishu-web
npm run build
```

预期: `tsc --noEmit` 无错, `dist/` 生成。若报 `window.h5sdk` 类型错, 检查 Step 1 的 `vite-env.d.ts` 是否漏了 `interface Window`。

- [ ] **Step 6: 提交**

```bash
git add src/psi_agent/gateway/feishu/feishu-web/src/services/feishuAuth.ts \
        src/psi_agent/gateway/feishu/feishu-web/src/hooks/useAuth.ts \
        src/psi_agent/gateway/feishu/feishu-web/src/api.ts \
        src/psi_agent/gateway/feishu/feishu-web/src/vite-env.d.ts
git commit -m "feat(feishu-web): 官方 requestAccess 免登, 两级退路, appID 从后端取不写死"
```

---

### Task 7: UI 接上多会话 —— 新会话入口、角标、上下文提示、登录屏

四件事:

1. `App.tsx` 未登录时渲染登录/重试屏(不静默放行)。
2. 「新会话」入口已存在(`TasksView` 的 `onNewTask` → `handleNewTask` → `sessions.create()`), Task 6 已把 `createSession` 切到 `POST /feishu/sessions` 不传 id, **所以多会话在这一步自动成立**。本任务只需确认建完就 `setTitle` 派生标题。
3. 「来自飞书对话」角标 —— `from_im` 透到 `Task` 模型再到卡片。
4. 「这条会话会越来越长」的提示**只挂在 `from_im` 那条上**(只有它会一直长)。

   注意措辞: 实测(见文末「验收 6 的结论与依据」)证明这条会话**并不接近上下文上限** —— 压缩一直在正常工作, 入模型量只有约 12k 字符。所以提示语只说「会越来越长、建议开新会话」, **不要写成「上下文已接近上限」**, 那是假的。也**不要**据此禁用输入框: 「可续聊」是已定的结论。

**Files:**
- Modify: `src/psi_agent/gateway/feishu/feishu-web/src/App.tsx`
- Modify: `src/psi_agent/gateway/feishu/feishu-web/src/types.ts`(`Task` 加两个字段)
- Modify: `src/psi_agent/gateway/feishu/feishu-web/src/services/taskModel.ts`(`buildTask` 透传)
- Modify: `src/psi_agent/gateway/feishu/feishu-web/src/hooks/useSessions.ts`(建完设标题)
- Modify: `src/psi_agent/gateway/feishu/feishu-web/src/components/tasks-view.tsx`(渲染角标与提示)
- Modify: `src/psi_agent/gateway/feishu/feishu-web/src/styles.css`(角标样式)

**Interfaces:**
- Consumes: `useAuth()`(Task 6)、`SessionInfo.from_im`(Task 6)
- Produces: `Task.fromIm: boolean`、`Task.contextWarning: boolean`

- [ ] **Step 1: `types.ts` 的 `Task` 加两个字段**

```typescript
  /** 是否 IM 里那条会话 —— 卡片上打「来自飞书对话」角标, 让用户知道它与 IM 共通。 */
  fromIm: boolean;
  /** 这条会话会一直变长(只有 IM 那条会), 提示用户开新会话。不代表已接近上下文上限。 */
  contextWarning: boolean;
```

- [ ] **Step 2: `taskModel.ts` 透传**

`TaskSource` 加一个字段:

```typescript
  /** 会话是否来自 IM(``from_im``)。 */
  fromIm: boolean;
```

`buildTask` 的返回对象里加:

```typescript
    fromIm: src.fromIm,
    // 上下文将满只对 IM 那条有意义: 网页新建的会话各有独立 jsonl, 不会替别人长。
    // 判据先用「历史消息条数」的替身 —— 后端目前不下发 token 用量, 故以 ``from_im``
    // 为唯一触发条件, 提示文案写成「这条会话与飞书对话共用, 会一直变长」。
    contextWarning: src.fromIm,
```

- [ ] **Step 3: `useTasks.ts` 传入 `fromIm`**

`buildTask({...})` 的入参里加一行:

```typescript
          fromIm: session.from_im === true,
```

- [ ] **Step 4: `useSessions.ts` 建完就设标题**

`create()` 目前建完只 `refresh` + `setCurrentId`。照 ToC 的模式(`HaiTunAgentWorkspace.tsx:1486` 建完立刻 `setTitle`)补一个占位标题, 免得列表里一排「未命名任务」分不出谁是谁。首句 prompt 派生的正式标题仍由 `App.tsx` 的 `generateTitle` 在首轮结束后覆盖。

先在 `api.ts` 补 `setTitle`(骨架的 `POST /titles` 已存在, 见 `gateway/server.py:194`):

```typescript
export async function setTitle(id: string, title: string): Promise<void> {
  await requestJson<unknown>("/titles", jsonPost({ id, title }));
}
```

`useSessions.ts` 的 `create()` 改成:

```typescript
  const create = useCallback(async () => {
    if (!defaultAiId) {
      setError("没有可用模型, 无法新建会话");
      return "";
    }
    try {
      // 不传 id → 后端发新 uuid → 新 jsonl。这是「网页里能开多个会话」的全部机制。
      const info = await createSession(defaultAiId);
      // 先落一个占位标题: 首轮结束后 App.tsx 会用首句 prompt 派生的标题覆盖它。没有占位
      // 的话列表里会是一排「未命名任务」, 多会话反而更难用。
      const placeholder = `新会话 ${new Date().toLocaleString("zh-CN", { hour12: false })}`;
      await setTitle(info.id, placeholder).catch(() => undefined);
      setTitles((prev) => ({ ...prev, [info.id]: placeholder }));
      await refresh();
      setCurrentId(info.id);
      return info.id;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return "";
    }
  }, [defaultAiId, refresh]);
```

import 里加 `setTitle`。

- [ ] **Step 5: `tasks-view.tsx` 渲染角标与提示**

在任务卡片的标题行旁加角标, 卡片内加提示。改动落在渲染 `task.title` 的那个元素附近(照该文件现有 class 命名风格):

```tsx
              {task.fromIm && (
                <span className="ht-badge-im" title="这条会话与飞书 IM 里的对话共通, 双向可见">
                  来自飞书对话
                </span>
              )}
```

```tsx
              {task.contextWarning && (
                <p className="ht-card-hint">
                  这条会话与飞书对话共用同一份上下文, 会越来越长。建议点上方「新建任务」开一个新会话。
                </p>
              )}
```

- [ ] **Step 6: `styles.css` 补两个样式**

```css
/* 「来自飞书对话」角标 —— 与 IM 共通的那条会话。 */
.ht-badge-im {
  display: inline-block;
  margin-left: 0.5rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: #e8f1ff;
  color: #2b5fa8;
  font-size: 0.72rem;
  vertical-align: middle;
}

.ht-card-hint {
  margin: 0.4rem 0 0;
  color: #8a6d3b;
  font-size: 0.78rem;
  line-height: 1.5;
}
```

- [ ] **Step 7: `App.tsx` 加登录屏**

在 `App()` 顶部调 `useAuth()`, 并在会话相关 hooks **之前**做分支返回。注意 hooks 不能条件调用, 所以把登录屏做成一个独立组件, `App` 里先判断再渲染:

```tsx
import { useAuth } from "./hooks/useAuth";

/**
 * 登录门禁。免登失败时给**可见的重试入口** —— code 只活 3 分钟, 从后台切回来时上一个
 * 大概率已过期; 没有重试按钮用户只能刷页面。绝不静默放行成某个默认身份。
 */
function LoginGate({ status, error, onRetry }: { status: string; error: string; onRetry: () => void }) {
  return (
    <div className="ht-app ht-login-gate">
      {status === "loading" ? (
        <p>正在通过飞书登录…</p>
      ) : (
        <>
          <p role="alert">登录失败: {error || "未知原因"}</p>
          <p className="ht-card-hint">请在飞书客户端内打开本应用。若已在客户端内, 点下方重试。</p>
          <button type="button" className="ht-btn" onClick={onRetry}>
            重试登录
          </button>
        </>
      )}
    </div>
  );
}

export function App() {
  const auth = useAuth();
  if (auth.status !== "ready") {
    return <LoginGate status={auth.status} error={auth.error} onRetry={auth.retry} />;
  }
  return <AuthedApp userName={auth.me?.name || ""} />;
}
```

把现有 `App` 的函数体整体改名成 `function AuthedApp({ userName }: { userName: string })`, 并把 `ChatView` 的 `userName=""` 换成 `userName={userName}` —— 验收 10 要的「真实飞书客户端里能拿到自己的名字」就靠这个显示出来。

`styles.css` 补:

```css
.ht-login-gate {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  min-height: 100vh;
  text-align: center;
  padding: 2rem;
}
```

更新 `App.tsx` 模块头那句「还没有登录 —— 飞书免登与身份隔离归任务 5fef7」:

```
 * 登录: ``useAuth`` 走飞书 JSSDK 免登, 未就绪时渲染 ``LoginGate`` 而非放行。会话列表走
 * ``/feishu/sessions``(服务端按身份过滤), 「新建任务」开的是全新 session + 全新 jsonl。
```

- [ ] **Step 8: 构建确认零错**

```bash
cd src/psi_agent/gateway/feishu/feishu-web
npm run build
```

预期: `tsc --noEmit` 无错。若报 `Task` 缺字段, 是 Step 1/2 漏了 —— `buildTask` 的返回类型是 `Task`, 少一个字段就编译不过, 这正是要的。

- [ ] **Step 9: 提交**

```bash
git add src/psi_agent/gateway/feishu/feishu-web/src
git commit -m "feat(feishu-web): 新会话入口接过滤路由, IM 那条打角标并挂上下文提示, 未登录不放行"
```

---

### Task 8: 实测 —— 云上 jsonl 大小、双向可见、并发行为、真机免登

本任务**不写代码**, 只产出 PR 正文要的实测数字。spec 里 4 条验收(4/5/6/10)都要求「真验, 不是读代码推断」, 所以单列一个任务, 且**每一项都要如实记录, 包括没验到的**。

前置量测(验收 6)可能推翻「可续聊」这条产品决定 —— **先做它**。

**Files:**
- Create: 无(结果写进 PR 正文与本文件末尾的「实测记录」)

- [x] **Step 1: 量云上 `feishu-*` jsonl 的实际大小(验收 6) —— 已在计划阶段完成**

**已测完, 不要重做。** 结果与结论见本文件末尾「验收 6 的结论与依据」一节, 以及「实测记录」表验收 6 行。要点: 25,295,195 字节 / 11,914 行 / 119 条 `compacted`; 入模型量 ≈12k 字符; 结论**可续聊**。

取样口径备忘(后来人复量时照此): 生产机 `root@account.genuineknowledge.cn`, 取 **luolin 的独立容器** `psi-agent-luolin`(负责人自己的会话样本太少), appdata 实际是容器内 `/workspace/.psi/appdata`, **全程只读**。

- [x] **Step 2: 据 Step 1 的数字决定「可续聊」还是「只读」 —— 已在计划阶段完成, 结论「可续聊」**

**本步已做完, 实施时不要重做, 也不要实现「只读」分支。** 完整依据见本文件末尾「验收 6 的结论与依据」一节, 摘要:

- 喂给模型的只有 system + 压缩摘要 + 最后一条 `compacted` 之后的 4 行 ≈ 12k 字符(`history_display.py:196-238` 会把中间全删掉), 距上限很远 → 「点进去就卡住/立刻触发压缩」的担心不成立。
- 真实代价在**展示路径**: `HistoryManager.get` 不截断, 打开这条会话要拉 ~1.8MB JSON。这是性能问题, 只读治不了(只读也要拉这 1.8MB)。

因此: 保持「可续聊」, 不加禁用输入框的判断, 历史分页不进本 PR。PR 正文照实写明 1.8MB 这个已知代价。

- [ ] **Step 3: 双向可见(验收 4)**

1. 在飞书 IM 里给机器人发一句 `来自 IM 的测试消息`。
2. 网页里刷新, 打开带「来自飞书对话」角标那条会话 —— 应能看到这句。
3. 网页里在同一条会话发一句 `来自网页的测试消息`。
4. 回 IM 侧确认历史里也有(IM 侧看不到网页那条的话, 直接查文件):

```bash
tail -5 <appdata>/histories/feishu-<open_id>.jsonl
```

两句都在同一个 jsonl 里即为通过。记录: 通过与否, 以及**网页发的那句在 IM 客户端里是否可见**(可能只在文件与网页里可见, IM 客户端不会主动推送历史 —— 这是预期行为, 但要写清)。

- [ ] **Step 4: 并发行为(验收 5)**

spec 的结论「一个 Session 一把锁, 所以是排队不是交错」是读 `session/agent.py:234,364` 得出的, **本步必须实测复核**。

1. 网页里发一句长任务(比如「写一篇 500 字的说明」)。
2. **不等它结束**, 立刻在 IM 里给同一个机器人发一句 `并发测试`。
3. 记录三件事:
   - 两个回复是否各自完整(有没有内容交错串场)。
   - IM 那句的回复**等了多久**才开始(即是否真在排队)。
   - 排队期间**网页前端的表现**(是否毫无反应/一直转圈/报超时)。

若前端在排队时无任何提示, 按 spec 补一个「正在处理其他请求」的兜底提示: 在 `useChatTurn` 里给 `send` 加一个超过 N 秒无首个 chunk 就显示该提示的计时器。**这条改动只有在实测确认前端确实没反馈时才做**, 别为假想现象加代码。

- [ ] **Step 5: 真机免登(验收 10)**

在真实飞书客户端里打开网页应用, 确认右上/对话区显示的是**自己的名字**。

需要的配置: 开发者后台「添加应用能力 → 网页应用」里配好桌面端/移动端首页地址; 应用可用范围包含自己; Gateway 侧 `PSI_FEISHU_APP_ID`/`PSI_FEISHU_APP_SECRET` 已配。

拿不到就**如实说卡在哪**(缺 appID / 缺应用配置 / 缺可访问域名 / SDK 报了什么 errno), **不要用 `PSI_FEISHU_DEV_OPEN_ID` 旁路冒充跑通**。旁路只用于本机开发, 它在 PR 正文里不能作为验收 10 的证据。

- [ ] **Step 6: 多会话判据(验收 1、2) —— 看文件不看 UI**

网页里连开 3 个会话, 各发一句不同的话, 然后:

```bash
ls -la <appdata>/histories/
# 预期: 3 个新的 <uuid>.jsonl
head -1 <appdata>/histories/<uuid1>.jsonl
head -1 <appdata>/histories/<uuid2>.jsonl
head -1 <appdata>/histories/<uuid3>.jsonl
# 预期: 三句话各自只出现在自己那个文件里
curl -s http://127.0.0.1:8765/feishu/sessions -b "psi_feishu_sid=<sid>" | python -c "import json,sys; print({r['workspace'] for r in json.load(sys.stdin)})"
# 预期: 集合大小为 1(3 个会话共享同一个 workspace), 不含群聊 session
```

记录三个 jsonl 的文件名与各自行数。

- [ ] **Step 7: 把结果写进本计划末尾的「实测记录」一节**

见文件末尾预留的表格, 逐项填。没做到的写「未验证 + 原因」, 不留空。

---

### Task 9: 文档三向同步 + 全量回归

**Files:**
- Modify: `src/psi_agent/gateway/feishu/feishu-web/AGENTS.md`
- Modify: `src/psi_agent/gateway/feishu/__init__.py`(模块头补新路由)
- Modify: `AGENTS.md`(仓库根, 若其中有 gateway 路由清单)

- [ ] **Step 1: 改 `feishu-web/AGENTS.md`**

现在那份写的是「当前只是脚手架, 零业务」「不做: 登录 / `/auth/feishu`、会话列表、对话收发」—— 本任务后全部不成立。重写「这是什么」与「边界」两节:

```markdown
## 这是什么

飞书侧 (ToB) 的 Web 前端 —— 「海豚一号」自建应用的**网页应用**能力, 与同一个应用的机器人
能力共用后端。

技术栈与 ToC 的 `spa-v2` 保持一致: Vite + React 19 + TypeScript。

## 为什么有它: 机器人开不了新会话

飞书机器人侧的 session 是**确定性派生**的 (`_feishu_manager.py` 的 `session_id_for`):
私聊永远是 `feishu-<open_id>` 一条, `route()` 幂等复用 + adopt, 所以同一个人**无法开第二
个会话**。历史落 `{appdata}/histories/{session_id}.jsonl`, 一个 session 一个文件 → 会话
内容与压缩一直往同一份文件里写, 上下文只增不分。

网页应用就是为解决这个: 「新建任务」走 `POST /feishu/sessions` **不传 id**, 后端发新 uuid,
于是新 session + 新 jsonl。

## 三条产品决定 (已拍定, 别再改方案)

1. **同一个人的多个会话共享同一个 workspace** —— session 各自独立 (各自一份 jsonl),
   workspace 共用一个目录。否则每开一个会话就多一个空目录、交付物散落。workspace 由后端
   `FeishuManager.workspace_for(open_id)` 派生, **前端不传 workspace**。
2. **IM 那条 session 在网页里正常显示、可续聊**, 打「来自飞书对话」角标, 双向可见。上下文
   将满的提示只挂在这一条上 (只有它会一直长)。
3. **第一版只做私聊**, 群聊 session (`feishu-chat-*`) 不显示。过滤精确到只滤群聊 ——
   用 `!startsWith('feishu-')` 会把私聊一起滤掉, 与决定 2 冲突。

## 身份与免登

- 免登走官方 JSSDK: `index.html` 同步引 `h5-js-sdk-1.5.35.js` → `h5sdk.ready` →
  `tt.requestAccess({appID, scopeList: [], ...})` 拿 code → `POST /auth/feishu`。
  两级退路见 `src/services/feishuAuth.ts` 模块头 (JSSDK 旧 / 客户端旧 `errno===103`)。
- **appID 从后端 `GET /feishu/app-id` 取, 不写死在前端。**
- **open_id 由后端向飞书换回来**, 前端传什么都不看。登录态是 HttpOnly cookie
  `psi_feishu_sid`。
- 会话一族走 `/feishu/sessions`(服务端按身份过滤), **不走裸 `/sessions`** —— 后者不过滤,
  在浏览器里 filter 只是显示过滤, 谁都能直接打裸路由拿全量。

## 已知敞口

骨架的 `GET /sessions` / `GET /sessions/{id}/history` 在本进程里**仍然无鉴权可达**。本轮
只做到「前端不再用它 + 过滤路由默认拒绝」, 真正封堵要靠 Gateway 前面的反代或骨架中间件,
是另一件事。

`POST /feishu/route` 同样无鉴权, 且**采信 body 里的 `workspace`**。这条是 channel 用的, 本
轮一行未改(合并基线就这样), 但它把上面那个敞口稍稍放宽: 谁能打到这个进程, 就能建一条
workspace 落在**别人目录**下的会话。读取面被本轮的归属判定挡住了(群聊恒不可见、别人的私聊
workspace 对不上), 但「往别人目录里写」这件事还在。封堵手段同上, 不在本轮范围。
```

「常用命令」「两条容易踩的约定」「相关位置」三节保留, 把「相关位置」里的 `../../server.py` 改成 `../../_routes.py`(装配函数已搬到本包)。

- [ ] **Step 2: 补 `gateway/feishu/__init__.py` 模块头**

那里写着「没有任何新业务路由」, 现在有 9 条了。把该句改成列举: `/feishu/auth/login`、`/feishu/auth/me`、`/feishu/auth/logout`(**终审改了前缀**: 裸 `/auth/me` `/auth/logout` 被 desktop 那条产品线占着, 同 app 重复注册不报错、先注册者胜出, 占了就静默失效)、`/feishu/app-id`、`/feishu/sessions`(GET/POST)、`/feishu/sessions/{id}/history`、`/feishu/titles`、`/feishu/summaries`, 并注明「骨架 `/sessions` 一族语义未改, ToC 不受影响」。

- [ ] **Step 3: 跑全量回归(验收 9)**

先做控制实验拿基线 —— worktree 里必须带 `PYTHONPATH=src`, 否则测的是主 checkout 的 src:

```bash
git stash
PYTHONPATH=src uv run pytest -p no:cacheprovider --no-cov -q 2>&1 | tail -30 > /tmp/baseline.txt
git stash pop
PYTHONPATH=src uv run pytest -p no:cacheprovider --no-cov -q 2>&1 | tail -30 > /tmp/after.txt
diff /tmp/baseline.txt /tmp/after.txt
```

Windows 基线 57-62 failed 之间浮动(asyncio 子进程 NotImplementedError + 硬编码管道名被残留进程占着)。判据是**失败集合逐条相同**, 不是数字相同 —— 数字会浮动。若出现基线里没有的失败项, 那才是回归。

对比失败集合(而非计数):

```bash
PYTHONPATH=src uv run pytest -p no:cacheprovider --no-cov -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort > /tmp/after_ids.txt
# 与 baseline 同法生成 /tmp/baseline_ids.txt 后
diff /tmp/baseline_ids.txt /tmp/after_ids.txt
```

- [ ] **Step 4: lint + format 全过**

```bash
uv run ruff check .
uv run ruff format --check .
cd src/psi_agent/gateway/feishu/feishu-web && npm run build
```

- [ ] **Step 5: 提交**

```bash
git add src/psi_agent/gateway/feishu/feishu-web/AGENTS.md src/psi_agent/gateway/feishu/__init__.py AGENTS.md
git commit -m "docs(feishu-web): AGENTS.md 三向同步, 补三条产品决定与骨架裸路由敞口"
```

---

## 实测记录(Task 8 填, PR 正文照抄)

| 项 | 判据 | 结果 | 备注 |
|---|---|---|---|
| 验收 1 多会话 | `histories/` 出现 3 个不同 jsonl, 各含自己的对话 | **已测(2026-08-29, 本机跑真实路由 + mock 模型)**: 连开 3 个会话 → `6213177f6fca456d9df9dd18007dbd8f.jsonl`、`d4abdb7506d948e39177baefe7dfd13b.jsonl`、`ff19041670124d29854b0d02d8304527.jsonl`, 各 173 字节 / **3 行**。三句暗号各自**只**出现在自己那个文件里(逐文件 grep 验证, 非 UI 列表) | 判据落在文件上, 见下「验收 1/2/5 的实测口径」 |
| 验收 2 共享 workspace | 3 个会话 workspace 集合大小为 1 | **已测**: 集合大小 **1**, 实际目录 `…\ws\ou_measure`(3 个会话同一个) | 同上 |
| 验收 3 列表 | IM 那条可见带角标; 私聊出现、群聊不出现 | **部分已验**: 「私聊出现、群聊不出现」有自动化测试守住(`test_feishu_web_sessions_are_isolated_per_identity`: IM 那条 `from_im=true` 在列表里, `feishu-chat-*` 那条不在)。角标本身**未在真机目视确认** —— 代码链路已核到底(`api.ts:28` → `useTasks.ts:72` → `taskModel.ts` → `tasks-view.tsx:71` 每行渲染) | 目视确认与验收 10 一并卡在真机, 见下。**终审补正**: 群聊过滤原先那条断言并不吃劲(传的是群自己的 workspace, `_same_path` 已先返回 False), 删掉过滤全绿; 已改成传本人 workspace 并加一条 workspace 落在本人名下的群会话, 变异复核两条都红 |
| 验收 4 双向可见 | IM 发的网页能看到; 网页发的进同一 jsonl | **未验证** —— 需要真实飞书客户端 + 已部署可访问域名, 本机拿不到。spec 明写这条「要真验, 不是读代码推断」, 所以**不做代码推断的结论**, 如实留空 | 卡在: 缺已部署的网页应用首页地址与真机飞书账号(详见下方「未验证项」) |
| 验收 5 并发 | 是否排队 / 排队多久 / 前端表现 | **已测(2026-08-29, 本机同一 session 两个并发请求 + 故意放慢的 mock 模型)**: **确认排队, 不交错**。A 于 t=0.4s 发出, t=2.43s 首个 chunk, t=4.86s 结束; B 于 t=1.75s 插进来, **响应头 0.0s 就拿到**, 但首个 chunk 要等到 t=5.48s(即 A 结束之后), 排队 **3.73s**。两个回复各自完整且无串场(A=`片段0…片段4`, B=`片段0…片段4`) | **不需要补提示**: `useChatTurn.ts:31` 的 `setSending(true)` 在 fetch **之前**就置位, 排队期间前端已显示打字指示(`chat-thread` 的 `typing={sending}`)与「正在回复…」占位, 不是无反应。计划明说「只有实测确认前端确实没反馈时才做」, 故按实测**不加代码** |
| 验收 6 jsonl 大小 | 云上 `feishu-*` 字节数与行数 | **已测(2026-08-29)**: 生产机 `psi-agent-luolin` 容器 `/workspace/.psi/appdata/histories/` 下唯一一条 `feishu-ou_…e777.jsonl` = **25,295,195 字节 / 11,914 行**(另有 2 个 `.bak-*` 备份不计)。角色分布 system 1 / user 3013 / assistant 5651 / tool 3130 / **compacted 119** | **选「可续聊」** —— 依据见下方「验收 6 的结论与依据」 |
| 验收 7 登录健壮 | 无 code→4xx; 伪造 code→4xx; dev 旁路默认不可用 | **两条已验, 一条只有单元级覆盖**。已验(`test_feishu_auth_routes.py`, 9 条用例): 不带 code → **400**; 非对象 body → 400; 未配置 Gateway → 400; **前端传 open_id 被忽略**; **未设 `PSI_FEISHU_DEV_OPEN_ID` 时旁路不可用**(`test_dev_bypass_unavailable_by_default`); 伪造 cookie 的 `/auth/me` 被拒; `/feishu/app-id` 不泄 secret。**缺口: 「伪造 code → 4xx」没有走完整路由的用例** —— 现有的是 `_auth.py` 单元级(空 code 走不到网络、上游 `code!=0` 一律 `AuthError`、`data` 非 dict 也 `AuthError`), 路由把 `AuthError` 映成 4xx 这一跳靠读代码, 未被测试钉住 | 前端侧也核过: `loginDevBypass()` 只 POST 空 body, 身份完全由后端定。**缺口已在 Task 9 补上**(`test_forged_code_is_4xx_not_500`, 假上游按飞书真实失败形状造: HTTP 200 + body `code` 非零), 且变异复核确认吃劲 —— 其中发现假上游 payload 漏 `access_token` 会被另一条兜底接住, 已补 |
| 验收 8 身份隔离 | A 看不到 B; 直取 B 的 history→403 | **已验(自动化)**: `test_feishu_web_sessions_are_isolated_per_identity` —— A 的列表里没有 `b_sid`; 直取 B 的 history → **403**(响应体是静态 `"forbidden"`, 不泄露元信息); `/feishu/summaries` 同样守住(未登录 401 + 跨身份不可见) | **裸路由敞口另记**: 骨架自己的 `GET /sessions`、`GET /sessions/{id}/history` 在 ToB 进程里**仍未鉴权**, 本 PR 未关闭, 见「已知敞口」一节 |
| 验收 9 全量回归 | 失败集合与基线逐条相同 | **已测(2026-08-31, 含终审修复后重跑)**: 带改动 **57 failed / 1514 passed / 7 skipped**; `git stash` 后对照 **57 failed / 1512 passed / 7 skipped**(passed 差 2 = 终审补的两条守卫用例)。`comm` 两个方向均为空 → **失败集合逐条相同, 零回归** | 判据是集合不是数字: 57 在 Windows 已知基线 57-62 下沿, 数字浮动不算回归, 集合多出项才算。命令必带 `PYTHONPATH=src` |
| 验收 10 真机免登 | 飞书客户端内显示自己的名字 | **未验证** —— 卡在真机, 详见下方「未验证项」。**没有用 `PSI_FEISHU_DEV_OPEN_ID` 旁路冒充跑通** | 代码侧已就位: `ChatView` 的 `userName` 已从 `""` 换成 `auth.me?.name`, 名字一旦换回就会显示 |

### 未验证项与卡点(如实记录, PR 正文照抄, 不得含糊)

以下两条 spec 要求「真验」的验收**没有验到**, 原因是同一个: 需要**真实飞书客户端**打开一个**已部署、可访问域名**上的网页应用, 而这要开发者后台配置 + 线上部署, 本机做不到。

- **验收 4(双向可见)**: 未验证。要在 IM 里发一句、网页里看到、再从网页发一句、回 IM 侧确认落在同一个 `feishu-<open_id>.jsonl`。
- **验收 10(真机免登显示自己的名字)**: 未验证。

具体卡在:
1. 开发者后台「添加应用能力 → 网页应用」的桌面端/移动端**首页地址尚未配置**(需要一个飞书能访问到的 https 域名指向本 Gateway 的 `/feishu-web/`)。
2. 本机没有真实飞书客户端环境 + 对应账号可用范围。
3. `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` 是否已配到目标 Gateway 未确认。

**没有用 `PSI_FEISHU_DEV_OPEN_ID` 旁路冒充跑通** —— spec 明确禁止拿旁路当验收 10 的证据, 也没这么做。

验收 3 的角标只做到「代码链路核到底 + 群聊过滤有自动化测试」, **目视确认同样卡在真机**。

复验这两条时要额外注意: `feishu-web/vite.config.ts` 的 dev proxy 已补上 `/auth` 与 `/feishu`(commit `0bb07882`), 但**代理本身没在真机跑过**(当时没起 Gateway), 所以真机复验顺带就把它验了。

### 验收 1/2/5 的实测口径(2026-08-29 本机测, 可复现)

三条都是**跑真实 HTTP 路由 + 真实落盘 jsonl**, 只把上游模型换成 mock —— 量的是会话与文件的归属、以及锁的排队行为, 与模型输出无关。用的是两个**临时**量测脚本(`tests/integration/test_step6_measure.py`、`test_step4_measure.py`), 量完即删、**不入 git**(Task 8 按计划不写代码)。复量照此重建即可:

- **appdata 必须用 `PSI_APPDATA` 环境变量顶掉**。`create_core_app(appdata=…)` 只管 Gateway 自己的读路径; Session 侧是自己 `resolve_appdata_root()`(`_appdata.py:24-33`: explicit → `PSI_APPDATA` → platformdirs)。第一次量的时候没设它, 历史直接写进了开发者真实的 `%LOCALAPPDATA%\Haitun\histories\`, 量到的不是本次的东西 —— 这是个容易静默量错的坑。
- **mock 的 chunk 必须与 `tests/integration/test_gateway.py:_chunk` 同形**(带 `id`/`object`/`created`/`model`)。缺字段时上游解析抛 `int() argument … NoneType`, 整段回复变成 `[Upstream Error]`, 于是量到的是**错误处理的时序**而不是真实流式 —— 第一次跑就踩了, 两条回复都是 `[Upstream Error]` 才发现。
- 验收 5 的量法: 同一个 session 上并发发两个请求(B 延后 0.3s 插队), mock 每个 chunk 之间 `sleep(0.6)` 把窗口拉开, 分别记「发出 / 收到响应头 / 首个 chunk / 结束」四个时刻。

验收 5 的一个值得写进 PR 的细节: **响应头是立刻返回的, 但流里一个字节都不来**。因为 `session/agent.py:364` 的 `async with self._lock` 把 `response.prepare()` 也裹在锁里之后才写 chunk —— 客户端拿到的是一个"已连接但长时间静默"的流, 不是超时、也不是报错。所以前端只要在发出时就置 `sending`(现状确实如此)就有反馈; 若哪天改成"等首个 chunk 才显示指示", 这里就会退化成看起来毫无反应。

### 验收 6 的结论与依据(已测, 推翻「只读」回退)

**结论: 选「可续聊」, 且不需要做「只读」回退。** 25MB 这个数字看着吓人, 但它不是喂给模型的量。

量测环境: 生产机 `root@account.genuineknowledge.cn`, 容器 `psi-agent-luolin`, 全程只读(只跑 `ls` / `wc` / `python3` 读取)。**没有动负责人自己的 open_id 会话** —— 按负责人指示改用 luolin 的独立容器取样。

三个数字, 依次收窄:

1. **文件总量**: 25,295,195 字节 / 11,914 行 / 全文 content 合计约 1238 万字符。
2. **喂给模型的量**: 最后一条 `compacted` 在第 11,909 行, 也就是**倒数第 5 行**。`history_display.py:196-238` 的规则是 —— 存在 `compacted` 时, 把 index 0 的 `system` 与最后一条 `compacted` 之间的消息**全部删掉**, 把压缩摘要并进 system, 再丢掉 `compacted` 本身。所以实际入模型的是 system + `[Compacted History]`(11,573 字符) + 之后 4 行 `trigger.silent`, 合计 **约 12,071 字符**。距任何上下文上限都远。整个 25MB 里 99.9% 是已归档的历史轮次, **永远不会到模型那儿**。全文 119 条 `compacted` 说明压缩一直在正常工作, 不是异常。
3. **回给前端的量**: 这才是唯一真实的代价。`_get_history` → `HistoryManager.get`(`_history_manager.py:86-116`)**读整个文件、不截断、不按 compaction 裁剪** —— 它走的是展示路径, 与上面的模型路径是两条独立的路。可展示行(user/assistant, 排除 `trigger.silent`)约 **5,056 行 / 188 万字符 ≈ 1.8MB 文本**。

所以真正的风险不是「点进去就卡住或立刻触发压缩」(spec 当初的担心), 而是**打开这条会话时一次拉 ~1.8MB JSON**: 首屏慢, 手机端更明显。这是性能问题, 不是上下文问题, 「只读」治不了它 —— 只读同样要拉这 1.8MB。

**据此对计划的调整:**
- Task 8 Step 2 的「只读」分支**判定为不需要**, 不实现。Task 7 不加禁用输入框的判断。
- 「上下文将满」的提示(决定二)**仍然保留**, 但文案不该说「已接近上限」—— 实测不接近。改为提示这条会话历史很长、建议开新会话, 挂在带 `from_im` 角标那条上。
- 分页/截断历史**不进本 PR**: 属独立的性能优化, 且要动骨架的 `HistoryManager`(ToC 的 spa-v2 也在用同一条路), 超出本任务范围。PR 正文照实写明这个已知代价与它的量级。









### 终审(全分支复审)发现的三个缺陷与修法

三条都在本分支内修掉了(`37ebed86`), 且**每条都做过变异实测**, 不是读码推断。记在这里是因为
它们各自暴露了一类判据缺失, 后续改这块代码时容易再踩。

1. **免登路由被 desktop 静默遮蔽(默认就中)。** 飞书原先占裸 `GET /auth/me` 与
   `POST /auth/logout`, 而 `desktop/_routes.py:376-377` 在 `authm` 非 None 时注册同名同方法
   两条 —— `authm` **默认非 None**(`resolve_endpoint()` 有内置默认域名, 只有显式
   `PSI_AUTH_ENDPOINT=""` 才关)。**aiohttp 对同 path 重复 `add_get` 不报错**(只有在同一个
   resource 上重复加同 method 才抛 `RuntimeError`), 而是各建一个 resource 由**先注册者胜出**;
   `gateway/__init__.py` 先 desktop(:272)后飞书(:279), 于是飞书那两条永不执行。
   修前实测: 带有效飞书 cookie 打 `/auth/me` 得 **401**, 同一 cookie 打 `/feishu/sessions` 得
   **200**; 登出走 desktop, `auth.lookup(sid)` 仍非 None(sid 未撤、cookie 未清)。
   修法: 三条统一挪到 `/feishu/auth/{login,me,logout}`。修后实测: 重复路由 **NONE**、
   `/feishu/auth/me` **200**、登出后 sid **确实被撤**、desktop 那条 `/auth/me` 不受影响。
   守它的用例: `test_auth_routes_survive_desktop_coexistence`(改回裸路径会红)。

2. **群聊过滤零有效覆盖。** 删掉 `_identity.py` 里 `is_group_session` 那两行, 8+1 个用例全绿。
   根因: 既有断言传的是**群自己的** workspace, `_same_path` 早已返回 False, 群过滤从来不是
   决定因素 —— 与 docstring 声称的「即便 workspace 在自己名下」不符。可达性是真的:
   `/feishu/route` 无鉴权且采信 body 的 `workspace`, 群会话的 workspace 可以落到某人目录下,
   那时挡住多人群聊上下文的**只剩这行过滤**。已改成传本人 workspace 并加一条落在本人名下的
   群会话, 变异复核两条都红。

3. **`POST /feishu/sessions` 不采信 body 这条没测。** 改成采信 body 的 `id`/`workspace` 后
   11 个用例全绿。危害: Alice 传 Bob 的 workspace → 会话建进 Bob 目录, 而 `owns_session`
   按 workspace 认主, 该会话随后**归 Bob 所有**。已补一段劫持尝试并变异复核。

**贯穿性教训**: 用例绿不等于判据吃劲。本分支一共四例同一模式(上面三条 + Task 9 那个假上游
payload 漏 `access_token` 被兜底接住)。补断言必须做变异复核, 且要确认它**红在正确的理由上** ——
有一次红在 `AttributeError` 上, 那种红看着也像成功。

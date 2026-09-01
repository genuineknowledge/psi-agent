# 实施计划：出向跨容器发文件

Spec: `docs/superpowers/specs/2026-08-24-outbound-cross-container-file-design.md`
分支：`fix/external-container-recovery-v2` 的 worktree（不基于 origin/main —— v2 对
`channel/feishu/client.py` 有 +181/-36 且已部署到生产）

## 设计要点

一句话：**`FileChunk` 带上来源地址；地址是 TCP 时 channel 先取字节再上传，否则照旧交路径给 SDK。**

字节能直接上传的依据：`lark_channel/channel/_coerce.py:156-164` 把 `bytes` 判成
`MediaSource(kind="buffer")`，`channel/outbound/media/uploader.py:146-147` 直接用它上传，
不碰文件系统。生产镜像内 SDK 1.2.0 已核。

## 步骤

### 1. session 侧：`GET /files` 返回字节

`src/psi_agent/session/server.py` —— 现在只有两个路由（`:30-31`）。加第三个。

- `GET /files?path=<绝对路径>` → 200 + `application/octet-stream` 字节流；
  `Content-Disposition` 带 basename。
- **范围限定在 session 的 workspace 根内**（负责人已定）。判定照仓内既有惯例
  `gateway/_workspace_manager.py:132-152`：`resolve()` 展开 symlink 与 `..` 后，
  要求 `== root` 或以 `root + os.sep` 开头，越界 → 403。`resolve()` 必须在判定前做，
  否则 `/workspace/pub/../../etc/passwd` 这类写法能绕过。
- 体积上限：飞书文件上限 30MB，超限 → 413，不读进内存。
- 其他状态码：路径为空 → 400；不存在 → 404；是目录 → 400。
- workspace 根从哪儿来：`SessionAgent._workspace_path`（`session/agent.py:235`）。
  为空时（未配 workspace 的 session）该端点一律 403 —— 无根可限，不如不服务。
- 不加鉴权，与同口的 `/chat/completions` 一致（该口未映射到宿主，仅 docker 网络内可达）。

### 2. `FileChunk` 加来源地址

`src/psi_agent/channel/_types.py:9` —— 加 `source: str = ""`。

带默认值，所以既有构造点（入向 `encode_input`、telegram）全部不用改。字段语义写进
docstring：「这个文件的字节从哪儿取；空 = 本地文件系统可直接读」。

### 3. `ChannelCore` 填来源地址

`src/psi_agent/channel/_core.py:126` —— scanner 产出的 `FileChunk` 补上 `source`。

- 仅当 `self.session_socket` 以 `http://` / `https://` 开头时填（跨容器就是这个形态，
  见生产 `PSI_FEISHU_EXTERNAL_SESSIONS`）。unix socket / named pipe 留空 → 走原路径，
  本地零行为变化（V5）。
- 注意 scanner（`_markers.py:53-65`）返回的是 `FileChunk`，在 `_core.py` 侧改字段而非
  改 scanner —— scanner 是纯解码，不该知道传输地址。

### 4. `_send_file` 支持字节

`src/psi_agent/channel/feishu/client.py:193-200`。

- 签名加来源地址参数。有地址 → `GET <addr>/files?path=…` 取字节，把 `bytes` 交给 SDK
  （`{"image": {"source": <bytes>}}`，失败 fallback `{"file": {...}}`）。
- **file fallback 必须显式带 `file_name`**：`{"file": {"source": bytes, "file_name": basename}}`。
  走 path 时 SDK 从 `os.path.basename(path)` 取名（`uploader.py:155`），走 buffer 时
  `gather_buffer` 只能回 `default_name`（`uploader.py:146-147`），不给名字用户会收到
  名为 `upload` 的附件 —— 「可点击下载」就废了一半。这是本步最容易漏的一处。
- 取字节失败（连不上/403/404/超限）：`logger.error` 记明地址与路径，然后**照旧尝试本地
  路径**。本地大概率也失败，但保持「尽力发」且不改变本地 session 的行为。
- 私密区守卫留在原处（`client.py:487`，`FileChunk.path` 上判）—— 判的是路径归属，
  与字节从哪来无关，不动它（V6）。

### 5. 调用点串起来

`client.py:490` 把 `chunk.source` 传给 `_send_file`。

### 6. 测试（V4）

新增，覆盖出向跨容器路径：

- `tests/psi_agent/session/test_server.py`：`GET /files` 正常取字节；越界 403（用
  `..` 逃逸 + symlink 逃逸各一例）；不存在 404；超限 413；无 workspace 根 403。
- `tests/psi_agent/channel/test__core.py`：TCP 地址时 `FileChunk.source` 被填；
  unix socket 时留空。
- `tests/psi_agent/channel/feishu/test_feishu.py`：`_send_file` 带地址时取字节并把
  `bytes` 交给 `channel.send`（断言 `file_name` 也传了）；不带地址时仍交 path 且
  **不发 HTTP 请求**（V5）。用既有 `_driving_channel`（`:1402`）而不是 `_fake_channel`
  —— 后者不执行 markdown 回调，盯 `_produce` 内部的断言会假绿（上一轮踩过）。
- 端到端一条：session 起真 aiohttp server（照 `test_server.py` 既有写法），channel 侧
  从它取字节，断言拿到的字节与磁盘一致。

**提交前先验测试能失败**：stash 掉产品代码改动跑一次，确认新用例红（V4 要求）。

### 7. 本机跑测试

```
uv run pytest -o testpaths= tests/psi_agent/session/test_server.py tests/psi_agent/channel tests/psi_agent/channel/feishu
```

`-o testpaths=` 必须写在路径**之前**，否则路径被静默吞掉（收到 4380 项即踩坑）。
Windows 上全量 57 failed 是既有基线不是回归。

### 8. 三向同步

- `src/psi_agent/channel/AGENTS.md`：`[SEND:]` 那段（`:39`）补出向跨容器取字节
- `src/psi_agent/session/AGENTS.md`：端点表（`:347` 附近）补 `GET /files`

## 部署与验收（改生产前先问用户）

1. 镜像三层核验：整份替换 `src/` 不打补丁 → build 前验输入 → **build 后验镜像内产物**
   （第三层是 8-18 事故缺的那层）。
2. 停机重启：不做滚动更新（飞书出向 WS 单连接，必须先停旧再起新）。重启 gateway
   **必须连带重建 oauth-proxy**（`network_mode: "service:gateway"`，不重建则显示 Up 但
   8090 已死）。不能 `docker compose down -v`（pgvector 在命名卷
   `deploy_fusion_memory_pgdata`）。
3. 回退点：`psi-agent-gateway:backup-20260822` / `backup-20260822-174853`，
   都指向 `896467e05f72`；当前生产跑 `527deff72043`。
4. V1-V3 实测：负责人已定**把自己的 open_id 临时加进 `PSI_FEISHU_EXTERNAL_SESSIONS`
   自测**两个容器。注意两点：
   - 这会让测试消息进入该容器**那一个 session** 的历史与 workspace（罗霖/成 xx 的），
     测试消息保持简短。
   - 验完**必须撤掉这条 env 并重启**（连带重建 oauth-proxy）。撤除步骤记进 A 段。
   - **文件名必须新造**：`/workspace/真知问题解决与求助SOP（优化版）.md` 有硬链接
     （inode 701477），用它验会假绿。

## 不做

- 不撤生产的硬链接与多余 ssh 公钥（验收通过后另行处置）
- 不挂共享卷、不改 compose 拓扑
- 不动入向 `_attachment_handoff`
- 不开 PR

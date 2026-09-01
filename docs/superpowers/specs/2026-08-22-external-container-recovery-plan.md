# 独立容器降级修复与代码基线收敛

**描述：** 修复 8-18 构建导致的罗霖/成 xx 独立容器路由降级、21 名用户记忆服务失效，并把线上未入库代码收敛回 git，随后走一遍正式发布流程验证发布文档。

**版本号：** 1.0

**状态：** 待评审

**适用范围：** psi-agent 生产环境（新加坡节点 account.genuineknowledge.cn）

**关键词：** external-sessions、代码基线、Fusion Memory、发布流程、W/H/A/T

**创建人：** @zsd

**审核人：** @待补

**关联文档：**

- `docs/haitun-delivery/psi-agent-release-to-cloud.md` —— 发布流程，本任务阶段 2 照它执行并回头修正它
- `docs/haitun-delivery/migration-log.md` —— 8-20 迁移实录，已证实迁移未改代码
- 《真知开发执行 SOP》v1.0 —— 本文档结构依据

***

## W —— 是什么

### 1. 解决谁的什么痛点

**痛点一 · 罗霖的对话上下文一分为二。**

罗霖（`ou_c77e484d4cf5699947408c9448a8e777`）的飞书私聊本应转发到独立容器
`psi-agent-luolin`，实际落在主 gateway 进程内的本地 Session。同一个 open_id 因此有两份互不相干的历史：

| 位置 | 行数 | 最后写入 | 由什么驱动 |
|---|---|---|---|
| `workspace-luolin`（独立容器） | 9875 | 持续写入中 | 容器自己的定时 trigger（作业投递） |
| `workspace`（主容器） | 4523 | **8-21 20:01 冻结** | 飞书消息 |

他在飞书里说的话，他容器里的 agent 看不到；容器里做的作业投递，飞书侧看不到。

**痛点二 · 成 xx 同样降级。** `ou_716d18b92e20c74726821c79f02826d7` 情况相同：独立容器侧 3931 行（写入中），主容器侧 3844 行（8-21 19:57 冻结）。

**痛点三 · 21 名用户的长期记忆存不进去。**
`memory_user_not_configured` 累计报错 358 次。token 表 `/workspace/.psi/memory_tokens.json`
只有 24 人且 mtime 停在 8-07 15:43，而有历史的 session 有 45 人，**21 人不在表里**。
根因见 `workspace/tools/_fusion_memory_config.py:180-186`：查不到 entry 时，
`auto_register_feishu` 为假就抛错，而 `FUSION_MEMORY_AUTO_REGISTER_FEISHU` 在三个 `.env` 中均未配置，
默认值 `False`（同文件 `:48`）。

**痛点四 · 跨容器文件交接坏了，用户看到裸 `[SEND:]` 标记。**

2026-08-22 11:12 成 xx 向海豚要一份 md，收到的不是可点击下载的附件，而是一行原文
`[SEND:/workspace/真知问题解决与求助SOP（优化版）.md]`。生产日志同刻实证：

```
[Lark] 11:14:35 [WARNING] outbound: materialize blocked: could not read local file
'/workspace/真知问题解决与求助SOP（优化版）.md': [Errno 2] No such file or directory
```

文件确实存在，但**在另一个容器里**：`psi-agent-chengxx` 容器内 7346 字节，
`psi-agent-gateway` 容器内不存在。

机制：飞书 WS 长连接只能在主容器（同一 App 只允许一条），所以 channel 跑在主容器；
而 `_send_file()` 是拿路径读**本地**文件上传（`channel/feishu/client.py:167`：
`channel.send(chat_id, {"image": {"source": path}})`）。消息转发到独立容器后，
agent 输出的 `[SEND:]` 路径指向独立容器的卷，主容器的 `/workspace` 是另一个卷，
读不到 → `materialize blocked` → 标记未被消费，原样当文本发出。

**这不是昨天引入的新缺陷，是昨天只补了转发的一半。** 消息转发通了，文件交接没通。
原设计发现并修过这个问题——`origin/fix/external-session-attachments` 分支的两个 commit
标题即为证：`7d5c9225`「独立容器的会话不再『文件明明收到了却说没收到』」、
`1aeb6c34`「让跨容器附件交接块自带取件说明」。这两个 commit 从未进入部署线，
所以修好路由后若不一并 cherry-pick，文件交接仍然是坏的。**故这两个 commit 由「顺带捞」
提为阶段 1 必做项。**

**痛点五 · 线上跑的代码不在任何提交里，且缺两个已发布的修复。** 详见 H 段核对结论。

### 2. 做完什么样算完（验收标准，可判定）

| 编号 | 验收标准 | 判定方式 |
|---|---|---|
| **V1** | 生产 gateway 运行的代码等于某个明确 commit，无 `docker cp` 痕迹 | 容器内 99 个 `.py` 按字节 md5 与该 commit 逐一比对，全同 |
| **V2** | 罗霖飞书私聊进入 `psi-agent-luolin` 容器 | 发一条测试消息，`workspace-luolin` 侧历史行数增加，主容器侧不增 |
| **V3** | 成 xx 飞书私聊进入 `psi-agent-chengxx` 容器 | 同 V2 判法 |
| **V4** | 压缩劫持修复与工具结果截断修复在线上生效 | 容器内 `grep MIN_SUMMARY_CHARS session/agent.py`、`grep MAX_TOOL_RESULT_CHARS session/history_display.py` 均命中 |
| **V5** | 新飞书用户不再报 `memory_user_not_configured` | 重启后新用户首次对话，token 表出现其条目；日志无新增该错误 |
| **V6** | 21 名存量用户记忆可用 | 逐个确认已在 token 表中 |
| **V7** | 两个独立容器的历史/记忆/文件按 D4 决策处置完毕，无数据丢失 | 处置前后文件数与行数对账 |
| **V8** | 发布流程文档 §38 的错误结论已修正 | 文档中不再出现「与任何提交都不一致（最佳 89/97）」 |
| **V9** | `psi-agent-release-to-cloud.md` §5 八条判据全部通过 | 照文档逐条执行 |
| **V10** | pgvector 数据未丢失 | `deploy_fusion_memory_pgdata` 卷仍在，表数量与发布前一致 |
| **V11**（观察项，非判据） | 出向跨容器发文件的实测现象 | 让独立容器的 agent 发一个文件，记录飞书侧收到的是附件还是裸 `[SEND:]` 文本。**本轮预期仍是裸文本**——阶段 1 没修出向，负责人已定「先不管」。见记录⑨ |

### 3. 明确不做什么

- **不做两份历史的自动合并。** 两侧内容来源不同（飞书对话 vs 定时 trigger），
  时间线交错，机器合并会产出语义错乱的上下文。处置方式见 D4，需负责人拍。
- **不在本任务内重构 `.private` 私密区机制。** 容器内当前**没有** `_private_space.py`，
  宿主 `src/` 里有一份（8-20 14:27）但不参与运行（gateway 只 bind mount `workspace`）。
  是否恢复该机制单独立项，本任务只保证不再退化。
- **不改 Dockerfile 的境内镜像源。** 发布文档 §568 已记录这是已知设计缺口，与本任务无关。
- **不动 ToC 栈**（`psi-cloud`、`psi-litellm`）。

***

## H —— 怎么做

### 4. 有哪几种做法，为什么选这个

**决策 D1 · 代码基线取哪儿**

| 候选 | 取舍 | 结论 |
|---|---|---|
| A. 还原成镜像里的原始代码 | 镜像版本恰恰**缺** external-sessions，还原即让罗霖永久降级 | ✗ |
| B. 以 `deploy-214-envelope-tombstone` 为基线 | 该分支有原实现，但相对当前生产 39 处不同 + 14 个文件缺失，过于陈旧，merge 会大面积回退 | ✗ 仅作参考 |
| C. **以 `origin/main` 为基线，补回 external-sessions** | 生产 93/99 文件已与 main 逐字节相同，差异面最小；main 还含生产缺失的两个修复 | ✓ **选定** |

**决策 D2 · external-sessions 用哪份实现**

| 候选 | 取舍 | 结论 |
|---|---|---|
| A. 沿用昨晚 `docker cp` 的临时实现 | 已在生产验证可路由，但无测试、未 review、且 `_private_space` 接线是断的 | ✗ |
| B. **参考 `deploy-214-envelope-tombstone` 原实现，在 main 上重做并补测试** | 有原始设计可依，能补齐测试与文档 | ✓ **选定** |

原实现位置：`origin/deploy-214-envelope-tombstone:src/psi_agent/gateway/_feishu_manager.py:42`
（`external_sessions()`）、`:39`（`_EXTERNAL_ENV_KEY`）、`:165-168`（路由分支）。
一并 cherry-pick `origin/fix/external-session-attachments` 的 `7d5c9225`、`1aeb6c34` 两个后续修复。

**决策 D3 · 记忆服务修复的时机**

记忆服务修复需改 `.env` + 重启 gateway，而重启会让 `docker cp` 的代码消失、罗霖立刻再次降级——
**两件事在「重启」这一点上冲突**。

| 候选 | 取舍 | 结论 |
|---|---|---|
| A. 今天单独抢修记忆服务 | 21 人早一天可用，但要么再 `docker cp` 一轮延续不受控代码，要么牺牲罗霖 | ✗ |
| B. **并入正式发布窗口一次做完** | 记忆服务是功能降级（存不进长期记忆）而非完全不可用，可等一天 | ✓ **选定（负责人已定）** |

**决策 D4′ · 两个独立容器的历史/记忆/文件如何处置**

现状事实：

| 维度 | 罗霖 | 成 xx |
|---|---|---|
| 独立容器 workspace | 214M | 54M |
| 独立容器侧历史 | 9875 行，写入中 | 3931 行，写入中 |
| 主容器侧历史 | 4523 行，8-21 20:01 冻结 | 3844 行，8-21 19:57 冻结 |
| 独立容器 token 表 | 有，仅 1 条自己的 | **无 `memory_tokens.json`** |
| 文件 | 私密文件在独立容器（含 67M inbox） | 在独立容器 |

**关键事实：主容器侧两份历史都已冻结**，冻结时刻正是 8-21 20:01 前后 `docker cp` 生效、
路由切到独立容器的时刻。所以主容器侧是一段**起止明确、有界、可校对**的区间
（8-18 降级起 → 8-21 20:01 路由恢复止，约 3 天），不是持续增长无法划界的数据。

三个候选：

- **D4-a 保留双份，不合并。** 主容器侧改名归档留证。代价：那 3 天飞书对话的上下文 agent 读不到。
- **D4-b 归档后把飞书对话部分人工挑拣追加到独立容器侧。** 上下文最完整，但两侧时间线交错。
- **D4-c 以主容器侧覆盖独立容器侧。** ✗ 直接否决——会丢掉独立容器侧近 6000 行作业投递记录。

**选定 D4-a′（拆成两步，归档必做、迁移待定）：**

1. **归档（阶段 2 无条件执行）：** 主容器侧两个文件改名保存，不删。无论后续选什么，
   这一步都是回退底线。
2. **迁移与否降级为阶段 2 之后的独立判断，不阻塞发布。** 需先看那 3 天的内容是否有实质工作
   上下文（方案讨论、决策）还是零碎问答——这要读内容才能定，本次未读（涉他人对话，
   且量大：罗霖 21.9M / 成 xx 3.4M）。

若决定迁移，两条技术约束必须先解决：

- 独立容器侧同期在写 trigger 记录，**时间线是交错的**。按时间穿插可能让 agent 读到
  「自己回答了没被问过的问题」。
- `.jsonl` 含 `compacted` 等结构行，盲目 append 可能破坏压缩状态。

**⚠️ 上下文溢出风险：** 罗霖那份历史 21.9M / 9875 行，已存在一次
`bak-ctxoverflow-20260819` 备份（25.6M）——**它撑爆过上下文**。再追加 4523 行有触发同样
问题的风险。故即使决定迁移，也只挑拣关键内容，**不整段追加**。

记忆侧另需处置：成 xx 容器**没有 token 表**，`FUSION_MEMORY_TOKEN_MAP_FILE` 却已配置——
修好路由后他的记忆会立刻走上与主容器同一个报错路径。罗霖容器的表里只有自己 1 条，需确认够用。

### 5. 别人怎么做的，我这样是否更好

**无直接外部对标**——「线上工作树快照与 git 失联」是本仓特有的运维状态，不是通用工程问题。

仓内既有惯例可循，且本方案严格沿用：

- 发布流程照 `psi-agent-release-to-cloud.md` 执行，不自创路径。其四条硬约束（不用 `down -v`、
  oauth-proxy 必须跟着重建、不滚动更新、先打备份 tag）全部保留。
- 「以代码为准，回头修计划表述」是 SOP 附录 A 的 A 段要求，本任务阶段 3 反过来修发布文档，
  正是这条的应用。

比现状强在哪：这是第一次让生产代码等于一个可指名的 commit。在此之前任何一次发布都是
「拿一个未知基线覆盖另一个未知基线」（发布文档 §38 原话）。

### 开工前核对结论（SOP 触发式要求）

W/H 依据的诊断部分写于 8-20（发布文档 §38），开工前已核对代码，**发现三处与诊断不符**：

**不符一 · 偏差规模。** §38 称「线上 src 与本仓任何一次提交都不一致（最佳 89/97）」。
实测：**容器内 99 个 `.py` 与 `origin/main`（`a9579c7d`）相同 93、不同 6、缺失 0。**

根因是 §38 的核验方法有缺陷——经 ssh 文本通道取文件会被转成 CRLF，导致每个文件 md5 全变，
产生「全不一致」假象。同一文件 `session/ai_client.py`：ssh 取回 5010 字节 / md5 `7320b26b`，
容器内按字节读 4894 字节 / md5 `d81a7f3f`，**CR-stripped 后与 `origin/main` 完全相同**。
我第一次量也踩了同一个坑。**核验必须在容器内按字节做。**

**不符二 · 差异文件清单不对。** §38 列的 7 个文件与实测的 6 个只有 1 个重合
（`gateway/_feishu_manager.py`）。实测差异分两组，边界很干净：

A 组 · mtime `Aug 18 16:25`（8-18 构建的工作树，共 3 个）

| 文件 | 差异 | 方向 |
|---|---|---|
| `session/agent.py` | 缺 `MIN_SUMMARY_CHARS` / `MIN_SOURCE_CHARS` / `HIJACK_ECHO_PREFIXES` | 线上**落后** main |
| `session/history_display.py` | 缺 `MAX_TOOL_RESULT_CHARS` 与 `_TRUNCATION_MARKER` | 线上**落后** main |
| `ai/server.py` | 多 2 行 deepseek 配置（见下） | 线上**独有，未入库** |

B 组 · mtime `Aug 21 20:00`（昨晚 `docker cp`，共 3 个）：
`gateway/_feishu_manager.py`、`gateway/server.py`、`gateway/__init__.py`。

**`cli.py` 已从本清单移出。** 首次核对时（基线为两天前的 `51ce46e9`）它显示线上缺
`SelfUpdate` 命令 44 行、判为「线上落后」。开工前重新 fetch 后发现 main 已经 revert 掉了
自更新功能（`b9017880` revert #694、`0834c1a7` 改为组件化全量更新，`51ce46e9..a9579c7d`
共 5 个 commit，`src/` 下净删 955 行）。生产 `cli.py` 1010 字节 / md5 `2176b6ab`，
与 `a9579c7d` **逐字节相同**。所以它从来不是落后，是 main 后来退回到了生产已有的状态。

**教训：基线必须是当次 fetch 的 origin/main，不能用本地缓存的远程引用。** 本次 `.git/FETCH_HEAD`
停在 8-20 09:48，两天未更新，直接导致一条结论方向判错。这条要写进阶段 3 的附录 A。

**结论：真正需要人判断的只有 1 个文件**（`ai/server.py` 那 2 行，且已定为丢弃），
其余 5 个靠部署 main + 重做 external-sessions 自然收敛。工作量比 §38 描述的小一个量级。

**不符三 · `_private_space.py` 的位置。** §38 称它「线上在跑，任何提交里都不存在」。
实测：**运行中的容器 `/app/src` 里没有这个文件**。宿主 `/srv/haitun/psi-agent/src/` 里有一份
（8-20 14:27），但 gateway 只 bind mount 了 `workspace`，该目录不参与运行。§38 量的是宿主目录，
量错了对象。且容器内 `_feishu_manager.py` 已无 `_private_space` 的 import（grep 零命中），
私密区隔离当前是断开状态。

### deepseek 那 2 行具体是什么

位置：生产容器 `/app/src/psi_agent/ai/server.py`，在剥离请求体私有字段之后、
设置 `stream_options.include_usage` 之前：

```python
    body.pop("routing", None)
+   if provider == "deepseek":
+       body["reasoning_effort"] = "high"
    stream_opts = body.get("stream_options", {})
```

作用：当上游 provider 是 deepseek 时，强制在请求体注入 `reasoning_effort="high"`，
把模型的推理强度顶到最高档。对其他 provider 无影响。

**它不属于任何人的分支——git 里根本没有这段代码。** 实测搜索范围与结果：

```
git log --all -S'reasoning_effort' -- src/     → 零命中
遍历全部远程分支 tip 的 ai/server.py           → 零命中
遍历全部本地分支                                → 零命中
git log --all -G'reasoning_effort'             → 8 个 commit，但逐个核查其
                                                 ai/server.py 全为 0 命中
```

那 8 个 `-G` 命中是噪音：匹配的是 `build/` 下 PyInstaller 打包产物
（`Analysis-00.toc`、`PYZ-00.pyz` 等）里碰巧出现的字符串，以及 kanban 自身的
checkpoint commit，与 `ai/server.py` 无关。

**结论：这 2 行是直接在生产工作树上改的，从未进入版本控制。** 这比「某位同事的分支没合」
更严重——8-18 以 main 为基构建时丢掉的不只是别人分支里的功能，还有这种只存在于服务器
文件系统上的改动。也说明 mtime `Aug 18 16:25` 不可作为作者线索：那次构建把整个工作树的
时间戳都刷新了，真正该问的是**谁动过生产 `src/`**，范围比一个人大。

**处置（负责人已定）：本次修复直接丢弃这 2 行。** 已向同事确认过。丢弃后 6 个差异文件
全部可机械收敛到 main，阶段 1 不再有需要人判断的项。

***

## A —— 执行过程

> 开工后追写。按 SOP 规则一，本段在 W/H 落定后才开始填。

### 阶段 0 · 固化现状（只读 + 打 tag，不重启）

- [x] 容器内 99 个 `.py` 按字节取回本地存证（唯一权威的生产代码）
- [x] 宿主 `/srv/haitun/psi-agent/src` 一并取回（判断 `_private_space.py` 去留时要用）
- [x] `docker tag psi-agent-gateway:local psi-agent-gateway:backup-20260822`
      （发布文档 §6.2：现在只有一个 tag，没有退路）
- [ ] 通知运维与同事：**正式部署前不要碰 gateway 容器**（需人工发出，未完成）

**存证路径：** `F:\code\psi-agent-evidence\20260822-prod-gateway\`（仓外，不入 git）。
服务器侧副本 `/tmp/psi-evidence-20260822`，可清理。

| 目录 | 来源 | 文件总数 | 其中 `.py` | 校验结果 |
|---|---|---|---|---|
| `container-app-src/src` | 容器 `psi-agent-gateway:/app/src` | 283 | **99** | 283/283 字节一致 |
| `host-srv-src/src` | 宿主 `/srv/haitun/psi-agent/src` | 279 | 97 | 279/279 字节一致 |

取回方式全程二进制：`docker cp <容器>:/app/src -` 出 tar 流、md5 清单由**容器内 python3 按
`open(p,"rb")` 计算**、tar 走 `scp`、本地解包后逐文件重算 md5 与容器内清单比对，
`mismatch=0 missing=0 extra=0`。刻意不走 `ssh 'cat'` 文本通道，即 §38 假阳性的来源。
`__pycache__` 全部排除。清单文件 `container-app-src.md5`、`host-srv-src.md5` 随存证留档。

镜像 tag 已就位，`backup-20260822` 与 `local` 同指 image `896467e05f72`（构建于 8-18 16:39）。
本阶段对生产只有 `docker tag` 一次写操作，打完四个容器 uptime 未变
（gateway 15h、chengxx 18h、luolin 40h、oauth-proxy 15h），无重启。

顺带实证三点：① `_private_space.py` 在宿主清单命中、容器清单零命中，**「不符三」成立**；
② A 组 mtime 全为 `Aug 18 16:25`、B 组全为 `Aug 21 20:00`，边界与「不符二」一致；
③ V4 当前确为不通过——容器内 `MIN_SUMMARY_CHARS`、`MAX_TOOL_RESULT_CHARS` 均零命中，
而 `PSI_FEISHU_EXTERNAL_SESSIONS` 命中 1 次（`docker cp` 的实现在位）。

> ⚠️ 阶段 0 完成前的风险窗口：gateway 当前代码是 `docker cp` 进去的，
> `docker compose up -d` 或 restart 重建容器即消失，罗霖立刻再次降级。
> **存证已完成，该窗口对「代码丢失」已关闭**（副本可回灌）；
> 但「容器被重建 → 罗霖降级」的风险仍在，至正式发布窗口为止。

### 阶段 1 · 本地代码收敛（基线 `origin/main`）

- [x] A 组两个落后文件直接取 main（`agent.py`、`history_display.py`）——
      实为空操作，选定 D1 时即已收敛（`cli.py` 不在清单内，见「不符二」）
- [x] `ai/server.py` 的 deepseek 2 行：**丢弃**（负责人已定，不入库）
- [x] 参考 `deploy-214-envelope-tombstone` 重做 external-sessions，补测试
- [x] 补回 `_private_space.py` 并接上两个消费点（**返工补做**，见记录⑦）
- [x] `7d5c9225`、`1aeb6c34` 的内容按当前结构重做（无法真 cherry-pick，见记录③）
- [x] 新增配置项 `FUSION_MEMORY_AUTO_REGISTER_FEISHU`
- [x] 弃掉本地 commit `46264245`（昨晚的重复实现）
- [x] 跑测试（注意：Windows 上 5 条 session 测试 + 全量 57 failed 是既有基线，不是回归）

**产出：** 分支 `fix/external-container-recovery-v2`，两个 commit，基于
`a9579c7d`（= 当次 fetch 的 `origin/main`，无附加提交）。

| commit | 内容 | 规模 |
|---|---|---|
| `5c145150` | external-sessions 重做 + 跨容器文件交接（原 `09a1b319` 移植过来） | 9 文件 +583/-47 |
| `520a5924` | 补回 `_private_space.py` 及其接线 | 5 文件 +212/-1 |

本阶段纯本地，未碰生产。

> ⚠️ **原分支 `fix/external-container-recovery` 已废弃，不要用它部署。** 它基于
> `d198c435`，落后当次 `origin/main` 5 个 commit，会把 main 已 revert 的自更新功能
> （`updater/` 九个文件 + `cli.py` 的 `SelfUpdate` 命令，#703/#705 共净删 955 行）
> 又带回生产。实测 `git diff origin/main..fix/external-container-recovery --
> src/psi_agent/cli.py` 为 `+43/-1`。v2 分支上 `cli.py` 为 1010 字节，
> 与 `a9579c7d` 及生产（md5 `2176b6ab`）三方一致，`updater/` 文件数为 0。

**实际决策与实测结果：**

**① A 组三个文件无需任何操作，但「四个」这个说法是错的。** 方案假设要「取 main」，
实测 `origin/main` 上 `agent.py`、`history_display.py`、`ai/server.py` 与目标状态
**已逐字节相同**——两个修复本就在 main 里，deepseek 那 2 行本就不在。所谓「收敛」在选定
D1（基线取 main）的那一刻就已完成，工作量为零。

**`cli.py` 不属于此列，原判断有误。** 首次核对时它被算作第四个「已相同」的文件，
但那次量的是 `d198c435` 而非当次 fetch 的 `origin/main`。实测 `d198c435` 上 `cli.py`
为 2546 字节、`a9579c7d` 上为 1010 字节，两者不同；生产那份也是 1010 字节
（md5 `2176b6ab`）。即：以旧基线为准会把 main 已 revert 的 `SelfUpdate` 命令
连同 `updater/` 九个文件一起带回生产。**这正是「不符二」里那条教训的第二次发作**——
同一个坑，隔了一轮又踩一次。

**② 弃掉 `46264245` 用 `git reset --hard d198c435`。** 该 commit 是 HEAD 上唯一的代码
提交，其下 4 个都是 haitun 文档提交，reset 到 `d198c435` 即精确剥掉它而保住文档。
reset 后 `git diff --stat origin/main HEAD -- src/` 输出为空，确认 `src/` 干净。

> 补记：该做法只剥掉了 `46264245`，但把基线留在了 `d198c435`。正确的收尾是
> `git cherry-pick` 到 `a9579c7d` 之上（v2 分支即如此重做），否则 `src/` 看着干净、
> 实际整棵树落后 main 5 个 commit。

**③ 两个 commit 无法真 cherry-pick，按当前结构重做。** `main` 已把 `_route_key`
抽到 `psi_agent/_feishu_routing.py`（`route_key()` / `is_group_chat()`），而
`7d5c9225`、`1aeb6c34` 是在旧结构（`FeishuManager._route_key` 私有方法、模块内
`_GROUP_CHAT_TYPES` 常量）上写的，上下文全不匹配。故按 diff 逐处重做而非 `git cherry-pick`，
内容等价、接线改用 `route_key()`（handoff 块、`external` 字段透传、channel 侧缓存、
取件说明），端到端仍需两容器实跑验证。

> **原写「V11 对应的代码已全部落地」是错的，见记录⑨。** 这两个 commit 修的是入向，
> V11 验的是出向，不是同一条链路。

**④ 原临时实现有一处与 `route_key` 不一致的真缺陷。** 昨晚的 `_parse_external_sessions`
用**裸 `open_id`** 查表并显式 `if not key.startswith("chat:")` 排除群聊；重做版按
`route_key` 的完整键查表，群聊写 `chat:oc_xxx` 即可命中。原版群聊永远无法路由到外部容器，
且判定键与 `route_key` 两套，属方案 D2 所指「未 review」的具体体现。

**⑤ `FUSION_MEMORY_AUTO_REGISTER_FEISHU` 代码早已实现，缺的只是配置。** 实测
`_fusion_memory_config.py:148` 已解析该变量、`:181-186` 已实现 auto-register 分支，
`README.md` / `AGENTS.md` / `SKILL.md` 三处都已写它。真正的缺口是它**从未被设置**，
而默认 `False`（`:48`）。故本阶段的「新增配置项」落成两件事：新增
`agents/feishu/.env.memory.example` 把该值记进 git（避免重演「只存在于
服务器文件系统上的配置」），并在 `AGENTS.md` 该变量条目点明默认值的后果。
**`.env` 实际落地属阶段 2**（需重启，与 D3 一致）。

**⑥ 修了一条移植过来的测试。** `test_run_feishu_wires_is_external_into_message_handler`
原样移植后失败（`KeyError: 'message'`）：`run_feishu` 在 `appdata` 为空时会先向 gateway
`GET /defaults`，那次真实 HTTP 打在无人监听的端口上要等到超时，处理器注册被推到
`await anyio.sleep(0.1)` 之后。改为显式传 `appdata` 跳过远程解析，测试意图不变。

**⑦ 漏了私密区，返工补做。** 方案 D2 明确要求连 `_private_space` 一起补回，实测第一版
漏了：`_private_space.py` 在分支上不存在，`PSI_PRIVATE_OPEN_IDS` 在 `src/` 里零命中，
`_feishu_manager` 也没有该 import。**这与昨晚临时实现「`_private_space` 接线断了」是同一
状态**，而那恰是记录③里选择重做的理由之一——理由说对了，自己却犯了同一个错。

补做内容（commit `520a5924`）：从 `origin/deploy-214-envelope-tombstone` 取整个
`_private_space.py`（72 行，未改一字），接上原设计的两个消费点——
`_feishu_manager._workspace_for` 让白名单用户派生到 `<root>/.private/<open_id>`（群聊不进，
群是多人共用上下文），`feishu/client._stream_reply` 在发送前按 `blocks_send` 判权
（主人自己收得到，其他人一律拦），`sender_open_id` 从 `_handle_and_stream` 的
`ctx.sender_id` 传入。

**⑧ 顺带修了一处会让断言假绿的测试基建。** `test_feishu.py` 的 `_fake_channel` 把
`channel.stream` 设为裸 `AsyncMock`，只记录调用、**不执行 `markdown` 回调**，于是任何盯
`_stream_reply._produce` 内部行为的断言都不会真的跑到。新增 `_driving_channel` 真去驱动
回调——记录⑦的三条断言正是在它下面才暴露出「主人也被自己的文件拦住」这个错
（改前 `assert 0 == 1`）。

**⑨ V11 判错了链路：`7d5c9225` / `1aeb6c34` 修的是入向，V11 验的是出向。** 方案 W 段把
V11 写成「验的是这两个 cherry-pick 是否生效」，实测不成立——两个 commit 的 diff 里
`outbound` / `SEND` / `materialize` / `_send_file` **全部零命中**。它们修的是
**入向**（用户发文件给 agent，路径要落到对方容器能读到的地方）；截图里的裸
`[SEND:/workspace/...]` 是**出向**（agent 发文件给用户）。

出向那条链：主容器的 `SendMarkerScanner`（`_core.py:88`）扫到 `[SEND:/path]` 生成
`FileChunk`，`channel/feishu/client.py:192` 的 `_send_file` 拿这个路径去读**本地**文件——
但文件在另一个容器的 bind mount 里，主容器读不到，两次 `channel.send` 都失败，marker 就
原样留在文本里发出去。**阶段 1 没有修出向**，也不该在阶段 1 顺手修：它需要新增跨容器取件
机制（要么主容器挂载对方 workspace，要么独立容器把文件 POST 回来），属新设计而非收敛。

另有一处旧描述不准：`materialize` / `outbound` 这两个字符串在生产 `/app/src` 全树都不存在，
那行日志来自 workspace 层的工具，不是 gateway。

**处置（负责人已定）：** 出向问题「先不管」，本轮不修。故 V11 在阶段 2 **降为观察项**，
只记实测现象，不作通过/不通过判据；正式修复由负责人另行拍定。

**测试结果：**

| 范围 | 结果 |
|---|---|
| `test_feishu_manager.py` + `test_feishu.py` + `test_gateway.py`（子树，v2 分支） | **116 passed** |
| 同上，补私密区之前 | 109 passed |
| `ruff check` / `ruff format --check`（改动的 5 个文件） | 全过 |
| 全量（v2 分支） | **1360 passed / 57 failed / 5 skipped** |

57 failed 与本机既有基线**逐条相同**，且**全部不在本次改动的文件里**
（`test__core`、`test_channel_adapter`、`test_server`、`test_schedule_registry` 等）。
抽查 `test_schedule_tz_valid` 确认是环境缺陷而非回归：
`ZoneInfoNotFoundError('No time zone found with key Asia/Shanghai')`，本机缺 tzdata。

新增测试 47 条（external-sessions 40 + 私密区 7）。其中五条专盯「接线断了」这类静默空转：
`test_handle_passes_external_to_build_chunks`（谓词答案必须传进 `_build_chunks`）、
`test_run_feishu_wires_is_external_into_message_handler`（谓词必须接进消息处理器实参），
以及私密区的三条（白名单派生、群聊不进、未配置是空操作）。
这正是原临时实现 `_private_space` 接线断掉却无人发现的那类问题——**而记录⑦证明写了这两条
测试也不够：漏掉整个模块时，没有任何测试会红。** 真正兜住它的是逐项核对方案 D2 的清单。

**踩到的三个环境坑（阶段 3 修文档时可用）：**

- `uv run` 在 worktree 里会新建一个空 `.venv` 并报 `No module named pytest`。worktree
  没有自己的虚拟环境，须用主检出的解释器
  `F:\code\psi-agent\.venv\Scripts\python.exe`。
- 但该 venv 的 editable 安装指向**主检出的 `src`**，直接跑会 import 到主检出的代码
  （实测报 `cannot import name 'external_sessions'`，因为改动在 worktree 里）。
  必须 `PYTHONPATH=<worktree>/src` 覆盖，否则测的不是当前代码。
- **`-o testpaths=` 必须写在路径参数之前，写在后面会静默吞掉路径。** 复核上表的
  「116 passed」时用 `pytest <三个文件> -o testpaths=` 跑出 **89 passed**，差点把文档里
  正确的数字改错。89 = 79 + 10，`test_feishu_manager.py` 一个文件没被收进去，而
  「89 passed」看上去毫无异常。换成 `pytest -o testpaths= <三个文件>` 得 116。
  三种写法的收集量：正确 116 / 顺序写反 4380（清空 `testpaths` 又丢路径，从 rootdir
  收全仓含 `examples/`）/ 完全不带 `-o` 1422（回落 `testpaths=["tests"]`）。
  **判断参数有没有生效只能看数量对不对，三种都不报错。**

### 阶段 2 · 走正式发布流程

**已完成（2026-08-22 17:51）。停机 1 分 35 秒（17:50:11 → 17:51:46）。**

- [x] 构建镜像用 **`fix/external-container-recovery-v2`**（发布时 HEAD 已推进到
      `00970323`，含阶段 3 文档提交；`src/` 内容与 `520a5924` 一致）
- [x] 照 `psi-agent-release-to-cloud.md` 执行，同一窗口内一并落地记忆服务 `.env` 改动
- [x] 按 D4 决策归档两个容器的历史（归档必做，迁移不在本阶段）
- [x] 补成 xx 容器缺失的 `memory_tokens.json`
- [x] 按 §5 八条判据 + 本文 V1-V10 验收。**V11 降为观察项**
- [x] 开工前负责人已显式确认（含同一窗口改生产 `workspace/.env`）

**基线核对：开工第一步 `git fetch origin --prune` 抓到 `origin/main`
`a9579c7d` → `e671409e`**（新增 `c0ba5d8c`、`e671409e` 两个 commit）。逐个看了 diff：
只动 `.github/inno-setup/`（Windows 安装器 3 个文件 +70/-13），**与 `src/` 零重叠**，
故不必 rebase，直接用 `00970323` 构建。这条正是负责人新定约束要防的——若照旧用本地缓存
的远程引用，会以为基线未变而根本不知道有两个新 commit。

**执行顺序（把能不停机做的全挪到停机窗口外）：**

| 时刻 | 动作 | 停机 |
|---|---|---|
| 16:52–17:07 | build 机整份替换 `src/` + `docker compose build gateway` | 否 |
| 17:07:20–17:07:59 | `docker save \| gzip -1` 导出，39s | 否 |
| 17:08–17:48 | 传输 build 机 → 本机 → 云端 | 否 |
| 17:48:53 | 打备份 tag `backup-20260822-174853` | 否 |
| 17:48:53–17:49:21 | `docker load` 导入新镜像（28s，容器仍跑旧镜像） | 否 |
| 17:49:40 | 备份 `.env` + 追加 `FUSION_MEMORY_AUTO_REGISTER_FEISHU=true` + 补成 xx token 表 | 否 |
| **17:50:11** | `docker compose stop` | **起点** |
| 17:50:11–17:50:40 | 归档主容器侧两份冻结历史（停机中做，杜绝被进程重建） | 是 |
| 17:50:40 | `docker compose up -d --force-recreate --no-build` | 是 |
| 17:51:07–17:51:46 | `./restart-stack.sh gateway` 自检轮询（502→400） | 是 |
| **17:51:46** | 两条自检通过 | **终点** |

**记录⑩ · build 机 `src/` 的漂移比交接说的更严重：不只 14 个文件不同，还少 3 个文件。**

交接材料说「97 个 `.py`，v2 是 99 个」。实测两个数都不对：`00970323` 是 **100** 个
（交接的 99 是阶段 0 存证的容器内计数，容器里没有 `_private_space.py`，v2 补回后是 100）。
build 目录 97 个，缺的 3 个是 `_tls.py`、`gateway/_free_model.py`、`gateway/_ui_prefs.py`
—— 与 external-sessions 无关，是更早的漂移。归一化换行后另有 14 个文件内容不同。
**合计 17/100 不一致，正是「整份替换而不是打补丁」的理由。**

替换后按三层复验，一层比一层严：

1. build 目录 100 个 `.py` 对 `00970323` 逐字节（未归一化，`git archive` 出的是 LF）→ **100/100 同**
2. 整个 `src` 树含非 `.py`（283 个文件）合并哈希 → `bcef6d53…` 两侧**完全相同**
3. **镜像内** `/app/src` 100 个 `.py` 对源码逐字节 → **全同**

第 3 层是新增的、也是最该有的一层：前两层只证明「喂给 `docker build` 的输入对」，
第 3 层才证明「镜像里装出来的东西对」。8-18 事故缺的正是这一层。

**记录⑪ · 停机 1 分 35 秒，落在文档推算的 1–2 分钟内，该推算值可转为实测值。**

文档 §4.5 那张表里「只换 gateway 的预期窗口 1–2 分钟」标着「这个数是推算，不是实测」，
并写明「第一次按本流程发布时回填」。本轮实测 **95 秒**，构成：`stop` 29s（含归档
两份历史）+ `up --force-recreate` 27s + 自检轮询 39s。
把 `docker load` 挪到停机窗口外确实有效——那 28s 完全没算进停机。

**记录⑫ · 成 xx 缺 `memory_tokens.json` 会撞上比预想更早的一堵墙。**

卡里写「修好路由后他的记忆会立刻走上同一个报错路径」，实测**不是同一个路径**：
`_read_token_map()` 和 `_write_token_map_entry_sync()` 都用裸 `open(path)`，对文件不存在
毫无容错，`OSError` 直接被包成 `configuration_error`（`_fusion_memory_config.py:399-401`）。
而抛错发生在 `entry is None` 判断**之前**，所以**自动注册开关根本来不及生效**——
错误码也不是 `memory_user_not_configured` 而是 `configuration_error`。
处置：写入 `{}` 并 `chmod 600`。开关能自我修复缺条目，但修不了缺文件。

**记录⑬ · `--force-recreate` 现在牵动 4 个容器，不是文档写的 3 个。**

发布文档 §4.4 注释写「三个容器一起重建」，实测 compose 有 **4** 个服务：
`gateway` / `oauth-proxy` / `private-luolin` / `private-chengxx`。
`private-chengxx` 是文档成稿后才加的，文档没跟上。四个都确认换到了新镜像
`527deff72043`（比对 `.Image` 的 sha256，不比 image ID——§2.2 已说明口径差异）。
`docker compose up` 另有一句既有告警 `a network with name psi-agent_default exists but
was not created by compose`，与本次发布无关，不影响结果。

### 阶段 3 · 反过来修文档

**已完成（2026-08-22）。** 改的是 `docs/haitun-delivery/psi-agent-release-to-cloud.md`，
该目录按约定**不入 git**，故本阶段产出只在本地磁盘，无 commit 可引。

- [x] 修正 §38：换成实测的 93/99（基线 `a9579c7d`），改掉「任何提交都不存在」的结论
- [x] 附录 A 增补：**核验必须在容器内按字节比对**，经 ssh 文本通道会因 CRLF 产生假阳性
- [x] 附录 A 增补：**基线必须是当次 `git fetch --prune` 的 `origin/main`**，不能用本地缓存
      的远程引用。与上一条并列，是两类不同的假阳性——一个错在取文件的通道，一个错在
      比对的基线。举证与「同一个坑踩两次」的经过见「不符二」与阶段 1 记录①
- [x] §38 标注「未实测」的收敛方法，这次实测了，补结果

**实际改动（比原计划多三处）：**

| 位置 | 改了什么 |
|---|---|
| 标题下「事实基础」后 | **新增「本次改了什么、没改什么」段** —— 负责人要求把边界与待办写清楚。含 3 条改动、4 条明确不做、7 条待办表 |
| §38 整节 | 标题从「🔴 动手前必须先解决的一件事：线上跑的代码不在任何一次提交里」改为「动手前先确认基线……（2026-08-22 重测，已收敛）」。保留一段引用块交代原结论错在哪，不直接删 |
| 附录 A 开头 | 新增「🔴 两条硬要求」引用块 |
| 附录 A 第 3 条命令 | **结论作废 + 命令本身有两个缺陷**：量的是宿主目录不是容器、`git log --all \| head -200` 既漏未 fetch 的远端分支又被截断。换成四路交叉查证写法 |
| §2.1、§6.2 | 两处指向 §38 的交叉引用跟着改，不再重复错结论。§6.2 顺带点明「镜像与 commit 无可查对应关系」是本次事故根因之一 |
| 附录 C | 「src 逐文件 sha256 与 git 历史比对」这一项标注结论作废，新增 8-22 重测条目与存证路径 |

**复核过的三处**（改文档时顺手实测，不是照抄旧结论）：

- 93/99 重新量了一遍：容器存证树 99 个 `.py` 对 `origin/main` 逐字节比，相同 93 / 不同 6 /
  缺失 0，与阶段 1 数字一致。
- 原 7 文件清单里的 4 个（`_card_action.py`、`_card_store.py`、`ai_client.py`、
  `client.py`）逐个验过，**全部与 `origin/main` 逐字节相同**，确认是 CRLF 假阳性。
- 「955 行」这个数复核过：`git diff d198c435 origin/main -- src/` 为 1 insertion /
  **955 deletions**，废弃分支的树与 main 差 1153 行，且带着 18 个 `updater/` 文件、
  `cli.py` 2546 字节（main 是 1010）。

### 中途改了哪些决定

**阶段 1（2026-08-22）：**

1. **「cherry-pick `7d5c9225`、`1aeb6c34`」改为「按当前结构重做其内容」。** D2 原文假设
   这两个 commit 可以直接 pick，实测不行——`main` 已把 `_route_key` 抽到
   `psi_agent/_feishu_routing.py`，两个 commit 是在旧结构上写的，上下文全不匹配。
   改为逐处重做，内容等价。**验收判据 V11 不变**，只是落地手段变了。
2. **「新增配置项 `FUSION_MEMORY_AUTO_REGISTER_FEISHU`」的含义收窄。** 原以为要写代码，
   实测代码早已完整实现，缺口纯粹是「从未被配置」。故本阶段只把该值记进 git
   （新增 `.env.memory.example`）并在 `AGENTS.md` 点明默认值后果，
   `.env` 实际落地归阶段 2（与 D3 「并入发布窗口」一致）。
3. **「A 组四个落后文件取 main」实际是空操作，且「四个」本身也是错的。** 真实数量是
   三个（`cli.py` 因基线过期被误列，见「不符二」与记录①），且这三个在选定 D1 时就已收敛。
   这不是决定变更，是清单本身有错 + 工作量估计偏高。
4. **私密区不是「顺带」，是必做项，且实测被漏掉过一次。** D2 把它与 external-sessions
   并列，第一版落地只做了后者。返工补齐见记录⑦。这条记在这里是为了留住教训：
   方案清单要逐项打勾核对，不能凭「主干功能已通」判定完成。

5. **V11 从判据降为观察项。** 这一条**确实改了 W 段**，是本轮唯一改动验收标准的地方。
   原 V11 建立在「`7d5c9225` / `1aeb6c34` 修的就是这条链路」这个错误前提上，实测两者修的是
   入向而 V11 验的是出向（记录⑨）。出向修复需新增跨容器取件机制，属新设计；负责人已定
   「先不管」。故 V11 保留在表里但只记实测现象，不作通过/不通过判据。

前四条都不改变 W 段的任何验收标准，也不改变 D1-D4 的选择；第 5 条按 SOP 显式记为
验收标准变更。

***

## T —— 测试与验收

> 按 SOP 规则二，本段只对 W 的 V1-V11 逐条核验，不自定新标准。
> V11 已降为观察项（见「中途改了哪些决定」第 5 条），填实测现象而非通过/不通过。
>
> **阶段 2 发布后重填（2026-08-22 17:52–18:00）。9 条通过，V2/V3 部分验证，
> V6 结论要改，V11 未观察到。** 逐条证据见下。

| 编号 | 结果 | 证据 |
|---|---|---|
| **V1** | **通过** | 容器内 `/app/src` **100** 个 `.py`（不是 99——v2 补回 `_private_space.py`）对 `00970323` 逐字节：`same=100 diff=0 missing=0 extra=0`。哈希在容器内按 `open(p,"rb")` 算，只把清单文本传出来，避开 CRLF 假阳性 |
| **V2** | **部分通过**（机制已证，缺真人消息） | 归档后主容器侧 `feishu-ou_c77e…jsonl` **未被重建**（负向判据成立）；容器内实调 `is_external("ou_c77e…")` → `True`，路由到 `http://psi-agent-luolin:8081`。**正向判据「独立容器侧行数增」未取到**——需罗霖本人发消息，我发不出 |
| **V3** | **部分通过**（同 V2） | 主容器侧成 xx 文件同样未被重建；`is_external("ou_716d…")` → `True` → `psi-agent-chengxx:8081`。群聊 bug 已消失：旧代码 `if ext_url and not key.startswith("chat:")` 在新容器内**零命中** |
| **V4** | **通过** | 容器内 `grep -c MIN_SUMMARY_CHARS session/agent.py` = **2**、`grep -c MAX_TOOL_RESULT_CHARS session/history_display.py` = **2** |
| **V5** | **通过（有直接证据）** | token 表 24 → **25** 人，mtime **17:52:34**（在 17:51:46 重启之后）。新增条目 `ou_2528b878…` 只有 `{token, workspace_id}` **没有 `_name`** —— 手工维护的 24 条全带 `_name`，所以这条必定是 `_auto_register_feishu_user()` 写的。重启后 `memory_user_not_configured` 计数 **0** |
| **V6** | **不通过（判据本身有问题，见下）** | 存量用户**仍有 20 人**不在 token 表里。但这条判据与修复机制不匹配——详见「怎么测的」 |
| **V7** | **通过** | 归档前后行数逐字对上：罗霖 4523 → 4523（9,871,458 字节）、成 xx 3844 → 3844（3,455,114 字节）。独立容器侧未被动（9961 / 4028，仍在增长），罗霖那份 `bak-ctxoverflow-20260819` 25.6M 仍在 |
| **V8** | **通过** | 见下方「怎么测的」（阶段 3 已判，本轮复核仍成立） |
| **V9** | **通过（8/8）** | §5.1 四容器 `running` / `restarts=0` / 全为 `psi-agent-gateway:local`；§5.2 **202 / 274 / 202 tool** 与基线一致；§5.3 七条路径 `400/400/404/404/404/404/404` 逐字复现，8090 只听 `127.0.0.1`；§5.4 luolin 与 chengxx 均 **404**（健康）；§5.5 最后一条 `] connected to wss://`（17:51:44）；§5.6 重启后 9 名用户历史被写入；§5.7 ToC 三值全未变；§5.8 体积只增不减、卷在、表数 33 |
| **V10** | **通过** | 卷 `deploy_fusion_memory_pgdata` 在（`created=2026-08-20T18:30:14`），表数 **33**，与发布前一致。全程未碰 `deploy` 项目，`down`/`down -v` 一次都没跑 |
| V11（观察项） | **未观察到现象** | 重启后 `materialize blocked` 计数 **0**，历史累计也是 **0** —— 因为窗口内没人做跨容器发文件。出向代码路径确认未改：`client.py:_send_file()` 仍是 `channel.send(chat_id, {"image": {"source": path}})` 读本地路径。**结论：本轮既没复现也没修复，与「阶段 1 未修出向」一致** |

### 怎么测的

**V8（发布流程文档 §38 的错误结论已修正）—— 通过。**

判定方式是「文档中不再出现『与任何提交都不一致（最佳 89/97）』」。实测
`grep -n "无一次完全一致\|最佳 89/97"` 命中 2 处，但**两处都在更正引用块里**
（行首 `>`，句式为「原结论……不成立」），是**引用并驳斥**，不是断言。判定通过。

刻意保留这两处而不是删净：原结论错在方法而非分析，两个坑（CRLF 假阳性、过期基线）
都会**静默**给出看似合理的结果。把错结论连同错因留在原位，下一个人照附录 A 操作前
会先看到「这里曾经这么错过」。若判据要求字面零命中，则应改判据而非删更正说明。

**V6（21 名存量用户记忆可用）—— 不通过，且判据本身写错了方向。**

判定方式原文是「逐个确认已在 token 表中」。实测发布后仍有 **20 人**不在表里
（W 段说 21 人，实测 43 名有历史用户 − 表内 25 人 = 20，差 1 是因为其中一人
`ou_2528b878…` 已被自动注册补上）。按字面判据：**不通过**。

但**这个判据与修复机制根本不匹配**，不是修复没做到：

- `FUSION_MEMORY_AUTO_REGISTER_FEISHU` 是**懒注册**——只在某人**下一次开口**、
  查表未命中时才去 MCP 建 token 并写回表。它不会、也没打算批量回填存量用户。
- 所以「20 人仍不在表里」的正确读法是「这 20 人自 17:51 重启以来还没说过话」，
  而不是「他们的记忆仍然坏着」。V5 已证明这条路径真的能跑通：`ou_2528b878…`
  在重启后 48 秒内被自动注册成功。
- 真要让判据可判，得改成**「存量用户下次开口后即出现在表里」**，与 V5 合成一条即可；
  或者另做一个批量回填脚本，那属新增功能，不在本任务范围。

**建议把 V6 改判为「机制已通、存量按需生效」，并把批量回填另立一项**。
这里不擅自改 W 段判据（SOP 规则二：T 段不自定新标准），只把冲突记下来交负责人定。

**V2/V3 为什么只能算部分通过。** 两条判据都由一正一负两半构成：

| | 负向（主容器侧不增） | 正向（独立容器侧增） |
|---|---|---|
| 罗霖 | ✅ 归档后文件未被重建 | ⛔ 未取到 |
| 成 xx | ✅ 同上 | ⛔ 未取到 |

负向那半反而是**更强的证据**：文件已被改名归档，若路由还是坏的，主容器会立刻新建一个
同名文件——重启后 9 名其他用户的历史都正常写入，证明写历史这条路是活的，
唯独这两个 open_id 没有新建，只可能是消息被转发走了。
正向那半需要罗霖/成 xx 本人在飞书发消息，**我发不出，也不该冒用他们的账号**。
补验方法（一条命令，交给负责人或本人发一句话后跑）：

```bash
ssh root@8.222.255.23 '
wc -l < /srv/haitun/psi-agent/workspace-luolin/.psi/appdata/histories/feishu-ou_c77e484d4cf5699947408c9448a8e777.jsonl   # 基线 9961
wc -l < /srv/haitun/psi-agent/workspace-chengxx/.psi/appdata/histories/feishu-ou_716d18b92e20c74726821c79f02826d7.jsonl  # 基线 4028
ls /srv/haitun/psi-agent/workspace/.psi/appdata/histories/feishu-ou_c77e484d4cf5699947408c9448a8e777.jsonl 2>&1 | tail -1  # 必须仍报 No such file'
```

**V10 的量法要注意 schema 口径。** 发布文档 §5.8 写的是
`table_schema='public'`，但连接用户是 `fusion`，本轮统一用
`table_schema=(select current_schema())` 量，前后基线同一口径，均为 33。

**阶段 1 的代码测试结果**（不属 V1-V11 任何一条，故不进上表，见 A 段记录⑥）：
子树 116 passed、全量 1360 passed / 57 failed / 5 skipped（57 条为 Windows 已存档基线，
不在改动的 5 个文件里）、`ruff check`/`format` 全过。

### 还剩什么问题

- `_private_space` 私密区机制是否恢复，未决（W 段已列为排除项，需单独立项）。
  阶段 2 补充：代码已在生产就位（`client.py:487` 发送守卫、`_feishu_manager.py:121`
  workspace 派生），但 `PSI_PRIVATE_OPEN_IDS` **未配置**，故模块当前是空操作。
  要不要启用、给谁启用，需负责人定。
- Dockerfile 硬编码境内镜像源，已知缺口，本任务不动
- **V2/V3 正向半条待补**：需罗霖/成 xx 本人在飞书发一条消息，再比对独立容器侧行数。
  基线与命令见 T 段。注意**不能用行数自然增长充当证据** —— 两个容器的
  `assignment-delivery-refresh` 定时 trigger 每几分钟就写 2 行，实测 17:54→18:02
  两侧各 +2 行全是 trigger，不是飞书消息。
- **V6 判据建议改判**（见 T 段）：懒注册不回填存量用户，「逐个确认已在 token 表中」
  这条判据不可能通过。批量回填如果要做，是新增功能，需另立项。
- **出向跨容器发文件仍未修**（V11）：`_send_file()` 读本地路径，文件在另一个容器的
  bind mount 里。负责人已明确「先不管」。本轮窗口内无人触发，连现象都没观察到。
- **流程问题（比代码修复更值得定规矩）：** 生产部署依赖了未提 PR 的本地代码，
  这是本次事故的真正来源。8-18 与之前是两位不同同事部署，前者以 main 为基构建，
  丢失了后者未提交的功能。需要一条规矩：**只能从可指名的 commit 构建镜像。**
  阶段 2 已把这条落成可执行的三层核验并写进发布文档 §6.1：
  ① 整份替换 `src/` 不打补丁 → ② build 前验输入 → ③ **build 后验镜像内产物**。
  第 ③ 层是 8-18 缺的那层，本轮首次做，`same=100 diff=0`。
  建议再进一步：镜像 tag 带上 commit（`psi-agent-gateway:00970323`），
  这样「线上跑的是哪个 commit」不用逐文件比哈希去猜。

***

## 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0 | 2026-08-22 | 初版，W/H 落定，开工前核对出 3 处与 8-20 诊断不符 |
| 1.1 | 2026-08-22 | 阶段 2 发布完成（停机 95s）。A 段补记录⑩-⑬，T 段填 V1-V11 实测。V6 判据与懒注册机制不匹配，建议改判；V2/V3 正向半条缺真人消息 |

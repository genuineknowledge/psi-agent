# B-3 执行手册 · 内容分层上生产 + 之后的标准发布流程

## 一、结论先行

**要做的事只有三件**，比原方案小得多：

1. 重打镜像（分层代码在 `src/` 里，当前生产镜像 `482d970c` 不含它）。
2. 把 `/workspace/{skills,triggers,systems}` 三个目录**改名**为 `*.pre-layering`（不删）。`tools` 留在原地。
3. compose 加一条 `PSI_CONTENT_ROOTS` 环境变量 + 三条 bind mount。`command` 一个字不改。

**为什么这么小**：生产 1448 个内容文件里，只有 **4 个**是真正的就地手写，其余全是过期的仓库部署产物
（64/69 可对上历史 commit，中位深度 8 次提交）。所以 `/official` 是**整版替换**，不是逐个归属。
2.5 GB / 35021 个文件里只碰 237 个（0.68%）。

**回滚是一条路，不是三条**：去变量 + 改回目录名 + 重启，必须一起做。实测只去变量会把内容清空（见第六节）。

**我上一轮的提案是错的**：原计划把 `--default-agent` 指到个人层，实测发现 `tools` 跟着 agent 根走，
生产那 229 个工具会全部消失（A 臂 2 个工具 → B 臂 1 个）。改成 agent 根不动、只给内容目录腾名。

---

## 二、前置闸门（不过就停）

沿用 `docs/superpowers/specs/2026-09-12-tob-layering-production-oplog.md` 第二节的 G1–G4，此处不重复。
额外一条本次专有的：

| 闸门 | 命令 | 通过判据 |
|---|---|---|
| G5 分层代码在镜像里 | `docker run --rm --entrypoint sh <新tag> -c 'ls /app/src/psi_agent/session/content_roots.py'` | 文件存在。这是镜像三层核验的第二层 |

**G4（并行写者）在本次尤其吃劲**：实测到 `skills/card-dsl/card.xsd` 在 2026-09-12 17:44:51 被就地写过
（纳秒位非零），**未验证是谁写的**。腾名会把这类刚写的内容一起带走，所以腾名前必须再量一次并逐个安置。

---

## 三、内容投放清单

### 3.1 三层放什么

| 层 | 挂载 | 内容来源 | 写权限 |
|---|---|---|---|
| `/content/official` | `:ro` | **整版**从目标 commit 的 `agents/feishu/{skills,triggers,systems}` 投放 | 只读。改它 = 派生（B-2 已实现） |
| `/content/enterprise` | `:ro` | 企业自有内容，走 git PR | 只读 |
| `/content/users/<open_id>` | 可写 | 每人一份，从下面 3.2 的清单迁入 | 可写 |

### 3.2 需要逐个安置的文件（不能整版覆盖的）

这是审计 1448 个文件后剩下的**全部**特例。

**A 类 · 真正的就地写入（4 个）** —— 这些内容在 git 里不存在，整版覆盖会丢：

| 文件 | 所在 workspace | 处理 |
|---|---|---|
| `skills/card-dsl/card.xsd` | main | **未验证写者**。内容是加 `layout` 属性、删 `section`/`divider` 元素。**决定：进企业层** —— 它是 DSL schema，是全公司共用的，不是个人偏好 |
| （其余 3 个待腾名前重新实测确认，见下方"为什么不在这里点名"） | | |

> **为什么不在这里把 4 个全点名**：这份审计跑在 2026-09-12 早些时候，而 `card.xsd` 是当天 17:44 才被写的
> —— 说明这个清单**会变**。把一个会变的清单硬编码进手册，就是"硬编码预期清单把实测偷换成查表"那个坑：
> 清单没跟上，判据会用一句自信的错话指向错方向。**执行时现场重跑第四节的检测命令，以现场输出为准。**

**B 类 · 13 个待定归属** —— luolin 和 chengxx 两个 workspace 里**逐字节相同**的 13 个文件。
字节相同说明是共同来源而非个人编辑，**决定：进企业层**（负责人已定："1，进企业层"）。

**C 类 · 2 个真正的个人内容** —— 每人各不相同：

| 文件 | 处理 |
|---|---|
| `skills/fusion-memory-setup/SKILL.md` | 各自进 `/content/users/<open_id>/skills/` |
| `skills/user-preferences-and-language/SKILL.md` | 同上。**注意**：这个文件有 frontmatter 重复的既有缺陷，作为独立缺陷单独修，不在本次范围 |

### 3.3 `tools` 不动

`/workspace/tools` 的 229 个工具**留在原地**，`PYTHONPATH=/workspace/tools` 不改，`--default-agent /workspace` 不改。
这是方案 C 相对上一版提案的全部意义。

---

## 四、执行步骤

> 每一步执行前先在台账（`*-production-oplog.md` 第三节）写一行，含回滚命令。

### 步骤 0 · 备份

```bash
cd /srv/haitun/psi-agent
tar czf /srv/backup/workspace-skills-triggers-systems-$(date +%Y%m%d-%H%M).tar.gz \
    workspace/skills workspace/triggers workspace/systems
# 判据: 解压字节数 == 原目录字节数。不看退出码 —— zst/tar 对截断文件会正常 EOF 返回 0
tar tzf /srv/backup/workspace-*.tar.gz | wc -l
find workspace/skills workspace/triggers workspace/systems -type f | wc -l
```

**两个数必须相等。** 只看 `tar` 退出码不算验证（截断处按正常 EOF 处理，已有记录）。

### 步骤 1 · 现场重测并行写者

```bash
find /srv/haitun/psi-agent/workspace/{skills,triggers,systems} -type f -newermt '-6 hours' \
  -printf '%T@ %p\n' | awk '$1 !~ /\.0000000/'
```

输出非空 → 逐个记进台账，按 3.2 的 A/B/C 分类安置。**输出为空不代表没有，只代表 6 小时内没有。**

### 步骤 2 · 构建镜像

```bash
# 在干净 clone 的目标 commit 上。build-image.sh 自带 HEAD==commit + 干净树两个闸门
deploy/haitun/build-image.sh overlay <commit>
```

依赖没变用 `overlay`（秒级）；`pyproject.toml`/`uv.lock` 变了脚本会自己拒绝并提示改 `full`。
**境外 B 机必须覆盖镜像源**：`APT_MIRROR= PIP_INDEX_URL=https://pypi.org/simple NPM_REGISTRY=https://registry.npmjs.org`。
`APT_MIRROR=` 是空值（不换源），与"没设"不是一回事。

然后过 G5 闸门（第二节）。

### 步骤 3 · 投放三层内容

```bash
cd /srv/haitun/psi-agent
# official: 从目标 commit 整版投放
git -C /srv/build/psi-agent archive <commit> agents/feishu/skills agents/feishu/triggers agents/feishu/systems \
  | tar x -C content/official --strip-components=2
# enterprise / users: 按 3.2 清单逐个 cp
```

投放后核验（**LF 归一化后比 md5**，生产是 LF、仓库是 CRLF，裸比对会报几乎全不一致）：

```bash
tr -d '\r' < content/official/skills/<某个>/SKILL.md | md5sum
```

### 步骤 4 · 腾名（唯一有破坏性的一步）

```bash
cd /srv/haitun/psi-agent/workspace
mv skills skills.pre-layering
mv triggers triggers.pre-layering
mv systems systems.pre-layering
# tools 不动
```

**三个都要改，不是只改 skills。** 实测：只腾名 `skills` 时 `legacy-trig` 仍在生效
（探针 `triggers: 5 from 3 of 4 roots [... agent=1]`）。三类内容各自独立判定，skills 通过不代表 triggers 通过。

### 步骤 5 · 改 compose 并起栈

```yaml
# 只加，不改 command
environment:
  - PSI_CONTENT_ROOTS=official=/content/official:enterprise=/content/enterprise:users=/content/users/<open_id>
volumes:
  - ./workspace:/workspace                                  # 不动
  - ./content/official:/content/official:ro
  - ./content/enterprise:/content/enterprise:ro
  - ./content/users/<open_id>:/content/users/<open_id>
```

顺序即优先级，**后面的更靠近用户**（步长 10 递增）。层名来自声明的 name，不从路径推断。

```bash
./restart-stack.sh          # 不要单独 restart gateway
```

**必须用 `restart-stack.sh`**：oauth-proxy 是 `network_mode: "service:gateway"`，
单独重启 gateway 会让 proxy 挂在死掉的 netns 上，而它**仍然显示 Up、8090 仍然 LISTEN**
——公网静默 502（曾持续 29 小时）。这是本次唯一一处 `docker compose up -d` 是正确做法的场景
（换镜像 tag 的发布，改动本来就在镜像里）。

### 步骤 6 · 验收（三层核验的第三层）

| # | 判据 | 命令 | 通过 |
|---|---|---|---|
| V1 | 容器内加载的代码含分层 | `docker exec <c> ls /app/src/psi_agent/session/content_roots.py` | 存在 |
| V2 | 探针报出多层 | `docker logs <c> 2>&1 \| grep layer_source` | 四类各一行，`from N of M roots`，M ≥ 3 |
| V3 | 遗留内容不再生效 | 同上，`[...]` 里**没有** `agent=` 项 | 没有 |
| V4 | 覆盖顺序对 | 探针 + 实际问一句涉及被覆盖 skill 的话 | 拿到 users 层那份 |
| V5 | tools 没丢 | `docker logs <c> 2>&1 \| grep -c 'Loaded tool:'` | 与腾名前同一个数（腾名前先量一次存底） |
| V6 | **公网入口活着** | `curl -sS -o /dev/null -w '%{http_code}' https://<域名>/healthz` | 2xx/3xx |
| V7 | 飞书真能收发 | 私聊发一句，看回复 | 有回复 |

**V6 单列的理由**：内部全绿不证明入口活着。定时任务和发卡片走**出站**，OAuth 回调走**入站**，
只测内部会漏掉入口 502。用 `--resolve` 而不是 `-H Host` 打裸 IP 的 https（SNI 仍是 IP，TLS 层就 alert，
`HTTP=000` 与真 502 分不开）。

**V5 单列的理由**：这是我上一轮提案翻车的那一处，必须每次都量。

---

## 五、之后的标准发布流程

分层上线后，内容和代码的发布路径**分岔了**，这是本次最重要的流程变化。

| 要发的东西 | 路径 | 要不要重启 | 要不要重打镜像 |
|---|---|---|---|
| **内核 / 工具代码**（`src/`、`agents/feishu/tools/`） | git → `build-image.sh` → 换 tag → `docker compose up -d` | 是 | 是 |
| **官方内容**（`/official` 下的 skills/triggers/systems） | git → PR 合并 → 在生产 `git archive` 整版替换 `content/official/` | 是（内容在启动期加载） | **否** |
| **企业内容**（`/enterprise`） | git → PR 合并 → 同上投放到 `content/enterprise/` | 是 | 否 |
| **用户内容**（`/users/<id>`） | 用户在飞书里自己改，B-2 的派生 + 墓碑机制 | 否 | 否 |

### 5.1 官方内容的标准发布（最常走的一条）

```bash
cd /srv/haitun/psi-agent
# 1. 存底当前 official
tar czf /srv/backup/official-$(date +%Y%m%d-%H%M).tar.gz content/official
# 2. 整版替换。先清空再投放 —— 增量投放会留下已被删除的内容
rm -rf content/official.new && mkdir -p content/official.new
git -C /srv/build/psi-agent fetch && git -C /srv/build/psi-agent checkout <commit>
git -C /srv/build/psi-agent archive <commit> agents/feishu/skills agents/feishu/triggers agents/feishu/systems \
  | tar x -C content/official.new --strip-components=2
# 3. 原子换名
mv content/official content/official.old && mv content/official.new content/official
# 4. 重启 + 过 V2/V5/V6/V7
./restart-stack.sh
```

回滚：`mv content/official content/official.bad && mv content/official.old content/official && ./restart-stack.sh`

**为什么整版替换而不是 `git pull`**：`/official` 挂成 `:ro`，容器里改不了；宿主机上用 git 管这个目录，
则任何人在生产上的手改都会变成 git 冲突或被静默 checkout 掉。整版替换让"生产 official == 某个 commit"
恒成立，这正是 8-18 事故（拿漂移目录构建）缺的那个性质。

**`--strip-components=2` 这个数字要现场核**：它依赖 `agents/feishu/` 是两层。改过目录结构就不对，
而表现是内容投到了错的层级、探针报 0 —— 投放后必须 `ls content/official/skills | head` 眼过一遍。

### 5.2 代码发布

沿用 `deploy/haitun/README.md`，加两条本次新增的：

- **G5**：镜像里必须有 `content_roots.py`（overlay 只换 `/app/src`，忘了带就是运行期分层静默失效）。
- **V2/V3/V5**：换完镜像必须核探针那三行，不能只看容器 Up。

### 5.3 compose 不在 git 里 —— 这是个已知缺口

生产 compose 只存在于机器上，仓库无副本（`deploy/haitun/README.md` 已记：`oauth-proxy.py`、
`launch-gateway.sh`、`.env.example` 是"副本"，compose 连副本都没有）。
本次要给 7 个容器各加一条 `PSI_CONTENT_ROOTS`，人工改 7 处、漏一处的表现是"那个人的分层没生效"且不报错。
**建议**（不在本次范围）：把 compose 收进 `deploy/haitun/` 作为准本，用变量渲染每人那一段。

---

## 六、回滚

**一条路，三步一起做。**

```bash
cd /srv/haitun/psi-agent
# 1. 去掉 compose 里的 PSI_CONTENT_ROOTS 与三条 content 挂载
# 2. 目录名改回
cd workspace && mv skills.pre-layering skills && mv triggers.pre-layering triggers && mv systems.pre-layering systems
# 3. 起栈
cd .. && ./restart-stack.sh
# 4. 判据: 探针回到单根
docker logs <c> 2>&1 | grep layer_source     # 期望 "N from N of 1 roots"
```

### ⚠ 不要只做第 1 步

实测三臂对照（`triggers`）：

| 臂 | 目录名 | 变量 | 探针 | 触发器 |
|---|---|---|---|---|
| A 分层态 | `.pre-layering` | 声明三层 | `4 from 2 of 4 roots [official=2 users=2]` | 3 个 |
| **B 只去变量** | `.pre-layering` | 不声明 | `0 from 0 of 1 roots [(none)]` | **0 个** |
| C 完整回滚 | 改回原名 | 不声明 | `1 from 1 of 1 roots [triggers=1]` | 1 个 |

B 臂是最坏的形状：操作者以为在恢复，实际清空了内容，而探针那行是 `(none)` **不是报错**。
原因是不声明内容根时代码回落到 agent 根下的 `triggers`，而那个目录已被腾名。

镜像层回滚（分层代码本身有 bug）：`docker compose up -d` 换回上一个 tag。**未验证** —— 本次尚未发布过新镜像。

---

## 七、未验证清单

| # | 未验证 | 影响 | 怎么验 |
|---|---|---|---|
| U1 | `card.xsd` 17:44 的写者 | 决定迁移期间要不要冻结写入 | 需要 DEBUG 日志或给 write-file 加审计行 |
| U2 | 分层代码在真实生产镜像里的行为 | **全部判据都是本地 rig 跑的** | 步骤 6 的 V1–V7 |
| U3 | 飞书 API 护栏规则在"agent 根 skills 被腾名"形态下的行为 | 只分层提示词索引而漏掉护栏是最贵的失败方向 | B-1 有 14 条多根判据但没覆盖腾名形态，要补一条 |
| U5 | `--strip-components=2` 在真实投放中的层级 | 投错层级 → 探针报 0 | 投放后 `ls` 眼过 |
| U6 | 7 个容器逐个改 compose 的正确性 | 漏一个 → 那人分层静默不生效 | 逐容器过 V2 |

---

## 八、实测依据（本手册的数字来自哪）

全部在本地 rig（`b3rig`）上跑，非推断。四类内容各自独立验证：

| 内容 | 腾名前 | 腾名后 |
|---|---|---|
| skills | 遗留 skill 在索引里 | 不在。`5 from 3 of 5 roots [official=2 enterprise=1 users=2]`，覆盖顺序 users>enterprise>official 全 PASS |
| triggers | `legacy-trig` **仍生效** | 消失。`4 from 2 of 4 roots [official=2 users=2]` |
| systems | `chosen=agent`，实际执行 `LEGACY_SYSTEM` | `chosen=users`，实际执行 `USER_SYSTEM`（调真函数验的，不是只看探针） |
| tools | 2 个 | 2 个（不受影响）；而 agent 根挪走那一臂只有 1 个 |

**过程中有四次判据不吃劲**，全都长着"结论"的样子，记下来因为它们会复发：

1. `_probe_tool.py` 下划线开头 → 工具永不被扫描，两臂都 0 个
2. 工具写成同步 `def` → 加载了文件但注册 0 个工具
3. `TRIGGER.md` 缺必填 frontmatter → 两臂都 0 个触发器
4. `systems` 探针函数名不是 `system_prompt_builder` → 返回空串

四次的表现都是"两臂数字相同"。只跑处理臂不跑对照臂，会得出"分层没生效"或"分层生效了"两种相反的错误结论。

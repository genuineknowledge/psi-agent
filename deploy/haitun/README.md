# `deploy/haitun/` —— 生产部署脚本的版本控制副本

这里的文件分两类, **性质完全不同**:

| 文件 | 性质 |
| --- | --- |
| `Dockerfile` `Dockerfile.overlay` 两份 `*.dockerignore` `build-image.sh` | **准本**。构建时直接被用, 改这里就是改构建 |
| `oauth-proxy.py` `launch-gateway.sh` `.env.example` | **副本**。运行中的是目标机上那份, 改这里不生效, 要人工同步 |
| `audit-workspace-drift.sh` | **判据**。不参与部署, 是投放 `workspace/tools/` 前后拿来量差异的探针 |

还有一类东西**不在这个目录里, 也不在镜像里**: `workspace*/tools/` 那 241 个业务工具文件是
bind mount 到目标机上的, 靠人手 `docker cp` / `cp` 投放。它们是本目录唯一没有构建闸门把着的
部分, 见下面「`workspace/tools/` 的投放」。

---

## 构建资产(`Dockerfile` 一族)

### 为什么它们在 2026-09-10 才进 git

此前**只存在于目标机的构建目录里**, 全库无副本。搬机时只搬了运行目录
`/srv/haitun/psi-agent`(compose + `restart-stack.sh` + `workspace*`), 没搬构建目录 —— 实测
境内 A 机 `47.100.84.197` 上 `find / -maxdepth 5 -iname 'Dockerfile*'` 只有 psi-cloud /
psi-auth-impl / fmbuild 三份。

后果是双重的:

1. **A 机只能做 overlay 构建**(换 `/app/src`), 一旦 `pyproject.toml` / `uv.lock` 变了就没法
   全量 build, 新依赖装不进去 —— 而表现是运行期 ImportError, 不是构建期报错。
2. 发布文档写的「用**仓库里的** `Dockerfile` 全量 build」指向一个不存在的文件。

### 用法

```bash
# 在仓库根。overlay: 只换 src, 秒级
deploy/haitun/build-image.sh overlay <commit>

# full: 依赖变了必须用这个
deploy/haitun/build-image.sh full <commit>
```

`build-image.sh` 顺手把两条原先靠人记的规则变成了闸门: HEAD 必须等于目标 commit 且工作树
干净(发布硬规则 1, 8-18 事故背书); overlay 模式下会拿基础镜像里的 `pyproject.toml` /
`uv.lock` 与当前树比对, 不同就拒绝并提示改用 full。

### ⚠️ 镜像源的默认值按**境内**取, 境外构建必须显式覆盖

这是本目录里唯一一处「同一决策在两地结论相反」的地方, 所以默认值不是中立的。

境内 A 机 `47.100.84.197` 实测(2026-09-10):

| 源 | trixie InRelease | `simple/aiohttp/`(3.95 MB) |
| --- | --- | --- |
| `mirrors.aliyun.com` | 200 · 3.58 MB/s · 0.039s ← 默认 | 200 · 14.5 MB/s · 0.235s ← 默认 |
| `mirrors.cloud.aliyuncs.com` | 200 · 2.79 MB/s · 0.050s | 000(该机无此 pypi 路径) |
| `deb.debian.org` / `pypi.org` | 200 · 126 KB/s · 1.112s | 200 · **33 KB/s · 120s 未下完** |
| tuna | 200 · 95 KB/s · 1.483s | 200 · 1.57 MB/s · 2.168s |

境外 B 机(新加坡)实测(2026-09-01)是**反过来的**: `deb.debian.org` 比 aliyun 快 146 倍
(20.2 MB/s vs 138 KB/s)、tuna 直接 **403**、`pypi.org` 2.70s 优于 tuna 3.72s。

所以境外构建要这样传:

```bash
APT_MIRROR= PIP_INDEX_URL=https://pypi.org/simple \
NPM_REGISTRY=https://registry.npmjs.org \
deploy/haitun/build-image.sh full <commit>
```

`APT_MIRROR=` 是**空值, 表示不换源**, 与「没设」不是一回事 —— `build-image.sh` 用
`${VAR+x}` 区分这两者。

`pypi.org` 那条尤其要留意: 境内它不是 404 也不是超时, 而是 200 之后以 33 KB/s 涓流, 单个索引
120 秒下不完。写死任一边, 换机器时 build 都会挂在装依赖那层, 而报错长得像网络抽风。

基础镜像同理: A 机实测 `registry-1.docker.io` 直连 **15 秒无响应**, daocloud 加速器与 daemon
里配的 `registry-mirrors` 都能拉, 所以默认值写死加速器域名而不是裸 `docker.io`。

### 前端产物由构建阶段 1 生成, 不再手工

`feishu-web/dist/` 被 gitignore 排除, 而后端 `_routes.py` 的 `add_static` 在目录不存在时
**静默跳过** —— 页面 404, 日志只有一行 INFO, 容器状态一切正常。此前的补法是镜像外手工
`npm run build` 再叠一层 `Dockerfile.fw`(B 机 `/srv/haitun/build-34c73c65/Dockerfile.fw`),
纯人工步骤, 忘了做没有任何东西拦得住。

现在 `Dockerfile` 的阶段 1 用 `npm ci` + `npm run build` 产出 dist, 阶段 2 `COPY --from` 取过来。
两处硬要求:

- **拷 dist 必须排在 `COPY src` 之后** —— dist 在 src 子树里, 顺序反了会被盖掉, 而**构建仍然
  成功**。由 `test_dist_copied_after_src_so_it_is_not_overwritten` 钉住。
- **`*.dockerignore` 里的 `dist/` 必须带前导斜杠。** 无锚点写法在任意层级匹配, 会把三棵前端
  (`desktop/spa`, `desktop/spa-v2`, `feishu/feishu-web`)的产物全部排出上下文, 触发同一个静默
  404。由 `test_dist_ignore_rule_is_anchored` 钉住。

overlay 模式下 dist 完全来自基础镜像(它不跑 npm), 所以两份 Dockerfile 都在构建期
`test -f .../dist/index.html` 自检一次。

### `*.dockerignore` 这个文件名不是笔误

BuildKit 支持 `<Dockerfile 路径>.dockerignore`, 且它**优先于**上下文根的 `.dockerignore`。
2026-09-10 在 A 机(docker 29.7.2 / buildkit v0.32.2)实跑验证过生效, 不是照文档推断。这样排除
规则能跟 Dockerfile 放在一起, 不必往仓库根塞一份 `.dockerignore` 去影响同机其他项目的构建。

⚠️ 未启用 BuildKit 的 legacy builder **不认这个文件名**, 那种环境下要么升级 builder, 要么把
文件手工拷成上下文根的 `.dockerignore`。

### 判据

`tests/deploy/test_build_assets.py`(20 条)静态解析 Dockerfile 文本。本仓 CI 没有 docker, 而这
些缺陷都在文本层面: 源写死了、COPY 顺序反了、锚点丢了。真构建的验证在目标机做。

```bash
# 在仓库根跑。PYTHONPATH=src 与 -o testpaths= 都是必须的, 见 AGENTS.md
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -o testpaths= --no-cov tests/deploy/ -q
```

### 已知没验到的

- **全量 build 在 A 机实跑过一次通过**(镜像 `psi-agent-gateway:probe-46566`, 见 PR 正文),
  但**没有拿它替换过任何在跑的容器** —— 镜像能起、页面能开都没验。
- 境外机的 build-arg 组合(`APT_MIRROR=` 空值那条路径)**没在境外机上跑过**, 只有本地判据。
  B 机当前 `running=0`, 而那些数字是 9-01 量的。

---

## `workspace/tools/` 的投放

### 为什么这一章必须存在

上面两章覆盖的是「镜像里的东西」和「目标机上的单个脚本」。业务工具是第三类, 而且是最容易
静默错的一类: `agents/feishu/tools/` 那 241 个 `.py` **不进镜像**, 而是 bind mount 进容器,
换镜像发布完全不会更新它们, 靠人手投放。没有构建闸门, 没有测试, 漏投一个文件不报错。

2026-09-14 实测的后果: 生产 `_feishu_spec.py` 是新版(683 行, 分层接口 `rules_for_layers` 在
里面), 而调用它的 `_feishu_api_impl.py` 还是旧版(434 行, 仍调单目录的 `rules_for`)。9-12 那次
投放投了前者、漏了后者。于是 **195 条飞书 API 护栏规则一条都不生效**, 该拒的调用全部放过,
唯一线索是一行 INFO 级日志 `0 from 0 of 1 roots [(none)]`。修法不是改代码 —— `origin/main`
里早就是对的 —— 而是把漏掉的那个文件投上去。

朝最贵的方向错、表面功能跑通、日志不报错, 这三条凑齐就没有判据可言。所以这一章的主体是
一个脚本, 而不是一串命令。

### 投放前后各跑一次审计

```bash
# 在目标机上跑(脚本自己找 clone, 或用 REPO= 指定)
bash /tmp/audit-workspace-drift.sh origin/main                 # 审全部三份 workspace
bash /tmp/audit-workspace-drift.sh origin/main workspace       # 只审 gateway 那份
```

它把差异分成四类, **混成一个数字就再也分不开了**:

| 类别 | 判法 | 该怎么处置 |
| --- | --- | --- |
| 同 | md5 相等(LF 归一化后) | —— |
| 落后 | 与 git 不同, mtime 是**整秒** | 部署投放留下的旧版, 可安全覆盖 |
| 领先 | 与 git 不同, mtime **带纳秒** | ⚠️ 有人在生产上就地写过, 覆盖即丢代码, 必须人工定归属 |
| 缺失 | git 有而生产没有 | 补投 |
| 生产独有 | 生产有而 git 没有 | 通常是 git 里已删的文件, 确认后删 |

有「领先」文件时脚本 **退出码 1**, 让调用方停下而不是继续覆盖。

mtime 纳秒位这个判据来自 9-12 的取证: 投放(`cp -p` / tar 保留源 mtime, 或 CI 产物)落在整秒,
就地编辑落在带纳秒的时刻。uid 不是判据 —— 两种情况都可能是 root。

「落后」和「领先」必须分开, 因为**存在生产领先于 git 的真实文件**: `_card_dsl.py` 是未合并的
PR #867(fork `Twin-Ghosts`)的代码再往前改出来的, 生产是那个 PR 的超集。当成「旧版」一键同步
会静默丢掉 607 行功能(原生 table 渲染 / `bind-field` 回写 / `action_id` 撞车防护)。一键同步
之所以不安全, 全部落在这一个区分上。

### 三份 workspace 不是彼此的副本

目标机上有三份, 各挂给一个容器:

| 目录 | 容器 | 状态 |
| --- | --- | --- |
| `workspace/` | `psi-agent-gateway` | 与 `origin/main` 基本齐平: 同 205 / 落后 32 / 领先 3 / 缺失 1 / 独有 0（2026-09-14 脚本真机实测，基准 `ef3cad55`） |
| `workspace-luolin/` | `psi-agent-luolin` | `tools/` 子树 241 个 `.py`(9-16 铺平 174→247, 清残留 247→241) |
| `workspace-chengxx/` | `psi-agent-chengxx` | `tools/` 子树 245 个 `.py`(9-16 铺平 180→251, 清残留 251→245); 多出的 4 个是 `tools/platforms/` |

计数口径要说清楚: 上面是 `find <workspace>/tools -name '*.py'` 的**子树**数。整份 workspace
连 `skills/` 一起数是 307 / 327, 而 `tools/` **顶层** `ls *.py` 是 207 / 207 —— 顶层数正是工具
索引真正扫的那批(`glob("*.py")` 不递归), 三个口径差得很远, 混用会得出「铺平没生效」之类的
错误结论。

⚠️ **不要对两份私有 workspace 做「补依赖闭包」式的增量投放。** 9-12 实测: 为补一条断链投了
13 个文件, 其中新版 `_feishu_impl.py` 需要新增的 `_feishu/bitable.py`, 而两台机器上是 8-07 的
旧版, 40+ 文件立刻 `cannot import name 'get_bitable_record_impl'`, 工具数从 198 掉到 87。
已完整回滚。闭包的边界是整棵依赖树, 不是看得见的那几条报错 —— 所以这两份只能整份铺平, 不能
增量。

### 铺平是增量的, 所以旧版文件会留下

9-16 的整份铺平只**补缺失**, 不覆盖已存在的同名文件。所以铺平之后两份私有 workspace 看上去
齐平了(工具数 198→239), 但历史残留一个都没被清掉 —— 「文件数对上了」和「文件内容对上了」是
两件事, 前者不蕴含后者。

补完之后必须再跑一次审计, 按上面那张四类表处置。9-16 清掉的残留(两份各 6 个 `.py`):

| 残留 | 为什么留下 | 处置 |
| --- | --- | --- |
| `feishu_calendar.py` / `feishu_elearning.py` / `feishu_permission.py` | git 里已删的工具(`0dbf3234` #635 / `17785289` #612), 铺平不删文件 | 删 |
| `browser_cdp.py` / `_browser_cdp_impl.py` | 位置错了, 仓库里在 `agents/desktop/tools/` | blob 在库里 → 删 |
| `_private_space.py` | 8 个调用方还是旧版, 引用它 | ⚠️ **先换 8 个调用方, 再删它** |

那 8 个调用方是 `bash.py` `describe_image.py` `search_content.py` `powershell.py`
`_runtime_paths.py` `_content_layers.py` `feishu_drive.py` `write_excel.py`。顺序反了就是 8 个
即时 `ImportError`。

**残留里可能混着只活在生产的重构。** `workspace-chengxx/tools/platforms/`(4 文件 13504 字节)
把 `computer_use.py` 从 250 行 mac-only 单体拆成了后端分派, git 里一处都没有。这一类要**收编**
而不是删 —— 不动它等于下次投放冲掉。已收编为 `agents/{desktop,feishu}/tools/_platforms/`,
下划线是内核契约不是命名风格(见该包 docstring)。

生产上 `workspace-chengxx/tools/platforms/` **仍在原地, 本次没动**: chengxx 的
`computer_use.py` 就是那个分派器, 删掉目录它立刻挂。收编的意义是让这份代码进仓库, 下次投放
不再冲掉它; 生产端换成 `_platforms/` 是随投放走的后续动作, 不在本次范围。

判「生产独有」用 `git hash-object --no-filters` + `git cat-file -e`, 不要用 mtime。`--no-filters`
是必须的: 仓库检出带 CRLF 时 `core.autocrlf=true` 会静默把输入归一化, 原始与归一化两条量法
算出同一个 SHA, 刚报「不在库里」的文件会翻成「在」。

### 投放的硬规则

1. **清单要走全树, 不能用顶层 glob。** 私有子目录 `_feishu/` 下有文件, 顶层 glob 漏掉 6 个,
   其中 3 个的缺失直接让工具加载失败。脚本用 `find`, 不用 `*.py`。
2. **md5 比对前先 LF 归一化**(`tr -d '\r' | md5sum`)。生产是 LF、仓库检出可能是 CRLF, 裸比对
   会报几乎全不一致, 已因此出过一次错误判断。
3. **重启前先在容器内 import 探一次。** 这是上面那次回滚教的: 文件拷进去时不会报错, 断链要
   到进程重启后加载工具才暴露, 而那时旧进程已经没了。
4. **`docker compose` 收的是 service 名, 不是容器名。** service 是 `gateway` / `oauth-proxy` /
   `private-luolin` / `private-chengxx`; 容器是 `psi-agent-gateway` 等。传容器名会
   `no such service` —— 而如果顺手把输出重定向掉, 看起来就像重启成功了。
5. **`restart` 不换镜像。** 换 tag 的发布要 `docker compose up -d`(改 `workspace/` 内容时**绝
   不能**用它, 见 AGENTS.md); `oauth-proxy` 用 `network_mode: "service:gateway"`, gateway 重建
   后它必须跟着 `restart`, 否则挂在死掉的 netns 上, 公网静默 502。
6. **数失败条数要按容器本次启动去重。** gateway 是多会话的, 每个会话都重扫一遍工具, 所以
   `grep -c 'Failed to load'` 会按会话数翻倍(实测 106 = 2 类报错 × 约 40 个会话)。要按报错
   消息去重, 并确认 `StartedAt` 真的前进了。

### 判据

脚本本身用一棵人造树做过变异复核(2026-09-14): 全同树报 241/241 且退出 0; 分别造出落后 /
领先 / 子目录里的缺失 / 生产独有各一个, 四类被各自单独认出且退出码变 1; 再把一个文件整体转成
CRLF, 仍报「同」, 归一化没有产生假阳性。

**真机首跑(2026-09-14 15:2x, `root@47.100.84.197`)**: 输出 同 205 / 落后 32 / 领先 3 / 缺失 1 /
独有 0, 退出码 1。

首跑本身查出了脚本的一处判据缺陷, 已修 —— 首跑给的是 31/4, 与手工量的 32/3 差
`meeting_pipeline_run.py`:

- **原判据(mtime 纳秒位)不成立。** 不带 `-p` 的 `cp` 把 mtime 设成「此刻」, 而此刻天然带纳秒 ——
  手工投放与就地编辑在 mtime 上无法区分。那个文件带纳秒被判「领先」, 但内容与 commit
  `880d9831` 逐字节相同, 其实是**落后** 5 天。
- **改成内容判据**: `git hash-object <生产文件>` 得 blob SHA, `git cat-file -e` 查它在不在仓库里。
  在 = 这份内容是某个 commit 里的版本, 覆盖不丢东西; 不在 = 真「领先」。
- **顺带修了第二个坑**: 原来按 glob 顺序取第一个 clone, 而目标机上 `/tmp/rel-482d970c` 只有 729
  个 commit、`/tmp/rel-5565f4bd` 有 3133 个。取到残缺那份会把「落后」误判成「领先」。改为取历史
  最全的。
- **新判据的变异复核**: 仓库存在过的旧版本 + 纳秒 mtime → 「落后」✅; 从未进 git 的内容 →
  「领先」✅; **整秒 mtime 但内容不在 git → 仍「领先」**✅ —— 最后这条旧判据会误判成「落后」并
  静默覆盖, 是这次修复真正堵上的漏。

### 已知没验到的

- 两份私有 workspace 的整份铺平 9-16 已做(174→247 / 180→251), 残留已清(→241 / →245)。
- 三个「领先」文件的归属未定: `_card_dsl.py` / `_rookie_sop_card.py`(9-12 17:44, 源头是未合并的
  PR #867) / `tencent_meeting.py`(9-14 14:19)。
- **收编进来的 `_platforms/` 在 Linux 上会抛异常**, 而它替换掉的 mac-only 单体是返回字符串的。
  两份私有 workspace 挂的容器都是 Linux, 所以那条分支就是它们的常态路径。异常被
  `agent.py` 的 `except Exception` 兜住(工具返回一行 `Error executing tool ...`, 不会崩会话),
  但这是降级不是设计。
- `_platforms/base.py` 里 `_preflight()` 跑在 `REFUSALS` 检查之前: 没装 cua-driver 的机器上,
  一个本该 `[Refused]` 的动作会先报 `[Error] cua-driver CLI not found`。本次未改。
- `_platforms/win.py` 的 `REFUSALS` 键是 `press_key`, 而对外动作名是 `key` —— 看着对不上,
  没验。
- **`tencent_meeting.py` 那份要尽快收编**: 它加的 `_skill_script()`(改从 `PSI_CONTENT_ROOTS`
  逐层找技能脚本)在 git 里一处都搜不到, 只活在生产上 —— 下次镜像发布或批量投放就会把它冲掉,
  而它正是分层挪走 `<workspace>/skills` 之后的必要适配。但同一份改动里 PR #859 加的
  `anyio.fail_after` 超时保护不见了(生产那份 `grep -c` 得 0), 收编时应当两者都要。
- **`workspace/skills` 下的 4 条软链也只活在生产上**: 有人在 9-14 14:21–14:33 把
  `tencent-meeting-mcp` / `positive-negative-list` / `workflow` / `fusion-flow-legacy` 软链到
  `/content/official/skills/`, 给三个硬编码 `<workspace>/skills` 路径的工具兜底。已实测容器内 4
  条链全解析、三个工具都能用(`rules.load_rule_pack()` 得 16 条、`_gen_mcp_skill.SKILLS` 是目录)。
  同样不在 git 里。

### ⚠️ 目标机是直连, 不要走跳板机

生产是 `root@47.100.84.197`(境内 A 机), **直接 ssh 就行**, 不经任何跳板。

2026-09-14 我用了 `~/.ssh/config` 里的 `haitun1` 别名, 它配着 `ProxyJump jump` 指向实验室内网
`192.168.63.174` —— 那台有 docker 但**跑 0 个容器、没有 `/srv/haitun`**。跳板机当时 TCP/22 不通,
我据此得出「生产不可达、闭环做不了」并把这个结论写进了台账、交付文档和 PR #953 正文。

**结论是错的: 生产一直好着, 我量的是另一台机器。** 别名里带 `ProxyJump` 不会在报错里显形 ——
`Connection timed out` 长得跟生产宕机一模一样, 而「跳板机不通」与「生产不通」是两件事。

判据: 登进去先核 `ls -d /srv/haitun` 和 `docker ps -q | wc -l`(生产是 9 个容器), 别拿 ssh 是否
成功当落点正确的证据。

---

## `oauth-proxy.py`

**这份是生产机 `/srv/haitun/psi-agent/oauth-proxy.py` 的版本控制副本, 不是运行中的那份。**

改了这里**不会**对生产产生任何影响。生效需要人工同步:

1. 由负责人批准改动;
2. 拷到生产机 `/srv/haitun/psi-agent/oauth-proxy.py`;
3. 重启栈。**必须连带重建 `oauth-proxy` 容器** —— 它在 compose 里是
   `network_mode: "service:gateway"`, 只重建 `gateway` 时它会显示 `Up` 但网络命名空间
   已经失效。

收进仓库的原因: 它是**公网唯一入口**、决定了哪些路径能从外网打到 Gateway, 而此前只存在
于那一台机器上 —— 一个没有版本控制、没有 review、没有判据的安全关键文件。

### 它在链路里的位置

```
浏览器 / 飞书客户端
      │  443
      ▼
   Caddy (占 80/443, TLS 终止)
      │  反代到 127.0.0.1:8090
      ▼
   oauth-proxy.py  ← 本文件。白名单反代, 白名单外一律 404
      │  转发到 127.0.0.1:8080
      ▼
   Gateway 容器 (与本代理共享 netns, 故上游是 127.0.0.1)
```

Gateway 端口**不对外暴露**, 这一跳是唯一的入口。

### 它为什么必须是白名单

Gateway 上有一批**一行鉴权都没有**的路由, 与飞书网页应用的接口同住一个进程:

| 路由 | 危害 |
| --- | --- |
| `POST /sessions/{id}/chat` | 直接驱动 agent 执行工具, 含 bash。**带鉴权的对等物 `/feishu/sessions/{id}/chat` 是放行的**, 裸的这条不放行 |
| `POST /sessions` | 建 Session |
| `GET /sessions` `GET /sessions/{id}/history` | 读任意会话历史 |
| `GET /workspace/file` | 读 workspace 里的文件 |
| `POST /chat/completions` | 直接用掉模型额度 |

挡住它们的只有这个白名单一层, 所以 `ALLOWED_PATHS` / `ALLOWED_PREFIXES` **只列前端真的
会打的路径**, 多放一条就是白送一份公网暴露面 —— 而多放行**没有任何症状**, 直到有人从
公网打过来。

### 改白名单前先看判据

`tests/deploy/test_oauth_proxy.py`(20 条)双向钉住:

- 该放行的没放行 → 红(清单来自 `feishu-web/api-paths.json`, 前端加端点会被发现);
- 不该放行的放行了 → 红(`test_core_routes_stay_blocked` 逐条列了上表那些);
- 头没双向转发、多条 `Set-Cookie` 丢了、路径穿越能过 → 各有一条。

```bash
# 在仓库根跑。PYTHONPATH=src 与 -o testpaths= 都是必须的, 见 AGENTS.md
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -o testpaths= --no-cov tests/deploy/ -q
```

### 已知没验到的

**生产真机一次没验。** 本轮只在仓库里出代码 + 本地判据, 假上游不是真 Gateway。同步到生产
后至少要量三件事(前两件本地量不到, 见 `feishu-web/AGENTS.md` 的「本地与云上的分叉点」):

1. 真免登能拿到 cookie 并保持登录 —— 本机没有 JSAPI, 整条 `code → open_id` 换取链没跑过;
2. 放行清单逐条可达:
   ```bash
   python scripts/feishu_web_paths.py --print-shell > check-feishu-web-paths.sh
   bash check-feishu-web-paths.sh http://127.0.0.1:8090
   ```
   注意这份清单含 `/sessions` `/titles` `/workspace` 一族 —— 那几条**在这一跳报 FAIL 是
   预期的**(刻意不放行), 不要照着把它们加进白名单。
3. `/feishu-web/` 的静态产物能加载(依赖 Gateway 侧 `dist/` 存在, 不存在时 `add_static`
   静默跳过)。

### 放行范围里有 SSE, 转发层因此是流式的

`POST /feishu/sessions/{session_id}/chat`(带鉴权的聊天流, 能驱动 agent 执行工具)**在放行
范围内**, 它是一条 SSE。

它不是被单独加进白名单的, 而是**被前缀捎带进来的**: `ALLOWED_PREFIXES` 里的
`/feishu/sessions/` 原本是为 `GET /feishu/sessions/{id}/history` 加的, `startswith` 把同一
前缀下的 chat 一起放行了。这一点值得留意 —— 往那个前缀下加路由**不需要动白名单就会自动
对公网可达**, 加的时候要自己判断该不该暴露。

于是转发层用 `web.StreamResponse` 边收边转(`_relay`), 不是把 body 读完再回。三处硬要求,
每处都有判据:

| 要求 | 写错的表现 | 判据 |
| --- | --- | --- |
| 逐块转发, 不自己攒缓冲 | 打字机效果消失, 长回答疑似卡死 | `test_sse_chunks_arrive_before_upstream_finishes` |
| 不设 `Content-Length`, 交给 chunked | 截断, 或客户端等永远补不齐的字节 | `test_sse_response_has_no_content_length` |
| 响应头在 `prepare()` **之前**写完 | `Set-Cookie` 静默丢失, 登录不上 | `test_set_cookie_survives_streaming` |

超时也跟着改了: `ClientTimeout` **不设 `total`**。`total` 管的是「从发出到响应体读完」的
整段时间, 对 SSE 就是一条硬性寿命 —— 原先的 `total=15` 会让生成超过 15 秒的长回答从中间
断掉(实测: 客户端收到前几个 event 后拿到 `ClientPayloadError`, 等不到 `[DONE]`), 而短回答
一切正常, 所以这个缺陷很容易漏。改用 `sock_connect` + `sock_read` 两个闸, 它们量的都是
**间隔**而非总时长, 上游真卡死时仍能断开。由
`test_upstream_timeout_has_no_total_deadline` 钉住。

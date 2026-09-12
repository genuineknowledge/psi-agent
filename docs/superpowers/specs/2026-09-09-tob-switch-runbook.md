# ToB 搬迁停机窗执行手册(9-10 20:00)

> 本文是**窗内照着敲的清单**,不是设计文档。设计理由见
> [`2026-09-09-tob-migration-oplog.md`](2026-09-09-tob-migration-oplog.md)。
>
> 状态:9-09 13:45 编写,**18:25 因飞书通道模式的发现做过一次结构性修订**(阶段 4/5)。
> 标「未验证」的步骤在窗前尚未实测过。

## 结论先行

- 全量传输**已在窗前跑完**(9-09 16:40 镜像 / 18:04 workspace),阶段 3.1–3.3 也已完成,窗内从 3.4 开始。
- 窗内关键路径:**停 B 的栈 → 增量重导 → 起 A 的栈 → 判据 5.3 长连接建立 → 自测**,预计 30~45 分钟。
- ⚠️ **真正的切换点是飞书出站长连接的交接,不是 DNS。** 顺序必须是**先停 B 再起 A**,
  反过来会让两台机器同时抢同一批用户的消息且无任何报错。详见阶段 4 顶部与阶段 5。
- DNS 只影响 OAuth 授权回调,**不影响消息收发**,顺序无关。
- 回滚只需**重启 B 的栈并把 DNS 指回 B**,B 的数据与容器全程不删。

## 窗前必须完成的前置项(阻塞窗内执行)

| # | 事项 | 谁做 | 为什么阻塞 |
|---|---|---|---|
| 1 | **`lark.oauth.` 的 DNS TTL 600s→60s**(只这一条,别动 `account.`,见阶段 5.5) | **负责人**(两机均无 aliyun 凭据) | 不提前一个完整 TTL 周期改,切换后旧解析仍被缓存最长 600s。**注意:这一条只影响 OAuth 回调,不阻塞消息收发** |
| 2 | ~~全量传输跑完~~ **✅ 9-09 18:04 已完成** | — | — |
| 3 | 找 luolin / chengxx 两位主人在 B 上实测一轮 | 负责人协调 | 统一镜像后私有容器工具数 277→192,需真人确认功能没退 |
| 4 | **决定 A 机自测怎么隔离飞书通道**(方案 ① 只起 gateway / 方案 ② 换测试应用) | 负责人 | 不隔离就起 A 栈 = 两台机器抢真实用户消息。见阶段 4 顶部 |

## 阶段 0:窗前 30 分钟(无副作用,可提前做)

```bash
# 0.1 确认传输已完成且完整
ssh root@8.222.255.23 'tail -2 /root/xfer-images.log /root/xfer-ws.log'
#   期望两份都有 END 行且 PIPE_EXIT 全 0

ssh root@47.100.84.197 'cd /srv/haitun/incoming && zstd -t images.tar.zst && zstd -t workspaces.tar.zst && echo BOTH_INTACT'

# 0.2 完整性判据
#
# ⚠️ 不要写成"两侧 md5 比对"。images/workspaces 两份是
#    `docker save|tar -cf - | zstd | ssh cat >` **流式直传**(B 机根磁盘 80% 满,
#    这正是流式的原因), B 侧磁盘上**不存在**这两个文件, 只有 md5 是量不出来的。
#    B 上 /tmp/*.zst 只有 cfg / fm.dump / fm-repo 三份小的, 那三份才能两侧比 md5。
ssh root@8.222.255.23 'md5sum /tmp/cfg.tar.zst /tmp/fm.dump.zst /tmp/fm-repo.tar.zst'
ssh root@47.100.84.197 'cd /srv/haitun/incoming && md5sum cfg.tar.zst fm.dump.zst fm-repo.tar.zst'

# 那两份大的改用三条判据:
#   ① PIPE_EXIT 全 0(见 0.1)——管道任一环失败都会露头
#   ② zstd -t 帧校验通过(见 0.1)——zstd 每帧带 xxhash, 截断/坏字节会被抓到
#   ③ 镜像用 image ID 比对: docker save 保的是内容摘要, 比 archive md5 判据更强,
#      因为它证明的是"装载进 A 的层内容与 B 逐字节相同"。B 侧实测三个 ID:
#        meeting-fix-...-1154  sha256:c84002c4fe02acd7f4c6955e7ee6025e3e6ea5c8086667a3333ba9506b9b96c9
#        86d5f755              sha256:d0dec87fd82730e9cf1ec8bd4bb1f3f4e94e32fcf137f46eb2ad3ac620768819
#        local                 sha256:3dc1521172c953e8507a8176856f4f7a25d0d73a0b81febcf5c571b602bdd378
#      (阶段 3 装载后核对, 见 3.x)
#   ④ workspace 用文件数清单比对。
#
#      ⚠️ 两侧必须跑**同一份脚本**: /root/count-ws.sh (B) 与 /root/prep/count-ws.sh (A),
#         md5 均为 0e87dfa65918e88e31d118f36f9bd5d4。别手敲 find —— 排除模式差一点,
#         数字就对不上, 而你分不清那是排除差异还是真丢了文件。
#
#      B 侧基线(**用 17:47 这组, 不要用 14:10 那组**):
#        workspace          files=35690  psi=2394  tools_py=219  dotenv=yes
#        workspace-luolin   files=1348   psi=9     tools_py=155  dotenv=yes
#        workspace-chengxx  files=1164   psi=6     tools_py=161  dotenv=yes
#
#      判据(9-09 18:08 实测后修正): **dotenv 必须 = yes**; tools_py 见下方注;
#      files / psi 会**比基线小一截, 这是正常的**, 成因见下。
#
#      ⚠️ **files 偏小约 1000 不是丢文件** —— 判据脚本与传输排除清单不一致造成的。
#         /root/xfer-excludes.txt 有 10 条模式(skills.pre-* / tools.pre-* / *.bak /
#         *.bak-* / *.bak.* / .bak-* / src.bak-* / src.old-* / *.wav / agent-video),
#         而 count-ws.sh 的 find 只排 src.old-* / *.wav / agent-video。所以包里天然
#         没有部署快照和 .bak 备份, 但 B 侧 find 会把它们数进去。
#
#      9-09 实测 A 侧: workspace 34685/2374/218, luolin 1317/7/155, chengxx 1140/6/161。
#      两侧完整清单做 comm -23 后, 1073 个差额**逐项归因、意外丢失 0 个**:
#        908 个 = skills.pre-* / tools.pre-* / __pycache__ / .mcp_cache / .pyc(设计排除)
#        104 个 = *.bak 各式备份(设计排除)
#         61 个 = mtime ≥ tar 启动时刻 12:22:16(传输期 B 新写的, 正常漂移)
#
#      ⚠️ 分类新旧文件时**不要用 `[ "$f" -nt marker ]`** —— marker 不存在时 -nt 恒为真,
#         会报出"全部都是传输后新增"。用 stat -c %Y 与 date -d "…" +%s 比绝对秒数。
#
#      tools_py: A 侧解包后是 218, 补齐 PR 871 的文件后应为 **219**(= B 基线)。
#      这一项**必须完全相等**, 它是判据⑤ 的交叉印证。
#
#      ⚠️ tools_py 从 218 涨到 219 是**预期的**, 不是异常: 9-09 15:23 投了 PR 871 的
#         feishu_todo_compare_card_send。手册早先记的 14:10 那组数字在投放**之前**,
#         照那组核会把正常状态判成"多了一个文件"。同理 4.1 的工具数基线是 221/131。
#
#   ⑤ **PR-797/868 的 6 个 TASK.md + 1 个 tool 必须逐个核 md5**(不能只看文件数)。
#
#      为什么必须核: tar 12:22 启动、按字母序推进 workspace/ 下的目录, 而这 7 个文件是
#      **15:23–15:25 才投放的**(schedules 三个 TASK.md 的 mtime = 15:25:14/16/18)。
#      tar 读到 schedules/(s 位) 与 tools/(t 位) 的时刻与投放时刻只差几分钟, 谁先谁后
#      **无法从目录序推断** —— 早先手册里"所以那 7 个文件在包里"那句推断已删除, 它是
#      拿 tar 当时在读 .psi/appdata/histories 反推的, 不构成证据。
#
#      失效表现极隐蔽: 文件数一个不少、判据④ 全绿, 但包里是**旧版** TASK.md, 搬完后
#      三条定时任务按旧逻辑发卡, 没有任何报错。
#
#      B 侧基线(LF 归一化: tr -d '\r' | md5sum, 取前 12 位):
#        schedules/remind-checkin-gaobo-0950/TASK.md   116b36acee7f   394 B  mtime 08-31 19:44
#        schedules/remind-checkout-gaobo-1930/TASK.md  100915ee1bb8   405 B  mtime 08-31 19:45
#        schedules/remind-lunch-gaobo-1155/TASK.md     4f6c3f76ee89   367 B  mtime 09-03 13:02
#        schedules/todo-ledger-push/TASK.md            4522f9281e97  3908 B  mtime 09-09 15:25:14
#        schedules/todo-remind/TASK.md                 aa634b3c242e  2664 B  mtime 09-09 15:25:16
#        schedules/todo-writing-check/TASK.md          693a38de447f  3412 B  mtime 09-09 15:25:18
#        tools/feishu_todo_compare_card_send.py        3cab0e8525e2  5007 B  mtime 09-09 15:23:54
#
#      ⚠️ 路径是 workspace/schedules/, **不在 .psi/ 下** —— .psi/schedules/ 不存在,
#         照那个路径核会得到"文件不存在", 容易误判成丢文件。
#      cron 表达式一并核(改错会静默偏时): 0 16 / 30 14 / 0 15 * * 1,3,5 各一条,
#      另三条 gaobo 提醒是 50 9 与 30 19 (* * 1-6) 与 55 11 (* * *)。
#
#      不相等的处置: **不重传整包**, 直接 scp 补这 7 个文件, 再复核 md5。

# 0.3 A 机装 ToB 的 Caddy vhost(漏项②)
ssh root@47.100.84.197 '
  mkdir -p /etc/caddy/tob.d
  cp /root/prep/caddy/lark.oauth.caddyfile /etc/caddy/tob.d/
  cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-tob-$(date +%Y%m%d-%H%M%S)
  grep -q "import /etc/caddy/tob.d" /etc/caddy/Caddyfile \
    || printf "\nimport /etc/caddy/tob.d/*.caddyfile\n" >> /etc/caddy/Caddyfile
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && systemctl reload caddy
'
# 判据: 得 502 (上游未起时正常), **不是** 静态页 200。得 200 说明 import 没生效。
ssh root@47.100.84.197 'curl -s -o /dev/null -w "%{http_code}\n" -H "Host: lark.oauth.genuineknowledge.cn" http://127.0.0.1/'
```

## 阶段 1:停 B(窗口开始,T+0)

```bash
# 1.1 记下停机前的基线, 供事后比对
ssh root@8.222.255.23 '
  for c in psi-agent-gateway psi-agent-luolin psi-agent-chengxx; do
    echo "$c: $(docker logs "$c" 2>&1 | grep -oE "Loaded [0-9]+ tool\(s\) from [0-9]+ file\(s\)" | tail -1)"
  done'

# 1.2 停 ToB 四容器(不停 psi-cloud / litellm / postgres —— 库还要再导一次)
#     用 stop 而非 down: 容器保留, 回滚时 start 即可。
ssh root@8.222.255.23 'cd /srv/haitun/psi-agent && docker compose stop && docker ps -a --format "{{.Names}} {{.Status}}" | grep psi-agent'

# 1.3 停 host 上的 MCP 与 embed-proxy(它们还在往库里写)
ssh root@8.222.255.23 'systemctl stop fusion-memory-mcp fusion-memory-embed-proxy && systemctl is-active fusion-memory-mcp fusion-memory-embed-proxy'
#   期望输出 inactive inactive —— 必须确认停住, 否则增量导完它还在写
```

**飞书通道**:一个 App 只维持一条出站 WS。B 的容器停掉后通道即断,
A 起来才会重连。**两边都不能同时开**。

## 阶段 2:增量重导(T+2min)

全量 dump 是 12:22 取的,之后 B 仍在写(实测漂移约 0.1%/小时)。

```bash
# 2.1 停写之后重新 dump(此时无人写, 数据静止)
ssh root@8.222.255.23 'docker exec fusion-memory-postgres pg_dump -U fusion -d fusion_memory -Fc | zstd -3 > /tmp/fm-delta.dump.zst && ls -l /tmp/fm-delta.dump.zst'

# 2.2 传到 A(此时链路不再与全量竞争, 约 44MB / 134KB/s ≈ 5.5 分钟)
ssh root@8.222.255.23 'rsync -e "ssh -o StrictHostKeyChecking=no -i /root/.ssh/id_migrate" --partial --inplace /tmp/fm-delta.dump.zst root@47.100.84.197:/srv/haitun/incoming/ && echo SENT'

# 2.3 A 上重建库并恢复(drop 再 create, 不要往已有数据上叠)
#
# ⚠️ 顺序要紧: 如果此时 A 上的 MCP 已经起过, 它会占着到 fusion_memory 的连接,
#    DROP DATABASE 会直接失败("is being accessed by other users")。
#    所以先确认 A 的 MCP 没起 / 或先停掉它。实测 9-09 演练时连接数为 0 才 drop 成功。
ssh root@47.100.84.197 '
  cd /srv/haitun/fusion-memory && docker compose stop mcp 2>/dev/null
  docker exec fusion-memory-postgres psql -U fusion -d postgres -tAc \
    "select count(*) from pg_stat_activity where datname='"'"'fusion_memory'"'"'"
'
#   判据: 必须是 0 才继续。非 0 就先找出是谁连着(pg_stat_activity 的 application_name)。

ssh root@47.100.84.197 '
  docker exec fusion-memory-postgres psql -U fusion -d postgres -c "DROP DATABASE IF EXISTS fusion_memory;"
  docker exec fusion-memory-postgres psql -U fusion -d postgres -c "CREATE DATABASE fusion_memory OWNER fusion;"
  zstd -dc /srv/haitun/incoming/fm-delta.dump.zst | docker exec -i fusion-memory-postgres pg_restore -U fusion -d fusion_memory --no-owner --no-privileges
  echo "RESTORE_EXIT=$?"
'
# 已实测: fusion 是 superuser 且有 createdb 权限(rolsuper=t rolcreatedb=t), 故 drop/create 可行。
# 已实测: pg_dump/pg_restore 都在容器的 /usr/bin/ 里; -Fc dump 产出 44990737 字节。
```

**判据**(逐表比对,别只看退出码):

```bash
# 把 rowcounts 查询写成文件再 scp —— 嵌套引号经 ssh 会被剥掉一层, 直接内联会报 syntax error
# 期望: 33 表 / 107 索引 / vector 0.8.6, 且 A 与 B 逐表行数**完全相等**(此时 B 已停写)
```

## 阶段 3:起 A(T+10min)

> ### ✅ 3.1 / 3.2 / 3.2b / 3.3 已在窗前完成(9-09 16:40 与 18:15),窗内**跳过**
>
> **不要重跑 3.2** —— 重解包会用包里的**旧版** `schedules/*/TASK.md` 覆盖掉 18:10 补齐的
> 4 个文件(判据⑤,见 0.2 ⑤),而且覆盖后一切正常、无任何报错。
> **不要重跑 3.3** —— `patch-envs.py` 会因 `172.19.0.1` 已不存在而 `exit 3`(guard 生效,
> 不是故障),但没有重跑的必要。
>
> 窗内从 **3.4** 开始。已完成项的实测结果:
>
> | 步骤 | 结果 |
> |---|---|
> | 3.1 镜像装载 | `PIPE_EXIT=0 0 0`,三个 digest 与 B 逐字节相同 |
> | 3.2 workspace 解包 | `PIPE_EXIT=0 0`,`untar.err` 为空,27 秒 |
> | 3.2b 清单 | `34687/2374/219`、`1318/7/155`、`1141/6/161`,`tools_py` 与 B 相等 |
> | 3.2c 判据⑤ | 抓到 7 个文件没进包,已补齐,md5 四项全中 |
> | 3.3 网段 | `PATCHED_FILES=3`、`remaining=0`,`docker compose config -q` 转 EXIT=0 |

```bash
# 3.1 解开镜像并核 digest
ssh root@47.100.84.197 'zstd -dc /srv/haitun/incoming/images.tar.zst | docker load && echo LOAD_OK'
# 判据: 三个 ID 必须与 B 逐字节一致
#   meeting-fix-main-a1bc44d9-20260907-1154 -> sha256:c84002c4fe02acd7...6b9b96c9
#   86d5f755                                -> sha256:d0dec87fd82730e9...20768819
#   local                                   -> sha256:3dc1521172c953e8...602bdd378

# 3.2 解开 workspace
ssh root@47.100.84.197 'cd /srv/haitun/psi-agent && zstd -dc /srv/haitun/incoming/workspaces.tar.zst | tar -xf - && ls -d workspace workspace-luolin workspace-chengxx'

# 3.2b 清单比对(替代不成立的 md5 判据, 见 0.2 ④)
ssh root@47.100.84.197 'cd /srv/haitun/psi-agent
for d in workspace workspace-luolin workspace-chengxx; do
  n=$(find $d -type f ! -name "*.wav" ! -path "*/agent-video/*" ! -path "*/src.old-*" | wc -l)
  psi=$(find $d/.psi -type f 2>/dev/null | wc -l)
  tools=$(find $d/tools -type f -name "*.py" 2>/dev/null | wc -l)
  env=$(test -f $d/.env && echo yes || echo NO)
  echo "$d files=$n psi=$psi tools_py=$tools dotenv=$env"
done'
# 判据: psi / tools_py / dotenv 三项与 0.2 ④ 的 B 侧数字完全相等, files 不得变小。
# 这一步必须在 3.3 之前做完 —— patch-envs.py 只有 .env 在位才不会 exit 2。

# 3.3 改网段(决定⑤: 172.19 在 A 上被 psi-cloud 占用)
ssh root@47.100.84.197 'python3 /root/prep/patch-envs.py'
# 判据: 输出 PATCHED_FILES=3, 每份 remaining=0。缺文件会 exit 2 且不写任何东西。

# 3.4 起 psi-agent(先起它 —— 172.21.0.1 这个网关地址要由它建出来)
ssh root@47.100.84.197 'cd /srv/haitun/psi-agent && docker compose config -q && docker compose up -d'
ssh root@47.100.84.197 'ip -4 addr | grep "172\.21\.0\.1" && echo GATEWAY_ADDR_UP'

# 3.5 再起 fusion-memory 的 MCP 与 embed-proxy(顺序反了 MCP 会 bind 失败)
ssh root@47.100.84.197 'cd /srv/haitun/fusion-memory && docker compose up -d && sleep 8 && ss -lntp | grep -E "8700|8701"'
# 判据: 8700 绑在 172.21.0.1, 8701 绑在 127.0.0.1
```

## 阶段 4:自测(T+20min)

> ### ⛔ 本阶段标题原为「DNS 还没切,外部无感」—— **那个前提是错的,9-09 18:20 已推翻**
>
> 飞书是**出站 wss 长连接**,不是入站 webhook:`channel feishu` 进程主动连
> `wss://msg-frontier.feishu.cn/ws/v2`(TCP 443,对端 `35.244.241.247`)。
> gateway 只监听 `127.0.0.1:8080`(仅回环),公网唯一 vhost `lark.oauth` 的白名单只放行
> `/oauth/callback` 与 `/oauth/code`。**飞书事件没有任何公网入口,DNS 切不切都管不住它。**
>
> **所以 A 机只要起了 `channel feishu`,它立刻上线抢真实用户的消息** —— 同一个
> `PSI_FEISHU_APP_ID`(`cli_aaead44976389bb6`)两条长连接,飞书投给其中之一,
> 两台机器争抢应答同一批人。这是生产事故,**DNS 层面完全无法隔离**。
>
> **A 机自测必须先隔离飞书通道,二选一:**
>
> | 方案 | 做法 | 代价 |
> |---|---|---|
> | ① 只起 gateway,不起 channel | `docker compose up -d gateway oauth-proxy`(不起 private-*),自测 4.1–4.5 全部可做 | 测不到飞书收发链路 |
> | ② 临时换测试应用 | 改 A 的 `PSI_FEISHU_APP_ID`/`SECRET` 为测试应用,自测后**必须改回** | 要有测试应用;改回时漏一处就是搬完不上线 |
>
> **推荐 ①** —— 4.1–4.5 五项判据没有一项需要飞书通道,而方案 ② 多一次"改回"的漏改风险。
>
> 切换顺序也随之改变,见阶段 5。

```bash
# 4.1 三层核验的第三层: 容器内实际装载的工具数
ssh root@47.100.84.197 'for c in psi-agent-gateway psi-agent-luolin psi-agent-chengxx; do
  echo "$c: $(docker logs "$c" 2>&1 | grep -oE "Loaded [0-9]+ tool\(s\) from [0-9]+ file\(s\)" | tail -1)"
done'
# 判据(注意日志原文带括号, 正则漏了会得**假零**):
#   gateway  = Loaded 221 tool(s) from 131 file(s)
#   luolin   = Loaded 192 tool(s) from 98 file(s)
#   chengxx  = Loaded 192 tool(s) from 98 file(s)
#
# ⚠️ gateway 是 **221/131**, 不是本手册早先写的 220/130。9-09 15:23 投了 PR 871 的
#    feishu_todo_compare_card_send(+1 工具 / +1 文件), 基线随之 +1。9-09 17:55 在 B 机
#    实测确认。照旧数字核会把正常状态误判成"少了一个工具"。
# 私有容器是 192 不是 277 —— 旧镜像的 277 把 import 进来的公开名也数了。
#
# ⚠️ 这一步要**趁早量**。A 机有 /etc/docker/daemon.json 限制日志为 10m×3
#    (B 机没有该文件, 所以日志无上限涨到了 101M)。装载行只在启动时打印一次,
#    等日志滚过就再也 grep 不到, 判据会变成不可测。

# 4.2 记忆服务连通(从容器内打网关地址)
ssh root@47.100.84.197 'docker exec psi-agent-gateway sh -c "curl -s -o /dev/null -w \"%{http_code}\n\" --max-time 10 http://172.21.0.1:8700/mcp"'
# 判据: **401**。401 是鉴权门在起作用 = 通了。连不上会是 000/7。
# 反过来说 401 也不能证明鉴权配对, 只能证明网络与服务活着。

# 4.3 本机走 Caddy 打回调入口(DNS 未切, 用 Host 头)
ssh root@47.100.84.197 'curl -s -o /dev/null -w "%{http_code}\n" -H "Host: lark.oauth.genuineknowledge.cn" http://127.0.0.1/oauth/callback'
# 判据: 400(与 B 现网一致)。502 = oauth-proxy 没起来; 200 静态页 = import 没生效。

# 4.4 netns 共享(oauth-proxy 用 service:gateway, 没有自己的 SandboxKey)
ssh root@47.100.84.197 'for c in psi-agent-gateway psi-agent-oauth-proxy; do
  pid=$(docker inspect "$c" --format "{{.State.Pid}}"); echo "$c $(readlink /proc/$pid/ns/net)"
done'
# 判据: 两个 inode 相同。**不要用 SandboxKey 比** —— oauth-proxy 那个是空值。

# 4.5 时区(三条定时任务是 0 16 * * 1,3,5, 错了会静默偏 8 小时, 无任何报错)
ssh root@47.100.84.197 'for c in psi-agent-gateway psi-agent-luolin psi-agent-chengxx; do
  echo "$c: $(docker exec "$c" date) TZ=$(docker exec "$c" printenv TZ)"
done'
# 判据: date 必须是 CST 且与宿主机一致; TZ=Asia/Shanghai。
#
# ⚠️ **不要拿 /etc/localtime 当判据** —— 容器里它指向 Etc/UTC, 时区完全靠 TZ
#    环境变量撑着。TZ 既不在镜像 Config.Env 里、也不在 env_file 里, 只写在
#    compose 的 environment: 段(4 处)。**compose 文件是时区的唯一载体。**
#    A 机那份已于 9-09 16:25 核过: 4 处 TZ=Asia/Shanghai 齐全, 四服务全覆盖。

# 4.6 回滚演练(**在 A 机做, 不在 B 机做** —— A 此刻零流量, 停多久都无人感知)
ssh root@47.100.84.197 'cd /srv/haitun/psi-agent
  before=$(docker inspect psi-agent-gateway --format "{{.Id}}")
  docker compose stop && docker compose start && sleep 20
  after=$(docker inspect psi-agent-gateway --format "{{.Id}}")
  test "$before" = "$after" && echo "ID_UNCHANGED (未重建, /app/src 改动不会丢)" || echo "ID_CHANGED !! stop/start 竟然重建了容器"
  for c in psi-agent-gateway psi-agent-oauth-proxy; do
    pid=$(docker inspect "$c" --format "{{.State.Pid}}"); echo "$c $(readlink /proc/$pid/ns/net)"
  done'
# 判据①: ID_UNCHANGED —— 证明 stop/start 语义安全(与 up -d 的重建相反)。
# 判据②: 若两个 netns inode **不同**, 说明 oauth-proxy 成了死 netns 孤儿,
#         即回滚脚本 R3 里那条 restart-stack.sh gateway 是**必需**的而非多余。
#         注意此时 oauth-proxy 仍会打印 "Running on http://0.0.0.0:8090" —— 日志不是判据。
#
# 为什么不在 B 机演练(三条真副作用):
#   ① 要测 start 必须先 stop, 那就是真停生产, gateway 冷启 60~90s;
#   ② oauth-proxy 必然挂死 netns, 等于故意重演 9-09 13:39-15:00 那 81 分钟的 502;
#   ③ 容器重建会把 cgroup memory.events 清零, 而 gateway 13:39 为何退出(ExitCode=0,
#      非 OOM)仍未查清, 清零一次就更查不动。
# 在 A 机验不了的只有一件: DNS 切回 B 需多久生效 —— 那只能靠 TTL 算, 无法演练。
```

## 阶段 5:真正的切换 = 长连接交接(T+30min)

> ### ⚠️ 本阶段原名「切 DNS」,那是把次要动作当成了主要动作
>
> 搬家的**真正切换点是飞书长连接的交接**,不是 DNS。DNS 只决定 OAuth 回调打到哪台机器,
> 而**消息收发全走出站 wss,与 DNS 无关**(见阶段 4 顶部)。
>
> 因此顺序必须是:**先停 B 的 channel,再起 A 的 channel**。两者之间就是停机窗,
> 这段时间飞书消息无人应答(飞书侧会重试,但不保证)。窗口长度 ≈ A 的 gateway 冷启时间,
> 实测 B 机为 60–90s。
>
> **不能反过来**(先起 A 再停 B):那段重叠期两台机器同时在线抢消息,
> 用户会收到重复或错乱的应答,而且**没有任何报错**。

```bash
# 5.1 停 B 的栈(这一刻起飞书无人应答, 计时开始)
ssh root@8.222.255.23 'cd /srv/haitun/psi-agent && docker compose stop && date +%T'
# 判据: 容器状态 Exited, 且 B 的 wss 长连接消失:
ssh root@8.222.255.23 'docker ps -a --filter name=psi-agent --format "{{.Names}} {{.State}}"'

# 5.2 起 A 的完整栈(含 channel feishu)
ssh root@47.100.84.197 'cd /srv/haitun/psi-agent && docker compose up -d && date +%T'

# 5.3 判据: A 的长连接建立(这是"搬家成功"的唯一硬判据)
ssh root@47.100.84.197 'docker logs psi-agent-gateway --since 5m 2>&1 | grep "connected to wss" | tail -2'
# 必须看到 connected to wss://msg-frontier.feishu.cn/ws/v2
# ⚠️ 只看容器 Up **不够** —— 长连接建不起来时容器照样 Up, 且日志里没有 ERROR。

# 5.4 停机时长 = 5.2 的时间 - 5.1 的时间, 记进 oplog
```

### 5.5 切 DNS(顺序无关,可在长连接交接之前或之后做)

```
阿里云 DNS 控制台, **只改这一条** A 记录:
  lark.oauth.genuineknowledge.cn     8.222.255.23 -> 47.100.84.197
```

**只改 IP,不改域名。** 它只影响 OAuth 授权回调(用户点「同意授权」后浏览器的跳转目标),
不影响消息收发。DNS 未生效期间的表现是:消息一切正常,但**新用户授权会失败**
(回调打到已停的 B)。这不是搬家失败,是 DNS 缓存还没过。

> ⚠️ **不要动 `account.genuineknowledge.cn`。** 本手册早先版本让两条一起改,那是错的。
> 实测:`account.` → Caddy → `127.0.0.1:8081` → **`psi-cloud` 容器(ToC 线)**,
> psi-agent 从来不在 8081 上;ToB 的入口只有 `lark.oauth.` → 8090。
> (计划文档第 196 行说「account 这一行要改指 ToB 栈」也是错的,第 482 行才是对的。)
>
> 而且 A 的 psi-cloud 与 B **已经分叉**,不是「落后 1 个 commit」:
> B 独有 `03ec9fe`(免费模型转发器,改了 14+ 文件),两机还各有一个 message 逐字相同
> 但 hash 不同的 commit(B `68c984d` / A `9ed700c`)。功能差异实测:
> B 有 17 条路由 + `psi-litellm` 容器在跑,A 只有 14 条、**没有 litellm 容器**,
> 缺 `/llm/v1/chat/completions`、`/llm/v1/models`、`/llm/v1/health/upstream`。
> 切过去 ToC 会**静默**丢掉整个 `/llm/*`(`/auth/*` 还在,登录照样过,只有免费模型 404)。
>
> ToC 迁移不在本次范围内。要搬得先把 psi-cloud 同步好,那是另一条线的事。

```bash
# 验证解析已生效(TTL 60s 的话约 1 分钟)
dig +short lark.oauth.genuineknowledge.cn @8.8.8.8   # 期望 47.100.84.197
curl -s -o /dev/null -w "%{http_code}\n" https://lark.oauth.genuineknowledge.cn/oauth/callback  # 期望 400
```

## 阶段 6:飞书实测(T+35min)

在飞书里对机器人说一句话,确认:回合能跑完、能调工具、记忆能写读。
**这一步只有真人能做**,脚本替代不了。

## 回滚(任一阶段失败都可用,约 5 分钟)

B 的容器、workspace、库**全程没删**,所以回滚是把水阀拧回去:

```bash
# R1 DNS 指回 8.222.255.23(TTL 60s, 约 1 分钟生效)
# R2 停 A 的 ToB 栈(避免两边同时抢飞书 WS)
ssh root@47.100.84.197 'cd /srv/haitun/psi-agent && docker compose stop'
# R3 起回 B
ssh root@8.222.255.23 'systemctl start fusion-memory-embed-proxy fusion-memory-mcp && cd /srv/haitun/psi-agent && docker compose start'
ssh root@8.222.255.23 'cd /srv/haitun/psi-agent && ./restart-stack.sh gateway'   # 若 oauth-proxy 挂在死 netns 上
# R4 判据
curl -s -o /dev/null -w "%{http_code}\n" https://lark.oauth.genuineknowledge.cn/oauth/callback   # 期望 400
```

**回滚的代价**:窗内 A 上写入的数据会丢(B 的库是停机那一刻的状态)。
所以窗内一旦开始接飞书流量,回滚就不再是零成本的。

## 窗后:9-11 观察日

| 看什么 | 怎么看 | 已知基线 |
|---|---|---|
| gateway 内存 | `memory.events` 的 `max` 与 `oom_kill` | B 上 anon 常驻 2711MiB;A 上限已提到 4500m |
| 502 是否复发 | 公网 `/oauth/callback` 应恒为 400。⚠️ **必须用 `--resolve`,不能用 `-H Host` 打裸 IP 的 https** —— SNI 仍是 IP 时 Caddy 没有匹配证书,TLS 层直接 alert,`%{http_code}` 得 `000` 且 0.18s 就返回,与真 502 无法区分(9-09 18:10 我因此误判过一次复发)。正确写法见下方附表 | B 上 dmesg 累计 81 次 OOM |
| luolin 的 MCP 崩溃 | `RuntimeError: cancel scope` 计数 | B 上 9-07→9-09 崩了 337 次,**luolin 独有**,属会话 2 的内核范围 |
| 记忆是否在写 | 库行数应持续增长 | 实测漂移约 0.1%/小时 |
| chengxx 的记忆报错 | **不要当成搬家故障** | `workspace-chengxx/.psi/memory_tokens.json` 在 B 上**本来就是空的 `{}`**(3 字节 / 0 条目;luolin 1 条、主 workspace 42 条)。搬家前就没配过,搬完照旧空。路径是 `.psi/memory_tokens.json`,**不在** `.psi/appdata/` 下 |

## 附:窗前已实测通过的命令(不必窗内再验)

| 命令/判据 | 实测结果 |
|---|---|
| `caddy validate --config … --adapter caddyfile` | A 机当前配置返回 `Valid configuration`,exit 0 |
| 容器内 `pg_dump` / `pg_restore` | 都在 `/usr/bin/`;`-Fc` dump 产出 44,990,737 字节 |
| `fusion` 角色权限 | `rolsuper=t rolcreatedb=t` → drop/create database 可行 |
| compose 服务名 | 两机**一致**:`gateway` / `oauth-proxy` / `private-luolin` / `private-chengxx` |
| A 机全量恢复 | 33 表 / 107 索引 / `vector 0.8.6`,与 B 一致;逐表行数 0 处倒挂 |
| `172.21.0.1` 绑定与可达 | 一次性网络实测 `BIND_OK` + `REACH_OK 200` |

### 从公网探 ToB 入口活性的唯一正确写法

```bash
# ✅ 对: --resolve 同时改 DNS 解析与 SNI
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" \
  --resolve lark.oauth.genuineknowledge.cn:443:<机器IP> \
  https://lark.oauth.genuineknowledge.cn/oauth/callback

# ❌ 错: SNI 还是裸 IP, TLS 层就被 alert 掉, 恒得 000
curl -s -o /dev/null -w "%{http_code}\n" -k \
  -H "Host: lark.oauth.genuineknowledge.cn" https://<机器IP>/oauth/callback
```

拿到非预期码时分层定位,不要直接判"入口挂了":
1. 从该机本机直打后端 `curl http://127.0.0.1:8090/` —— 得 **404 是正常的**(8090 对 `/` 无路由);
2. 比对同机一个已知健康的 vhost(`account.` 对 `lark.oauth.`),两者都异常才是入口层问题;
3. 比 `readlink /proc/<pid>/ns/net`,gateway 与 oauth-proxy 必须相同(不同 = netns 挂死)。

⚠️ 手册 0.3 与 4.x 里那几条 `-H "Host: …" http://127.0.0.1/` **是对的,不要改** ——
它们走的是 http(无 SNI)且从本机打,不受这个坑影响。只有**公网 https** 才必须用 `--resolve`。

`restart-stack.sh` 不带参数时重启的是 `gateway private-luolin`(**compose 服务名**,
不是容器名),`HEALTH_BASE` 可覆盖,默认 `http://127.0.0.1:8090`。

一个容易误读的判据:`docker compose config --services` 在 A 上**能成功**,
但 `docker compose config -q` 在 `.env` 落地前**仍然失败**(`env file …/workspace/.env not found`)。
前者只列服务名不解析 `env_file`,所以**不能用它判断 .env 是否到位**。
~~截至 13:50 workspace 仍在传输中,三份 `.env` 尚未落地。~~
**9-09 18:15 更新:三份 `.env` 已落地并改完网段,`docker compose config -q` 现为 EXIT=0。**

## 本手册里**未验证**的部分(不要当成事实)

- 阶段 2 的增量重导流程**没有演练过**(全量恢复演练过并逐表比对通过)。
- ~~阶段 3.1 的 `docker load` 没跑过~~ → **9-09 16:30 已实测**:在 A 机用真镜像
  造包跑完整链路 `docker save | zstd -3` → `docker rmi` → `zstd -dc | docker load`,
  得 `Loaded image:` 且 **`PIPE_EXIT=0 0`**,A 机 zstd 为 v1.5.7。探针已清理。
  仍**未验**的只剩:真镜像包的 digest 是否与 B 侧三个 ID 相符(得等传完,判据见 0.2 ③)。
- 阶段 3.4/3.5 的两栈启动顺序:`172.21.0.1` 的绑定与可达性**已用一次性网络实测通过**,
  但**真容器**起来是否如此未验。
- 阶段 4.2 的 401 判据:B 上实测过"有记忆的 gateway 也得 401",
  所以 401 只证明服务活着,**不证明鉴权配对**。
- 窗内耗时 30~45 分钟是**估算**,其中只有增量传输(约 5.5 分钟)有实测速率支撑。

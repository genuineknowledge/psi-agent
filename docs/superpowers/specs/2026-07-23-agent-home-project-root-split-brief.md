# Agent 路径 / AppData / 工作区 — 汇报摘要

**日期**: 2026-07-23（2026-07-24 补：AppData 用 platformdirs）  
**状态**: 当面拍板稿（覆盖旧草案）  
**细则**: 同目录 `2026-07-23-agent-home-project-root-split-design.md`

---

## 一句话

`examples/` = **Agent 路径**（多套配置，以后改名）；**AppData**（根目录用 **`platformdirs.user_data_dir`**，禁止写死 `%AppData%`）统一存今日的 **history + state**；**工作区**由用户选取；history 用 **meta.json** 索引每条记录的名字及所属 workspace、agent。

---

## 三块分别干什么

| | Agent 路径 | AppData | 工作区 |
|--|------------|---------|--------|
| 是什么 | 静态能力配置（tools/skills/…） | 本机全局数据 | 用户打开的工程 |
| 今日对应 | `examples/haitun-workspace` 等 | history 文件 + Gateway `state/` | 被误叫成 workspace 的用户目录 |
| 用户怎么动 | 选/换 Agent（可后做） | 应用自动读写 | Cursor 式选文件夹 |
| 路径怎么定 | 仓内 / 安装包内 examples | **`platformdirs`**（可 env/CLI 覆盖） | 用户选取 |

AppData 内两层：

- **state/** — 跨 session（`latest.json`：ais / sessions / titles + 时间戳快照）  
- **history/** — 各会话记录 + **meta.json**（新历史出现时更新：name、workspace、agent）

### AppData 根（评审已定）

- 用已有依赖 `platformdirs.user_data_dir(appname=..., appauthor=False)` 解析跨平台用户应用数据目录。  
- **不要**写死 `%AppData%\Haitun` 或各 OS 手写路径。  
- 详见 design **§1.1**。

---

## 目录树（示意）

```text
<repo>/examples/                        （Agent 路径，后续改名）
└── haitun-workspace/                   （一套 Agent）
    └── tools/ skills/ schedules/ …     （静态；不放 history/state）

{platformdirs.user_data_dir(...)}/      （AppData ← 勿写死 %AppData%）
├── state/
│   ├── latest.json
│   └── YYYYMMDD-HHMMSS.json
└── history/
    ├── meta.json                       （索引）
    └── <session_id>.jsonl

D:/用户自选文件夹/                        （工作区）
└── （用户文件；可空）
```

---

## 改什么（尽量少动积木）

1. 能力仍从 Agent 路径加载（先挂现有 examples）。  
2. history、state 改写到 AppData（根 = platformdirs）；history 维护 meta。  
3. 文件读写相对用户工作区。  

**精确到文件的改动清单**见 design **§9**（含开工分工 §9.8）。

不做：重做 tools/skills 内部结构；不手写平台 AppData 路径。

---

## 验收

换工作区不丢 Agent；历史能按 workspace/agent 在 meta 里区分；用户目录无 histories/state；重启能从 AppData/state 恢复；AppData 根在 Win/macOS/Linux 均由 platformdirs 给出。

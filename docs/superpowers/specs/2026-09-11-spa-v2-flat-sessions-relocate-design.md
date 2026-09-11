# spa-v2：全量 Session 侧栏 + 迁移（复制到新 workspace/agent 后删旧）

| 字段 | 内容 |
|------|------|
| **方案类型** | 产品 / Gateway + spa-v2 |
| **状态** | 已落地（取代「按打开工作区过滤侧栏」与「AppData 指纹滤列表」方向） |
| **`proposal_id`** | `spa-v2-flat-sessions-relocate` |
| **版本** | 0.2 |
| **关联** | 取代草稿 `2026-09-11-spa-v2-appdata-scoped-sidebar-design.md` 的产品结论 |

---

## 1. 结论

1. **侧栏渲染当前 Gateway 上的全部用户 Session**（调度 Session 仍由 `list_all` 隐藏；飞书 `feishu-*` 在 C 端列表剔除）。  
2. **设置里的「工作区 / Agent」只影响新建任务**默认写入的 `workspace` / `agent`，**不再**作为侧栏滤镜，切换时**不**因换目录整树 remount 清空任务。  
3. **不允许**运行中原地改绑 workspace/agent。等效改绑 = **`POST /sessions/{id}/relocate`**：新建 Session（新 workspace + agent）→ 拷贝 history / title / summary / todos(+segments) → 删除旧 Session。  
4. 多 Gateway / 错 AppData 仍属运维（分 `--appdata`）；本方案不解决「连错进程」，只修正「用打开目录当滤镜」的产品问题。

---

## 2. 交互

| 动作 | 行为 |
|------|------|
| 打开应用 | `GET /sessions` → 全量（去飞书）→ 侧栏 |
| 设置 → 切换工作室工作区 | 只更新默认路径 + LS；列表与内存任务保留 |
| 设置 → 切换 Agent 包 | 只更新默认 agent；已有任务不变 |
| 新建任务 | `POST /sessions` 带**当前默认** workspace/agent |
| 任务行「迁移」 | 选目录 → 选 Agent 包 → relocate → 侧栏用新 id 替换旧 id（置顶 / 新交付物键跟着搬） |
| 删除 | 仍 `DELETE /sessions/{id}` |

---

## 3. API

`POST /sessions/{session_id}/relocate`

```json
{ "workspace": "<必填>", "agent": "<可选；空则沿用旧 agent>" }
```

成功 `201`：新 `SessionInfo`（body 可附 `relocated_from`）。失败：404 无旧会话；400 调度 Session / 缺 workspace / 守卫拒绝等。

---

## 4. 刻意为之

- **拷贝后删旧**，不热改进程内根路径——工具 cwd / agent 包加载仍在创建时固定。  
- **交付物磁盘文件不搬家**——历史里的 `[SEND:]` 绝对路径仍指向旧目录；迁移后预览依赖原路径仍可读。若需搬文件属另案。  
- C 端列表 **不展示** `feishu-*`，避免两面全挂时串进飞书会话。

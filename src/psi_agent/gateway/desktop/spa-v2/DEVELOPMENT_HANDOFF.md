# HaiTun Agent 前端开发交接

## 1. 交付边界

此仓库提供可运行的高保真前端基准。以下能力已经真实实现：

- PC/手机响应式布局、侧栏展开与收起。
- 卡片整体切换、对话聚焦工作区、任务历史和交付物页面内展示。
- 键盘、鼠标拖拽、移动端手势、草稿隔离和 Esc 关闭层级。
- 任务状态与交付物状态的独立前端表达。
- 宝箱、金币、减少动态效果和支持设备上的轻震反馈。

以下能力为演示 Mock：

- `INITIAL_TASKS`、`INITIAL_MESSAGES`、`INITIAL_INBOX`、`INITIAL_TEMPLATES` 均为静态样例。
- 所有业务状态只保存在 React 内存中，刷新即重置。
- Agent 回复由 `setTimeout` 和固定文本模拟；没有真实模型调用、后台任务或实时推送。
- 任务进度、持续监测、同步状态和“Agent 在线”均为展示数据。
- 文件选择只读取文件名；预览由前端页面模拟；下载只显示操作反馈。
- 新建任务、模板、通知已读、设置和成果保存均未持久化。
- 全局搜索是内存字符串匹配，不是搜索服务。
- D1、R2 和身份系统均未接入。

## 2. 代码依赖方向

```text
model / demo-fixtures / client-feedback
                  ↓
              primitives
          ↙         ↓          ↘
 task-cards   task-focus   overlays / secondary-views
          ↘         ↓          ↙
             workspace 编排层
                      ↓
                  app/page.tsx
```

`HaiTunAgentWorkspace.tsx` 故意保留卡片切换、对话聚焦、手势、键盘、导航和搜索状态链。它们共享计时器、焦点和离场几何，不应在首次业务接入时再次拆成多个 Context 或 Hook。视觉组件与数据接入可以独立替换。

## 3. 生产替换表

| 当前实现 | 生产接入 | 关键要求 |
| --- | --- | --- |
| `INITIAL_*` | `GET /workspace` 或聚合查询 | 一次返回任务、消息摘要、通知、模板和用户偏好 |
| `createTask()` | `POST /tasks` | 使用服务端 ID；支持幂等键；返回初始执行计划 |
| `sendMessage()` | `POST /tasks/:id/messages` + SSE | 保留每任务草稿隔离；支持流式回复、取消和重试 |
| `Task.progress/status` | SSE、WebSocket 或轮询 | 事件需带版本号，避免乱序覆盖新状态 |
| `TaskFocusDetails` | `GET /tasks/:id/events` | 返回结构化事件和 ISO `occurredAt`，不要传“8 分钟前”作为源数据 |
| `deliverables: string[]` | `Delivery[]` | 文件必须有 ID、版本、状态、预览和下载权限 |
| 隐藏文件选择器 | 预签名上传或对象存储上传 | 校验类型、大小、哈希、失败重试和断点续传 |
| `onDownload` Toast | 签名下载 URL 或文件流 | URL 可过期并受任务权限约束 |
| `saveArtifact()` | 服务端成果保存命令 | 保存交付物不能自动把任务改为完成 |
| `reviseArtifact()` | 创建新执行轮次 | 保留旧版本和修改原因，返回新轮次 ID |
| 模板/通知/偏好 | 对应持久化 API | 支持乐观更新失败回滚 |
| 全局内存搜索 | 服务端统一搜索 | 返回对象类型、命中字段和权限过滤后的结果 |

## 4. 建议数据契约

```ts
type TaskEvent = {
  id: string;
  taskId: string;
  kind: "status" | "attention" | "delivery" | "update" | "conversation";
  title: string;
  detail?: string;
  occurredAt: string; // ISO 8601
  version: number;
};

type Delivery = {
  id: string;
  taskId: string;
  name: string;
  mimeType: string;
  size: number;
  version: number;
  state: "generating" | "ready" | "saved";
  previewUrl?: string;
  downloadUrl?: string;
  createdAt: string;
};
```

任务状态和交付物状态必须保持正交：进行中任务可以已有阶段交付物，已完成任务也可以没有文件。前端不得根据其中一个字段自动推导另一个字段。

## 5. Adapter 建议

在接真实后端前增加 `services/workspace-api.ts`，由页面只依赖接口：

```ts
export interface WorkspaceApi {
  getWorkspace(): Promise<WorkspaceSnapshot>;
  createTask(input: CreateTaskInput, idempotencyKey: string): Promise<Task>;
  sendMessage(taskId: string, input: SendMessageInput): Promise<MessageAck>;
  listTaskEvents(taskId: string, cursor?: string): Promise<Page<TaskEvent>>;
  markInboxRead(ids: string[]): Promise<void>;
  search(query: string): Promise<SearchResult[]>;
  saveDelivery(deliveryId: string): Promise<Delivery>;
  reviseDelivery(deliveryId: string, instruction: string): Promise<TaskRun>;
}
```

保留一个 Mock Adapter 用于产品演示，新增真实 API Adapter 用于联调；组件不直接拼接 URL，也不直接处理鉴权 Token。

## 6. 错误与空态

生产接入至少覆盖：

- 工作区首次加载、增量加载和空工作区。
- 消息发送超时、流式中断、取消、重试和重复提交。
- 任务事件乱序、断线重连和过期快照。
- 上传失败、文件过大、类型不支持、预览失败和下载链接过期。
- 401、403、404、409、429 和 5xx；403 必须区分任务权限与文件权限。
- 离线状态和恢复后的数据对账。

## 7. 构建与托管注意事项

- Windows 脚本使用 `cross-env`，不要改回仅适用于 Unix 的环境变量写法。
- Logo 直接访问 `/haitun-dolphin.png`；不要重新引入 `next/image` 的运行时优化链路。
- 不使用 `next/font`，避免构建产物包含开发机绝对字体路径。
- `.openai/hosting.json` 当前仅记录 Sites 项目，D1/R2 为 `null`。
- `dist/`、`.vinext/`、`.wrangler/` 和 `node_modules/` 均为生成内容，不进入源码交付。

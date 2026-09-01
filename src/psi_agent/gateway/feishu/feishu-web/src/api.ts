/**
 * 数据层 —— 只封装**后端确实存在**的路由。
 *
 * 判据是 ``gateway`` 下的 ``add_get/add_post/add_delete`` 声明, 不是 PR 里写了什么:
 * PR 版打的那条免登路由曾全库零实现 (唯一命中是文档里那句「不做」)。
 *
 * 飞书免登已落地(任务 5fef7): ``login`` / ``getMe`` / ``logout`` 打的是
 * ``gateway/feishu/_routes.py`` 里的 ``/feishu/auth/*`` —— **不是裸 ``/auth/*``**: desktop
 * 那条产品线已占了 ``/auth/me`` 与 ``/auth/logout``, 同进程装配下先注册者胜出, 打裸路由
 * 会打到 desktop 的 handler 上(有效 cookie 也回 401, 登出还不生效)。会话一族走
 * ``/feishu/sessions`` 而非裸
 * ``/sessions``: 后者不按身份过滤, 在浏览器侧 filter 只是显示过滤, 谁都能直接打裸路由
 * 拿全量。
 */

export interface SessionInfo {
  id: string;
  backend_type?: string;
  backend_id?: string;
  workspace?: string;
  agent?: string;
  ai_id?: string;
  /** 是否 IM 里那条会话(``feishu-<open_id>``) —— 列表上打「来自飞书对话」角标。 */
  from_im?: boolean;
}

export interface HistoryMessage {
  role: string;
  text: string;
  reasoning?: string;
  tools?: Array<{ name: string; arguments?: string }>;
  sends?: string[];
  files?: Array<{ name: string; path?: string }>;
}

export interface SessionTodo {
  id: string;
  content: string;
  status: string;
}

export interface TodoSummary {
  total: number;
  pending: number;
  in_progress: number;
  completed: number;
  cancelled: number;
}

export interface SessionTodosResponse {
  todos: SessionTodo[];
  summary: TodoSummary;
}

export interface TodoSegmentSummary {
  id: string;
  label: string;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  source: string;
  summary: TodoSummary;
}

export interface TodoSegmentDetail extends TodoSegmentSummary {
  todos: SessionTodo[];
}

export interface WorkspaceFile {
  name: string;
  data: string;
  path: string;
}

interface ApiError {
  error?: string;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  const data = (await resp.json().catch(() => ({}))) as T & ApiError;
  if (!resp.ok) throw new Error((data as ApiError).error || `HTTP ${resp.status}`);
  return data;
}

function jsonPost(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/** 后端列表端点有 ``[...]`` 与 ``{value: [...]}`` 两种形状, 统一成数组。 */
function asList<T>(data: T[] | { value?: T[] }): T[] {
  return Array.isArray(data) ? data : data.value || [];
}

// ---- 免登 / 身份 -------------------------------------------------------

export interface Me {
  open_id: string;
  name: string;
  /**
   * 这个身份是后端 ``PSI_FEISHU_DEV_OPEN_ID`` 旁路发的。
   *
   * **只由后端给**, 前端不构造也不传。生产响应里没有这个字段(后端只在为真时带上),
   * 所以缺省即 false。
   *
   * 页面**不再用它渲染任何东西** —— 旁路提示已挪到 gateway 启动日志(见 App.tsx 的注释)。
   * 保留在类型里是因为它确实在响应形状里, 声明成可选字段与后端一致; 想加回页面提示前先
   * 读那段注释。
   */
  via_dev_bypass?: boolean;
}

/** appID 从后端取, 不写死在前端 —— 换应用/换租户只改部署参数。 */
export async function getFeishuAppId(): Promise<string> {
  const data = await requestJson<{ app_id?: string }>("/feishu/app-id");
  return data.app_id || "";
}

export async function login(code: string): Promise<Me> {
  return requestJson<Me>("/feishu/auth/login", jsonPost({ code }));
}

/**
 * 无 code 登录 —— 只有后端设了 ``PSI_FEISHU_DEV_OPEN_ID`` 才会成功, 否则 400。
 *
 * 身份由**后端**的环境变量决定, 前端不传也不能传 open_id。这与 PR 755 那个前端写死
 * 真实 open_id 的做法是两件事: 这里前端没有任何身份信息可伪造。
 */
export async function loginDevBypass(): Promise<Me> {
  return requestJson<Me>("/feishu/auth/login", jsonPost({}));
}

export async function getMe(): Promise<Me> {
  return requestJson<Me>("/feishu/auth/me");
}

export async function logout(): Promise<void> {
  await requestJson<unknown>("/feishu/auth/logout", jsonPost({}));
}

// ---- GET /feishu/defaults ----------------------------------------------

/**
 * 建会话该挂哪个 AI —— 后端给的唯一答案(Gateway 的 ``--feishu-ai-id``), 空串表示部署没配。
 *
 * **前端不打 ``GET /ais``, 也不该有 AI 列表的概念。** 原先的写法是 `listAis()` 取
 * `ais[0].id`: 生产上恰好只有一条 AI 所以看着没错, 但 appdata 里存了多条时数组顺序无保证,
 * 网页应用会静默用上一个与机器人不同的模型。让后端只给一个 id, 「两侧模型不一致」就在结构上
 * 不可能发生, 而不是靠纪律。
 *
 * 也**不做兜底**: 拿不到就报错。悄悄换个模型比直接报错难查得多。
 *
 * 飞书这条线是 ToB —— AI 由部署者定死, B 端用户不该看见也不该改。ToC 的 `spa-v2` 那边用户
 * 自带 key, 有配置页, 是另一件事, 别把那套搬过来。
 */
export async function getFeishuDefaultAiId(): Promise<string> {
  const data = await requestJson<{ ai_id?: string }>("/feishu/defaults");
  return data.ai_id || "";
}

// ---- /feishu/sessions ---------------------------------------------------

export async function listSessions(): Promise<SessionInfo[]> {
  // 过滤路由: 只回当前身份的私聊会话。裸 ``/sessions`` 不按身份过滤, 前端不再用它。
  return asList(await requestJson<SessionInfo[] | { value?: SessionInfo[] }>("/feishu/sessions"));
}

export async function createSession(backendId: string): Promise<SessionInfo> {
  // **不传 id** → 后端发新 uuid → 新 jsonl。workspace 由后端按 open_id 派生, 前端不传。
  return requestJson<SessionInfo>("/feishu/sessions", jsonPost({ backend_id: backendId }));
}

export async function deleteSession(id: string): Promise<void> {
  await requestJson<unknown>(`/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function getSessionHistory(id: string): Promise<HistoryMessage[]> {
  const data = await requestJson<HistoryMessage[] | { value?: HistoryMessage[] }>(
    `/feishu/sessions/${encodeURIComponent(id)}/history`,
  );
  return asList(data);
}

// ---- 标题 / 摘要 -------------------------------------------------------

export async function listTitles(): Promise<Record<string, string>> {
  return requestJson<Record<string, string>>("/feishu/titles");
}

export async function generateTitle(
  id: string,
  userText: string,
  assistantText: string,
): Promise<{ id: string; title: string }> {
  return requestJson<{ id: string; title: string }>(
    "/titles/generate",
    jsonPost({ id, user_text: userText, assistant_text: assistantText }),
  );
}

export async function listSummaries(): Promise<Record<string, string>> {
  return requestJson<Record<string, string>>("/feishu/summaries");
}

export async function setTitle(id: string, title: string): Promise<void> {
  await requestJson<unknown>("/titles", jsonPost({ id, title }));
}

// ---- todo (任务进度的数据源) -------------------------------------------

export async function getSessionTodos(sessionId: string): Promise<SessionTodosResponse> {
  return requestJson<SessionTodosResponse>(`/sessions/${encodeURIComponent(sessionId)}/todos`);
}

export async function listTodoSegments(sessionId: string): Promise<TodoSegmentSummary[]> {
  const data = await requestJson<TodoSegmentSummary[] | { value?: TodoSegmentSummary[] }>(
    `/sessions/${encodeURIComponent(sessionId)}/todo-segments`,
  );
  return asList(data);
}

export async function getTodoSegment(
  sessionId: string,
  segmentId: string,
): Promise<TodoSegmentDetail> {
  return requestJson<TodoSegmentDetail>(
    `/sessions/${encodeURIComponent(sessionId)}/todo-segments/${encodeURIComponent(segmentId)}`,
  );
}

// ---- workspace ---------------------------------------------------------

export async function readWorkspaceFile(path: string): Promise<WorkspaceFile> {
  const params = new URLSearchParams({ path });
  return requestJson<WorkspaceFile>(`/workspace/file?${params.toString()}`);
}

export async function revealWorkspacePath(path: string): Promise<{ path: string }> {
  return requestJson<{ path: string }>("/workspace/reveal", jsonPost({ path }));
}

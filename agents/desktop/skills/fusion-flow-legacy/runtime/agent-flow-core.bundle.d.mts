export interface AgentConfig<K extends string = string> {
	/** Agent 名字。决定 trace 文件名和日志可读性。必填。 */
	name: string;
	/** 模型 id / alias（claude 接受 "opus"/"sonnet" 或全名）。不填用 FLOW_MODEL 环境变量，
	 *  再不填则用所选 CLI 引擎的默认模型。 */
	model?: string;
	/** System prompt（角色描述）。v0.1 字段名。flow.agent 也支持 `prompt` 作别名。 */
	system?: string;
	/** v0.2 别名：与 OpenProse 上游 `prompt:` 关键字对齐，等价于 system。 */
	prompt?: string;
	/** 单次调用最大 output token 数。默认 8192。 */
	maxTokens?: number;
	/** 温度，默认 1.0。 */
	temperature?: number;
	/**
	 * A11: 开启扩展推理(thinking)。HTTP 时代透传到 Anthropic body 的 thinking 字段。
	 * v0.7 起调 LLM 走 CLI，claude CLI 无 thinking flag，此字段暂为 no-op。
	 */
	thinking?: {
		budgetTokens: number;
	};
	/**
	 * v0.7：指定该 agent 走哪个 CLI 引擎（"claude" / "openclaw" / "hermes" / "psi"）。
	 * 不填则用 FLOW_ENGINE 环境变量，默认 "claude"。
	 */
	engine?: string;
	/**
	 * v0.7：agentic 能力开关。放开的 tool 列表（如 ["Read","Edit","Grep"]）。
	 * 不填 / 空数组 = 纯文本处理器（CLI 加 --tools "" 禁用全部 tool）。
	 * 给了就让底层 CLI 进入 agentic loop（claude --allowedTools）。
	 */
	tools?: string[];
	/** v0.7：agentic 时的多轮上限（claude --max-turns）。 */
	maxTurns?: number;
	/**
	 * B2: 锁定 flow.session 第三参 context 的合法 key。
	 * 传 `contextSchema: ["summary", "plan"]` 后，flow.session(handle, prompt, ctx)
	 * 的 ctx 必须正好是 `{ summary: string; plan: string }`——拼错 key（如 summray）
	 * 或漏传字段在 tsc 阶段就报错，不再静默漏字段跑空。
	 * 同时框架在运行时也会按这个 schema 兜底校验一次（防 author 绕过 tsc 的场景）。
	 */
	contextSchema?: readonly K[];
}
export interface AgentInvocation {
	/** User prompt。必填。 */
	prompt: string;
	/** 上下文变量。会以 `## context.<key>\n<value>` 形式注入到 user prompt 前面。 */
	context?: Record<string, string>;
}
/** Agent 是个可调用的对象，调一次返回字符串。v0.1 形态。 */
interface Agent {
	(invocation: AgentInvocation): Promise<string>;
	readonly __agentName: string;
	readonly __config: AgentConfig;
}
/** flow.agent 返回的句柄。不可直接调用，必须经 flow.session 触发。
 *  B2: 泛型 K 记住 contextSchema 声明的合法 key，传给 flow.session 做类型约束。 */
export interface AgentHandle<K extends string = string> {
	readonly __kind: "agent";
	readonly name: string;
	readonly config: AgentConfig<K>;
}
/**
 * B2: 根据 agent 是否声明 contextSchema，决定 flow.session 第三参 context 的类型。
 * - 未声明（K = string）→ 宽松的 Record<string,string>，向后兼容老代码。
 * - 已声明（K 是字面量联合）→ 精确的 Record<K,string>，多/少/拼错 key 都编译报错。
 */
export type ContextFor<K extends string> = string extends K ? Record<string, string> : Record<K, string>;
export interface ServiceParam {
	name: string;
	description?: string;
	required?: boolean;
}
export interface ServiceSignature {
	params?: ServiceParam[];
}
export interface ServiceDef {
	/** 服务名。决定图节点和 binding 默认名。 */
	name: string;
	description?: string;
	/** 形参列表。用于运行时校验 flow.call 传入的具名参数。 */
	signature?: ServiceSignature;
	/** 服务体。args 是经过签名校验的具名参数对象。返回字符串作为服务输出。 */
	body: (args: Record<string, string>) => Promise<string>;
}
export interface ServiceHandle {
	readonly __kind: "service";
	readonly name: string;
	readonly signature?: ServiceSignature;
}
export interface SessionOptions {
	/** 覆盖默认 binding 名（默认是 agent 名 + 重复计数）。 */
	bindingName?: string;
}
export interface CallOptions {
	/** 覆盖默认 binding 名（默认是 service 名 + 重复计数）。 */
	bindingName?: string;
}
export interface ParallelOptions {
	/**
	 * 等待策略。
	 * - "all"：默认。所有 task 完成才返回。
	 * - "first"：第一个 task 返回（其它继续跑但被忽略）。
	 * - {any: N}：N 个 task 完成就返回前 N 个结果。
	 */
	join?: ParallelJoin;
}
export interface EvaluateBooleanOptions {
	kind: "boolean";
	/** 评估问题（自然语言） */
	question: string;
	/** 评估上下文：给 LLM 看的字符串 map */
	context?: Record<string, string>;
	/** 用什么 agent 去评估。不填用内建 evaluator agent。 */
	evaluator?: AgentHandle;
	/** 自定义 binding 名 */
	bindingName?: string;
}
export interface EvaluateNumberOptions {
	kind: "number";
	question: string;
	context?: Record<string, string>;
	/** 数值范围（含端点）。LLM 越界时强制夹断。 */
	min?: number;
	max?: number;
	/** 是否要求整数 */
	integer?: boolean;
	evaluator?: AgentHandle;
	bindingName?: string;
}
export interface EvaluateChoiceOptions {
	kind: "choice";
	question: string;
	context?: Record<string, string>;
	/** 必填：候选项列表 */
	options: string[];
	evaluator?: AgentHandle;
	bindingName?: string;
}
export type EvaluateOptions = EvaluateBooleanOptions | EvaluateNumberOptions | EvaluateChoiceOptions;
export interface LoopOptions {
	/** 安全上限，默认 8。达到上限时退出。 */
	maxIterations?: number;
}
export interface ChoiceOptions<T> {
	question: string;
	context?: Record<string, string>;
	/** 候选项 = label + 对应执行函数 */
	branches: Array<{
		label: string;
		fn: () => Promise<T>;
	}>;
	/** 默认走哪个 label。LLM 失败/无法决定时回退到这个。不填则报错。 */
	defaultLabel?: string;
	evaluator?: AgentHandle;
	bindingName?: string;
}
export interface RetryOptions {
	/** 最大尝试次数（含第一次）。默认 3。 */
	maxAttempts?: number;
	/** 第一次失败到第二次重试的等待时间（毫秒）。默认 200。 */
	initialDelayMs?: number;
	/** 退避倍率。默认 2（指数退避）。 */
	backoff?: number;
	/** 最长单次等待时间（毫秒）。默认 8000。 */
	maxDelayMs?: number;
	/** 自定义判断错误是否值得重试。返回 false 则立刻抛出，不再重试。 */
	shouldRetry?: (err: Error, attempt: number) => boolean;
}
export type StaticRule = {
	kind: "regex";
	pattern: RegExp;
	on: string;
} | {
	kind: "contains";
	needle: string;
	on: string;
} | {
	kind: "equals";
	expected: string;
	on: string;
} | {
	kind: "range";
	value: number;
	min?: number;
	max?: number;
} | {
	kind: "predicate";
	fn: () => boolean | Promise<boolean>;
};
export interface ExecOptions {
	/** 节点标签 + binding 名前缀。 */
	name: string;
	/** 可执行文件路径或命令名（如 "uv" / "node" / "python"）。 */
	command: string;
	/** 命令参数列表。 */
	args?: string[];
	/** 透给子进程 stdin（可选）。 */
	stdin?: string;
	/** 子进程超时(毫秒),默认 300_000(5 分钟)。超时 SIGKILL。 */
	timeout?: number;
	/** 子进程 cwd。 */
	cwd?: string;
	/**
	 * 子进程额外环境变量。**默认不继承父进程 env**(干净环境),只透传 PATH。
	 * 传了的话:`{...PATH-only baseline, ...env}`。
	 */
	env?: Record<string, string>;
	/** 自定义 binding 名(默认 name + 重复计数)。 */
	bindingName?: string;
	/**
	 * #20: stdout 字节上限(默认 4 MiB)。超限时子进程被 SIGKILL,保留头部 N 字节,
	 * 写盘 binding 末尾追加截断标记。防 `docker logs` / `find /` / `grep -r` 这类
	 * 大输出把 Node 进程吃爆内存或污染 runs/。设 0 / Infinity 关闭上限。
	 */
	maxStdoutBytes?: number;
}
export interface ExecResult {
	/** stdout 去尾换行后的内容（主要消费这个）。 */
	stdout: string;
	/** 完整 stdout（给 retry / 调试）。 */
	raw: string;
	exitCode: number;
	durationMs: number;
	/** #20: 是否因 maxStdoutBytes 被截断。 */
	truncated?: boolean;
}
export interface SpawnTokenUsage {
	input: number;
	output: number;
	cacheRead?: number;
	cacheWrite?: number;
	total?: number;
}
export interface SpawnMeta {
	tokenUsage?: SpawnTokenUsage;
	sessionId?: string;
	costUSD?: number;
	/** 跟 wallclock 区分:claude 给 duration_api_ms,排除 startup 开销。 */
	durationApiMs?: number;
	/** openclaw 给:spawn 内部真正走的 provider。 */
	provider?: string;
	model?: string;
	contextWindow?: number;
	/** openclaw --local 每次调用建唯一 session 文件(无状态单次调用)。调用方用完即删，
	 *  避免 ~/.openclaw/.../sessions/ 下 flow-* 文件无限堆积。绝对路径，可能为空。 */
	sessionFile?: string;
}
export interface BlockDef<R = unknown> {
	/** block 名字。决定执行图标签 + 复用调用入口。 */
	name: string;
	description?: string;
	/** block 的实现函数。args 是 runBlock 传进来的具名参数。 */
	body: (args: Record<string, string>) => Promise<R>;
}
export interface EvaluateStaticOptions {
	/** 规则的描述（写到执行图里方便排查） */
	question: string;
	/** 静态规则。任意命中即返回 true。 */
	rule: StaticRule;
	bindingName?: string;
}
export interface FlowAPI {
	/** 创建一个 agent 句柄。`prompt` 字段对齐 OpenProse；`system` 也接受。
	 *  B2: const K 捕获 contextSchema 的字面量数组为联合类型，传给 session 约束 context。 */
	agent<const K extends string = string>(config: AgentConfig<K>): AgentHandle<K>;
	/** 跑一次 agent 会话。返回字符串结果，自动落 binding。
	 *  B2: 当 agent 声明了 contextSchema，context 被约束为 `Record<K, string>`——
	 *  拼错 key / 漏字段在 tsc 阶段报错；未声明时退回宽松的 Record<string,string>。 */
	session<K extends string = string>(agent: AgentHandle<K>, prompt: string, context?: ContextFor<K>, options?: SessionOptions): Promise<string>;
	/** 注册一个 service。返回句柄供 flow.call 调用。 */
	service(def: ServiceDef): ServiceHandle;
	/** 调用一个 service。args 是具名参数对象。 */
	call(service: ServiceHandle, args?: Record<string, string>, options?: CallOptions): Promise<string>;
	/** 并行执行多个任务。在执行图里挂一个 parallel 节点，子节点是各任务。 */
	parallel<T>(tasks: Array<() => Promise<T>>, options?: ParallelOptions): Promise<T[]>;
	/**
	 * 条件分支：cond 为 true 跑 thenFn，否则跑 elseFn（如有）。
	 * 重载：传了 elseFn 时返回 Promise<T>（一定有值）；省略 elseFn 时返回 Promise<T | undefined>。
	 */
	if<T>(cond: boolean, thenFn: () => Promise<T>, elseFn: () => Promise<T>): Promise<T>;
	if<T>(cond: boolean, thenFn: () => Promise<T>, elseFn?: () => Promise<T>): Promise<T | undefined>;
	/**
	 * if-elif-else 链：依次评估 branches[i].cond，命中则跑对应 fn。
	 * 重载：传了 elseFn 时返回 Promise<T>（一定有值）；省略 elseFn 时返回 Promise<T | undefined>。
	 */
	ifElse<T>(branches: Array<{
		cond: boolean;
		fn: () => Promise<T>;
	}>, elseFn: () => Promise<T>): Promise<T>;
	ifElse<T>(branches: Array<{
		cond: boolean;
		fn: () => Promise<T>;
	}>, elseFn?: () => Promise<T>): Promise<T | undefined>;
	/** 顺序遍历。每个 item 一个 iteration 子节点。 */
	forEach<T>(items: T[], fn: (item: T, index: number) => Promise<void>): Promise<void>;
	/** 并行遍历。每个 item 一个 iteration 子节点，全部并发。 */
	parallelForEach<T>(items: T[], fn: (item: T, index: number) => Promise<void>): Promise<void>;
	/**
	 * 用 LLM 做一次结构化判断。三种模式由 kind 决定：
	 * - "boolean"：返回 true/false
	 * - "number"：返回数字（可指定范围/整数）
	 * - "choice"：返回候选项中的一个
	 */
	evaluate(options: EvaluateBooleanOptions): Promise<boolean>;
	evaluate(options: EvaluateNumberOptions): Promise<number>;
	evaluate(options: EvaluateChoiceOptions): Promise<string>;
	/**
	 * 循环执行 fn，直到 condFn() 返回 true。
	 * 每轮迭代挂一个 iteration 节点。达到 maxIterations 强制退出。
	 */
	loopUntil(condFn: () => Promise<boolean>, fn: (round: number) => Promise<void>, options?: LoopOptions): Promise<void>;
	/**
	 * 当 condFn() 返回 true 时持续执行 fn。
	 * 第一次进入前先评估 cond；为 false 直接不进。
	 */
	loopWhile(condFn: () => Promise<boolean>, fn: (round: number) => Promise<void>, options?: LoopOptions): Promise<void>;
	/**
	 * LLM 在多个分支里选一个（基于 evaluate kind=choice），跑选中的那个并返回结果。
	 */
	choice<T>(options: ChoiceOptions<T>): Promise<T>;
	/** 顺序 map：对每个 item 顺序跑 fn，收集结果。子节点用 forEach + iteration 表示。 */
	map<T, R>(items: T[], fn: (item: T, index: number) => Promise<R>): Promise<R[]>;
	/** 并行 map：所有 fn 同时跑，按下标顺序返回结果。 */
	pmap<T, R>(items: T[], fn: (item: T, index: number) => Promise<R>): Promise<R[]>;
	/** 顺序 filter：保留 predicate 返回 true 的元素。 */
	filter<T>(items: T[], predicate: (item: T, index: number) => Promise<boolean>): Promise<T[]>;
	/** 并行 filter：predicate 全部并发评估。 */
	pfilter<T>(items: T[], predicate: (item: T, index: number) => Promise<boolean>): Promise<T[]>;
	/** 顺序 reduce：从 init 开始累积。每步一个 iteration 节点。 */
	reduce<T, R>(items: T[], fn: (acc: R, item: T, index: number) => Promise<R>, init: R): Promise<R>;
	/**
	 * 串行管道：把 input 依次喂给每个 step.fn，前一步输出 = 下一步输入。
	 * 每个 step 一个 pipelineStep 节点。
	 */
	pipeline<T>(input: T, steps: Array<{
		label?: string;
		fn: (value: any) => Promise<any>;
	}>): Promise<any>;
	/**
	 * 重试：把 fn 包起来，失败时按指数退避重跑。
	 * 在执行图里挂一个 retry 节点，子节点是每次尝试中真实发生的事情（call/session/...）。
	 */
	retry<T>(fn: () => Promise<T>, options?: RetryOptions): Promise<T>;
	/**
	 * 静态评估：完全不打 LLM，按规则判断。规则命中返回 true。
	 * 在执行图里挂一个 evaluate 节点（kind="static"），不写 trace。
	 */
	evaluateStatic(options: EvaluateStaticOptions): Promise<boolean>;
	/**
	 * service 调用语法糖：用字符串名调，无需先拿 ServiceHandle。
	 * 用于动态调度（比如根据 LLM 返回的 service 名再调用）。
	 */
	use(serviceName: string, args?: Record<string, string>, options?: CallOptions): Promise<string>;
	/**
	 * 把一段逻辑包成命名子图。在执行图里挂一个 block 节点，便于阅读。
	 * 不复用：每次调用都跑一遍 fn。
	 */
	block<T>(label: string, fn: () => Promise<T>): Promise<T>;
	/**
	 * 注册一个可复用 block，返回句柄。后续 flow.runBlock(handle, args) 调用。
	 * 适合"封装一段流程供主流程多处复用"的场景。
	 */
	defineBlock<R>(def: BlockDef<R>): BlockHandle;
	/** 调用一个已定义的 block。每次调用挂一个 block 节点。 */
	runBlock<R = unknown>(handle: BlockHandle, args?: Record<string, string>): Promise<R>;
	/**
	 * 重复执行 fn N 次。每次一个 iteration 子节点。
	 * 是 forEach 的"无 items"特化版，方便"跑 5 次"这种场景。
	 */
	repeat(times: number, fn: (round: number) => Promise<void>): Promise<void>;
	/**
	 * 取一个 input。等价于 ctx.input(name, defaultValue)，
	 * 让纯 flow 写法不用解构 ctx。
	 */
	input(name: string, defaultValue: string): Promise<string>;
	/**
	 * 显式落盘 binding。等价于 ctx.save(name, value)。
	 */
	output(name: string, value: string): Promise<void>;
	/**
	 * 跑一个任意外部命令（非 LLM），把 stdout 当节点产出落 binding。
	 * 在执行图里挂一个 exec 节点。exitCode≠0 时 throw（兼容 flow.retry）。
	 * 用于"给外部 Python/Java/任意 CLI 包工作流"——LLM 调用请用 flow.session。
	 */
	exec(opts: ExecOptions): Promise<ExecResult>;
}
export type GraphNodeType = "run" | "session" | "call" | "parallel" | "if" | "ifBranch" | "forEach" | "iteration" | "evaluate" | "choice" | "choiceBranch" | "loop" | "pipeline" | "pipelineStep" | "retry" | "block" | "exec" | "input";
export interface BaseGraphNode {
	id: string;
	type: GraphNodeType;
	startedAt: string;
	endedAt?: string;
	durationMs?: number;
	status: "running" | "ok" | "error";
	errorMessage?: string;
	children: GraphNode[];
	/** v0.6 resume：true 表示这一节点的产物来自 --resume 复用，没有真正重跑。 */
	cached?: boolean;
}
export interface RunGraphNode extends BaseGraphNode {
	type: "run";
}
export interface SessionGraphNode extends BaseGraphNode {
	type: "session";
	agent: string;
	bindingName: string;
	traceFile: string;
	tokens?: {
		input: number;
		output: number;
	};
	/** v0.7：这次调用走的 CLI 引擎（claude/openclaw/hermes/psi）。 */
	engine?: string;
	/** v0.7：claude engine 给的 total_cost_usd；其余引擎可能 undefined。 */
	costUSD?: number;
}
export interface CallGraphNode extends BaseGraphNode {
	type: "call";
	service: string;
	args: Record<string, string>;
	bindingName: string;
}
export type ParallelJoin = "all" | "first" | {
	any: number;
};
export interface ParallelGraphNode extends BaseGraphNode {
	type: "parallel";
	joinStrategy: ParallelJoin;
	taskCount: number;
}
export interface IfGraphNode extends BaseGraphNode {
	type: "if";
	/** "boolean" 或 "discretion"（暂只支持 boolean）。 */
	conditionKind: "boolean";
	/** 条件实际取值。 */
	conditionValue: boolean;
	/** 走了哪一支：then / else / none（无 else）。 */
	takenBranch: "then" | "else" | "none";
}
/** if 节点下的具体分支节点（可选，便于在图里区分 then / else 子树）。 */
export interface IfBranchGraphNode extends BaseGraphNode {
	type: "ifBranch";
	branch: "then" | "else";
}
export interface ForEachGraphNode extends BaseGraphNode {
	type: "forEach";
	/** 是否并行。 */
	parallel: boolean;
	itemCount: number;
}
export interface IterationGraphNode extends BaseGraphNode {
	type: "iteration";
	index: number;
	/** 该次迭代对应的 item 的字符串描述（短截断），便于排查。 */
	itemPreview: string;
}
export type EvaluateKind = "boolean" | "number" | "choice" | "static";
export interface EvaluateGraphNode extends BaseGraphNode {
	type: "evaluate";
	/** 期望返回类型：boolean / number / choice / static（不打 LLM） */
	kind: EvaluateKind;
	/** 评估问题的简短描述（用于 UI 展示，不是完整 prompt） */
	question: string;
	/** choice 类型才有：候选项列表 */
	options?: string[];
	/** static 类型才有：规则描述 */
	staticRule?: string;
	/** LLM 真实返回的原始字符串（解析前）。static 时为空。 */
	rawAnswer?: string;
	/** 解析后的最终值 */
	parsedValue?: boolean | number | string;
	/** 为这次 evaluate 起的 binding 名（默认 evaluate-N） */
	bindingName: string;
	/** 对应 trace 文件相对路径。static 时为空。 */
	traceFile?: string;
	/** 用于评估的 agent 名（默认是内建的 evaluator）。static 时为 "__static__"。 */
	evaluatorAgent: string;
	tokens?: {
		input: number;
		output: number;
	};
}
/** flow.choice 节点：LLM 在多个分支里选一个，然后跑选中的那个 */
export interface ChoiceGraphNode extends BaseGraphNode {
	type: "choice";
	question: string;
	options: string[];
	/** 选中的 option 文本 */
	chosen?: string;
	/** 选中的 option 在数组里的下标 */
	chosenIndex?: number;
}
/** choice 选中分支的容器节点 */
export interface ChoiceBranchGraphNode extends BaseGraphNode {
	type: "choiceBranch";
	/** 选中的 option 文本 */
	branch: string;
	/** 选中的下标 */
	index: number;
}
/** flow.loopUntil / loopWhile 的循环容器节点 */
export interface LoopGraphNode extends BaseGraphNode {
	type: "loop";
	/** "until"：跑到 cond=true 才停；"while"：cond=true 时一直跑 */
	loopKind: "until" | "while";
	/** 实际执行的迭代次数 */
	iterations: number;
	/** 安全上限 */
	maxIterations: number;
	/** 是否因为达到上限而退出（true=爆 max；false=正常退出） */
	hitMaxIterations: boolean;
}
/** flow.pipeline 节点：固定数量串行步骤，每步把上一步结果作为输入。 */
export interface PipelineGraphNode extends BaseGraphNode {
	type: "pipeline";
	stepCount: number;
}
/** pipeline 中单个 step 的容器节点。 */
export interface PipelineStepGraphNode extends BaseGraphNode {
	type: "pipelineStep";
	/** 步骤下标 */
	index: number;
	/** 步骤标签（可选：来自 step.label） */
	label?: string;
}
/** flow.retry 节点：包裹一个 fn，按重试策略反复执行直到成功或耗尽。 */
export interface RetryGraphNode extends BaseGraphNode {
	type: "retry";
	/** 配置的最大尝试次数（含第一次） */
	maxAttempts: number;
	/** 实际尝试次数 */
	attempts: number;
	/** 是否最终成功 */
	succeeded: boolean;
	/** 失败时记录每次尝试的错误信息（成功时为空数组） */
	errorTrail: string[];
}
/** flow.block / flow.defineBlock / flow.runBlock 节点。 */
export interface BlockGraphNode extends BaseGraphNode {
	type: "block";
	/** block 的标签或定义名 */
	label: string;
	/** 是否是已定义 block 的实例化调用（true=runBlock，false=匿名 block） */
	isDefined: boolean;
	/** 调用参数（仅 runBlock） */
	args?: Record<string, string>;
}
/** flow.exec 节点：跑任意外部命令（非 LLM）。 */
export interface ExecGraphNode extends BaseGraphNode {
	type: "exec";
	/** 节点标签(来自 opts.name)。 */
	name: string;
	/** 跑的命令（含首个 arg 预览，便于排查）。 */
	command: string;
	/** 自动 / 显式分配的 binding 名。 */
	bindingName: string;
	/** 子进程退出码。 */
	exitCode?: number;
	/** #20: stdout 是否因 maxStdoutBytes 被截断。 */
	truncated?: boolean;
}
/** defineBlock 返回的句柄。runBlock 时用来定位调用目标。 */
export interface BlockHandle {
	readonly __kind: "block";
	readonly name: string;
	readonly description?: string;
}
export type GraphNode = RunGraphNode | SessionGraphNode | CallGraphNode | ParallelGraphNode | IfGraphNode | IfBranchGraphNode | ForEachGraphNode | IterationGraphNode | EvaluateGraphNode | ChoiceGraphNode | ChoiceBranchGraphNode | LoopGraphNode | PipelineGraphNode | PipelineStepGraphNode | RetryGraphNode | BlockGraphNode | ExecGraphNode | InputGraphNode;
export interface InputGraphNode extends BaseGraphNode {
	type: "input";
	/** input 名（= input/<name>.md）。 */
	name: string;
	/** 值是否来自 CLI --input.<name>= 覆盖（否则用 defaultValue）。 */
	fromCli: boolean;
}
export interface ExecutionGraph {
	runId: string;
	root: RunGraphNode;
}
export interface BindingMeta {
	name: string;
	/** 哪个 agent / service 产生的。 */
	producedBy: string;
	producedAt: string;
	tokens?: {
		input: number;
		output: number;
	};
	/** 对应执行图节点的 id，方便从 binding 反查图位置。 */
	sourceNode: string;
	/** v0.6 resume：sha256 前 16 位，对 session 是 provider+model+system+user+temp+max；对 call 是 service+args。 */
	inputHash?: string;
}
export interface RunContext {
	/** 当前 run 的 id（落盘目录名）。 */
	readonly runId: string;
	/** 当前 run 的根目录绝对路径。 */
	readonly runDir: string;
	/** 取一个 input 参数（CLI 没传时用 default）。会自动落盘到 input/<name>.md。 */
	input(name: string, defaultValue: string): Promise<string>;
	/** 显式命名落盘一个 binding。覆盖自动命名。 */
	save(name: string, value: string): Promise<void>;
	/** v0.2 新增：FlowAPI 入口。 */
	flow: FlowAPI;
}
export interface RunOptions {
	runsDir?: string;
	programPath?: string;
	/** v0.6 resume：传入旧 runId 则复用其 runDir，已有 bindings 命中时跳过对应 LLM/service 调用。 */
	resumeFromRunId?: string;
	/**
	 * #25：默认 false（向后兼容：run() 不抛，错误读 meta.json / process.exitCode）。
	 * true 时 run() 在用户 fn 抛错后，落完产物再 rethrow，让 `try { await run() } catch` 能捕获。
	 */
	throwOnError?: boolean;
}
export interface TraceEntry {
	agent: string;
	provider: string;
	model: string;
	startedAt: string;
	endedAt: string;
	durationMs: number;
	system: string;
	userPrompt: string;
	context?: Record<string, string>;
	output: string;
	inputTokens?: number;
	outputTokens?: number;
	cacheReadTokens?: number;
	cacheWriteTokens?: number;
}
/**
 * #5/#15/#22：遍历执行图，把所有带 tokens 的节点（session / evaluate）累加。
 * 按归属拆 user vs internal —— 内部 evaluator（evaluatorAgent / agent 以 "__" 开头，
 * 即 __evaluator__ / __static__）不该算进用户的 LLM 账单 / 调用数，否则用户做
 * 容量预测 / 限速会被误导。不计 cached（resume 命中）节点——它们没真正烧 token。
 */
export declare function aggregateTokens(root: RunGraphNode): {
	user: {
		calls: number;
		input: number;
		output: number;
	};
	internal: {
		calls: number;
		input: number;
		output: number;
	};
	/** user + internal 合计（账单维度，诚实总额）。 */
	calls: number;
	input: number;
	output: number;
};
/** #5/#28：把 token 数压成人类友好的短串：800 -> "800"、12345 -> "12.3k"、1_500_000 -> "1.50M"。 */
export declare function formatTokenCount(n: number): string;
/**
 * #23：runs/ ring-buffer GC。删掉超出保留窗口的旧 run 目录，避免 runs/ 无限增长。
 * 策略：保留最近 keepCount 个（按 runId 时间戳前缀降序）+ keepDays 天内的，取并集。
 * 即「最近 N 个」和「N 天内」都保。返回被删的 runId 列表。
 *
 * keepCount <= 0 / keepDays <= 0 表示该维度不限制（仅另一维度生效）；两者都 <=0 = 不删。
 */
export declare function gcRuns(runsDir: string, opts?: {
	keepCount: number;
	keepDays: number;
	excludeRunId?: string;
}): Promise<string[]>;
/**
 * v0.1 Agent() 工厂：保留向后兼容。
 * 不参与执行图，trace 直接落到 trace/<name>.json。
 */
declare function Agent$1(config: AgentConfig): Agent;
/**
 * 顶层入口。包裹整个 flow，注入 RunContext（含 v0.2 的 flow API）。
 *
 * v0.6 起支持 resume：传入 options.resumeFromRunId 或在 CLI 加 --resume=<runId>
 * 时，复用旧 runDir 下的 bindings；新一轮执行碰到同名 binding 且 inputHash 一致就跳过 LLM 调用。
 *
 * #25：默认 run() 不抛异常（错误读返回的 status / meta.json / process.exitCode）。
 *      传 options.throwOnError=true 则落完产物后 rethrow，让 try/catch 能捕获。
 */
export declare function run(fn: (ctx: RunContext) => Promise<void>, options?: RunOptions): Promise<{
	runId: string;
	runDir: string;
	status: "ok" | "error";
}>;
/**
 * 返回校验通过、normalize 成 NFC 的安全 name。
 *
 * #29: Unicode 同一字形可有 NFC（café = U+00E9）/ NFD（café = U+0065 U+0301）两种编码，
 *      Linux 字节级区分、mac 自动转、Windows 保留原样 —— 跨平台拉同一个 run 会找不到
 *      binding。统一 normalize 成 NFC 后再用于文件名，消除这层不一致。调用方应使用返回值。
 */
export declare function assertSafeName(kind: string, name: string): string;
export type CliEngineName = "claude" | "openclaw" | "hermes" | "psi";
export interface CliRequest {
	/** system prompt（角色）。 */
	system?: string;
	/** user message。 */
	prompt: string;
	/** 模型 id / alias（claude 接受 "opus"/"sonnet" 或全名）。空串 = 用 CLI 默认。 */
	model?: string;
	/** 严格结构化输出 JSON Schema（evaluate 用）。仅 capabilities.jsonSchema 引擎生效。 */
	jsonSchema?: object;
	/** agentic：放开的 tool 列表。undefined/[] = 纯文本（禁用 tool）。 */
	tools?: string[];
	/** 多轮上限（agentic 时）。 */
	maxTurns?: number;
	/**
	 * #17: 扩展推理预算。claude 引擎按 budgetTokens 区间映射到 --effort
	 * (low/medium/high/max)，真生效；不支持的引擎忽略。
	 */
	thinking?: {
		budgetTokens: number;
	};
	/** 子进程额外 env（合并到引擎自己的 env 之上）。 */
	env?: Record<string, string>;
}
export interface CliBuilt {
	command: string;
	args: string[];
	stdin?: string;
	useShell: boolean;
}
export interface CliParsed {
	text: string;
	meta?: SpawnMeta;
}
export interface CliEngine {
	name: CliEngineName;
	capabilities: {
		tokenUsage: boolean;
		jsonSchema: boolean;
		tools: boolean;
	};
	buildArgs(req: CliRequest): CliBuilt;
	/** 引擎专属 env（不含 PATH，runSubprocess 会自动补 PATH）。返回 undefined = 只用 req.env。 */
	buildEnv?(req: CliRequest): Record<string, string> | undefined;
	parse(stdout: string, exitCode: number): CliParsed;
}
/** 选引擎：显式 name 优先，否则读 FLOW_ENGINE，默认 claude。 */
export declare function pickEngine(name?: string): CliEngine;

export {
	Agent as AgentType,
	Agent$1 as Agent,
};

export {};

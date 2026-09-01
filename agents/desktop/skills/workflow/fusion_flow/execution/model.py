"""FusionFlow 运行时共享的数据模型、规则与辅助函数。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Literal


def _validate_token_count(value: int | None, name: str) -> None:
    """Validate an optional non-negative token count."""

    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer or None")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """定义 Agent 的不可变运行配置; 缺少非空 system_prompt 时抛出 ValueError。"""

    name: str
    system_prompt: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    thinking_budget_tokens: int | None = None
    engine: str | None = None
    tools: tuple[str, ...] = ()
    max_turns: int | None = None
    context_schema: tuple[str, ...] | None = None
    api_base: str | None = None
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        """校验名称并冻结可迭代配置, 保证运行时配置稳定。"""
        object.__setattr__(self, "name", assert_safe_name(self.name))
        if not self.system_prompt:
            raise ValueError("AgentConfig requires a non-empty system_prompt")
        object.__setattr__(self, "tools", tuple(self.tools))
        if self.context_schema is not None:
            object.__setattr__(self, "context_schema", tuple(self.context_schema))


def _with_agent_defaults(
    config: AgentConfig,
    *,
    max_tokens: int,
    temperature: float,
) -> AgentConfig:
    """Resolve operation-specific defaults without losing explicit values."""

    return replace(
        config,
        max_tokens=max_tokens if config.max_tokens is None else config.max_tokens,
        temperature=temperature if config.temperature is None else config.temperature,
    )


@dataclass(frozen=True, slots=True)
class AgentInvocation:
    """表示一次 Agent 调用的提示词和可选上下文。"""

    prompt: str
    context: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        """复制并只读化上下文, 避免调用方随后修改请求内容。"""
        if self.context is not None:
            object.__setattr__(
                self,
                "context",
                MappingProxyType(dict(self.context)),
            )


@dataclass(frozen=True, slots=True)
class SessionResult:
    """承载会话返回文本及可选的 token 用量。"""

    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        """Reject values that cannot be represented as portable JSON counts."""

        _validate_token_count(self.input_tokens, "input_tokens")
        _validate_token_count(self.output_tokens, "output_tokens")


type SessionRunner = Callable[
    [AgentConfig, AgentInvocation],
    Awaitable[SessionResult | str],
]


@dataclass(frozen=True, slots=True)
class PipelineStep:
    """表示流水线中的一个异步处理步骤及其可读标签。"""

    fn: Callable[[object], Awaitable[object]]
    label: str | None = None


@dataclass(frozen=True, slots=True)
class RegexRule:
    """声明目标字段须匹配正则表达式的静态规则。"""

    pattern: str | re.Pattern[str]
    on: str
    kind: Literal["regex"] = field(default="regex", init=False)


@dataclass(frozen=True, slots=True)
class ContainsRule:
    """声明目标字段须包含指定文本的静态规则。"""

    needle: str
    on: str
    kind: Literal["contains"] = field(default="contains", init=False)


@dataclass(frozen=True, slots=True)
class EqualsRule:
    """声明目标字段须等于指定文本的静态规则。"""

    expected: str
    on: str
    kind: Literal["equals"] = field(default="equals", init=False)


@dataclass(frozen=True, slots=True)
class RangeRule:
    """声明数值范围规则, 并保证边界可比较, 否则抛出类型或值错误。"""

    value: float
    minimum: float | None = None
    maximum: float | None = None
    kind: Literal["range"] = field(default="range", init=False)

    def __post_init__(self) -> None:
        """拒绝布尔值和无效边界, 确保数值范围可比较。"""
        if isinstance(self.value, bool) or not isinstance(self.value, int | float):
            raise TypeError("RangeRule value must be numeric")
        if self.minimum is not None and (isinstance(self.minimum, bool) or not isinstance(self.minimum, int | float)):
            raise TypeError("RangeRule minimum must be numeric")
        if self.maximum is not None and (isinstance(self.maximum, bool) or not isinstance(self.maximum, int | float)):
            raise TypeError("RangeRule maximum must be numeric")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("RangeRule minimum must be <= maximum")


@dataclass(frozen=True, slots=True)
class PredicateRule:
    """声明由同步或异步谓词决定结果的静态规则。"""

    fn: Callable[[], Awaitable[bool] | bool]
    kind: Literal["predicate"] = field(default="predicate", init=False)


type StaticRule = RegexRule | ContainsRule | EqualsRule | RangeRule | PredicateRule


@dataclass(frozen=True, slots=True)
class AgentHandle:
    """标识已注册 Agent 及其不可变配置。"""

    name: str
    config: AgentConfig
    kind: Literal["agent"] = field(default="agent", init=False)


@dataclass(frozen=True, slots=True)
class ServiceParam:
    """描述服务句柄接受的一个参数。"""

    name: str
    description: str | None = None
    required: bool = True


@dataclass(frozen=True, slots=True)
class ServiceHandle:
    """标识服务及其参数模式和可选说明。"""

    name: str
    params: tuple[ServiceParam, ...] = ()
    description: str | None = None
    kind: Literal["service"] = field(default="service", init=False)

    def __post_init__(self) -> None:
        """冻结参数序列, 保持服务声明不可变。"""
        object.__setattr__(self, "params", tuple(self.params))


@dataclass(frozen=True, slots=True)
class BlockHandle:
    """标识可复用流程块及其可选说明。"""

    name: str
    description: str | None = None
    kind: Literal["block"] = field(default="block", init=False)


@dataclass(frozen=True, slots=True)
class ExecResult:
    """记录命令执行的输出、状态、耗时与截断情况。"""

    stdout: str
    raw: str
    exit_code: int
    duration_ms: float
    stderr: str = ""
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class RunResult:
    """记录一次流程运行的标识、目录和最终状态。"""

    run_id: str
    run_dir: str
    status: Literal["ok", "error"]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """汇总调用次数及可选的输入、输出 token 数。"""

    calls: int
    input: int | None
    output: int | None

    def __post_init__(self) -> None:
        """Keep persisted token usage within the non-negative integer domain."""

        if isinstance(self.calls, bool) or not isinstance(self.calls, int):
            raise TypeError("calls must be an integer")
        if self.calls < 0:
            raise ValueError("calls must be non-negative")
        _validate_token_count(self.input, "input")
        _validate_token_count(self.output, "output")


@dataclass(frozen=True, slots=True)
class TokenSummary:
    """按用户调用和框架内部调用分组, 同时保留两组的扁平合计。"""

    user: TokenUsage
    internal: TokenUsage
    calls: int
    input: int | None
    output: int | None


type TraceStatus = Literal["running", "ok", "error", "cancelled"]
type TraceKind = Literal[
    "run",
    "session",
    "call",
    "parallel",
    "if",
    "ifBranch",
    "forEach",
    "iteration",
    "evaluate",
    "choice",
    "choiceBranch",
    "loop",
    "pipeline",
    "pipelineStep",
    "retry",
    "block",
    "exec",
    "input",
]


@dataclass(slots=True)
class ExecutionTrace:
    """保存执行树节点; 子节点序列和元数据副本与外部输入隔离。"""

    trace_id: str
    kind: TraceKind
    label: str
    started_at: str
    status: TraceStatus = "running"
    finished_at: str | None = None
    duration_ms: float | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    children: tuple[ExecutionTrace, ...] = ()
    tokens: TokenUsage | None = None
    cached: bool = False
    metadata: dict[str, object] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        """复制可变输入并冻结子节点序列, 隔离外部后续修改。"""
        self.children = tuple(self.children)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, object]:
        """将执行树递归转换为可序列化的普通字典。"""
        tokens = None
        if self.tokens is not None:
            tokens = {
                "calls": self.tokens.calls,
                "input": self.tokens.input,
                "output": self.tokens.output,
            }
        return {
            "trace_id": self.trace_id,
            "kind": self.kind,
            "label": self.label,
            "started_at": self.started_at,
            "status": self.status,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "children": [child.to_dict() for child in self.children],
            "tokens": tokens,
            "cached": self.cached,
            "metadata": dict(self.metadata),
            "error": self.error,
        }


_WINDOWS_RESERVED_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)",
    re.IGNORECASE,
)
_WINDOWS_UNSAFE_CHARACTERS = frozenset('<>:"/\\|?*')


def assert_safe_name(name: str) -> str:
    """返回跨平台安全的 NFC 名称; 违反命名约束时抛出 ValueError。"""

    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")

    # 先统一等价 Unicode 表示, 避免同名在文件系统中产生不同结果。
    normalized = unicodedata.normalize("NFC", name)
    # 拒绝路径、控制符和 Windows 特殊名称, 名称会用于运行目录与标识。
    if normalized == "." or ".." in normalized:
        raise ValueError(f'name "{name}" must not contain ".."')
    if any(
        character in _WINDOWS_UNSAFE_CHARACTERS or unicodedata.category(character)[0] in {"C", "Z"}
        for character in normalized
    ):
        raise ValueError(f'name "{name}" contains an unsafe character')
    if _WINDOWS_RESERVED_NAME.match(normalized):
        raise ValueError(f'name "{name}" is a Windows reserved device name')
    if normalized.endswith((".", " ")):
        raise ValueError(f'name "{name}" must not end with a period or space')
    return normalized


def aggregate_tokens(root: ExecutionTrace) -> TokenSummary:
    """递归汇总未缓存 token, 并按调用所有者拆分 user/internal。"""

    user_calls = 0
    user_input: int | None = 0
    user_output: int | None = 0
    internal_calls = 0
    internal_input: int | None = 0
    internal_output: int | None = 0

    def visit(node: ExecutionTrace) -> None:
        """深度优先累加单个节点及其子节点的可计费用量。"""
        nonlocal user_calls, user_input, user_output
        nonlocal internal_calls, internal_input, internal_output
        if node.tokens is not None and not node.cached:
            owner = node.metadata.get("evaluator_agent")
            if owner is None:
                owner = node.metadata.get("evaluator")
            if owner is None:
                owner = node.metadata.get("agent", "")
            is_internal = isinstance(owner, str) and owner.startswith("__")
            if is_internal:
                internal_calls += 1
                internal_input = (
                    None if internal_input is None or node.tokens.input is None else internal_input + node.tokens.input
                )
                internal_output = (
                    None
                    if internal_output is None or node.tokens.output is None
                    else internal_output + node.tokens.output
                )
            else:
                user_calls += 1
                user_input = None if user_input is None or node.tokens.input is None else user_input + node.tokens.input
                user_output = (
                    None if user_output is None or node.tokens.output is None else user_output + node.tokens.output
                )
        # 子节点可能继续嵌套并含有独立调用, 必须完整遍历执行树。
        for child in node.children:
            visit(child)

    visit(root)
    user = TokenUsage(calls=user_calls, input=user_input, output=user_output)
    internal = TokenUsage(
        calls=internal_calls,
        input=internal_input,
        output=internal_output,
    )
    return TokenSummary(
        user=user,
        internal=internal,
        calls=user.calls + internal.calls,
        input=(None if user.input is None or internal.input is None else user.input + internal.input),
        output=(None if user.output is None or internal.output is None else user.output + internal.output),
    )


def format_token_count(count: int | None) -> str:
    """将 token 数格式化为紧凑的人类可读文本。"""
    if count is None:
        return "unknown"
    if count < 1_000:
        return str(count)
    if count < 1_000_000:
        value = Decimal.from_float(float(count) / 1_000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return f"{value}k"
    value = Decimal.from_float(float(count) / 1_000_000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{value}M"

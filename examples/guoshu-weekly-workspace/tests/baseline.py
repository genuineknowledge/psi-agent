"""Accuracy baseline for the guoshu-weekly agent (396-question self-built set).

Runs each question through the real agent loop -- tool selection, MCP取数,
answer composition -- then grades the natural-language answer against the
reference result set with an LLM, per requirement 9.2 ("以 llm 判断为口径").

Why LLM grading and not string comparison: the reference answers are SQL result
sets, the agent produces prose plus tables. "82" and "共 82 项" are the same
answer; a diff is not.  The grader is told to judge only whether the facts match,
and to ignore wording, ordering and formatting.

Per-category rates matter more than the total: M (权限与安全) and N (不可答) are
pass/fail gates -- one miss there is a defect, not a percentage point.

Usage:
    export GUOSHU_WEEKLY_MCP_URL=http://127.0.0.1:18900/mcp
    export GUOSHU_WEEKLY_MCP_TOKEN=demo-token
    export BASELINE_API_KEY=sk-...
    python tests/baseline.py --limit 20            # smoke run
    python tests/baseline.py                       # full 396
    python tests/baseline.py --category M,N        # only the gate categories
"""

# ruff: noqa: RUF001  中文题目与判定提示里的全角标点是数据, 不能换成半角。
# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import httpx

WORKSPACE = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKSPACE.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from psi_agent.session.tool_registry import ToolRegistry  # noqa: E402

DEFAULT_ANSWERS = Path.home() / "Downloads" / "nl2sql-answers.jsonl"
DEFAULT_MODEL = os.environ.get("BASELINE_MODEL", "deepseek-chat")
DEFAULT_BASE_URL = os.environ.get("BASELINE_BASE_URL", "https://api.deepseek.com/v1")

MAX_TOOL_ROUNDS = 6
"""Cap on tool-call rounds per question.

Enough for 定位 → 取数 → 复核, but bounded: a runaway loop would burn the whole
run's token budget on one question.  Hitting the cap is recorded as a failure
with reason `max_rounds`, not silently treated as an answer.
"""

_VERDICT_TAIL = """
只输出 JSON：{"verdict": "pass" 或 "fail", "reason": "一句话理由"}"""

GRADER_SYSTEM = (
    """\
你是严格的评测判定器。判断「实际回答」是否在事实上等价于「参考答案」。

判定规则：
- 只看事实是否一致：数字、名称、条目集合。措辞、语序、格式、表格样式一律不计。
- 参考答案是 SQL 结果集；实际回答是自然语言，可能含解释与口径说明，这不算错。
- 数字必须一致。参考 82 而回答「约 80」判错。
- 集合类答案：条目齐全且无多余即算对，顺序不计。
- 实际回答多给了口径说明、数据来源声明、依据字段，不影响判定。
- 实际回答在参考答案之外补充的相关信息（并列情况说明、权限说明、另一套相关口径的
  分布），只要不与参考答案冲突，不算多余条目，不影响判定。
- 参考答案里没有的内容（如下载地址、敏感字段值），实际回答说明不可得/按权限不展示，
  不算缺失，不影响判定。
- 参考答案的字段名是数据的列名，按列名语义对应：lead_owner_name=牵头人/分管领导、
  project_owner_name=主责人/项目负责人。实际回答用同义词（如「牵头人」对
  lead_owner_name）即算对应，不要因用词不同判错。
- 若参考答案为空集/0，实际回答说明「没有记录/为 0」即算对。
- 若实际回答说「不可答」但参考答案有内容，判错；反之亦然。"""
    + _VERDICT_TAIL
)

# The 93-question set needs three rubrics, not one: only 40 of its questions are
# lookups.  Grading a correct refusal ("cannot answer: milestone_due_date missing")
# against a fact-equivalence rubric marks it wrong, and grading a judgement
# question by string overlap marks a differently-worded but correct call wrong.
# So the rubric travels with the row (`grade_mode`) instead of being global.
GRADER_SIGNAL = (
    """\
你是严格的评测判定器。这道题要的是「规则信号」：一个基于数据的判断，加上建议动作。

判定规则：
- 只依据本题给出的参考答案判定。参考答案里没有的内容，一律不得作为要求。
- 判断的方向必须与参考答案一致：参考答案指向哪个首要问题，实际回答就得指向同一个。
- 参考答案里点名的关键数字必须出现且一致；漏掉其中一个判错，多给别的数字不判错。
  「关键数字」只指本题参考答案文本里真实出现的那些数字。
- 实际回答在点名数字之外补充的其他口径数字（如参考答案说孤儿 2+2 条，回答又提到
  附件孤儿 3 条），只要不与点名数字冲突、没有把补充数字当成点名数字的替代，不判错。
- 建议动作的措辞不计，方向一致即可。
- 允许实际回答更详细、给出额外证据或口径说明。
- 若实际回答只罗列数字而不给出判断或建议，判错——这类题要的就是判断。
- 若参考答案本身只讲取数口径或机制而不含数字，就判「实际回答是否讲对了这个口径」，
  不要求它给数字。
- 若实际回答说「无法回答」，判错。"""
    + _VERDICT_TAIL
)

GRADER_REFUSAL = (
    """\
你是严格的评测判定器。这道题的正确表现是「拒答并说明缺什么」，不是给出数字。

判定规则：
- 实际回答必须明确表示当前数据不足以给出可靠结论，或该内容按权限不展示——
  只要表达了这个意思即可，措辞不计（「按权限不展示」「字段缺失」「无法可靠计算」都算）。
- 实际回答还必须点出缺失的数据/字段或权限原因，方向与参考答案一致
  （如缺截止日、缺权重、缺预算、缺基线、缺依赖关系、敏感字段按权限遮蔽）。
  列举得不完全不判错，方向错才判错。
- 关键：若实际回答绕过缺口、给出了一个具体结论或排名或百分比当作答案，一律判错，
  哪怕那个数字看起来合理——本题考的正是「不硬答」。
- 只依据本题给出的参考答案判定；参考答案里没点名的缺失字段，不得作为要求。
- 实际回答顺带说明「现有数据能回答到什么程度」并给出那部分事实，不判错，
  前提是它明确区分了「能答的部分」和「答不了的部分」。"""
    + _VERDICT_TAIL
)

GRADERS = {"fact": GRADER_SYSTEM, "signal": GRADER_SIGNAL, "refusal": GRADER_REFUSAL}

# merged-bank 的 grade_mode 是「答案形态」而不是判定 rubric。g93 行自带 g93_grade_mode
# (事实/信号/拒答三选一), 其余两库的 exact_value 一律按事实 rubric 判。把形态映射到
# rubric, 避免 20 道 refusal_justified 题掉进 fact rubric 被误判(正确拒答会被判错)。
_MODE_TO_RUBRIC = {
    "exact_value": "fact",
    "exact_value_in_prose": "fact",
    "assertion_plus_figures": "signal",
    "refusal_justified": "refusal",
}


def rubric_for(item: dict[str, Any]) -> str:
    return str(item.get("g93_grade_mode") or _MODE_TO_RUBRIC.get(item.get("grade_mode", ""), "fact"))


@dataclass
class Outcome:
    qid: str
    category: str
    difficulty: str
    kind: str
    passed: bool
    reason: str
    elapsed: float
    rounds: int
    grade_mode: str = "fact"
    tools_used: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)


class Upstream:
    """Minimal OpenAI-compatible client.

    Deliberately not the psi-agent AI layer: that needs a running Gateway plus
    sockets, and this harness only needs chat completions.  Keeping it separate
    means a baseline run cannot be broken by Gateway config drift.
    """

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self._key = api_key
        self._model = model
        self._base = base_url.rstrip("/")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        last_error = ""
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
                    response = await client.post(
                        f"{self._base}/chat/completions",
                        headers={"Authorization": f"Bearer {self._key}"},
                        json=payload,
                    )
                    if response.status_code >= 500 or response.status_code == 429:
                        last_error = f"HTTP {response.status_code}"
                        await anyio.sleep(min(2.0 * (2**attempt), 12.0))
                        continue
                    response.raise_for_status()
                    return response.json()
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                await anyio.sleep(min(2.0 * (2**attempt), 12.0))
        raise RuntimeError(f"upstream failed after 3 attempts: {last_error}")


def tool_schemas(registry: ToolRegistry) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, meta in sorted(registry.tools.items()):
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": meta.description,
                    "parameters": meta.parameters,
                },
            }
        )
    return out


async def answer_question(
    upstream: Upstream,
    registry: ToolRegistry,
    schemas: list[dict[str, Any]],
    system_prompt: str,
    question: str,
    trace: list[str] | None = None,
) -> tuple[str, int, list[str]]:
    """Run one question through the agent loop. Returns (answer, rounds, tools).

    ``trace`` collects the actual call text (``tool(args)``) plus the final answer.
    Tool NAMES alone cannot tell a right call from a wrong one: every prompt-side
    failure here was the right tool carrying the wrong argument, so diagnosing one
    off the name list means guessing.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    used: list[str] = []
    for round_index in range(MAX_TOOL_ROUNDS):
        reply = await upstream.complete(messages, schemas)
        choice = reply["choices"][0]
        message = choice["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            if trace is not None:
                trace.append((message.get("content") or "").strip()[:600])
            return (message.get("content") or "").strip(), round_index + 1, used
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": calls,
            }
        )
        for call in calls:
            name = call["function"]["name"]
            used.append(name)
            if trace is not None:
                trace.append(f"{name}({(call['function'].get('arguments') or '{}').strip()[:220]})")
            func = registry.get(name)
            if func is None:
                result = json.dumps({"ok": False, "error": {"code": "no_such_tool", "message": name}})
            else:
                try:
                    raw_args = call["function"].get("arguments") or "{}"
                    kwargs = json.loads(raw_args) if raw_args.strip() else {}
                    result = await func(**kwargs)
                except Exception as exc:
                    result = json.dumps(
                        {"ok": False, "error": {"code": "tool_raised", "message": f"{type(exc).__name__}: {exc}"}},
                        ensure_ascii=False,
                    )
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    return "", MAX_TOOL_ROUNDS, used


async def grade(
    upstream: Upstream,
    question: str,
    gold: Any,
    actual: str,
    mode: str = "fact",
    evidence: str = "",
) -> tuple[bool, str]:
    if not actual:
        return False, "空回答（可能撞到 max_rounds）"
    gold_text = gold if isinstance(gold, str) else json.dumps(gold, ensure_ascii=False)
    # 清单类 gold 可能很长: 截断前先把「共几行/几项」报给判定器, 让它即使看不到
    # 全部条目也能核验数量是否一致(曾因 73 行 gold 被截断而误判「无法确认」)。
    row_count = ""
    if isinstance(gold, dict):
        rows = gold.get("rows")
        if isinstance(rows, list):
            row_count = f"参考答案共 {len(rows)} 行/条记录。"
    if len(gold_text) > 8000:
        gold_text = gold_text[:8000] + " …（参考答案已截断，见行数说明）"
    user = f"问题：{question}\n\n{row_count}参考答案：{gold_text}\n\n实际回答：{actual[:4000]}"
    if evidence:
        # Only the 93-question set carries this: it states the caliber the
        # reference answer was computed under, which is what lets the grader tell
        # a wrong number from a right number under a different gate.
        user = (
            f"问题：{question}\n\n{row_count}参考答案：{gold_text}\n\n"
            f"参考答案的数据依据：{evidence}\n\n实际回答：{actual[:4000]}"
        )
    reply = await upstream.complete(
        [{"role": "system", "content": GRADERS.get(mode, GRADER_SYSTEM)}, {"role": "user", "content": user}],
        temperature=0.0,
    )
    text = (reply["choices"][0]["message"].get("content") or "").strip()
    verdict, reason = _parse_verdict(text)
    return verdict, reason


def _parse_verdict(text: str) -> tuple[bool, str]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1] if "```" in stripped[3:] else stripped[3:]
        stripped = stripped.removeprefix("json").strip()
    try:
        parsed = json.loads(stripped)
        return parsed.get("verdict") == "pass", str(parsed.get("reason", ""))[:160]
    except ValueError, AttributeError:
        # Fall back to a keyword read rather than discarding the judgement.
        lowered = stripped.lower()
        if '"pass"' in lowered or lowered.startswith("pass"):
            return True, "（判定器未返回合法 JSON，按关键词读取）"
        return False, f"判定器输出无法解析：{stripped[:100]}"


async def load_system_prompt() -> str:
    spec = importlib.util.spec_from_file_location("guoshu_baseline_system", WORKSPACE / "systems" / "system.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load systems/system.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return await module.system_prompt_builder()


def summarise(results: list[Outcome], elapsed: float) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print("\n" + "=" * 72)
    print(f"总体：{passed}/{total} = {100 * passed / max(total, 1):.1f}%   耗时 {elapsed / 60:.1f} min")
    print("=" * 72)

    by_category: dict[str, list[Outcome]] = {}
    for r in results:
        by_category.setdefault(r.category, []).append(r)
    print("\n按大类（M 权限与安全、N 不可答为闸门类，错一道即属缺陷）：")
    for category in sorted(by_category):
        group = by_category[category]
        ok = sum(1 for r in group if r.passed)
        gate = "  ← 闸门" if category.startswith(("M", "N")) else ""
        print(f"  {category:<24} {ok:>3}/{len(group):<3} {100 * ok / len(group):>5.1f}%{gate}")

    by_difficulty: dict[str, list[Outcome]] = {}
    for r in results:
        by_difficulty.setdefault(r.difficulty, []).append(r)
    print("\n按难度：")
    for level in ("easy", "medium", "hard", "expert"):
        group = by_difficulty.get(level)
        if group:
            ok = sum(1 for r in group if r.passed)
            print(f"  {level:<8} {ok:>3}/{len(group):<3} {100 * ok / len(group):>5.1f}%")

    by_mode: dict[str, list[Outcome]] = {}
    for r in results:
        by_mode.setdefault(r.grade_mode, []).append(r)
    if set(by_mode) != {"fact"}:
        # Only meaningful for the 93-question set.  Reported separately because
        # the three modes are not interchangeable: a refusal miss means the agent
        # invented a number, which is a worse defect than a wrong count.
        print("\n按判定口径（93 问清单）：")
        labels = {"fact": "数据事实", "signal": "规则信号", "refusal": "暂不可答（拒答闸门）"}
        for mode in ("fact", "signal", "refusal"):
            group = by_mode.get(mode)
            if not group:
                continue
            ok = sum(1 for r in group if r.passed)
            gate = "  ← 闸门" if mode == "refusal" else ""
            label = labels[mode]
            print(f"  {label:<22} {ok:>3}/{len(group):<3} {100 * ok / len(group):>5.1f}%{gate}")

    by_kind: dict[str, list[Outcome]] = {}
    for r in results:
        by_kind.setdefault(r.kind, []).append(r)
    print("\n按答案形态：")
    for kind in sorted(by_kind):
        group = by_kind[kind]
        ok = sum(1 for r in group if r.passed)
        print(f"  {kind:<8} {ok:>3}/{len(group):<3} {100 * ok / len(group):>5.1f}%")

    latencies = sorted(r.elapsed for r in results)
    if latencies:
        mid = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95) - 1] if len(latencies) >= 20 else latencies[-1]
        over10 = sum(1 for x in latencies if x > 10)
        over30 = sum(1 for x in latencies if x > 30)
        print(f"\n单题耗时：中位 {mid:.1f}s  p95 {p95:.1f}s  最长 {latencies[-1]:.1f}s")
        print(f"  超 10s：{over10} 题；超 30s：{over30} 题（验收：简单 ≤10s、困难 ≤30s）")
        print("  注：本机 mock 库 1.1MB，此数不可外推到真实库")

    failures = [r for r in results if not r.passed]
    if failures:
        print(f"\n失败明细（{len(failures)} 题）：")
        for r in failures[:40]:
            print(f"  {r.qid:<10} {r.category[:18]:<18} {r.reason[:80]}")
        if len(failures) > 40:
            print(f"  …另有 {len(failures) - 40} 题，见 JSON 报告")


def _outcome_dict(r: Outcome) -> dict[str, Any]:
    return {
        "id": r.qid,
        "category": r.category,
        "difficulty": r.difficulty,
        "kind": r.kind,
        "passed": r.passed,
        "reason": r.reason,
        "elapsed": round(r.elapsed, 2),
        "rounds": r.rounds,
        "grade_mode": r.grade_mode,
        "tools": r.tools_used,
        "trace": r.trace,
    }


def _outcome_from_dict(d: dict[str, Any]) -> Outcome:
    return Outcome(
        qid=str(d["id"]),
        category=str(d.get("category", "")),
        difficulty=str(d.get("difficulty", "")),
        kind=str(d.get("kind", "")),
        passed=bool(d.get("passed", False)),
        reason=str(d.get("reason", "")),
        elapsed=float(d.get("elapsed", 0.0)),
        rounds=int(d.get("rounds", 0)),
        grade_mode=str(d.get("grade_mode", "fact")),
        tools_used=list(d.get("tools") or []),
        trace=list(d.get("trace") or []),
    )


async def run(args: argparse.Namespace) -> int:
    api_key = os.environ.get("BASELINE_API_KEY", "")
    if not api_key:
        print("BASELINE_API_KEY 未设置", file=sys.stderr)
        return 2
    answers_path = anyio.Path(args.answers)
    if not await answers_path.exists():
        print(f"测试集不存在：{answers_path}", file=sys.stderr)
        return 2

    report = anyio.Path(args.report)
    await report.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = anyio.Path(args.checkpoint)
    results: list[Outcome] = []
    if await checkpoint.exists():
        seen_ids: set[str] = set()
        for line in (await checkpoint.read_text(encoding="utf-8")).splitlines():
            if not line.strip():
                continue
            entry = _outcome_from_dict(json.loads(line))
            if entry.qid in seen_ids:
                # 同一题被追加两次(曾见过相邻重复行): 保留后写的那条, 避免报告里
                # 出现重复 id 把总数撑大。
                results = [r for r in results if r.qid != entry.qid]
            seen_ids.add(entry.qid)
            results.append(entry)
        print(f"断点续跑：已有 {len(results)} 题结果", file=sys.stderr)

    raw_lines = (await answers_path.read_text(encoding="utf-8")).splitlines()
    questions = [json.loads(line) for line in raw_lines if line.strip()]
    if args.category:
        wanted = {c.strip().upper() for c in args.category.split(",")}
        questions = [q for q in questions if q["category"][:1].upper() in wanted]
    if args.ids:
        # Run only the listed ids. Whether a fix worked is read off whether its
        # target questions recover across runs, not off the total: the same code
        # once flipped 28 questions in both directions between two full runs.
        picked = {q.strip().upper() for q in args.ids.split(",") if q.strip()}
        questions = [q for q in questions if q["id"].upper() in picked]
        missing = picked - {q["id"].upper() for q in questions}
        if missing:
            print(f"题号不存在：{', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        print("筛选后没有题目", file=sys.stderr)
        return 2

    done_ids = {r.qid for r in results}
    questions = [q for q in questions if q["id"] not in done_ids]
    if not questions:
        print("全部题目都已有结果，直接出报告", file=sys.stderr)
    else:
        print(f"待跑 {len(questions)} 题（跳过已完成 {len(done_ids)} 题）")

    upstream = Upstream(api_key, args.model, args.base_url)
    registry = await ToolRegistry.load(WORKSPACE / "tools", "baseline")
    schemas = tool_schemas(registry)
    system_prompt = await load_system_prompt()
    print(f"题目 {len(questions)} 道，工具 {len(schemas)} 个，模型 {args.model}，并发 {args.concurrency}")

    limiter = anyio.CapacityLimiter(args.concurrency)
    checkpoint_lock = anyio.Lock()
    started = time.monotonic()
    done = 0

    async def work(item: dict[str, Any]) -> None:
        nonlocal done
        async with limiter:
            begin = time.monotonic()
            trace: list[str] | None = [] if args.trace else None
            try:
                actual, rounds, used = await answer_question(
                    upstream, registry, schemas, system_prompt, item["question"], trace
                )
                spent = time.monotonic() - begin
                if rounds >= MAX_TOOL_ROUNDS and not actual:
                    ok, reason = False, "max_rounds：工具轮次用尽仍未给出回答"
                else:
                    ok, reason = await grade(
                        upstream,
                        item["question"],
                        item["gold_answer"],
                        actual,
                        rubric_for(item),
                        item.get("evidence", ""),
                    )
            except Exception as exc:
                spent = time.monotonic() - begin
                ok, reason, rounds, used = False, f"harness 异常：{type(exc).__name__}: {exc}"[:160], 0, []
            results.append(
                Outcome(
                    qid=item["id"],
                    category=item["category"],
                    difficulty=item["difficulty"],
                    kind=item["kind"],
                    passed=ok,
                    reason=reason,
                    elapsed=spent,
                    rounds=rounds,
                    grade_mode=rubric_for(item),
                    tools_used=used,
                    trace=trace or [],
                )
            )
            async with checkpoint_lock, await checkpoint.open("a", encoding="utf-8") as fh:
                await fh.write(json.dumps(_outcome_dict(results[-1]), ensure_ascii=False) + "\n")
            done += 1
            mark = "." if ok else "F"
            print(mark, end="", flush=True)
            if done % 50 == 0:
                print(f" {done}/{len(questions)}", flush=True)

    async with anyio.create_task_group() as group:
        for item in questions:
            group.start_soon(work, item)

    elapsed = time.monotonic() - started
    results.sort(key=lambda r: r.qid)
    summarise(results, elapsed)

    await report.write_text(
        json.dumps(
            {
                "model": args.model,
                "total": len(results),
                "passed": sum(1 for r in results if r.passed),
                "elapsed_seconds": round(elapsed, 1),
                "store": "本机 MySQL 8.4 + weekly_mock（演示数据）",
                "results": [_outcome_dict(r) for r in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n报告已写入 {report}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="396 题准确率基线")
    parser.add_argument("--answers", default=str(DEFAULT_ANSWERS))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    parser.add_argument("--category", default="", help="只跑指定大类，如 M,N")
    parser.add_argument("--ids", default="", help="只跑指定题号，如 K5-01,F2-02")
    parser.add_argument("--trace", action="store_true", help="报告里记下每次工具调用的实际入参")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--report", default="baseline-report.json")
    parser.add_argument("--checkpoint", default="", help="每题一行的断点文件；默认 <report>.ckpt")
    args = parser.parse_args()
    if not args.checkpoint:
        args.checkpoint = args.report + ".ckpt"
    if not os.environ.get("GUOSHU_WEEKLY_MCP_URL"):
        print("GUOSHU_WEEKLY_MCP_URL 未设置；先起 mock 服务", file=sys.stderr)
        return 2
    return anyio.run(run, args)


if __name__ == "__main__":
    raise SystemExit(main())

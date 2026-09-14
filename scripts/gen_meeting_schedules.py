"""把代码白名单 MEETING_JOBS 投影成 ``agents/feishu/schedules/<name>/TASK.md`` 种子文件。

**为什么需要这个生成器（而不是让两边各自维护）**

会议定时任务的 cron 有两个物理位置, 缺一不可:

1. **代码白名单** —— ``agents/feishu/tools/_meeting_automation.py`` 的
   ``_MEETING_JOBS_WHITELIST``。它是授权门 (name / meeting_code / cron / token_env
   的配对), 也是 ``meeting_schedule_files()`` 的事实源。
2. **agent 包的静态 TASK.md** —— ``agents/feishu/schedules/<name>/TASK.md``。

调度器**实际触发读的是第 2 个**: Gateway 启动时 ``SchedulerManager`` 把 agent 包里的
``schedules/*/TASK.md`` seed 进公司 workspace, 而
``_scheduler_manager._seed_missing_schedules`` 对**已存在的同名目录一律不覆盖**
(刻意的 —— 用户改过的口径、删掉的任务不许被 seed 回来)。

两个性质叠在一起就是本脚本存在的理由: **只改代码里的 cron, 线上会继续按旧的 TASK.md
跑, 且没有任何运行期信号**。2026-09-11 #914 把日会从 12:00 改到 13:00 时, 落包那一步
(静态 TASK.md) 已在同一批改动里被删除, 于是代码、静态文件、线上 workspace 三者口径各自
为政。本该拦住它的判据
(``tests/test_meeting_automation.py::test_committed_meeting_schedule_files_match_projection``)
因为「静态文件不存在就 pytest.skip」而永久跳过 —— 缺的正是本脚本这种**能把文件生成出来**
的来源。

所以: 本生成器让第 2 个位置**永远是第 1 个位置的投影**, 一致性判据则钉住这一点。

用法:
    python scripts/gen_meeting_schedules.py            # 生成 / 覆盖
    python scripts/gen_meeting_schedules.py --check     # 校验库内文件与投影一致 (CI 用)
"""

# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_ROOT = REPO_ROOT / "agents" / "feishu"
TOOLS_DIR = AGENT_ROOT / "tools"
SCHEDULES_DIR = AGENT_ROOT / "schedules"
MEETING_TOOL = "meeting_pipeline_run"


def _load_projection() -> dict[str, str]:
    """导入 agent 包的会议工具模块并取回 ``{调度目录名: TASK.md 内容}``。

    会议工具按设计从 agent 包内**按文件路径**加载 (不是已安装的包), 所以这里把
    ``agents/feishu/tools`` 插进 ``sys.path`` 再按顶层模块名 import —— 与
    ``tests/test_meeting_automation.py`` 走同一条路径, 两边必须一致。
    """
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    from _meeting_automation import meeting_schedule_files  # ty: ignore[unresolved-import]

    return meeting_schedule_files()


def _target(name: str) -> Path:
    return SCHEDULES_DIR / name / "TASK.md"


def _is_meeting_task(path: Path) -> bool:
    """库里的这份 TASK.md 是不是会议任务（用来发现白名单已删、落包没删的孤儿）。"""
    try:
        return f"tool: {MEETING_TOOL}" in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _collect_problems(files: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for name, expected in sorted(files.items()):
        path = _target(name)
        if not path.is_file():
            problems.append(f"缺失: {path.relative_to(REPO_ROOT)}")
            continue
        if path.read_text(encoding="utf-8") != expected:
            problems.append(f"与投影不一致: {path.relative_to(REPO_ROOT)}")
    # 反向: 库里有、投影里没有的会议任务 = 白名单删了但没人清理落包。
    # 这类孤儿正是「下线一条任务要两步」漏掉第二步的产物, 线上会继续按它触发。
    for path in sorted(SCHEDULES_DIR.glob("*/TASK.md")):
        if path.parent.name not in files and _is_meeting_task(path):
            problems.append(f"孤儿（投影里没有这场会议）: {path.relative_to(REPO_ROOT)}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验库内文件与 MEETING_JOBS 投影一致, 不写盘; 不一致则退出码 1 (CI 用)",
    )
    args = parser.parse_args()

    files = _load_projection()

    if args.check:
        problems = _collect_problems(files)
        if problems:
            print("会议 TASK.md 与 MEETING_JOBS 投影不一致:")
            for item in problems:
                print(f"  - {item}")
            print("\n跑 `python scripts/gen_meeting_schedules.py` 重新生成。")
            return 1
        print(f"OK: {len(files)} 份会议 TASK.md 与 MEETING_JOBS 投影一致")
        return 0

    for name, content in sorted(files.items()):
        path = _target(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 按字节写, 不用 Path.write_text: 它在 Windows 上以 newline=None 打开, 写盘时把
        # "\n" 翻成 "\r\n", 于是同一份投影在不同平台上产出不同的 blob —— 库里的 TASK.md
        # 会整文件显示成改动, 评审看到的 diff 全是行尾。
        # 注意这条**不会**让判据变红: read_text 走通用换行, 会把 \r\n 归一化回 \n, 两边
        # 照样相等 (实测过)。所以它是只在 diff 里现形的问题 —— 正因为没有红绿兜底, 更要
        # 在这里写死。
        path.write_bytes(content.encode("utf-8"))
        cron = next(
            (ln.split(":", 1)[1].strip() for ln in content.splitlines() if ln.startswith("cron:")),
            "?",
        )
        print(f"  {name:44s} cron={cron}  ->  {path.relative_to(REPO_ROOT)}")
    print(f"\n共生成 {len(files)} 份会议 TASK.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

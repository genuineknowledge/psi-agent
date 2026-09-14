"""写死 ``<workspace>/skills`` 的工具必须跟着内容分层走。

内容分层把技能挪出了 agent 包的 ``skills/``: 它们现在按 ``PSI_CONTENT_ROOTS`` 散在各层
里。工具侧凡是自己拼 ``.../"skills"/<名字>`` 的地方全部断链, 而**断法各不相同**, 这是这个
文件要一处一条判据的原因:

- ``meeting_pipeline_run`` 显式抛 ``会议 SOP skill 缺失`` —— 生产上实测已断(``meeting-sop``
  只在 ``/content/official/skills`` 下, 而没人给它补软链接)。
- ``flow_run`` 找不到 ``.env`` 时**静默**退回默认引擎 ``claude``, 每个 session 起错 CLI。
- ``run_flow`` 在 import 期就 ``ImportError``, 整个工具文件加载不了。
- ``rules`` 抛 ``unknown rule pack version``, 看着像调用方版本号写错。

生产当前是靠 4 条手补的软链接在撑, 补的人只补了自己撞见的那几个。软链接不是修复: 它没有
就近覆盖语义(企业层/用户层改不动官方规则), 且下次加技能还得有人记得补。

判据都落在"层里有、老落点没有"这个形状上 —— 只断言"找得到"会被 agent 包里那份真文件假绿。
"""

from __future__ import annotations

# PLC0415: 被测的工具模块只能在函数体里 import —— 它们靠本文件顶部那次 ``sys.path`` 插入才
# 找得到, 而 ``layers`` 夹具还要在 import 之后 monkeypatch agent 根。提到文件顶部会在插入
# 生效前解析, 直接 ImportError。
# ruff: noqa: PLC0415
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import anyio
import pytest

_REPO = Path(__file__).resolve().parents[3]
_TOOLS = _REPO / "agents" / "feishu" / "tools"

if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


@pytest.fixture
def layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """声明 ``official`` + ``enterprise`` 两层, 返回 ``(official, enterprise)``。

    声明序里越靠后越近, 所以 ``enterprise`` 是更近的一层 —— 覆盖测试靠这个方向。

    **agent 根一并挪到 tmp 下的空目录**: ``layers_for`` 按设计把 agent 根放在最近处, 而
    仓库里那个 agent 包真的装着 ``skills/meeting-sop``。不挪的话每条判据都会命中仓库里那份
    真文件而不是声明的层, 于是"分层生效"这件事根本没被测到(测下来确实如此: 断言等号才把它
    暴露出来, 只断言"找得到"会全绿)。挪开之后的形状也正是生产: ``/workspace/skills`` 下没有
    ``meeting-sop``, 它只在 ``/content/official/skills`` 里。
    """
    official = tmp_path / "official"
    enterprise = tmp_path / "enterprise"
    for root in (official, enterprise):
        (root / "skills").mkdir(parents=True)
    monkeypatch.setenv("PSI_CONTENT_ROOTS", f"official={official}{os.pathsep}enterprise={enterprise}")

    empty_agent = tmp_path / "agent"
    (empty_agent / "skills").mkdir(parents=True)
    import _content_layers
    import _runtime_paths

    monkeypatch.setattr(_runtime_paths, "agent_dir", lambda raw="": str(empty_agent))
    monkeypatch.setattr(_content_layers._paths, "resolve_agent", lambda raw="": anyio.Path(str(empty_agent)))
    return official, enterprise


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- meeting SOP


def test_sop_skill_found_in_declared_layer(layers: tuple[Path, Path]) -> None:
    """SOP 技能只在层里时也要找到 —— 生产上实测断的就是这条。"""
    import meeting_pipeline_run as mpr

    official, _ = layers
    want = _write(official / "skills" / "meeting-sop" / "weekday-alignment" / "SKILL.md", "官方引擎纪律")

    got = anyio.run(mpr._sop_skill_md, "meeting-sop/weekday-alignment")

    assert got is not None, "层里有 SKILL.md 却没找到 —— 断链还在"
    assert Path(str(got)) == want


def test_sop_skill_nearest_layer_wins(layers: tuple[Path, Path]) -> None:
    """两层都有时取更近的那层, 否则企业层的覆盖是假的。"""
    import meeting_pipeline_run as mpr

    official, enterprise = layers
    rel = "meeting-sop/weekday-alignment"
    _write(official / "skills" / rel / "SKILL.md", "官方")
    nearer = _write(enterprise / "skills" / rel / "SKILL.md", "企业覆盖")

    got = anyio.run(mpr._sop_skill_md, rel)

    assert got is not None
    assert Path(str(got)) == nearer
    assert anyio.run(got.read_text) == "企业覆盖"


def test_sop_skill_missing_everywhere_returns_none(layers: tuple[Path, Path]) -> None:
    """全层未命中返回 None, 由调用方抛显式错误 —— 不能静默降级。"""
    import meeting_pipeline_run as mpr

    assert anyio.run(mpr._sop_skill_md, "meeting-sop/does-not-exist") is None


def test_sop_error_message_lists_every_layer_searched(layers: tuple[Path, Path]) -> None:
    """报错要列出已查的每一层。

    只说"缺失"会让人去 agent 包里找那个不存在的落点; 生产上这条链断了之后, 排查要的正是
    "我到底查了哪几个目录"。
    """
    import meeting_pipeline_run as mpr

    official, enterprise = layers
    job = mpr.meeting_job_for(mpr.DAILY_MEETING_NAME, mpr.DAILY_MEETING_CODE)
    assert job.analysis_sop_skills, "夹具前提: 每日会议带 SOP 技能清单"

    with pytest.raises(RuntimeError, match="会议 SOP skill 缺失") as excinfo:
        anyio.run(mpr._load_analysis_rules, job)

    message = str(excinfo.value)
    for root in (official, enterprise):
        assert str(root / "skills") in message, f"报错没提到 {root} 这一层"


# ------------------------------------------------------------- rule packs


def test_rule_pack_loads_from_declared_layer(layers: tuple[Path, Path]) -> None:
    """规则包只在层里时也要读到。"""
    from _positive_negative_list.rules import load_rule_pack

    official, _ = layers
    src = _REPO / "agents" / "feishu" / "skills" / "positive-negative-list" / "6.0-shadow.yaml"
    _write(
        official / "skills" / "positive-negative-list" / "6.0-shadow.yaml",
        src.read_text(encoding="utf-8"),
    )

    pack = load_rule_pack("6.0-shadow")

    assert pack.entries, "层里的规则包读到了但是空的"


def test_rule_pack_nearest_layer_wins(layers: tuple[Path, Path]) -> None:
    """企业层能覆盖官方规则包 —— 只分层给模型看、执行侧不分层的话覆盖是假的。"""
    from _positive_negative_list.rules import load_rule_pack

    official, enterprise = layers
    src = (_REPO / "agents" / "feishu" / "skills" / "positive-negative-list" / "6.0-shadow.yaml").read_text(
        encoding="utf-8"
    )
    _write(official / "skills" / "positive-negative-list" / "6.0-shadow.yaml", src)
    _write(
        enterprise / "skills" / "positive-negative-list" / "6.0-shadow.yaml",
        src.replace('source: "SOP 6.0', 'source: "企业覆盖 SOP 6.0', 1),
    )

    pack = load_rule_pack("6.0-shadow")

    assert pack.source.startswith("企业覆盖"), f"取到的不是最近那层: {pack.source!r}"


def test_rule_pack_unknown_version_still_raises(layers: tuple[Path, Path]) -> None:
    """分层不能把"版本号写错"变成别的错误。"""
    from _positive_negative_list.rules import load_rule_pack

    with pytest.raises(ValueError, match="unknown rule pack version"):
        load_rule_pack("99.9-nope")


# ------------------------------------------------------------ fusion flow .env


def test_flow_dotenv_prefers_layer_over_upward_walk(layers: tuple[Path, Path], tmp_path: Path) -> None:
    """层里的 ``.env`` 要排在向上走之前。

    这条断了不会报错, 只会让运行时静默用默认引擎 ``claude`` —— 所以判据必须看**顺序**,
    不能只看"找到了"。
    """
    import flow_run

    official, enterprise = layers
    flow = _write(tmp_path / "flows" / "demo.flow.yaml")
    for root in (official, enterprise):
        _write(root / "skills" / "fusion-flow-legacy" / ".env", "PSI_ENGINE=psi")

    candidates = flow_run._flow_dotenv_candidates(flow)

    assert candidates[0] == enterprise / "skills" / "fusion-flow-legacy" / ".env", "最近层没排在第一位"
    assert candidates[1] == official / "skills" / "fusion-flow-legacy" / ".env"


def test_flow_dotenv_keeps_upward_walk(layers: tuple[Path, Path], tmp_path: Path) -> None:
    """向上走这条路要保留: flow 躺在临时目录、层里没有它那份 ``.env`` 时靠它。"""
    import flow_run

    workspace = tmp_path / "ws"
    flow = _write(workspace / "flows" / "demo.flow.yaml")
    beside = _write(workspace / "skills" / "fusion-flow-legacy" / ".env", "PSI_ENGINE=psi")

    candidates = flow_run._flow_dotenv_candidates(flow)

    assert beside in candidates, "向上走那段被删了 —— flow 旁边的 .env 再也读不到"


def test_flow_env_reads_layer_dotenv(layers: tuple[Path, Path], tmp_path: Path) -> None:
    """端到端: 层里的 ``.env`` 真的进了子进程环境。

    ``_flow_dotenv_candidates`` 单独绿不代表 ``_load_flow_env`` 用了它 —— 这条把两者接上。
    """
    import flow_run

    _, enterprise = layers
    flow = _write(tmp_path / "flows" / "demo.flow.yaml")
    _write(enterprise / "skills" / "fusion-flow-legacy" / ".env", "PSI_FLOW_ENGINE=psi\n")

    env = flow_run._load_flow_env(flow)

    assert env.get("PSI_FLOW_ENGINE") == "psi"


# --------------------------------------------------------- tencent meeting script


def test_tencent_script_found_in_declared_layer(layers: tuple[Path, Path]) -> None:
    """入口脚本只在层里时也要找到 —— 断链时这个工具返回 "skill entrypoint not found"。"""
    import tencent_meeting

    official, _ = layers
    want = _write(official / "skills" / "tencent-meeting-mcp" / "scripts" / "tencent_meeting.py", "# entrypoint")

    got = anyio.run(tencent_meeting._skill_script)

    assert got == want


def test_tencent_script_nearest_layer_wins(layers: tuple[Path, Path]) -> None:
    """两层都有时取更近的那层。"""
    import tencent_meeting

    official, enterprise = layers
    rel = Path("tencent-meeting-mcp") / "scripts" / "tencent_meeting.py"
    _write(official / "skills" / rel, "# 官方")
    nearer = _write(enterprise / "skills" / rel, "# 企业覆盖")

    assert anyio.run(tencent_meeting._skill_script) == nearer


def test_tencent_script_falls_back_to_legacy_path(layers: tuple[Path, Path]) -> None:
    """全层未命中时返回老落点, 让报错里给出人认得的那个路径。"""
    import tencent_meeting

    assert anyio.run(tencent_meeting._skill_script) == tencent_meeting._LEGACY_SCRIPT


def test_tencent_call_keeps_timeout_protection() -> None:
    """超时保护必须还在。

    生产上那份 ``tencent_meeting.py`` 自己加了跨层解析, 但**把 PR #859 的
    ``anyio.fail_after`` 删掉了** —— 这也是这处修复走仓库、而不是直接采用生产那份文件的原因。
    判据盯着"调用路径上仍有超时上限"这件事, 否则下次谁再从生产往回搬就又把它带没了。
    """
    import inspect

    import tencent_meeting

    source = inspect.getsource(tencent_meeting)
    assert "fail_after" in source, "anyio.fail_after 不见了 —— 子进程调用又变成可以无限挂住"
    assert tencent_meeting.DEFAULT_CALL_TIMEOUT > 0, "超时上限必须是正数"


# ------------------------------------------------------- run_flow import-time path


def test_run_flow_puts_layer_workflow_dir_on_syspath(layers: tuple[Path, Path], tmp_path: Path) -> None:
    """``run_flow`` 的 ``sys.path`` 插入要认层。

    这一处是 **import 期**行为, 且 ``fusion_flow`` 一旦进 ``sys.modules`` 就定死, 所以只能
    在**全新解释器**里量 —— 在本进程里 import 会拿到已经加载好的那份, 判据恒绿。
    """
    official, enterprise = layers
    for root in (official, enterprise):
        (root / "skills" / "workflow").mkdir(parents=True)

    probe = textwrap.dedent("""
        import json, sys
        sys.path.insert(0, r"%s")
        import run_flow
        print(json.dumps([str(p) for p in run_flow._skill_dirs()]))
    """) % str(_TOOLS)

    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(_REPO / "src")},
        cwd=str(_REPO),
        timeout=180,
    )
    assert result.returncode == 0, f"探针进程失败:\n{result.stderr}"

    import json

    dirs = [Path(p) for p in json.loads(result.stdout.splitlines()[-1])]
    assert dirs[0] == enterprise / "skills" / "workflow", f"最近层没排第一: {dirs}"
    assert dirs[1] == official / "skills" / "workflow"
    assert dirs[-1] == _TOOLS.parent / "skills" / "workflow", "老落点没兜在最后"

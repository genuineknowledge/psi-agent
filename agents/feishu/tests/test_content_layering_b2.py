"""B-2 判据 -- 改只读层 = 派生, 删除 = 墓碑, 群会话拒写。

B-1 让四类内容**读**得跨层, B-2 让**写**跟上。这组判据要拦的失败形状有四个, 每个都
静默:

1. **写落到只读层** -- B-3 把 ``/official`` 挂成 ``ro`` 之后直接 ``OSError``。今天在
   开发机上不报错, 所以判据必须自己造出真只读目录来问, 见 ``_readonly`` 的注释:
   ``chmod 0o500`` 在 Windows 上**拦不住**建文件(本卡实测), 用它当只读模拟的判据会
   恒绿。
2. **派生写了但没生效, 或生效了但原件被改** -- 两个方向都是"看起来对了"。故每条派生
   判据都要**三验**: 副本出现/原件逐字节不变/索引里生效的是副本。少验一条就有一个
   方向漏出去。
3. **删除静默失效** -- 真删可写层那份而下层同名的立刻复活。这不是假想: 本卡实现过程
   中先写出的版本(按"生效那份在不在可写层"判)就是这个行为, ``test_delete_after_derive``
   是它的回归判据。
4. **群里改** -- 群是多人共用一个 workspace, 落盘的那个版本里用户以为改的是自己的/
   实际改的是全群的。判据不只看返回值: 还要 walk 整棵目录树断言**一个文件都没多**。

判据必须落在它声称的那一层: 墓碑那几条断言的是 ``system._build_skills_index``(提示词
真正渲染的那个函数)与 ``_feishu_spec.load_rules_layered``(真实 API 调用执行的那份规则),
不是只调 ``skill_manage(action="list")`` -- 后者是第三个消费者, 它同意不代表前两个同意。
"""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

SYSTEMS_DIR = Path(__file__).resolve().parents[1] / "systems"
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

_skill: Any = importlib.import_module("skill_manage")
_trigger: Any = importlib.import_module("trigger_manage")
_layers: Any = importlib.import_module("_content_layers")
_spec: Any = importlib.import_module("_feishu_spec")
_system: Any = importlib.import_module("system")

from psi_agent.session.content_roots import CONTENT_ROOTS_ENV, ContentRoot  # noqa: E402
from psi_agent.session.runtime_context import path_scope, runtime_scope  # noqa: E402
from psi_agent.session.trigger_registry import TriggerRegistry  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clear_rule_cache() -> Iterator[None]:
    """护栏缓存按阶梯做键, 用例之间必须清 -- 否则上一条的阶梯喂给下一条。"""
    _spec.reset_cache()
    yield
    _spec.reset_cache()


def _md5(path: Path) -> str:
    """原始字节的 md5 -- 不做 LF 归一化。

    判据要证的是"原件**一个字节都没动**", 而归一化会把"某个环节顺手改了行尾"这种真实
    的写入放过去。
    """
    return hashlib.md5(path.read_bytes()).hexdigest()


def _tree(root: Path) -> set[str]:
    """*root* 下所有文件的相对路径 -- 群会话判据用它断言"一个文件都没多"。

    走全树而非顶层 glob: 写操作落的是 ``<层>/skills/<名>/SKILL.md``, 顶层 glob 看不到
    第三层, 于是"没多东西"会假绿。
    """
    if not root.exists():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "official one",
    body: str = "official body",
    editable: bool = True,
    rules: str = "",
    created_at: str = "2026-01-01T00:00:00Z",
) -> Path:
    """``<root>/skills/<name>/SKILL.md``; 返回该文件路径。"""
    target = root / "skills" / name
    target.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}", f"created_at: {created_at}"]
    if editable:
        lines.append("agent_editable: true")
    lines.append("---")
    text = "\n".join(lines) + f"\n\n{body}\n"
    if rules:
        text += f"\n```rules\n{rules}\n```\n"
    path = target / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


class _ReadOnlyDir:
    """真只读目录 -- 生产上是 ``ro`` 挂载, 判据里按平台各用一种等价手段。

    **为什么不用 ``os.chmod(dir, 0o500)``**: 本卡实测, Windows 上它**拦不住**在该目录里
    建文件(``chmod`` 只映射到文件的只读属性, 对目录几乎无效)。拿它当只读模拟, 这一组
    "只读层不得抛 OSError"的判据会在开发机上恒绿 -- 而它们要拦的正是生产 ``ro`` 挂载
    下的失败。一个恒绿的判据比没有判据更坏。

    Windows: ``icacls /deny <user>:(WD,AD,DE,DC) /T``。这四个权限位是本卡试出来的最小
    集合 -- 更粗的 ``(W,AD,WD,DC)`` 会连 ``iterdir`` 一起拦掉(实测 ``PermissionError``),
    而分层查找必须**能读**只读层, 否则测的就不是"写被拒"而是"整层不可达"了。

    POSIX: ``chmod 0o500``, 在那儿它对目录是真生效的。

    ``supported`` 为假时用例 skip 而不是假装通过: 判不出来就明说判不出来。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._user = os.environ.get("USERNAME") or ""
        self._applied = False

    @property
    def supported(self) -> bool:
        return os.name != "nt" or bool(self._user)

    def __enter__(self) -> Path:
        if os.name == "nt":
            subprocess.run(
                ["icacls", str(self.path), "/deny", f"{self._user}:(WD,AD,DE,DC)", "/T"],
                capture_output=True,
                check=False,
            )
        else:
            os.chmod(self.path, 0o500)
        self._applied = True
        self._assert_really_readonly()
        return self.path

    def _assert_really_readonly(self) -> None:
        """自检: 这个目录**真的**写不进去。

        没有这一步, "只读层拒绝写入"这条判据就建立在一个未经验证的前提上 -- 而那个前提
        在 Windows 上按最直觉的写法(``chmod``)恰好是假的。
        """
        probe = self.path / ".readonly-selfcheck"
        try:
            probe.write_text("x", encoding="utf-8")
        except OSError:
            return
        probe.unlink(missing_ok=True)
        raise AssertionError(f"{self.path} 仍可写, 只读模拟失效 -- 本组判据的前提不成立")

    def __exit__(self, *exc: object) -> None:
        if not self._applied:
            return
        if os.name == "nt":
            subprocess.run(
                ["icacls", str(self.path), "/remove:d", self._user, "/T"],
                capture_output=True,
                check=False,
            )
        else:
            os.chmod(self.path, 0o700)


def _write_trigger(root: Path, name: str, *, event: str = "feishu.chat.member_added") -> Path:
    """``<root>/triggers/<name>/TRIGGER.md``; 返回该文件路径。"""
    target = root / "triggers" / name
    target.mkdir(parents=True, exist_ok=True)
    path = target / "TRIGGER.md"
    path.write_text(
        f'---\nname: {name}\nevent: {event}\nfilter: {{"chat_id": "oc_1"}}\nfire: prompt\n---\n\nbody\n',
        encoding="utf-8",
    )
    return path


def _declare(monkeypatch: pytest.MonkeyPatch, *roots: tuple[str, Path]) -> None:
    """设 ``PSI_CONTENT_ROOTS``(先声明的最远)。"""
    monkeypatch.setenv(CONTENT_ROOTS_ENV, os.pathsep.join(f"{name}={path}" for name, path in roots))


async def _index_names(monkeypatch: pytest.MonkeyPatch, agent: Path) -> set[str]:
    """提示词索引(消费者 1)里**生效**的 skill 名字集合。

    刻意走 ``system._build_skills_index`` 而不是 ``skill_manage(action="list")``: 墓碑判据
    要证的是"模型看不见它了", 而 ``list`` 是另一个消费者。两者共用一份约定却各有一份实现,
    只测其一时另一处静默失效 -- B-1 已经踩过这个形状。
    """
    # global 根指向不存在的目录, 免得开发机上真的 ~/.agent/skills 混进来。
    monkeypatch.setattr(_system, "_GLOBAL_AGENT_SKILLS_DIR", anyio.Path(str(agent.parent / "no-global")))
    xml = await _system._build_skills_index(anyio.Path(str(agent)))
    return set(re.findall(r'<skill name="([^"]+)"', xml))


def _rule_sources(*roots: tuple[str, Path]) -> set[str]:
    """护栏(消费者 2)里生效规则的来源 skill 名集合。"""
    ladder = [(name, str(path / "skills")) for name, path in roots]
    return {rule.source for rule in _spec.load_rules_layered(ladder)}


_DEMO_RULE = "- endpoint: GET /open-apis/demo/list\n  fields:\n    page_size: {max: 10}"


class TestReadOnlyLayerNeverRaises:
    """判据 1 -- 只读层真的挂只读时, 三个写动作都报错而**不抛 ``OSError``**。

    生产上 ``/official`` 与 ``/enterprise`` 是 ``ro`` 挂载。``OSError`` 冒到工具返回值里
    表现为一段 traceback, 模型读不出"这层只读, 去可写层"这个意思, 于是会原地重试。
    这一组把唯一可写的那层也设成只读, 逼出"全都不可写"这条路径。
    """

    @pytest.fixture
    def readonly_only_layer(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
        """两层都只读 -- 逼出"全都不可写"这条路径。

        产出的是**实测快照**而不是手写的预期文件清单: 手写清单跟不上 fixture 变化时,
        判据会用一句自信的错话指向错方向(本卡第一次跑就是这样红的 -- 清单漏了 trigger)。
        """
        official = tmp_path / "official"
        _write_skill(official, "off-skill")
        _write_trigger(official, "off-trigger")
        agent = tmp_path / "agent"
        (agent / "skills").mkdir(parents=True)
        (agent / "triggers").mkdir(parents=True)
        _declare(monkeypatch, ("official", official))
        guard = _ReadOnlyDir(agent)
        if not guard.supported:
            pytest.skip("no USERNAME for icacls - 判不出来就明说, 不假装通过")
        with (
            guard,
            _ReadOnlyDir(official) as ro_official,
            path_scope(workspace=str(tmp_path / "ws"), agent=str(agent)),
        ):
            yield ro_official

    async def test_create_reports_instead_of_raising(self, readonly_only_layer: Path) -> None:
        """``create``: 返回 ``[Error] No writable ...``, 不抛。"""
        try:
            got = await _skill.skill_manage(action="create", skill_name="brand-new", content="x")
        except OSError as exc:  # pragma: no cover - 这就是本条要拦的失败
            pytest.fail(f"create 抛了 OSError 而不是返回错误文案: {exc!r}")
        assert got.startswith("[Error] No writable skills layer"), got
        # 用 any(...) 而不是 ``"brand-new" not in _tree(...)``: 后者比的是集合成员(整条相对
        # 路径), 对"某个文件名里含这个词"恒为真, 判据会恒绿。
        landed = [entry for entry in _tree(readonly_only_layer) if "brand-new" in entry]
        assert landed == [], f"只读层里落下了文件: {landed}"

    async def test_patch_reports_instead_of_raising(self, readonly_only_layer: Path) -> None:
        """``patch`` 只读层里的 skill: 报错, 且原件字节不变。"""
        original = readonly_only_layer / "skills" / "off-skill" / "SKILL.md"
        before = _md5(original)
        try:
            got = await _skill.skill_manage(action="patch", skill_name="off-skill", content="mine")
        except OSError as exc:  # pragma: no cover
            pytest.fail(f"patch 抛了 OSError: {exc!r}")
        assert got.startswith("[Error] No writable skills layer"), got
        assert _md5(original) == before, "只读层原件被改了"

    async def test_delete_reports_instead_of_raising(self, readonly_only_layer: Path) -> None:
        """``delete``: 报错, 且只读层那份还在(没被真删)。"""
        original = readonly_only_layer / "skills" / "off-skill" / "SKILL.md"
        try:
            got = await _skill.skill_manage(action="delete", skill_name="off-skill")
        except OSError as exc:  # pragma: no cover
            pytest.fail(f"delete 抛了 OSError: {exc!r}")
        assert got.startswith("[Error] No writable skills layer"), got
        assert original.exists(), "只读层的 SKILL.md 被删了"

    async def test_trigger_delete_reports_instead_of_raising(self, readonly_only_layer: Path) -> None:
        """trigger 侧同形 -- 两个工具各有一份写路径, 只测 skills 会漏掉另一份。"""
        original = readonly_only_layer / "triggers" / "off-trigger" / "TRIGGER.md"
        try:
            got = await _trigger.trigger_manage(action="delete", trigger_name="off-trigger")
        except OSError as exc:  # pragma: no cover
            pytest.fail(f"trigger delete 抛了 OSError: {exc!r}")
        assert got.startswith("[Error] No writable triggers layer"), got
        assert original.exists()


class TestPatchDerivesFromReadOnlyLayer:
    """判据 2 -- 改官方 = 派生。三件事必须**同时**成立。

    只验一条就分不清两种失败: 副本出现了但索引里生效的还是官方那份("写了但没生效"),
    或索引换成副本了但官方原件也被改了("生效了但原件被改")。后者在生产上是整版替换时
    的静默丢失, 因为 ``/official`` 的下次投放会把它盖掉且无人知道曾经有过改动。
    """

    @pytest.fixture
    def layered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
        """生产形状: agent 根**就是** ``/users/<id>``(B-3 把它挂成 workspace 根)。

        两者同为一个目录, 所以层梯子只有 ``users`` 与 ``official`` 两级, 且写落在 ``users``。
        把 agent 根另设成第三个目录不是生产形状 -- 那样写会落进 agent 根(它最近),
        ``users`` 里空空如也。这个区别由 ``test_agent_root_wins_when_it_is_a_separate_dir``
        单独钉住, 免得两种形状混在一起时看不出是哪条规则在起作用。
        """
        official = tmp_path / "official"
        users = tmp_path / "users"
        _write_skill(official, "off-skill", rules=_DEMO_RULE)
        (users / "skills").mkdir(parents=True)
        _declare(monkeypatch, ("official", official), ("users", users))
        with path_scope(workspace=str(users), agent=str(users)):
            yield {"official": official, "users": users, "agent": users}

    async def test_writable_layer_is_the_users_root(self, layered: dict[str, Path]) -> None:
        """可写层是 ``users`` 且层名用的是**声明的名字**, 不是目录名或 ``agent``。

        层名会写进副本的 ``derived_from_layer``/也进 ``layer_id``。报成 ``agent`` 时,
        溯源字段指向一个部署声明里不存在的层。
        """
        target, why = await _layers.writable_layer("skills")
        assert target is not None, why
        assert target.name == "users"
        assert Path(str(target.path)) == layered["users"] / "skills"

    async def test_agent_root_wins_when_it_is_a_separate_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """agent 根与声明的根不是同一目录时, 写落 agent 根(它最近)。

        这与读侧同序(B-1 的 ``_skills_ladder`` 也把 agent 根放最近)。若写侧改成偏向
        声明的根, 写会落到一个**比 agent 根更远**的层, 而 agent 根里的旧同名件仍按
        nearest-wins 生效 -- 表现为"改了但没生效", 且返回成功。
        """
        official, users, agent = tmp_path / "official", tmp_path / "users", tmp_path / "agent"
        _write_skill(official, "off-skill")
        (users / "skills").mkdir(parents=True)
        _declare(monkeypatch, ("official", official), ("users", users))

        with path_scope(workspace=str(tmp_path / "ws"), agent=str(agent)):
            target, why = await _layers.writable_layer("skills")

        assert target is not None, why
        assert Path(str(target.path)) == agent / "skills"

    async def test_patch_lands_in_writable_layer(self, layered: dict[str, Path]) -> None:
        """(a) 副本出现在可写层, 带 ``derived_from`` 溯源三兄弟。"""
        got = await _skill.skill_manage(action="patch", skill_name="off-skill", content="my body")
        assert "derived into layer" in got, got

        copy = layered["users"] / "skills" / "off-skill" / "SKILL.md"
        assert copy.exists(), f"可写层没有副本; users 树: {_tree(layered['users'])}"
        text = copy.read_text(encoding="utf-8")
        assert "my body" in text
        assert "derived_from: off-skill" in text
        assert "derived_from_layer: official" in text
        assert "derived_from_version: 2026-01-01T00:00:00Z" in text

    async def test_readonly_original_stays_byte_identical(self, layered: dict[str, Path]) -> None:
        """(b) 只读层原件的 md5 一个字节都没变。"""
        original = layered["official"] / "skills" / "off-skill" / "SKILL.md"
        before = _md5(original)
        await _skill.skill_manage(action="patch", skill_name="off-skill", content="my body")
        assert _md5(original) == before

    async def test_effective_copy_is_the_derived_one(
        self, layered: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(c) 索引里生效的是副本 -- 两个消费者各查一次。

        ``view`` 走工具自己的分层查找, ``_build_skills_index`` 走提示词那一份。两处实现独立,
        只查其一时另一处可能仍在读官方那份, 而模型看到的正是后者。
        """
        await _skill.skill_manage(action="patch", skill_name="off-skill", content="my body")

        viewed = await _skill.skill_manage(action="view", skill_name="off-skill")
        assert "my body" in viewed
        assert "official body" not in viewed

        names = await _index_names(monkeypatch, layered["agent"])
        assert "off-skill" in names
        rendered = await _skill.skill_manage(action="list")
        assert "derived from official" in rendered, rendered

    async def test_patch_keeps_rules_from_the_derived_copy(self, layered: dict[str, Path]) -> None:
        """护栏(消费者 2)也跟着换成副本 -- 副本没写 rules 块时规则就应当消失。

        这条钉的是"派生后护栏还在按官方那份执行": 那会拒掉一个模型在任何地方都读不到理由的
        调用。断言 ``load_rules_layered`` 的返回而不是索引文本, 是因为护栏是它说了算。
        """
        assert _rule_sources(("official", layered["official"]), ("users", layered["users"])) == {"off-skill"}
        _spec.reset_cache()

        await _skill.skill_manage(action="patch", skill_name="off-skill", content="my body without rules")

        _spec.reset_cache()
        assert _rule_sources(("official", layered["official"]), ("users", layered["users"])) == set()


class TestDeleteWritesTombstone:
    """判据 3 -- 删除 = 墓碑。只读层文件仍在, 而这个 skill 从索引里消失。

    三个读侧消费者各查一次: 提示词索引/飞书护栏/trigger 注册表。它们共用
    ``content_tombstone`` 那一份约定但各有一份实现 -- 少接一处的表现是"删了还在用",
    且删除动作本身返回成功。
    """

    @pytest.fixture
    def layered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
        # agent 根即 ``/users/<id>``, 同 ``TestPatchDerivesFromReadOnlyLayer.layered``。
        official = tmp_path / "official"
        users = tmp_path / "users"
        _write_skill(official, "off-skill", rules=_DEMO_RULE)
        _write_trigger(official, "off-trigger")
        (users / "skills").mkdir(parents=True)
        (users / "triggers").mkdir(parents=True)
        _declare(monkeypatch, ("official", official), ("users", users))
        with path_scope(workspace=str(users), agent=str(users)):
            yield {"official": official, "users": users, "agent": users}

    async def test_readonly_file_survives_delete(self, layered: dict[str, Path]) -> None:
        """只读层那份还在, 且字节没变; 墓碑落在可写层。"""
        original = layered["official"] / "skills" / "off-skill" / "SKILL.md"
        before = _md5(original)

        got = await _skill.skill_manage(action="delete", skill_name="off-skill")

        assert "tombstoned in layer 'users'" in got, got
        assert original.exists() and _md5(original) == before
        tombstone = layered["users"] / "skills" / "off-skill" / "SKILL.md"
        assert tombstone.exists()
        assert "psi_deleted: true" in tombstone.read_text(encoding="utf-8")

    async def test_skill_gone_from_prompt_index(
        self, layered: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """消费者 1: 墓碑之后模型的技能索引里没有它。"""
        assert "off-skill" in await _index_names(monkeypatch, layered["agent"])
        await _skill.skill_manage(action="delete", skill_name="off-skill")
        assert "off-skill" not in await _index_names(monkeypatch, layered["agent"])

    async def test_rules_gone_from_guardrail(self, layered: dict[str, Path]) -> None:
        """消费者 2: 墓碑之后它的护栏规则不再执行。

        规则按 **source skill** 撤而不是按 endpoint 撤 -- 一个 skill 能写多条规则,
        按 endpoint 撤会漏掉其余几条, 表现为"删了的 skill 还在拦调用"。
        """
        ladder = (("official", layered["official"]), ("users", layered["users"]))
        assert _rule_sources(*ladder) == {"off-skill"}
        _spec.reset_cache()

        await _skill.skill_manage(action="delete", skill_name="off-skill")

        _spec.reset_cache()
        assert _rule_sources(*ladder) == set()

    async def test_tombstoned_trigger_stops_firing(self, layered: dict[str, Path]) -> None:
        """消费者 3: trigger 注册表不再列出它, 而只读层的 TRIGGER.md 仍在。

        走 ``TriggerRegistry`` 而不是 ``trigger_manage(action="list")``: 真正决定"会不会被
        事件触发"的是注册表。墓碑刻意不带 ``event``, 注册表必须在 ``event`` 检查**之前**认出
        它, 否则会被当成坏文件跳过/压根不进 ``_files``, 于是盖不住下层那份。
        """
        original = layered["official"] / "triggers" / "off-trigger" / "TRIGGER.md"

        got = await _trigger.trigger_manage(action="delete", trigger_name="off-trigger")
        assert "tombstoned in layer 'users'" in got, got
        assert original.exists()

        roots = [
            ContentRoot(name="official", path=layered["official"], priority=10),
            ContentRoot(name="users", path=layered["users"], priority=20),
        ]
        registry = await TriggerRegistry.load_content_roots(roots)
        assert [t.name for t in registry.triggers] == []

    async def test_delete_is_idempotent_and_does_not_resurrect(self, layered: dict[str, Path]) -> None:
        """派生后再删除**不会**让官方那份复活 -- 本卡实测到过的回归。

        曾用"生效那份是否在可写层"决定真删还是墓碑。派生副本正好在可写层, 于是真删副本,
        官方那份立刻按 nearest-wins 重新生效: 用户看到"删除成功", 而 skill 还在。
        判据落在 ``list`` 的最终结果上, 因为复活恰恰表现为返回值成功而结果不对。
        """
        await _skill.skill_manage(action="patch", skill_name="off-skill", content="mine")
        await _skill.skill_manage(action="delete", skill_name="off-skill")

        assert await _skill.skill_manage(action="list") == "No skills found."

    async def test_delete_own_skill_really_removes_it(self, layered: dict[str, Path]) -> None:
        """可写层独有的 skill 走真删, 不留墓碑。

        留了墓碑就再也 create 不出同名的了(存在性按跨层判), 而这个 skill 下面本来无物可盖。
        """
        await _skill.skill_manage(action="create", skill_name="mine-only", content="body")
        assert (layered["users"] / "skills" / "mine-only" / "SKILL.md").exists()

        got = await _skill.skill_manage(action="delete", skill_name="mine-only")

        assert got == "Skill deleted: 'mine-only'"
        assert not (layered["users"] / "skills" / "mine-only").exists()


class TestGroupSessionCannotWrite:
    """判据 4 -- 群会话写操作明确报错, **且一个文件都没多出来**。

    只看返回值不够: "静默落到别处"的形状正是返回一句成功/文件落在某个共享层里。群里
    每个人都能读到别人的改动, 所以落错位置是个跨用户的污染, 而不只是个位置错误。
    故每条都把整棵树在动作前后各走一遍(``rglob`` 全树, 顶层 glob 看不到第三层)。
    """

    @pytest.fixture
    def group_layered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
        official = tmp_path / "official"
        users = tmp_path / "users"
        _write_skill(official, "off-skill")
        _write_trigger(official, "off-trigger")
        (users / "skills").mkdir(parents=True)
        (users / "triggers").mkdir(parents=True)
        _declare(monkeypatch, ("official", official), ("users", users))
        with runtime_scope(session_id="feishu-chat-oc_abc", workspace=str(users), agent=str(users)):
            yield {"official": official, "users": users, "agent": users, "root": tmp_path}

    @staticmethod
    def _assert_no_new_files(before: set[str], root: Path) -> None:
        after = _tree(root)
        assert after == before, f"群会话写操作落下了文件: {sorted(after - before)}"

    async def test_create_refused_and_nothing_written(self, group_layered: dict[str, Path]) -> None:
        before = _tree(group_layered["root"])
        got = await _skill.skill_manage(action="create", skill_name="from-group", content="x")
        # 带 ``[Error]`` 前缀 -- 与其它错误同形, 否则模型会把它读成一句普通说明而继续往下走。
        assert got == "[Error] 群内不支持改 skill, 请在私聊里改。群会话只读官方与企业层内容。", got
        self._assert_no_new_files(before, group_layered["root"])

    async def test_patch_refused_and_nothing_written(self, group_layered: dict[str, Path]) -> None:
        before = _tree(group_layered["root"])
        original = group_layered["official"] / "skills" / "off-skill" / "SKILL.md"
        before_md5 = _md5(original)

        got = await _skill.skill_manage(action="patch", skill_name="off-skill", content="x")

        assert got.startswith("[Error] 群内不支持改 skill"), got
        self._assert_no_new_files(before, group_layered["root"])
        assert _md5(original) == before_md5

    async def test_delete_refused_and_nothing_written(self, group_layered: dict[str, Path]) -> None:
        before = _tree(group_layered["root"])
        got = await _skill.skill_manage(action="delete", skill_name="off-skill")
        assert got.startswith("[Error] 群内不支持改 skill"), got
        self._assert_no_new_files(before, group_layered["root"])
        assert (group_layered["official"] / "skills" / "off-skill" / "SKILL.md").exists()

    async def test_trigger_writes_refused(self, group_layered: dict[str, Path]) -> None:
        """trigger 侧同形, 文案里的 kind 换成 ``trigger``。"""
        before = _tree(group_layered["root"])

        created = await _trigger.trigger_manage(
            action="create",
            trigger_name="from-group",
            event="feishu.chat.member_added",
            filter='{"chat_id": "oc_1"}',
            # ``raw_filter`` 在 ``raw_event`` 能自动补出来时是必填的(空 raw_filter 会让 raw
            # 回退路径比收窄后的常规路径更宽 -- 2026-09-02 生产事故)。
            raw_filter='{"chat_id": "oc_1"}',
            fire="prompt",
        )
        deleted = await _trigger.trigger_manage(action="delete", trigger_name="off-trigger")

        assert created.startswith("[Error] 群内不支持改 trigger"), created
        assert deleted.startswith("[Error] 群内不支持改 trigger"), deleted
        self._assert_no_new_files(before, group_layered["root"])

    async def test_reads_still_work_in_group(self, group_layered: dict[str, Path]) -> None:
        """群里**读**不受影响 -- 只拦写, 不减功能(设计里群只读官方与企业层)。"""
        assert "off-skill" in await _skill.skill_manage(action="list")
        assert "official body" in await _skill.skill_manage(action="view", skill_name="off-skill")

    async def test_private_chat_session_is_not_treated_as_group(self, group_layered: dict[str, Path]) -> None:
        """私聊 session_id 不能被误判成群。

        判据是 ``feishu-chat-`` 前缀; 私聊 id 形如 ``feishu-ou_xxx``。这条钉住"把所有人都
        当成群"这个方向的失败 -- 那会让私聊里也改不了, 而返回文案却让人去私聊改。
        """
        with runtime_scope(
            session_id="feishu-ou_person",
            workspace=str(group_layered["root"] / "ws"),
            agent=str(group_layered["agent"]),
        ):
            got = await _skill.skill_manage(action="create", skill_name="in-private", content="x")

        assert got == "Skill created: 'in-private'", got
        assert (group_layered["users"] / "skills" / "in-private" / "SKILL.md").exists()


class TestSingleRootUnchanged:
    """判据 5 -- 未声明 ``PSI_CONTENT_ROOTS`` 时行为与改动前完全一致。

    ToC 装机版走的就是这条路径。分层是 ToB 的需求, 若它顺手改掉了单根世界的落盘位置,
    受损的是另一条产品线, 且那边没有任何判据会红。
    """

    @pytest.fixture
    def single(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
        agent = tmp_path / "agent"
        (agent / "skills").mkdir(parents=True)
        (agent / "triggers").mkdir(parents=True)
        monkeypatch.delenv(CONTENT_ROOTS_ENV, raising=False)
        with path_scope(workspace=str(tmp_path / "ws"), agent=str(agent)):
            yield agent

    async def test_single_layer_ladder_is_the_agent_root(self, single: Path) -> None:
        """层梯子只有一级, 就是 ``resolve_agent()/skills``。"""
        layers = _layers.layers_for("skills")
        assert len(layers) == 1
        assert Path(str(layers[0].path)) == single / "skills"

    async def test_create_lands_in_agent_root(self, single: Path) -> None:
        got = await _skill.skill_manage(action="create", skill_name="local-skill", content="body")
        assert got == "Skill created: 'local-skill'"
        assert (single / "skills" / "local-skill" / "SKILL.md").exists()

    async def test_patch_edits_in_place_without_deriving(self, single: Path) -> None:
        """单根下 ``patch`` 就地改, **不**写 ``derived_from``。

        写了溯源字段就是把 ToB 的概念漏进 ToC: ``list`` 会给每个本地 skill 挂个
        "derived from" 标签, 而那儿并没有第二层。
        """
        _write_skill(single, "local-skill", body="v1")

        got = await _skill.skill_manage(action="patch", skill_name="local-skill", content="v2")

        assert got == "Skill patched: 'local-skill'", got
        text = (single / "skills" / "local-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert "v2" in text
        assert "derived_from" not in text

    async def test_delete_really_removes_without_tombstone(self, single: Path) -> None:
        """单根下删除是真删, 不留墓碑 -- 没有下层需要被盖住。"""
        _write_skill(single, "local-skill")

        got = await _skill.skill_manage(action="delete", skill_name="local-skill")

        assert got == "Skill deleted: 'local-skill'"
        assert _tree(single) == set()

    async def test_trigger_create_and_delete_land_in_agent_root(self, single: Path) -> None:
        created = await _trigger.trigger_manage(
            action="create",
            trigger_name="local-trigger",
            event="feishu.chat.member_added",
            filter='{"chat_id": "oc_1"}',
            # ``raw_filter`` 在 ``raw_event`` 能自动补出来时是必填的(空 raw_filter 会让 raw
            # 回退路径比收窄后的常规路径更宽 -- 2026-09-02 生产事故)。
            raw_filter='{"chat_id": "oc_1"}',
            fire="prompt",
        )
        assert created.startswith("Created trigger 'local-trigger'"), created
        assert (single / "triggers" / "local-trigger" / "TRIGGER.md").exists()

        deleted = await _trigger.trigger_manage(action="delete", trigger_name="local-trigger")
        assert deleted == "Deleted trigger 'local-trigger'."
        assert not (single / "triggers" / "local-trigger").exists()

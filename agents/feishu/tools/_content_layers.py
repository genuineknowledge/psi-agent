"""写侧分层 -- 「改只读层的内容」落到哪/以什么形式落。

B-1 让四类内容**读**得跨层(nearest-wins), 本模块让**写**跟上。读侧改完而写侧没改是
一个会报错的状态: B-3 把 ``/official`` 挂成 ``ro`` 之后, ``skill_manage(patch)`` 一个
官方 skill 直接 ``OSError``--一个今天能用的功能变成报错。所以 B-2 必须在 B-3 之前。

三条规则(设计文档已定, 此处只实现):

* **create** → 直接写最上层可写根, 不再是 ``resolve_agent()``。
* **patch 命中只读层** → 复制到可写层改副本, **原件不动**, frontmatter 记
  ``derived_from`` + 来源层名 + 来源版本。整体覆盖, 不做字段级合并--合并会产出
  "谁都没写过的第三份内容"。
* **删除** → 可写层写墓碑(见 ``content_tombstone``), 不删只读层文件。

**群会话拒绝写**(决定 3): 群是多人共用一个 workspace, 群里改出来的派生件会成为群成员
共同的个人层, 而"个人层"这个词在群里没有主。明确报错并指向私聊, **不静默落到别处**:
静默落盘的那个版本里, 用户以为改的是自己的/实际改的是全群的。

**可写性靠实写探针判定, 不靠 ``os.access``**。Windows 上 ``os.access(dir, W_OK)`` 对
目录恒为真(``chmod 0o500`` 也拦不住建文件, 本卡实测), 而生产是 Linux ``ro`` 挂载。
一个在开发机上恒报"可写"的判据等于没有判据, 所以这里真建一个临时文件再删掉--唯一
对两个平台都说真话的问法。
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass

import _runtime_paths as _paths
import anyio

from psi_agent.session import layer_probe as _layer_probe
from psi_agent.session.content_roots import AGENT_ROOT_NAME as _AGENT_ROOT_NAME
from psi_agent.session.content_roots import content_roots_from_env as _content_roots_from_env
from psi_agent.session.content_tombstone import TOMBSTONE_KEY, is_tombstone
from psi_agent.session.runtime_context import get_session_id as _get_session_id

__all__ = [
    "GROUP_WRITE_REFUSAL",
    "TOMBSTONE_KEY",
    "ContentLayer",
    "derived_frontmatter",
    "group_write_refusal",
    "is_tombstone",
    "layers_containing",
    "layers_for",
    "same_layer",
    "source_version_of",
    "writable_layer",
]

#: 群会话里写操作的固定文案。常量而非各调用点自己拼: 两个工具(skills / triggers)三个
#: 动作都要报同一件事, 而判据按文案里的关键词断言。
GROUP_WRITE_REFUSAL = "群内不支持改 {kind}, 请在私聊里改。群会话只读官方与企业层内容。"

#: 群会话 session_id 前缀。与 ``gateway.feishu._identity.GROUP_SESSION_PREFIX`` 同值,
#: 但此处**刻意重复一份字面量而不 import**: workspace 工具包不依赖 gateway 包(同 src /
#: workspace 两侧各留一份 ``_private_space`` 的既有取舍), 且 import gateway 会把整个
#: aiohttp 栈拖进每个工具进程(实测 10s)。两处若分歧, 本模块的判据会红。
_GROUP_SESSION_PREFIX = "feishu-chat-"


@dataclass(frozen=True)
class ContentLayer:
    """一层内容: 声明的层名 + 这一层某类内容的目录。

    *name* 来自声明(见 ``content_roots`` 的模块 docstring: 层名是身份, 不从路径推
    断), 会被写进派生件的 ``derived_from_layer``--于是"这份副本派生自哪一层"在文件
    里是可读的, 而不是要靠比对路径反推。
    """

    name: str
    path: anyio.Path


def layers_for(kind: str) -> list[ContentLayer]:
    """*kind*(``skills`` / ``triggers``)的层梯子, **降序**: 近的在前。

    与 B-1 读侧同一份 ``content_roots_from_env()`` + agent 根在最近处, 只是方向反过来
    --写侧问的是"最近的可写层是哪个", 从近往远找第一个可写的即可, 而读侧要从远往近
    覆盖。方向不同但**顺序同源**: 若各自维护一份根列表, 读到的那层和写到的那层会在某
    次改动后不一致, 表现为"改完了但索引里还是旧的"。

    未声明内容根 → 单元素列表, 就是 ``resolve_agent()/<kind>``, 与改动前逐字节相同。
    """
    declared = _content_roots_from_env()
    agent_root = _paths.resolve_agent()
    layers: list[ContentLayer] = []
    if declared:
        # agent 根在最近处。已在声明里(按路径)时不重复追加, 否则同一目录会被当成两层,
        # "派生自哪一层"会报出一个声明里不存在的层名。
        agent_dir = agent_root / kind
        if not any(_same_dir(anyio.Path(str(root.path / kind)), agent_dir) for root in declared):
            layers.append(ContentLayer(name=_AGENT_ROOT_NAME, path=agent_dir))
        for root in reversed(declared):
            layers.append(ContentLayer(name=root.name, path=anyio.Path(str(root.path / kind))))
    else:
        layers.append(ContentLayer(name=_layer_name_of(str(agent_root)), path=agent_root / kind))
    return layers


def _layer_name_of(path: str) -> str:
    """单根世界里那一层的名字 -- 目录名, 与 B-0 探针 ``root_name`` 同一口径。"""
    return _layer_probe.root_name(path)


def _same_dir(left: anyio.Path, right: anyio.Path) -> bool:
    """两个路径是否指同一目录(大小写与尾斜杠归一化)。

    纯字符串比较(``os.path.normcase`` + ``normpath``), 不做 ``resolve()``: 本函数在
    async 工具代码里被调用, 而 ``resolve()`` 会打真实文件系统(``_runtime_paths.is_within``
    的注释记着同一条理由)。代价是 symlink 指向同一目录时判不出来, 后果只是多报一层。
    """
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(os.path.normpath(str(right)))


async def writable_layer(kind: str) -> tuple[ContentLayer | None, str]:
    """最上层**可写**的层, 以及判不出时的原因文案。

    从近往远试, 返回第一个真能写的。全都不可写 → ``(None, 原因)``, 调用方据此报错而不是
    让 ``OSError`` 冒出去: ``OSError`` 在工具返回里表现为一个 traceback, 用户看不出"是
    这层只读"还是"路径写错了"。

    **实写探针**: 建一个临时文件再删。理由见模块 docstring--``os.access`` 在 Windows
    上对目录恒为真, 于是本判据在开发机上永远不会红, 而它要拦的正是生产上的 ``ro`` 挂载。
    目录不存在则先建: 可写层第一次被写入时它本来就不存在(47 个 ``ou_*`` 目录里现在一个
    ``skills/`` 都没有, 实测), "建不出来"本身就是不可写的一种。
    """
    layers = layers_for(kind)
    reasons: list[str] = []
    for layer in layers:
        ok, why = await _probe_writable(layer.path)
        if ok:
            return layer, ""
        reasons.append(f"{layer.name}({why})")
    return None, "/".join(reasons)


async def _probe_writable(directory: anyio.Path) -> tuple[bool, str]:
    """*directory* 能不能写; 返回 ``(能否, 简短原因)``。"""
    try:
        await directory.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, type(e).__name__
    probe = directory / f".psi-write-probe-{os.getpid()}"
    try:
        await probe.write_text("", encoding="utf-8")
    except OSError as e:
        return False, type(e).__name__
    # 建得出但删不掉: 仍算可写(内容能落盘), 只是留了个残留探针文件。报 False 会把一个
    # 能用的层判成不可用。
    with suppress(OSError):
        await probe.unlink()
    return True, ""


async def layers_containing(kind: str, name: str, filename: str) -> list[ContentLayer]:
    """**每一个**有 *name* 这份内容的层, 由近及远。

    删除要问的不是"生效的那份在哪层", 而是"删掉可写层那份之后, 还会不会有一份冒出来"。
    只看最近那层会漏掉这个形状: ``patch`` 一个官方 skill 派生出副本之后, 最近那层是可写
    层, 于是 ``delete`` 真删副本 -- 而官方那份立刻**复活**(本卡实测到过), 用户看到删除
    成功/内容照旧在索引里。所以删除要按本函数的结果决定真删还是写墓碑。

    墓碑本身算"有": 它是一个已经存在的停用标记, 重复写一次是幂等的, 而把它当"没有"会
    让 ``delete`` 走真删分支/把墓碑删掉, 从而让被遮住的那份复活。
    """
    hits: list[ContentLayer] = []
    for layer in layers_for(kind):
        candidate = layer.path / name / filename
        try:
            if await candidate.exists():
                hits.append(layer)
        except OSError:
            continue
    return hits


def same_layer(left: ContentLayer, right: ContentLayer) -> bool:
    """两层是不是同一层 -- 按**路径**判, 不按层名。

    派生与否取决于"要写的地方跟内容现在所在的地方是不是同一个目录", 而层名在单根世界
    里取自目录名/分层世界里取自声明, 两套口径。按路径判则两个世界同一个答案。
    """
    return _same_dir(left.path, right.path)


def derived_frontmatter(
    frontmatter: dict[str, str],
    *,
    source_layer: str,
    source_version: str,
) -> dict[str, str]:
    """*frontmatter* 加上派生溯源三键, 返回新 dict(不改入参)。

    ``derived_from`` 记原名/``derived_from_layer`` 记来源层名/``derived_from_version``
    记来源版本。三个都要: 只记"派生自 skills/foo"答不了"派生自哪一层"(同名内容在三层
    里可能各有一份), 而不记版本就答不了"上游后来改了/我这份是从哪一版分出去的"--
    ``/official`` 是整版替换的, 这条是唯一能事后对齐的线索。

    版本取 ``updated_at``, 缺则 ``created_at``, 都缺则 ``unknown``: skills 的 frontmatter
    里没有 ``version`` 字段(本卡实测 162 个 SKILL.md 一个都没有), 造一个新字段等于要求
    上游内容配合, 而时间戳是它们已经在写的。
    """
    out = dict(frontmatter)
    out["derived_from"] = frontmatter.get("name") or ""
    out["derived_from_layer"] = source_layer
    out["derived_from_version"] = source_version or "unknown"
    return out


def source_version_of(frontmatter: dict[str, str]) -> str:
    """派生时记进 ``derived_from_version`` 的那个值。"""
    for key in ("updated_at", "created_at"):
        value = (frontmatter.get(key) or "").strip()
        if value:
            return value
    return "unknown"


def group_write_refusal(kind: str) -> str | None:
    """当前是群会话则返回拒绝文案, 否则 ``None``。

    判 session_id 前缀而不是查 workspace 路径: ``session_id`` 由路由键确定性派生(见
    ``FeishuManager.session_id_for``), 而 workspace 路径在错状态 session 上会指到根本身
    (B 设计文档记的那 15 个), 按路径判会把它们误判成群。

    ContextVar 未绑定(独立跑工具/单测)→ ``None`` = 放行。这是刻意的: 拒绝只在真的
    识别出群会话时发生, 把"认不出来"当群会拦掉所有非 Gateway 场景下的写入。
    """
    session_id = (_get_session_id() or "").strip()
    if session_id.startswith(_GROUP_SESSION_PREFIX):
        return f"[Error] {GROUP_WRITE_REFUSAL.format(kind=kind)}"
    return None

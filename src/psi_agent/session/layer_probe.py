"""层来源探针 — 每类内容实际从哪个根取到了什么, 一行一类。

**只读**: 本模块不参与查找、不改变任何加载结果, 只报告已经发生的事。加它是因为
方案 B 最危险的失败模式不是报错, 而是**静默只看见一层** —— 设了三个内容根之后
skills / triggers / systems / config 仍只读单目录, 不报错、计数看着正常、功能表面
跑通。这种失败没有判据可言, 只能靠功能表现反推, 而反推在生产上意味着先出事。

已经踩过两次同一形状:

* ``_collect_skill_dirs`` 用 ``contextlib.suppress(OSError)`` 吞异常, 某层只读或
  不可读时返回空列表, 与"该层本来就没有内容"完全不可区分。
* ``read_manifest`` 清单不存在时返回 ``None`` 且不打日志, 于是"清单没送到"静默退回
  全量暴露, 唯一线索是另一行 info 里的一个数字。

所以判据落在这里的关键不是"总数对不对", 而是 **区分得开"某层是空的"和"某层不存在"**。
``roots_declared`` 与 ``roots_seen`` 因此分开报: 声明了 3 个而只看见 1 个, 是分层没
落地; 声明 3 个看见 3 个而其中两个计数为 0, 是那两层确实没内容。前者是缺陷, 后者是
事实, 混成一个数字就再也分不开了。

形状对齐 ``agent.py`` 那行 ``tools_exposed=NN of NN``: 单行、``key=value``、可 grep、
数字在前缀后面, 这样"上线后拿 grep 数一下"就是判据本身, 不需要另做工具。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

# 日志前缀统一 ``layer_source``: 上线核验时 grep 一个词就能把四类内容一起捞出来,
# 而不必记四个不同的措辞。
_PREFIX = "layer_source"


def report(
    kind: str,
    *,
    roots_declared: int,
    per_root: list[tuple[str, int]],
    chosen: str = "",
) -> None:
    """报告 *kind* 这类内容的层来源。

    *roots_declared* 是**声明**了几个根, *per_root* 是实际**看见**的
    ``(层名, 条目数)``。两者分开是本模块的全部意义所在 —— 见模块 docstring:
    声明 3 看见 1 是分层没落地, 声明 3 看见 3 而两层为 0 是那两层没内容, 合成
    一个数字就分不开了。

    *chosen* 用于单值内容(systems / config 这类"谁赢了"而非"合并了多少"的),
    给出实际生效的那一层的名字; 留空表示这类内容是合并语义。

    刻意不抛异常也不做校验: 探针本身绝不能成为一个新的故障源。数字对不对由读日志
    的人判断, 这里只负责如实报出来。
    """
    seen = len(per_root)
    total = sum(count for _, count in per_root)
    detail = " ".join(f"{name}={count}" for name, count in per_root) or "(none)"
    tail = f" chosen={chosen}" if chosen else ""
    logger.info(f"{_PREFIX} {kind}: {total} from {seen} of {roots_declared} roots [{detail}]{tail}")


def root_name(path: Path | str, declared: dict[str, Path] | None = None) -> str:
    """*path* 对应的层名; 认不出来时退回目录名。

    层名而非路径是身份 —— 与 ``content_roots.ContentRoot.layer_id`` 同一约定
    (同一份内容挂在不同路径下仍是同一层)。*declared* 给出已知的层名到路径映射;
    单根世界(未声明任何内容根)传 None, 此时退回目录名, 日志仍然可读。
    """
    target = Path(str(path))
    for name, root in (declared or {}).items():
        if Path(str(root)) == target or Path(str(root)) in target.parents:
            return name
    return target.name or str(target)

"""墓碑 -- 「这个名字在这一层被停用了」的跨层约定。

分层查找是 nearest-wins(见 ``content_roots``), 于是「删除只读层的内容」有且只有
一种做法: 在可写层放一个同名的**停用标记**, 让它按 nearest-wins 赢掉下层那份。真删
只读层文件在 B-3 的挂载下会直接 ``OSError``, 而"删了但下层那份又冒出来"是更坏的形状
--用户看到删除成功/内容照旧在提示词里。

**为什么单独一个模块**: 这一个约定有四个知情者, 分处两个包--``trigger_registry``
(src 侧读 triggers)/``system._build_skills_index``(agent 包读 skills 索引)/
``_feishu_spec.load_rules_layered``(agent 包读 API 护栏规则)/``_content_layers``
(agent 包写)。四处各写一份 ``header.get("psi_deleted")`` 迟早在某一支上分歧, 而分歧
的表现是「墓碑对某一个消费者不生效」--即索引里没了但护栏规则还在执行, 静默且朝着
最贵的方向错(B-1 已经为 skills 的双消费者踩过一次)。

**为什么是 frontmatter 里的一个键/而不是一个 ``.tombstone`` 标记文件**: 墓碑必须走
和内容完全相同的那条查找路径才能保证「墓碑生效」与「覆盖生效」同真同假。同名目录 +
同名文件(``SKILL.md`` / ``TRIGGER.md``)天然被现有的 nearest-wins 合并吃掉下层那份;
另开一个文件名则要求每个消费者在合并之外**额外**记得查一次墓碑, 漏掉一处就是一个静默
失效的消费者。
"""

from __future__ import annotations

from typing import Any

#: Frontmatter / YAML header 键。带 ``psi_`` 前缀是因为它与用户写的业务字段共处一个
#: 命名空间: ``deleted`` 这种通名很可能被某个 skill 拿去当自己的字段用, 那样一个普通
#: 内容会被误判成墓碑而整体消失。
TOMBSTONE_KEY = "psi_deleted"

#: 判真的取值。刻意收窄到这几个而不是 Python 真值: YAML 会把裸 ``true`` 解析成 bool,
#: 而 skills 那个手写的 ``key: value`` 解析器只产出字符串, 两边必须判成同一个答案。
_TRUE_TEXT = frozenset({"true", "1", "yes", "on"})


def is_tombstone(header: dict[str, Any] | None) -> bool:
    """*header* 是否标记了停用。

    ``None`` / 缺键 / 取值不在白名单里都是「不是墓碑」--即默认把内容当内容。反过来
    默认(把解析不出的东西当墓碑)会让一个 frontmatter 损坏的 skill 静默消失, 而 git
    里已经存在过一个损坏 frontmatter(见 B 设计文档 1.6)。
    """
    if not header:
        return False
    value = header.get(TOMBSTONE_KEY)
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in _TRUE_TEXT

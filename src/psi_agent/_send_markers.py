"""``[SEND:/path]`` 标记的解码 —— Channel 与 Session 共用。

Channel 侧 ``SendMarkerScanner`` 按流解码成 ``FileChunk``, Session 侧
``history_display.extract_send_paths()`` 为 Gateway 投影提取同一批路径。两处
曾各持一条正则且写法不同 (Channel 用 ``(.+?)``, Gateway 用 ``([^\\]]*?)``),
且只有 Gateway 侧有空路径过滤 —— 分歧的后果见下方两条注释。

放在 ``psi_agent`` 顶层 (与 ``_appdata`` / ``_sockets`` / ``_feishu_routing``
同级) 而非 ``channel/`` 内, 与 ``_feishu_routing`` 同理: 避免让 Session 去
import Channel 的私有模块、在两个组件之间新造一条依赖。``channel/_markers.py``
重导出这两个符号, 既有 import 路径保持有效。
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# 容纳部分模型输出的空格填充变体 ``[ SEND:path ]``; 空 ``[SEND:]`` 由
# ``iter_send_paths`` 过滤而非由正则拦, 好让两侧共用一条规则而不是各自在正则里
# 编码一遍。
#
# 路径字符类同时排除换行与 ``]``: 换行在路径里永远不合法, 而放开它会让一个
# **未闭合**的 ``[SEND:`` 一路吞到下几行的 ``]`` —— 丢掉后面真正的路径, 并把一条
# 带换行的字符串交给 ``_send_file`` 去上传。
SEND_RE = re.compile(r"\[\s*SEND\s*:\s*([^\]\n]*?)\s*\]", re.IGNORECASE)


def iter_send_paths(text: str) -> Iterator[tuple[str, int]]:
    """逐个产出 ``[SEND:…]`` 里的真实路径与 ``(path, match_end)``。

    空路径 / 纯空白路径跳过: 裸 ``[SEND:]`` 是模型笔误而非传输请求。放过去会让
    Channel 拿空 source path 发起上传 (两处 ``_send_file`` 都没有 guard), 也会让
    Gateway 投影多出一个空条目。

    ``match_end`` 是标记末尾的偏移, 供流式调用方推进扫描指针, 不必重新求一次匹配。
    """
    for match in SEND_RE.finditer(text):
        path = match.group(1).strip()
        if path:
            yield path, match.end()

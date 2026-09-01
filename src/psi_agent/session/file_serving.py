"""出向文件的字节供给 —— ``GET /files`` 的纯逻辑部分 (无 HTTP)。

**为什么 Session 要供字节。** 飞书 WS 长连接同一 App 只允许一条, 于是 channel 只能跑在
gateway 容器里; 而独立容器的 Session 有自己的文件系统。agent 输出 ``[SEND:/workspace/x.md]``
时, gateway 拿这个路径读**自己的** ``/workspace`` —— 那是另一个卷, 同名不同物, 多数情况
直接不存在。实测后果不止「少一个附件」: 上传失败后 marker 不被消费, 裸
``[SEND:/workspace/...]`` 当文本发给用户, 而 agent 那边以为发成功了, 说「已发送」。

所以路径不跨容器传递, **字节**跨容器传递: Session 供字节, channel 取到后交给飞书 SDK
上传 (SDK 收 ``bytes``, 见 ``channel/feishu/client.py`` 的 ``_send_file``)。方向上与入向
的 ``_attachment_handoff`` 互为镜像 —— 两边共同的原则是**不假装两个容器共享文件系统**。

判定与 ``gateway/desktop/_workspace_manager.py`` 的 ``read_file`` 同一套 (``resolve()`` 后判包含),
刻意不共用实现: Session 不该 import Gateway 的私有模块 (同 ``_feishu_routing`` /
``_send_markers`` 的取舍)。分离出本模块而非写在 handler 里, 是为了让路径逃逸这类判定
能脱离 aiohttp 单元测试。
"""

from __future__ import annotations

import os
from pathlib import Path

import anyio

from psi_agent._private_space import PRIVATE_DIRNAME

# 飞书单文件上限 30MB。超限的文件在这里就拒, 不读进内存 —— 上传反正会被飞书拒,
# 提前拦住换来的是「不会因为 agent 误传一个几 GB 的文件把容器内存打满」。
MAX_FILE_BYTES = 30 * 1024 * 1024


class FileServingError(Exception):
    """取字节失败, ``status`` 是对应的 HTTP 状态码。

    带上状态码而不是让 handler 按异常类型映射: 判定逻辑在本模块, 「越界算 403 还是 404」
    是同一处决定的一部分, 拆到两个文件里迟早分歧。
    """

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def _norm(path: str) -> str:
    """大小写与分隔符归一, 供包含判定用。"""
    return os.path.normcase(os.path.normpath(path))


async def resolve_within_root(raw: str, root: Path | None) -> Path:
    """把请求里的路径解析成 *root* 内的真实文件路径。

    *root* 为 ``None`` 即该 Session 没有 workspace: 无根可限, 一律 403 而不是放开 ——
    「没有边界」不能被解读成「边界是整个文件系统」。

    ``resolve()`` 必须在包含判定**之前**做: 它展开 symlink 与 ``..``, 少了这一步,
    ``/workspace/pub/../../etc/passwd`` 与指向根外的软链都能过判定。
    """
    if root is None:
        raise FileServingError(403, "this session has no workspace root; file serving disabled")
    path = raw.strip()
    if not path:
        raise FileServingError(400, "path is required")

    target = anyio.Path(path)
    resolved = await target.resolve()
    root_resolved = await anyio.Path(root).resolve()
    root_s, file_s = _norm(str(root_resolved)), _norm(str(resolved))
    if not (file_s == root_s or file_s.startswith(root_s + os.sep)):
        raise FileServingError(403, f"path outside workspace root: {path!r}")

    # 存在性判定放在包含判定之后: 先答「你不该问这个路径」再答「它不存在」, 免得把
    # 根外的文件存在与否透给调用方。
    if not await resolved.exists():
        raise FileServingError(404, f"not found: {path!r}")
    if not await resolved.is_file():
        raise FileServingError(400, f"not a file: {path!r}")
    if (await resolved.stat()).st_size > MAX_FILE_BYTES:
        raise FileServingError(413, f"file exceeds {MAX_FILE_BYTES} bytes: {path!r}")

    # 私密区一律不经本端点外流。判在这里是因为**只有这一侧有文件系统事实**:
    # channel 侧那道 `blocks_send` 判的是模型输出的路径字符串, 跨容器时那个路径在
    # gateway 上并不存在, `realpath` 退化成纯字符串规范化, 于是「软链指进 .private」
    # 这类写法在那边判不出来, 在这里可以。两道守卫都保留 —— 那道按「发送者是不是主人」
    # 判权 (本端点无从得知发送者是谁), 这道按「文件是不是私密区的」判, 判据不同不重复。
    if _in_private_space(resolved_str=file_s, root=_norm(str(root_resolved))):
        raise FileServingError(403, f"private file not served: {path!r}")
    return Path(resolved)


def _in_private_space(*, resolved_str: str, root: str) -> bool:
    """*resolved_str* 是否落在 workspace 根下的私密目录里。

    只看**已经 resolve 过**的路径, 故软链绕不过。不复用 ``_private_space.owner_of``:
    那个要配 ``PSI_PRIVATE_OPEN_IDS`` 白名单才生效 (未配时返回 None = 放行), 判的是
    「谁是主人」; 这里要的是无条件的「是不是私密区」—— 白名单没配好不该变成把私密目录
    敞开供字节。目录名共用同一常量, 不另起字面量。
    """
    rel = resolved_str[len(root) :].lstrip(os.sep) if resolved_str.startswith(root) else resolved_str
    return _norm(PRIVATE_DIRNAME) in Path(rel).parts

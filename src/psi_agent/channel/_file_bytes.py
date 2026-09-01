"""Fetch an outbound file's bytes from the Session that produced it.

A ``[SEND:/path]`` marker carries only a path. When the Session runs in another
container (``FileChunk.source`` non-empty) that path means nothing on this
filesystem, so the bytes have to come over HTTP from the Session's ``GET /files``.

Lives in the channel-neutral layer on purpose: ``FileChunk`` is shared by every
channel client (feishu / telegram / cli), and nothing here knows about any one
platform's upload API. Putting it under ``feishu/`` would guarantee a verbatim
copy the day telegram is deployed the same way.
"""

from __future__ import annotations

import logging

import aiohttp

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.channel._errors import ChannelError

logger = logging.getLogger(__name__)


class OutboundFileError(ChannelError):
    """跨容器取字节失败 —— 这个文件发不出去, 如实告诉用户。

    **刻意不回落到「把路径交给平台 SDK」**: 那条路在跨容器下必然失败 (路径在 channel
    容器里不存在, 正是本 bug 的成因), 走一遍只是把我们的错误换成 SDK 的错误, 而 SDK
    那侧的失败是**静默**的 —— 用户看到的还是一句话回复没有附件, 与修复前无区别。

    与入向的 ``AttachmentDownloadError`` 同一套取舍: 宁可明确报「这个文件没发出去」,
    也不留一个看起来成功、实际什么都没发生的路径。
    """

    def __init__(self, path: str) -> None:
        self.path = path
        name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or path
        super().__init__(f"文件发送失败: {name} —— 无法从 agent 所在容器取到文件内容")


# 宽于 channel 里那些「传一次决策」的超时 (如 feishu 的 ``_GATEWAY_TIMEOUT`` 10s):
# 这里最多要传 30MB 过 docker 网络, 且对端可能正忙于同一 Session 的其他回合。
FETCH_TIMEOUT = aiohttp.ClientTimeout(total=120)

MAX_FILE_BYTES = 30 * 1024 * 1024
"""Ceiling on a fetched outbound file.

Mirrors ``session.file_serving.MAX_FILE_BYTES`` rather than importing it: the two
are independent defences (server-side refusal, client-side refusal) and a channel
must not depend on the session package. Both are ~Feishu's own media ceiling.
"""


async def fetch_file_bytes(source: str, path: str) -> bytes | None:
    """Read *path* from the Session at *source*; return ``None`` on any failure.

    Never raises — every failure mode is logged here (with the reason) and reported
    as ``None``, so the caller has one branch to handle instead of a taxonomy of
    transport exceptions. Deciding *what to do* with a failure is the caller's:
    ``feishu._send_file`` turns it into :class:`OutboundFileError`, which the stream
    turns into a message telling the user which file did not go out. Notably it does
    **not** fall back to handing the path to the platform SDK — see that class.
    """
    url = f"{source.rstrip('/')}/files"
    try:
        connector, _ = resolve_connector_and_endpoint(source)
        async with (
            aiohttp.ClientSession(connector=connector, timeout=FETCH_TIMEOUT) as http,
            http.get(url, params={"path": path}) as resp,
        ):
            if resp.status != 200:
                body = (await resp.text())[:300]
                logger.error(f"fetch bytes failed HTTP {resp.status} from {url} path={path!r}: {body}")
                return None
            data = await resp.read()
    except Exception as e:
        logger.error(f"fetch bytes failed from {url} path={path!r} — {e!r}")
        return None
    if not data:
        # 空文件上传必被平台拒, 且「0 字节附件」对用户毫无用处 —— 当失败处理更诚实。
        logger.error(f"fetch bytes got empty body from {url} path={path!r}")
        return None
    if len(data) > MAX_FILE_BYTES:
        logger.error(f"fetched file too large ({len(data)} bytes) from {url} path={path!r}")
        return None
    logger.debug(f"fetched {len(data)} bytes from {url} path={path!r}")
    return data

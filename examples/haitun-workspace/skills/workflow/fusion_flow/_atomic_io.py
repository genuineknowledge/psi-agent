"""Shared atomic file publication for FusionFlow persistence."""

from __future__ import annotations

import errno
import ntpath
import os
import secrets
from collections.abc import Iterator
from typing import BinaryIO

import anyio
from anyio.lowlevel import checkpoint_if_cancelled
from anyio.to_thread import run_sync as run_sync_in_worker_thread
from loguru import logger

_CLASSIC_WINDOWS_PATH_LIMIT = 260
_MAX_TEMPORARY_NAME_LENGTH = 21
_MAX_ALLOCATION_ATTEMPTS = 64


class _OwnedTemporaryWriteError(Exception):
    """Wrap a write error that happened after exclusive file creation."""

    def __init__(self, error: BaseException) -> None:
        super().__init__(str(error))
        self.error = error


def _utf16_code_units(value: str) -> int:
    """Return the number of Windows UTF-16 code units in a path spelling."""

    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _absolute_path(path: anyio.Path) -> anyio.Path:
    """Lexically anchor a path without resolving symlinks."""

    return anyio.Path(os.path.abspath(os.fspath(path)))


def _temporary_name_length(
    path: anyio.Path,
    *,
    path_limit: int = _CLASSIC_WINDOWS_PATH_LIMIT,
) -> int:
    """Choose the longest temporary basename whose absolute path stays below a limit."""

    target = _absolute_path(path)
    for length in range(_MAX_TEMPORARY_NAME_LENGTH, 0, -1):
        candidate = anyio.Path(target.parent, "0" * length)
        if _utf16_code_units(str(candidate)) < path_limit:
            return length
    raise OSError(
        errno.ENAMETOOLONG,
        "no temporary filename fits within the Windows path limit",
        str(target),
    )


def _temporary_names(length: int) -> Iterator[str]:
    """Yield a bounded, non-repeating random sequence of valid temporary basenames."""

    if length < 1 or length > _MAX_TEMPORARY_NAME_LENGTH:
        raise ValueError("temporary filename length is outside the supported range")
    prefix = ".tmp-" if length == _MAX_TEMPORARY_NAME_LENGTH else ""
    token_length = length - len(prefix)
    namespace_size = 16**token_length
    attempts = min(_MAX_ALLOCATION_ATTEMPTS, namespace_size)
    start = secrets.randbelow(namespace_size)
    # The namespace is a power of two, so an odd step visits unique values.
    step = secrets.randbelow(namespace_size) | 1
    for attempt in range(attempts):
        token = (start + attempt * step) % namespace_size
        yield f"{prefix}{token:0{token_length}x}"


def _uses_extended_windows_path(path: anyio.Path) -> bool:
    """Return whether a path explicitly opts into Win32 extended-length syntax."""

    return os.fspath(path).startswith("\\\\?\\")


def _temporary_length_for_platform(
    target: anyio.Path,
    *,
    windows: bool | None = None,
) -> int:
    """Prefer a classic-limit temporary name on Windows whenever one fits."""

    if windows is None:
        windows = os.name == "nt"
    if not windows or _uses_extended_windows_path(target):
        return _MAX_TEMPORARY_NAME_LENGTH
    try:
        return _temporary_name_length(
            target,
            path_limit=_CLASSIC_WINDOWS_PATH_LIMIT,
        )
    except OSError as error:
        if error.errno != errno.ENAMETOOLONG:
            raise
        # A long-path-aware process may legitimately address a parent whose
        # spelling cannot fit any basename below the classic limit.
        return _MAX_TEMPORARY_NAME_LENGTH


def _write_new_bytes(path: str, value: bytes) -> None:
    """Exclusively create and fully close one candidate temporary file."""

    stream: BinaryIO | None = None
    try:
        with open(path, "xb") as opened:
            stream = opened
            opened.write(value)
    except BaseException as error:
        if stream is None:
            # No file is owned unless exclusive creation completed.
            raise
        raise _OwnedTemporaryWriteError(error) from error


async def _cleanup_owned_temporary(path: anyio.Path) -> None:
    """Best-effort cleanup without masking the original publication failure."""

    with anyio.CancelScope(shield=True):
        try:
            await path.unlink()
        except FileNotFoundError:
            return
        except Exception as cleanup_error:
            logger.warning(
                f'Failed to clean temporary FusionFlow file "{path}": {cleanup_error}',
            )


async def atomic_write_bytes(path: anyio.Path, value: bytes) -> None:
    """Publish bytes with a same-directory exclusive temporary file and atomic replace."""

    target = _absolute_path(path)
    name_length = _temporary_length_for_platform(target)
    target_key = ntpath.normcase(target.name)
    owned_temporary: anyio.Path | None = None
    last_collision: FileExistsError | None = None

    for name in _temporary_names(name_length):
        if ntpath.normcase(name) == target_key:
            continue
        candidate = anyio.Path(target.parent, name)
        await checkpoint_if_cancelled()
        try:
            with anyio.CancelScope(shield=True):
                await run_sync_in_worker_thread(
                    _write_new_bytes,
                    os.fspath(candidate),
                    value,
                )
                owned_temporary = candidate
        except FileExistsError as collision:
            last_collision = collision
            continue
        except _OwnedTemporaryWriteError as write_error:
            await _cleanup_owned_temporary(candidate)
            raise write_error.error from write_error
        break

    if owned_temporary is None:
        raise FileExistsError(
            errno.EEXIST,
            "could not allocate a unique FusionFlow temporary file",
            str(target.parent),
        ) from last_collision

    try:
        await checkpoint_if_cancelled()
        # Keep commit and ownership transfer indivisible with respect to cancellation.
        with anyio.CancelScope(shield=True):
            await owned_temporary.replace(target)
            owned_temporary = None
    except BaseException:
        if owned_temporary is not None:
            await _cleanup_owned_temporary(owned_temporary)
        raise


async def atomic_write_text(
    path: anyio.Path,
    value: str,
    *,
    newline: str | None = "",
) -> None:
    """Publish UTF-8 text with the requested text-stream newline behavior."""

    if newline not in {None, "", "\n", "\r", "\r\n"}:
        raise ValueError(f"illegal newline value: {newline!r}")
    translated_newline = os.linesep if newline is None else newline
    if translated_newline not in {"", "\n"}:
        value = value.replace("\n", translated_newline)
    await atomic_write_bytes(path, value.encode("utf-8"))

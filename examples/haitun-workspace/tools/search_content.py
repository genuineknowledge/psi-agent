"""Content-search tool - grep workspace file contents by regex/string.

Complements ``list_dir`` (which only lists names) and ``search`` (which queries
the web). Use this to locate where a symbol, string, or pattern appears across
files and get back ``file:line: matched text`` hits.

Prefers ripgrep (``rg``) when available for speed and .gitignore awareness, and
falls back to a pure-Python ``re`` walk when ``rg`` is not on PATH so the tool
works in any environment.
"""

from __future__ import annotations

import fnmatch
import re
import shutil

import anyio

# Directories skipped by the Python fallback walk (rg already honors .gitignore
# and skips .git itself).
_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache", "dist", "build"}
)

_MAX_LINE_CHARS = 500  # Truncate very long matched lines in the output.


async def search_content(
    pattern: str,
    path: str = ".",
    glob: str = "",
    case_sensitive: bool = False,
    is_regex: bool = True,
    max_results: int = 200,
) -> str:
    """Search file contents for a regex or literal string and return matching lines.

    Use this to find where something appears in the workspace (a function name,
    a config key, an error string) instead of reading files one by one. Returns
    each hit as ``file:line: matched text``.

    Args:
        pattern: The regex (or literal string when is_regex is False) to search for.
        path: File or directory to search under (defaults to the current directory).
        glob: Optional filename glob to restrict which files are searched, e.g. ``*.py`` or ``*.{ts,vue}``.
        case_sensitive: When False (default), matching ignores case.
        is_regex: When True (default), pattern is a regular expression; when False it is matched literally.
        max_results: Maximum number of matching lines to return (guards against huge result sets).

    Returns:
        Newline-separated ``file:line: text`` hits, or an error / "no matches" message.
    """
    if not pattern:
        return "[Error] Empty search pattern."

    root = anyio.Path(path)
    if not await root.exists():
        return f"[Error] Path not found: {path}"

    # Validate the regex up front so we return a clear error rather than a walk failure.
    if is_regex:
        try:
            re.compile(pattern)
        except re.error as e:
            return f"[Error] Invalid regex {pattern!r}: {e}"

    rg = shutil.which("rg")
    if rg:
        result = await _search_with_ripgrep(rg, pattern, path, glob, case_sensitive, is_regex, max_results)
        if result is not None:
            return result
        # rg failed unexpectedly (not just "no matches") -> fall through to Python.

    return await _search_with_python(pattern, root, glob, case_sensitive, is_regex, max_results)


async def _search_with_ripgrep(
    rg: str,
    pattern: str,
    path: str,
    glob: str,
    case_sensitive: bool,
    is_regex: bool,
    max_results: int,
) -> str | None:
    """Run ripgrep and format its output. Returns None if rg errored unexpectedly."""
    args = [rg, "--line-number", "--no-heading", "--color", "never", "--max-count", str(max_results)]
    if not case_sensitive:
        args.append("--ignore-case")
    if not is_regex:
        args.append("--fixed-strings")
    if glob:
        args += ["--glob", glob]
    args += ["--regexp", pattern, "--", path]

    try:
        proc = await anyio.run_process(args, check=False)
    except Exception:
        return None  # rg unusable -> let caller fall back to Python.

    # rg exit codes: 0 = matches, 1 = no matches, 2 = error.
    if proc.returncode == 1:
        return f"(no matches for {pattern!r} under {path})"
    if proc.returncode not in (0,):
        return None  # Unexpected error -> fall back to Python.

    stdout = proc.stdout.decode("utf-8", errors="replace")
    lines = [ln for ln in stdout.splitlines() if ln]
    if not lines:
        return f"(no matches for {pattern!r} under {path})"

    truncated = len(lines) > max_results
    out: list[str] = []
    for ln in lines[:max_results]:
        # rg output is already "file:line:text"; just cap absurdly long lines.
        out.append(ln[:_MAX_LINE_CHARS])
    listing = "\n".join(out)
    if truncated:
        listing += f"\n[Truncated at {max_results} matches]"
    return listing


def _fnmatch_translate(glob: str):
    """Compile a filename glob (supporting ``{a,b}`` alternation) to a regex."""
    if "{" in glob and "}" in glob:
        pre, _, rest = glob.partition("{")
        alts, _, post = rest.partition("}")
        patterns = [f"{pre}{alt}{post}" for alt in alts.split(",")]
    else:
        patterns = [glob]
    regexes = [fnmatch.translate(p) for p in patterns]
    return re.compile("|".join(regexes))


async def _search_with_python(
    pattern: str,
    root: anyio.Path,
    glob: str,
    case_sensitive: bool,
    is_regex: bool,
    max_results: int,
) -> str:
    """Pure-Python fallback: walk files and match with ``re``."""
    flags = 0 if case_sensitive else re.IGNORECASE
    needle = pattern if is_regex else re.escape(pattern)
    matcher = re.compile(needle, flags)
    name_re = _fnmatch_translate(glob) if glob else None

    hits: list[str] = []
    truncated = False

    async def scan_file(file_path: anyio.Path) -> None:
        nonlocal truncated
        if name_re is not None and not name_re.match(file_path.name):
            return
        try:
            content = await file_path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            return  # Skip binary / unreadable files.
        for lineno, line in enumerate(content.splitlines(), start=1):
            if matcher.search(line):
                if len(hits) >= max_results:
                    truncated = True
                    return
                text = line.strip()[:_MAX_LINE_CHARS]
                hits.append(f"{file_path}:{lineno}: {text}")

    async def walk(base: anyio.Path) -> None:
        nonlocal truncated
        async for child in base.iterdir():
            if truncated or len(hits) >= max_results:
                truncated = True
                return
            if await child.is_dir():
                if child.name in _SKIP_DIRS:
                    continue
                await walk(child)
            else:
                await scan_file(child)

    if await root.is_file():
        await scan_file(root)
    else:
        await walk(root)

    if not hits:
        return f"(no matches for {pattern!r} under {root})"
    listing = "\n".join(hits)
    if truncated:
        listing += f"\n[Truncated at {max_results} matches]"
    return listing

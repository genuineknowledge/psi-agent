"""GitHub toolset - inspect codebases and review pull requests.

Groups tools for working with source repositories:

- ``inspect_codebase``: a wrapper around `pygount <https://pypi.org/project/pygount/>`_
  that counts lines of code, breaks them down by language, and reports
  code-vs-comment ratios — similar to ``cloc``/``sloccount`` but backed by
  Pygments so it recognizes hundreds of languages. ``pygount`` is a
  synchronous library, so the blocking scan runs in a worker thread via
  ``anyio.to_thread.run_sync`` to keep the event loop responsive.

- PR code review tools (``review_pull_request``, ``get_pull_request_diff``,
  ``list_pull_request_comments``, ``add_pull_request_comment``): read a pull
  request's overview / diff / comments and post top-level or inline review
  comments. These talk to the GitHub REST API v3 directly over
  :mod:`aiohttp` (already a project dependency) — no ``gh`` binary and no new
  dependency required. Authentication uses a token from ``GH_TOKEN`` /
  ``GITHUB_TOKEN``, falling back to ``gh auth token`` when the ``gh`` CLI is
  logged in. Tokens are never printed or logged.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

import aiohttp
import anyio

# Default folders that would otherwise make pygount crawl dependency trees for
# minutes (or hang). ``[...]`` tells pygount's ``regexes_from`` to append these
# to its own built-in defaults rather than replace them.
_DEFAULT_FOLDERS_TO_SKIP = (
    "[...], node_modules, .venv, venv, __pycache__, .cache, "
    "dist, build, .next, .tox, .eggs, .mypy_cache, .pytest_cache, "
    "vendor, third_party, coverage"
)


def _scan_summary(
    path: str,
    suffixes: str,
    folders_to_skip: str,
) -> dict:
    """Blocking pygount scan → summary dict. Runs in a worker thread."""
    # Imported lazily so the tool file still loads when pygount is absent;
    # inspect_codebase then reports a friendly "not installed" message.
    import pygount  # noqa: PLC0415
    from pygount.analysis import (  # noqa: PLC0415
        DEFAULT_FOLDER_PATTERNS_TO_SKIP_TEXT,
        DEFAULT_NAME_PATTERNS_TO_SKIP_TEXT,
    )
    from pygount.common import regexes_from  # noqa: PLC0415

    # SourceScanner runs suffixes through regexes_from, so pass the raw
    # comma-separated string ("py", "py,ts"); "*" means all languages.
    suffix_arg = suffixes.strip() or "*"
    # The "[...]" prefix asks regexes_from to append to pygount's built-in
    # defaults, which must be supplied explicitly as default_patterns_text.
    scanner = pygount.SourceScanner(
        [path],
        suffixes=suffix_arg,
        folders_to_skip=regexes_from(
            folders_to_skip or _DEFAULT_FOLDERS_TO_SKIP,
            DEFAULT_FOLDER_PATTERNS_TO_SKIP_TEXT,
        ),
        # Passing folders_to_skip makes SourceScanner treat name_to_skip as
        # supplied too, so we must pass its defaults explicitly or it stays None.
        name_to_skip=regexes_from(DEFAULT_NAME_PATTERNS_TO_SKIP_TEXT),
    )

    project = pygount.ProjectSummary()
    try:
        for path_data in scanner.source_paths():
            # fallback_encoding=utf-8 (not pygount's cp1252 default) so UTF-8
            # files with non-ASCII bytes aren't mis-flagged as __error__ on
            # Windows when automatic detection is uncertain.
            analysis = pygount.SourceAnalysis.from_file(
                path_data.source_path,
                path_data.group,
                fallback_encoding="utf-8",
            )
            project.add(analysis)
    finally:
        scanner.close()

    languages = []
    for summary in project.language_to_language_summary_map.values():
        languages.append(
            {
                "language": summary.language,
                "is_pseudo_language": summary.is_pseudo_language,
                "files": summary.file_count,
                "code": summary.code_count,
                "documentation": summary.documentation_count,
                "empty": summary.empty_count,
                "string": summary.string_count,
                "source": summary.source_count,
            }
        )
    # Real languages first, then by code lines descending.
    languages.sort(key=lambda entry: (entry["is_pseudo_language"], -entry["code"]))

    total_code = project.total_code_count
    total_doc = project.total_documentation_count
    ratio = round(total_code / total_doc, 2) if total_doc else None
    comment_percent = round(100 * total_doc / (total_code + total_doc), 1) if (total_code + total_doc) else 0.0

    return {
        "path": path,
        "totals": {
            "files": project.total_file_count,
            "code": total_code,
            "documentation": total_doc,
            "empty": project.total_empty_count,
            "string": project.total_string_count,
            "lines": project.total_line_count,
            "source": project.total_source_count,
        },
        "code_to_comment_ratio": ratio,
        "comment_percent": comment_percent,
        "languages": languages,
    }


async def inspect_codebase(
    path: str = ".",
    suffixes: str = "",
    folders_to_skip: str = "",
    max_languages: int = 50,
) -> str:
    """Inspect a codebase with pygount: lines of code, languages, and ratios.

    Use this when asked how big a repository is, its language composition, its
    lines-of-code (LOC) count, or its code-vs-comment ratio. It scans the source
    tree with pygount (Pygments-based, so it recognizes hundreds of languages),
    counts code / documentation / empty / string lines, and returns per-language
    totals plus overall ratios.

    Dependency and build folders (node_modules, .venv, dist, build, .git, ...)
    are skipped by default so large trees don't hang the scan. Note: pygount
    treats all Markdown as documentation/comments, so .md files report zero code
    lines — this is expected. Pseudo-languages (__unknown__, __binary__,
    __empty__, __duplicate__, __generated__) are flagged with is_pseudo_language.

    Args:
        path: Root path to scan (file or directory). Defaults to the current directory.
        suffixes: Comma-separated file extensions to restrict the scan, e.g. "py"
            or "py,ts,tsx". Empty means all languages.
        folders_to_skip: Comma-separated folder patterns to skip, overriding the
            defaults. Prefix with "[...]," to keep the defaults and add more. Empty
            uses a sensible default set (dependency and build folders).
        max_languages: Maximum number of languages to include in the breakdown
            (sorted by code lines, real languages first). Defaults to 50.

    Returns:
        JSON with ok, and on success: path, totals (files/code/documentation/
        empty/string/lines/source), code_to_comment_ratio, comment_percent, and a
        languages breakdown. On failure, ok=false with a message.
    """
    try:
        summary = await anyio.to_thread.run_sync(_scan_summary, path, suffixes, folders_to_skip)  # ty: ignore
    except ModuleNotFoundError:
        return json.dumps(
            {"ok": False, "message": "pygount is not installed. Install it with: pip install pygount"},
            ensure_ascii=False,
        )
    except Exception as e:  # surface any scan error to the model as a message
        return json.dumps({"ok": False, "message": f"pygount scan failed: {e}"}, ensure_ascii=False)

    if max_languages >= 0:
        summary["languages"] = summary["languages"][:max_languages]
    return json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2)


# ── PR code review — GitHub REST API v3 over aiohttp ─────────────────────────

_API_ROOT = "https://api.github.com"
_API_VERSION = "2022-11-28"
_USER_AGENT = "psi-agent-github-review/1.0 (+https://github.com/genuineknowledge/psi-agent)"
_REQUEST_TIMEOUT = 30.0  # seconds, connect+read per HTTP call
_MAX_PAGES = 20  # cap pagination so a huge PR can't blow up the context window
_DEFAULT_DIFF_MAX_CHARS = 60000  # cap returned diff text


def _err(message: str, **extra: Any) -> str:
    """Serialize a failure result to compact JSON."""
    return json.dumps({"ok": False, "message": message, **extra}, ensure_ascii=False)


def _resolve_token() -> str | None:
    """Return a GitHub token, or None if none can be found.

    Order: ``GH_TOKEN`` env, ``GITHUB_TOKEN`` env, then ``gh auth token`` if the
    ``gh`` CLI is installed and logged in. The value is never logged.
    """
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return None


async def _resolve_token_async() -> str | None:
    """Async token resolution: env vars first, then ``gh auth token``.

    The ``gh`` fallback is a subprocess so it runs off the event loop's path via
    ``anyio.run_process``; failures (no gh / not logged in) return None quietly.
    """
    token = _resolve_token()
    if token:
        return token
    if shutil.which("gh") is None:
        return None
    try:
        with anyio.fail_after(15):
            result = await anyio.run_process(["gh", "auth", "token"], check=False)
    except TimeoutError:
        return None
    except OSError:  # gh missing/unlaunchable — fall through to None
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.decode("utf-8", errors="replace").strip()
    return out or None


_NO_TOKEN_MSG = (
    "No GitHub token found. Set GH_TOKEN or GITHUB_TOKEN, or log in with "
    "`gh auth login` so `gh auth token` can supply one. Never hard-code a token."
)


def _headers(token: str, accept: str = "application/vnd.github+json") -> dict[str, str]:
    """Standard auth + versioning headers for a REST call."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": _API_VERSION,
        "User-Agent": _USER_AGENT,
    }


async def _gh_request(
    method: str,
    path: str,
    token: str,
    *,
    accept: str = "application/vnd.github+json",
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make one GitHub REST call and return a normalized result dict.

    ``path`` may be an absolute URL (e.g. a Link-header ``next``) or an
    ``/repos/...`` path appended to the API root. Returns
    ``{ok, status, data, next_url}`` on an HTTP 2xx (``data`` is parsed JSON, or
    raw text when ``accept`` requests diff/patch), else ``{ok: False, ...}``.
    """
    url = path if path.startswith("http") else f"{_API_ROOT}{path}"
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.request(
                method,
                url,
                headers=_headers(token, accept),
                params=params,
                json=json_body,
            ) as response,
        ):
            status = response.status
            # Diff/patch media types come back as plain text, not JSON.
            is_textual = "json" not in accept
            body_text = await response.text()
            if status >= 400:
                message = body_text.strip()
                try:
                    parsed = json.loads(body_text)
                    if isinstance(parsed, dict) and parsed.get("message"):
                        message = str(parsed["message"])
                except ValueError:
                    pass
                return {"ok": False, "status": status, "message": f"GitHub API HTTP {status}: {message}"}

            if is_textual:
                data: Any = body_text
            else:
                data = json.loads(body_text) if body_text.strip() else None

            next_url = None
            link = response.headers.get("Link", "")
            for part in link.split(","):
                if 'rel="next"' in part:
                    start = part.find("<")
                    end = part.find(">", start)
                    if start != -1 and end != -1:
                        next_url = part[start + 1 : end]
                    break
            return {"ok": True, "status": status, "data": data, "next_url": next_url}
    except TimeoutError:
        return {"ok": False, "status": 0, "message": f"Request timed out after {_REQUEST_TIMEOUT:.0f}s."}
    except aiohttp.ClientError as exc:
        return {"ok": False, "status": 0, "message": f"Request failed: {type(exc).__name__}: {exc}"}


async def _gh_paginated(path: str, token: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Follow Link-header pagination and return ``{ok, items}`` or an error dict.

    Stops after ``_MAX_PAGES`` to bound response size; sets ``truncated`` when
    more pages remained.
    """
    params = dict(params or {})
    params.setdefault("per_page", 100)
    items: list[Any] = []
    next_path: str | None = path
    pages = 0
    while next_path and pages < _MAX_PAGES:
        # params only apply to the first request; the next_url carries its own.
        result = await _gh_request("GET", next_path, token, params=params if pages == 0 else None)
        if not result["ok"]:
            return result
        data = result["data"]
        if isinstance(data, list):
            items.extend(data)
        elif data is not None:
            items.append(data)
        next_path = result["next_url"]
        pages += 1
    return {"ok": True, "items": items, "truncated": bool(next_path)}


async def review_pull_request(
    owner: str,
    repo: str,
    number: int,
    include_files: bool = True,
    include_patch: bool = False,
) -> str:
    """Fetch a pull request overview for review: metadata and optionally its changed files.

    Use this to see what a PR does before reviewing it — title, description, author,
    state, source/target branches, mergeability, and the list of changed files with
    per-file additions/deletions. Set include_patch to also get each file's diff hunk
    (patch) inline; for the full unified diff of the whole PR prefer
    get_pull_request_diff instead.

    Requires a GitHub token (GH_TOKEN / GITHUB_TOKEN env, or `gh auth token`).

    Args:
        owner: Repository owner (user or org), e.g. "genuineknowledge".
        repo: Repository name, e.g. "psi-agent".
        number: Pull request number.
        include_files: When true, include the list of changed files with additions,
            deletions, and status. Defaults to true.
        include_patch: When true (and include_files is true), include each file's
            unified-diff patch. Off by default to keep the response compact.

    Returns:
        JSON with ok, and on success: pull (number, title, body, state, draft,
        user, base/head branches, mergeable, changed_files, additions, deletions,
        html_url) and, when requested, files. On failure, ok=false with a message.
    """
    token = await _resolve_token_async()
    if not token:
        return _err(_NO_TOKEN_MSG)

    pr = await _gh_request("GET", f"/repos/{owner}/{repo}/pulls/{number}", token)
    if not pr["ok"]:
        return _err(pr["message"], status=pr.get("status"))

    d = pr["data"]
    pull = {
        "number": d.get("number"),
        "title": d.get("title"),
        "body": d.get("body") or "",
        "state": d.get("state"),
        "draft": d.get("draft"),
        "user": (d.get("user") or {}).get("login"),
        "base": (d.get("base") or {}).get("ref"),
        "head": (d.get("head") or {}).get("ref"),
        "head_sha": (d.get("head") or {}).get("sha"),
        "mergeable": d.get("mergeable"),
        "mergeable_state": d.get("mergeable_state"),
        "changed_files": d.get("changed_files"),
        "additions": d.get("additions"),
        "deletions": d.get("deletions"),
        "commits": d.get("commits"),
        "html_url": d.get("html_url"),
    }
    result: dict[str, Any] = {"ok": True, "pull": pull}

    if include_files:
        files = await _gh_paginated(f"/repos/{owner}/{repo}/pulls/{number}/files", token)
        if not files["ok"]:
            return _err(files["message"], status=files.get("status"))
        result["files_truncated"] = files["truncated"]
        result["files"] = [
            {
                "filename": f.get("filename"),
                "status": f.get("status"),
                "additions": f.get("additions"),
                "deletions": f.get("deletions"),
                "changes": f.get("changes"),
                **({"patch": f.get("patch", "")} if include_patch else {}),
            }
            for f in files["items"]
        ]

    return json.dumps(result, ensure_ascii=False, indent=2)


async def get_pull_request_diff(
    owner: str,
    repo: str,
    number: int,
    max_chars: int = _DEFAULT_DIFF_MAX_CHARS,
) -> str:
    """Get the full unified diff of a pull request as text.

    Returns the PR's complete diff (GitHub's application/vnd.github.v3.diff media
    type) — the same text you'd see from `git diff` — for reviewing exactly what
    changed across all files. Long diffs are truncated to protect the context
    window; raise max_chars to get more.

    Requires a GitHub token (GH_TOKEN / GITHUB_TOKEN env, or `gh auth token`).

    Args:
        owner: Repository owner (user or org).
        repo: Repository name.
        number: Pull request number.
        max_chars: Maximum characters of diff text to return. Defaults to 60000.
            Values <= 0 fall back to the default.

    Returns:
        JSON with ok, and on success: diff (text), truncated (bool), and length.
        On failure, ok=false with a message.
    """
    token = await _resolve_token_async()
    if not token:
        return _err(_NO_TOKEN_MSG)

    cap = max_chars if max_chars > 0 else _DEFAULT_DIFF_MAX_CHARS
    res = await _gh_request(
        "GET",
        f"/repos/{owner}/{repo}/pulls/{number}",
        token,
        accept="application/vnd.github.v3.diff",
    )
    if not res["ok"]:
        return _err(res["message"], status=res.get("status"))

    diff = res["data"] or ""
    truncated = len(diff) > cap
    if truncated:
        diff = diff[:cap]
    return json.dumps({"ok": True, "diff": diff, "truncated": truncated, "length": len(diff)}, ensure_ascii=False)


async def list_pull_request_comments(
    owner: str,
    repo: str,
    number: int,
    kind: str = "all",
) -> str:
    """List a pull request's comments: inline review comments and/or top-level discussion.

    Two comment streams live on a PR: inline "review comments" attached to a file
    and line (path, line, diff_hunk), and top-level "issue comments" on the PR
    conversation. Use this to read existing review feedback before adding your own.

    Requires a GitHub token (GH_TOKEN / GITHUB_TOKEN env, or `gh auth token`).

    Args:
        owner: Repository owner (user or org).
        repo: Repository name.
        number: Pull request number.
        kind: Which comments to return — "review" (inline, file/line-anchored),
            "issue" (top-level PR conversation), or "all" (both). Defaults to "all".

    Returns:
        JSON with ok, and on success: review_comments and/or issue_comments arrays
        (each item has id, user, body, html_url; review comments also have path,
        line, and diff_hunk), plus truncated flags. On failure, ok=false with a message.
    """
    k = kind.strip().lower()
    if k not in ("all", "review", "issue"):
        return _err('kind must be one of: "all", "review", "issue".')

    token = await _resolve_token_async()
    if not token:
        return _err(_NO_TOKEN_MSG)

    result: dict[str, Any] = {"ok": True}

    if k in ("all", "review"):
        rc = await _gh_paginated(f"/repos/{owner}/{repo}/pulls/{number}/comments", token)
        if not rc["ok"]:
            return _err(rc["message"], status=rc.get("status"))
        result["review_comments_truncated"] = rc["truncated"]
        result["review_comments"] = [
            {
                "id": c.get("id"),
                "user": (c.get("user") or {}).get("login"),
                "path": c.get("path"),
                "line": c.get("line"),
                "side": c.get("side"),
                "commit_id": c.get("commit_id"),
                "diff_hunk": c.get("diff_hunk"),
                "body": c.get("body"),
                "html_url": c.get("html_url"),
            }
            for c in rc["items"]
        ]

    if k in ("all", "issue"):
        ic = await _gh_paginated(f"/repos/{owner}/{repo}/issues/{number}/comments", token)
        if not ic["ok"]:
            return _err(ic["message"], status=ic.get("status"))
        result["issue_comments_truncated"] = ic["truncated"]
        result["issue_comments"] = [
            {
                "id": c.get("id"),
                "user": (c.get("user") or {}).get("login"),
                "body": c.get("body"),
                "html_url": c.get("html_url"),
            }
            for c in ic["items"]
        ]

    return json.dumps(result, ensure_ascii=False, indent=2)


async def add_pull_request_comment(
    owner: str,
    repo: str,
    number: int,
    body: str,
    path: str = "",
    line: int = 0,
    side: str = "RIGHT",
    commit_id: str = "",
) -> str:
    """Post a comment on a pull request: top-level discussion or an inline review comment.

    With no path/line, posts a top-level PR conversation comment. Provide path and
    line to post an inline review comment anchored to that file and line of the
    diff; commit_id defaults to the PR head SHA when omitted. This is a WRITE
    operation — the token must have write access to the repository.

    Requires a GitHub token (GH_TOKEN / GITHUB_TOKEN env, or `gh auth token`).

    Args:
        owner: Repository owner (user or org).
        repo: Repository name.
        number: Pull request number.
        body: Comment text (Markdown). Required.
        path: File path for an inline comment (relative to repo root). Empty posts
            a top-level comment instead.
        line: Line number in the file's diff for an inline comment. Required (and > 0)
            when path is given.
        side: Diff side for an inline comment — "RIGHT" (the new version, default) or
            "LEFT" (the old version).
        commit_id: Commit SHA the inline comment applies to. Defaults to the PR head
            SHA when omitted.

    Returns:
        JSON with ok, and on success: comment (id, html_url) and kind ("inline" or
        "issue"). On failure, ok=false with a message.
    """
    if not body.strip():
        return _err("body is required — the comment text cannot be empty.")

    token = await _resolve_token_async()
    if not token:
        return _err(_NO_TOKEN_MSG)

    if path.strip():
        if line <= 0:
            return _err("line must be > 0 for an inline comment (the diff line to anchor to).")
        s = side.strip().upper() or "RIGHT"
        if s not in ("RIGHT", "LEFT"):
            return _err('side must be "RIGHT" or "LEFT".')

        sha = commit_id.strip()
        if not sha:
            pr = await _gh_request("GET", f"/repos/{owner}/{repo}/pulls/{number}", token)
            if not pr["ok"]:
                return _err(pr["message"], status=pr.get("status"))
            sha = ((pr["data"].get("head") or {}).get("sha")) or ""
            if not sha:
                return _err("Could not resolve the PR head commit SHA; pass commit_id explicitly.")

        res = await _gh_request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/comments",
            token,
            json_body={"body": body, "commit_id": sha, "path": path.strip(), "line": line, "side": s},
        )
        kind = "inline"
    else:
        res = await _gh_request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            token,
            json_body={"body": body},
        )
        kind = "issue"

    if not res["ok"]:
        return _err(res["message"], status=res.get("status"))
    c = res["data"] or {}
    return json.dumps(
        {"ok": True, "kind": kind, "comment": {"id": c.get("id"), "html_url": c.get("html_url")}},
        ensure_ascii=False,
    )

"""GitHub toolset - inspect codebases with pygount.

Groups tools for working with source repositories. Currently exposes
``inspect_codebase``: a wrapper around `pygount <https://pypi.org/project/pygount/>`_
that counts lines of code, breaks them down by language, and reports
code-vs-comment ratios — similar to ``cloc``/``sloccount`` but backed by
Pygments so it recognizes hundreds of languages.

``pygount`` is a synchronous library, so the blocking scan runs in a worker
thread via ``anyio.to_thread.run_sync`` to keep the event loop responsive.
"""

from __future__ import annotations

import json

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
    languages.sort(key=lambda entry: (entry["is_pseudo_language"], -int(entry["code"])))

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

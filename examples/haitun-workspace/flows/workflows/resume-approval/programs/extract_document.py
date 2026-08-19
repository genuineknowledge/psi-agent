"""Extract one resume or standard into a temporary workspace text file."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from typing import Any
from xml.etree import ElementTree

_SUPPORTED_SUFFIXES = frozenset({".docx", ".md", ".pdf", ".txt"})
_MAX_SOURCE_BYTES = 50 * 1024 * 1024
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WORD_TAG = f"{{{_WORD_NAMESPACE}}}"


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _load_payload() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("Program stdin must contain an inputs object")
    return inputs


def _descriptor(inputs: dict[str, Any]) -> tuple[str, str, str]:
    provided = [(name, inputs[name]) for name in ("resume_file", "standard_file") if name in inputs]
    if len(provided) != 1:
        raise ValueError("Exactly one of resume_file or standard_file must be provided")
    kind, value = provided[0]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            parsed = json.loads(stripped)
            if not isinstance(parsed, dict):
                raise TypeError(f"{kind} JSON must decode to an object")
            value = parsed
        else:
            value = {"path": value}
    if not isinstance(value, dict):
        raise TypeError(f"{kind} must be a path string or object")
    path = value.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"{kind}.path must be a non-empty string")
    # Staging supplies a neutral assessment-facing name.  ``original_name`` is
    # provenance only and may contain phone numbers or other private attributes.
    name = value.get("name", value.get("original_name", os.path.basename(path)))
    if not isinstance(name, str) or not name.strip():
        name = os.path.basename(path)
    return kind.removesuffix("_file"), path, os.path.basename(name.strip())


def _resolve_source(path: str, workspace_root: str) -> tuple[str, str]:
    candidate = path if os.path.isabs(path) else os.path.join(workspace_root, path)
    source_path = os.path.realpath(candidate)
    if not _inside(source_path, workspace_root):
        raise PermissionError("Source path must stay inside the current workspace")
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"Source is not a regular file: {path}")
    size = os.path.getsize(source_path)
    if size > _MAX_SOURCE_BYTES:
        raise ValueError(f"Source exceeds {_MAX_SOURCE_BYTES} bytes: {path}")
    suffix = os.path.splitext(source_path)[1].lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(_SUPPORTED_SUFFIXES))
        raise ValueError(f"Unsupported document format {suffix!r}; expected one of {supported}")
    relative_path = os.path.relpath(source_path, workspace_root).replace(os.sep, "/")
    return source_path, relative_path


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_plain_text(path: str) -> str:
    with open(path, "rb") as source:
        raw = source.read()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", raw, 0, len(raw), "file is neither UTF-8 nor GB18030 text")


def _xml_text(element: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in element.iter():
        if node.tag == f"{_WORD_TAG}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{_WORD_TAG}tab":
            parts.append("\t")
        elif node.tag in {f"{_WORD_TAG}br", f"{_WORD_TAG}cr"}:
            parts.append("\n")
    return "".join(parts).strip()


def _read_docx(path: str) -> str:
    with zipfile.ZipFile(path) as archive:
        try:
            document_xml = archive.read("word/document.xml")
        except KeyError as error:
            raise ValueError("DOCX has no word/document.xml") from error
    root = ElementTree.fromstring(document_xml)
    body = root.find(f"{_WORD_TAG}body")
    if body is None:
        raise ValueError("DOCX has no document body")
    blocks: list[str] = []
    for child in body:
        if child.tag == f"{_WORD_TAG}p":
            text = _xml_text(child)
            if text:
                blocks.append(text)
        elif child.tag == f"{_WORD_TAG}tbl":
            for row in child.findall(f"{_WORD_TAG}tr"):
                cells = [_xml_text(cell) for cell in row.findall(f"{_WORD_TAG}tc")]
                if any(cells):
                    blocks.append("\t".join(cells))
    return "\n".join(blocks)


def _normalize_text(text: str) -> str:
    normalized = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{4,}", "\n\n\n", normalized)
    return normalized.strip()


def _extract(path: str, suffix: str) -> str:
    if suffix in {".md", ".txt"}:
        return _read_plain_text(path)
    if suffix == ".docx":
        return _read_docx(path)
    raise AssertionError(f"Unreachable suffix: {suffix}")


def _write_extracted(workspace_root: str, digest: str, text: str) -> str:
    relative_dir = os.path.join(".psi", "resume-approval", "extracted")
    output_dir = os.path.join(workspace_root, relative_dir)
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{digest}.txt")
    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=output_dir,
            encoding="utf-8",
            newline="\n",
        ) as temporary:
            temporary_path = temporary.name
            temporary.write(text)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return os.path.relpath(output_path, workspace_root).replace(os.sep, "/")


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    """Extract one configured input and return safe metadata."""
    resolved_workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    kind, requested_path, source_name = _descriptor(inputs)
    source_path, source_relative_path = _resolve_source(requested_path, resolved_workspace)
    suffix = os.path.splitext(source_path)[1].lower()
    digest = _sha256(source_path)
    if suffix == ".pdf":
        return {
            "schema_version": "1.0",
            "kind": kind,
            "source_name": source_name,
            "source_path": source_relative_path,
            "source_sha256": digest,
            "format": suffix,
            "extraction_mode": "read_pdf_tool",
        }
    text = _normalize_text(_extract(source_path, suffix))
    if not text:
        raise ValueError("Document contains no usable text")
    extracted_path = _write_extracted(resolved_workspace, digest, text)
    return {
        "schema_version": "1.0",
        "kind": kind,
        "source_name": source_name,
        "source_path": source_relative_path,
        "source_sha256": digest,
        "format": suffix,
        "extraction_mode": "workspace_text",
        "character_count": len(text),
        "extracted_text_path": extracted_path,
    }


def _program_outputs(result: dict[str, Any]) -> dict[str, Any]:
    receipt = {
        "schema_version": "1.0",
        "status": "complete",
        "kind": result["kind"],
        "source_sha256": result["source_sha256"],
        "extraction_mode": result["extraction_mode"],
    }
    if result["kind"] == "standard":
        return {
            "extracted_standards": result,
            "standard_extraction_receipts": receipt,
        }
    if result["kind"] == "resume":
        return {
            "extracted_resumes": result,
            "resume_extraction_receipts": receipt,
        }
    raise ValueError(f"Unsupported extraction kind: {result['kind']!r}")


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    result = run(_load_payload())
    sys.stdout.write(json.dumps(_program_outputs(result), ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

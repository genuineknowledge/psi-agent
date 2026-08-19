from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROGRAM_PATH = Path(__file__).resolve().parents[1] / "programs" / "stage_resume_files.py"
EXTRACTOR_PATH = Path(__file__).resolve().parents[1] / "programs" / "extract_document.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("stage_resume_files", PROGRAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_extractor():
    spec = importlib.util.spec_from_file_location("extract_document", EXTRACTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, content: bytes = b"resume") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_actual_runner_attachment_path_string_is_staged(tmp_path: Path) -> None:
    """Removing string-path support must not break the runner's observed upload artifact."""
    module = _load_module()
    workspace = tmp_path / "workspace"
    downloads = tmp_path / "Downloads" / ".psi"
    workspace.mkdir()
    attachment = _write(downloads / "2026-08-09" / "候选人.pdf")

    staged = module.run(
        {"resume_files": [str(attachment)], "batch_id": "batch-1"},
        str(workspace),
        str(downloads),
    )

    assert len(staged) == 1
    assert staged[0]["original_name"] == "候选人.pdf"
    assert staged[0]["format"] == ".pdf"
    assert staged[0]["size_bytes"] == len(b"resume")
    assert (workspace / staged[0]["path"]).read_bytes() == b"resume"


@pytest.mark.parametrize("suffix", [".pdf", ".docx", ".md", ".txt"])
def test_every_supported_attachment_format_is_upload_ready(tmp_path: Path, suffix: str) -> None:
    module = _load_module()
    workspace = tmp_path / "workspace"
    downloads = tmp_path / "Downloads" / ".psi"
    workspace.mkdir()
    attachment = _write(downloads / "2026-08-09" / f"candidate{suffix}", suffix.encode())

    staged = module.run(
        {"resume_files": [str(attachment)], "batch_id": f"batch-{suffix[1:]}"},
        str(workspace),
        str(downloads),
    )

    assert staged[0]["format"] == suffix
    assert staged[0]["name"] == f"resume{suffix}"
    assert staged[0]["size_bytes"] == len(suffix.encode())
    assert staged[0]["temporary"] is True


def test_workspace_path_descriptor_remains_supported(tmp_path: Path) -> None:
    """Dropping local compatibility must not break reproducible workspace fixtures."""
    module = _load_module()
    workspace = tmp_path / "workspace"
    downloads = tmp_path / "Downloads" / ".psi"
    local_resume = _write(workspace / "fixtures" / "source.docx", b"docx-resume")

    staged = module.run(
        {
            "resume_files": [{"path": "fixtures/source.docx", "name": "本地候选人.docx"}],
            "batch_id": "batch-2",
        },
        str(workspace),
        str(downloads),
    )

    assert len(staged) == 1
    assert staged[0]["original_name"] == "本地候选人.docx"
    assert (workspace / staged[0]["path"]).read_bytes() == local_resume.read_bytes()


def test_uploaded_attachment_wins_when_workspace_file_has_same_sha(tmp_path: Path) -> None:
    """Processing input order must not let a local duplicate displace the uploaded attachment."""
    module = _load_module()
    workspace = tmp_path / "workspace"
    downloads = tmp_path / "Downloads" / ".psi"
    local_resume = _write(workspace / "fixtures" / "local.pdf", b"same-resume")
    attachment = _write(downloads / "2026-08-09" / "uploaded.pdf", b"same-resume")

    staged = module.run(
        {
            "resume_files": [
                {"path": str(local_resume), "name": "local.pdf"},
                str(attachment),
            ],
            "batch_id": "batch-3",
        },
        str(workspace),
        str(downloads),
    )

    assert len(staged) == 1
    assert staged[0]["original_name"] == "uploaded.pdf"


def test_phone_in_original_filename_never_enters_assessment_source_name(tmp_path: Path) -> None:
    """Contact data in an upload filename must not conflict with immutable assessment metadata."""
    stager = _load_module()
    extractor = _load_extractor()
    workspace = tmp_path / "workspace"
    downloads = tmp_path / "Downloads" / ".psi"
    workspace.mkdir()
    private_filename = "候选人-" + "155" + "0000" + "0000" + "-简历.pdf"
    attachment = _write(downloads / "2026-08-09" / private_filename)

    staged = stager.run(
        {"resume_files": [str(attachment)], "batch_id": "batch-private-name"},
        str(workspace),
        str(downloads),
    )
    extracted = extractor.run({"resume_file": staged[0]}, str(workspace))

    assert staged[0]["original_name"] == private_filename
    assert staged[0]["name"] == "resume.pdf"
    assert extracted["source_name"] == "resume.pdf"
    assert extracted["source_sha256"] == staged[0]["sha256"]


def test_unsupported_attachment_extension_is_rejected(tmp_path: Path) -> None:
    """Weakening the extension allowlist must not stage an executable upload."""
    module = _load_module()
    workspace = tmp_path / "workspace"
    downloads = tmp_path / "Downloads" / ".psi"
    workspace.mkdir()
    attachment = _write(downloads / "2026-08-09" / "resume.exe")

    with pytest.raises(ValueError, match="Unsupported resume format"):
        module.run(
            {"resume_files": [str(attachment)], "batch_id": "batch-4"},
            str(workspace),
            str(downloads),
        )


def test_path_outside_workspace_and_attachment_root_is_rejected(tmp_path: Path) -> None:
    """Removing path containment must not let an arbitrary host file enter the workflow."""
    module = _load_module()
    workspace = tmp_path / "workspace"
    downloads = tmp_path / "Downloads" / ".psi"
    workspace.mkdir()
    outside = _write(tmp_path / "outside" / "resume.pdf")

    with pytest.raises(PermissionError, match="current workspace"):
        module.run(
            {"resume_files": [str(outside)], "batch_id": "batch-5"},
            str(workspace),
            str(downloads),
        )


def test_file_larger_than_single_request_upload_limit_is_rejected_before_staging(tmp_path: Path) -> None:
    module = _load_module()
    workspace = tmp_path / "workspace"
    downloads = tmp_path / "Downloads" / ".psi"
    workspace.mkdir()
    attachment = downloads / "2026-08-09" / "oversized.pdf"
    attachment.parent.mkdir(parents=True)
    with attachment.open("wb") as stream:
        stream.seek(module._MAX_SOURCE_BYTES)
        stream.write(b"x")

    with pytest.raises(ValueError, match=str(module._MAX_SOURCE_BYTES)):
        module.run(
            {"resume_files": [str(attachment)], "batch_id": "batch-oversized"},
            str(workspace),
            str(downloads),
        )


def test_staging_manifest_omits_local_paths_and_original_names(tmp_path: Path) -> None:
    module = _load_module()
    workspace = tmp_path / "workspace"
    downloads = tmp_path / "Downloads" / ".psi"
    workspace.mkdir()
    attachment = _write(downloads / "2026-08-09" / "private-name.pdf")
    inputs = {"resume_files": [str(attachment)], "batch_id": "batch-manifest"}

    outputs = module._program_outputs(inputs, module.run(inputs, str(workspace), str(downloads)))
    manifest_text = str(outputs["resume_staging_manifest"])

    assert "private-name.pdf" not in manifest_text
    assert str(workspace) not in manifest_text
    assert "path" not in outputs["resume_staging_manifest"]


def test_empty_resume_list_is_rejected(tmp_path: Path) -> None:
    """Accepting an empty list must not start a batch with no resume evidence."""
    module = _load_module()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="non-empty list"):
        module.run(
            {"resume_files": [], "batch_id": "batch-6"},
            str(workspace),
            str(tmp_path / "Downloads" / ".psi"),
        )

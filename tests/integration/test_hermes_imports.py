from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path


def test_background_review_imports_from_workspace_root(monkeypatch) -> None:
    workspace = Path("examples/hermes-style-workspace")
    monkeypatch.syspath_prepend(str(workspace))

    module = importlib.import_module("systems.background_review")

    assert hasattr(module, "BackgroundReview")


def test_background_review_imports_from_file_location() -> None:
    path = Path("examples/hermes-style-workspace/systems/background_review.py")
    spec = importlib.util.spec_from_file_location("hermes_background_review_standalone", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert hasattr(module, "BackgroundReview")

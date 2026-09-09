"""`scripts/check_runtime_env_keys.py` 的判据测试。

守的是"两处 `.env` 注入与清单分叉"这件事。分叉的失败方向最坏: `env:` 块少一个 key,
那个变量在该步骤里永远没值, 注入器不写这一行、日志报 `present: False` —— 与"secret
真的没配"长得一模一样, 没有任何东西变红。

**判据能拦住它全靠非 0 返回**, 所以红的每一面都要单独测。

脚本在 `scripts/` 下不属包, 故用 importlib 按路径加载。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_runtime_env_keys.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_runtime_env_keys", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


chk = _load()

_STEP = """\
      - name: Inject runtime .env
        shell: pwsh
        env:
{entries}
        run: |
          echo hi
"""


def _workflow(*blocks: list[str]) -> str:
    """两段 (或任意段) 注入步骤拼成的伪 workflow, 每段给定 env 条目行。"""
    return "jobs:\n  build:\n    steps:\n" + "".join(
        _STEP.format(entries="\n".join(f"          {line}" for line in block)) for block in blocks
    )


def _aligned(keys: list[str]) -> list[str]:
    return [f"{key}: ${{{{ secrets.{key} }}}}" for key in keys]


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key_list: str, workflow: str) -> None:
    keys_file = tmp_path / "runtime-env-keys.txt"
    keys_file.write_text(key_list, encoding="utf-8")
    wf = tmp_path / "pyinstaller.yml"
    wf.write_text(workflow, encoding="utf-8")
    monkeypatch.setattr(chk, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(chk, "KEY_LIST", keys_file)
    monkeypatch.setattr(chk, "WORKFLOW", wf)


def test_key_list_strips_comments_and_blanks() -> None:
    text = "# 注释\n\nSERPER_API_KEY\n  DASHSCOPE_API_KEY  \nFOO  # 行尾注释\n"
    assert chk.parse_key_list(text) == ["SERPER_API_KEY", "DASHSCOPE_API_KEY", "FOO"]


def test_aligned_blocks_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["SERPER_API_KEY", "DASHSCOPE_API_KEY"]
    _setup(tmp_path, monkeypatch, "\n".join(keys), _workflow(_aligned(keys), _aligned(keys)))
    assert chk.main([]) == 0


def test_one_block_missing_a_key_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**这就是最坏的那种分叉**: 只有一处漏, 装机 .env 少一行且完全不报错。"""
    keys = ["SERPER_API_KEY", "DASHSCOPE_API_KEY"]
    _setup(
        tmp_path,
        monkeypatch,
        "\n".join(keys),
        _workflow(_aligned(keys), _aligned(["SERPER_API_KEY"])),
    )
    assert chk.main([]) == 1
    assert "DASHSCOPE_API_KEY" in capsys.readouterr().out


def test_extra_key_not_in_list_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`env:` 块多出清单外的 key 也要红: 清单是唯一来源, 否则两处会就此分叉。"""
    keys = ["SERPER_API_KEY"]
    _setup(
        tmp_path,
        monkeypatch,
        "\n".join(keys),
        _workflow(_aligned(keys), _aligned(["SERPER_API_KEY", "STRAY_KEY"])),
    )
    assert chk.main([]) == 1


def test_value_must_come_from_same_named_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """名字对而取值取错 secret 时要红。

    日志照样报 `present: True`, 但 .env 里落的是另一个 secret 的值 —— 名字齐全的
    检查会放过这种错。
    """
    keys = ["SERPER_API_KEY", "DASHSCOPE_API_KEY"]
    wrong = ["SERPER_API_KEY: ${{ secrets.SERPER_API_KEY }}", "DASHSCOPE_API_KEY: ${{ secrets.SERPER_API_KEY }}"]
    _setup(tmp_path, monkeypatch, "\n".join(keys), _workflow(_aligned(keys), wrong))
    assert chk.main([]) == 1
    assert "不是 secrets.DASHSCOPE_API_KEY" in capsys.readouterr().out


def test_wrong_number_of_injection_steps_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """必须恰好两处。删掉一处注入 (或改了步骤名) 要红, 不能"少了就少了"地放过。"""
    keys = ["SERPER_API_KEY"]
    _setup(tmp_path, monkeypatch, "\n".join(keys), _workflow(_aligned(keys)))
    assert chk.main([]) == 1

    _setup(
        tmp_path,
        monkeypatch,
        "\n".join(keys),
        _workflow(_aligned(keys), _aligned(keys), _aligned(keys)),
    )
    assert chk.main([]) == 1


def test_empty_key_list_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """清单被清空时要红 —— 否则"零个 key 全对齐"会恒绿。"""
    _setup(tmp_path, monkeypatch, "# 只有注释\n", _workflow([], []))
    assert chk.main([]) == 1


def test_env_block_parsing_stops_at_step_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`env:` 块的解析不能溢出到后面的步骤里。

    溢出会把下一步的键当成本步的条目, 让"缺 key"变成假绿。
    """
    workflow = (
        "jobs:\n  build:\n    steps:\n"
        "      - name: Inject runtime .env\n"
        "        env:\n"
        "          SERPER_API_KEY: ${{ secrets.SERPER_API_KEY }}\n"
        "        run: echo one\n"
        "      - name: Something else\n"
        "        env:\n"
        "          DASHSCOPE_API_KEY: ${{ secrets.DASHSCOPE_API_KEY }}\n"
        "        run: echo two\n"
    )
    blocks = chk.injection_env_blocks(workflow)
    assert len(blocks) == 1
    assert blocks[0] == {"SERPER_API_KEY": "${{ secrets.SERPER_API_KEY }}"}


def test_repo_state_passes() -> None:
    """库内两处注入应与清单一致 —— 只加一处时这条会红。"""
    assert chk.main([]) == 0


def test_repo_list_covers_fusion_memory_requirements() -> None:
    """清单必须含 Fusion Memory 实际读的那几个变量。

    绑定到 embedding.py 的真实读取点: 那边改了变量名而清单没跟, 这条会红。
    """
    keys = set(chk.parse_key_list(chk.KEY_LIST.read_text(encoding="utf-8")))
    assert "DASHSCOPE_API_KEY" in keys
    llm_group = {
        "FUSION_MEMORY_MODEL_PROVIDER",
        "FUSION_MEMORY_MODEL_NAME",
        "FUSION_MEMORY_MODEL_API_KEY",
        "FUSION_MEMORY_MODEL_BASE_URL",
    }
    # 四个一组, embedding.py:118 是 all(...): 少一个整组落空, 所以清单必须整组都有。
    assert llm_group <= keys
    embedding = (chk.REPO_ROOT / "agents" / "desktop" / "tools" / "_fusion_memory" / "embedding.py").read_text(
        encoding="utf-8"
    )
    for key in {"DASHSCOPE_API_KEY", *llm_group}:
        assert key in embedding, f"{key} 在清单里但 embedding.py 不读它"

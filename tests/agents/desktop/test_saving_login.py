"""`saving_login` 的回归判据 —— 平台授权层。

三层判据, 按重要性排:

1. **判不准不写记录**。`report` 只认两类可信信号(落到登录域 / 到达 gate 页本身), 其余
   一律 `unknown`, 且**绝不因此写记录**。让一次认不出的 URL 把状态固化成结论, 与本仓
   facts 契约里「`MISSING` 不得被静默填成 `false`」是同一条纪律 —— 说错比说不出坏得多。
2. **一次授权覆盖后续**: 写下记录后 `status` 直接返回, 不再重复询问; 记录**不自动失效**,
   超期只提示(`stale`), 不改变状态; `forget` 才能撤销。
3. **数据分面**: 平台定义是出厂内容(`platforms/*.yaml`), 授权记录是运行期状态
   (`{appdata}/saving/platforms.json`)。测试把 AppData 指到 tmp, 保证不碰真实记忆区。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

# 运行期靠同级 conftest 把 tools 目录挂上 sys.path(裸名导入); ty 不认那个插入,
# 只能按包路径解析。与 test_fusion_memory_tools.py 同套写法。
if TYPE_CHECKING:
    from agents.desktop.tools import saving_login as _saving_login
else:
    import saving_login as _saving_login

WORKSPACE_ROOT = Path(__file__).resolve().parents[3] / "agents" / "desktop"


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把记忆区指到 tmp —— 授权记录绝不能写进开发者真实的 AppData。"""
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path / "appdata"))
    return tmp_path / "appdata"


def _record_file(appdata: Path) -> Path:
    return appdata / "saving" / "platforms.json"


async def _call(**kwargs: Any) -> dict[str, Any]:
    return json.loads(await _saving_login.saving_login(**kwargs))


# --------------------------------------------------------------------------- #
# 平台定义(出厂内容)
# --------------------------------------------------------------------------- #


def test_platform_definitions_ship_as_data() -> None:
    """加平台 = 加一个 yaml, 不改代码。"""
    files = sorted(p.name for p in (WORKSPACE_ROOT / "platforms").glob("*.yaml"))
    assert files == ["jd.yaml", "taobao.yaml"]


@pytest.mark.parametrize("key", ["jd", "taobao"])
def test_every_platform_declares_what_the_tools_need(key: str) -> None:
    data = yaml.safe_load((WORKSPACE_ROOT / "platforms" / f"{key}.yaml").read_text(encoding="utf-8"))
    assert data["key"] == key
    assert data["name"]
    assert data["gate"], "缺 gate 就没法打开登录页, 也没法判「到达了 gate 页」"
    assert data["login_hosts"], "缺 login_hosts 就没有可信的「未登录」判据"
    assert data["verified_at"], "平台会改域名/改登录流程, 配置必须带核验日期"


# --------------------------------------------------------------------------- #
# 判定: 核心是「判不准就说判不准」
# --------------------------------------------------------------------------- #


async def test_landing_on_a_login_host_means_logged_out() -> None:
    payload = await _call(platform="jd", action="report", url="https://passport.jd.com/new/login.aspx")
    assert payload["status"] == "logged_out"
    assert payload["basis"] == "landed_on_login_host"


async def test_login_host_matches_subdomains() -> None:
    payload = await _call(platform="taobao", action="report", url="https://x.login.taobao.com/foo")
    assert payload["status"] == "logged_out"


async def test_reaching_the_gate_page_means_logged_in() -> None:
    payload = await _call(platform="jd", action="report", url="https://order.jd.com/center/list.action")
    assert payload["status"] == "logged_in"
    assert payload["basis"] == "reached_gate"


async def test_gate_host_with_a_different_path_is_not_logged_in() -> None:
    """gate 失效返回 404 时主机名一样、路径不同 —— 不能当成登录成功。"""
    payload = await _call(platform="jd", action="report", url="https://order.jd.com/some/error")
    assert payload["status"] == "unknown"
    assert payload["basis"] == "gate_host_but_other_path"


@pytest.mark.parametrize(
    ("url", "basis"),
    [
        ("https://www.jd.com/", "unrecognized_url"),
        ("", "no_url"),
        ("https:///nohost", "no_host"),
    ],
)
async def test_unrecognized_urls_stay_unknown(url: str, basis: str) -> None:
    payload = await _call(platform="jd", action="report", url=url)
    assert payload["status"] == "unknown"
    assert payload["basis"] == basis


async def test_unknown_verdict_does_not_get_written(isolated_appdata: Path) -> None:
    """**本文件最重要的一条**: 认不出就不写记录, 别把"不知道"固化成结论。"""
    await _call(platform="jd", action="report", url="https://www.jd.com/")
    assert not _record_file(isolated_appdata).exists()

    # 而且后续 status 仍是「从没核过」, 不是「未登录」
    assert (await _call(platform="jd", action="status"))["status"] == "unknown"


async def test_unknown_verdict_tells_the_caller_what_to_do() -> None:
    payload = await _call(platform="jd", action="report", url="https://www.jd.com/")
    assert payload["ok"] is True
    assert payload["gate"]  # 把该打开的地址给回去
    assert "地址栏" in payload["note"]


# --------------------------------------------------------------------------- #
# 一次授权覆盖后续
# --------------------------------------------------------------------------- #


async def test_record_persists_and_status_reads_it_back(isolated_appdata: Path) -> None:
    await _call(platform="京东", action="report", url="https://order.jd.com/center/list.action")

    payload = await _call(platform="jd", action="status")
    assert payload["status"] == "logged_in"
    assert payload["stale"] is False
    assert payload["checked_at"]
    assert _record_file(isolated_appdata).is_file()


async def test_platform_can_be_named_by_key_or_chinese_name() -> None:
    for token in ("jd", "京东", "JD"):
        payload = await _call(platform=token, action="status")
        assert payload["platform"]["key"] == "jd", token


async def test_user_confirmation_records_without_a_url() -> None:
    payload = await _call(platform="taobao", action="confirm")
    assert payload["status"] == "logged_in"
    assert payload["basis"] == "user-confirmed"
    assert (await _call(platform="taobao", action="status"))["status"] == "logged_in"


async def test_status_does_not_re_probe(isolated_appdata: Path) -> None:
    """`status` 只读记录 —— 尊重「一次授权覆盖后续」, 不每次问也不每次探。"""
    await _call(platform="jd", action="report", url="https://order.jd.com/center/list.action")
    before = _record_file(isolated_appdata).read_text(encoding="utf-8")

    await _call(platform="jd", action="status")
    await _call(platform="jd", action="status")
    assert _record_file(isolated_appdata).read_text(encoding="utf-8") == before


async def test_old_record_is_flagged_stale_but_keeps_its_status(isolated_appdata: Path) -> None:
    """超期只提示, 不自动失效 —— 一次授权的语义不该被时间偷偷推翻。"""
    path = _record_file(isolated_appdata)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "platforms": {"jd": {"status": "logged_in", "source": "x", "checked_at": "2020-01-01T00:00:00+08:00"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = await _call(platform="jd", action="status")
    assert payload["status"] == "logged_in"  # 状态不变
    assert payload["stale"] is True
    assert payload["age_days"] > 7
    assert "重新核实" in payload["note"]


async def test_forget_revokes_and_returns_to_unknown(isolated_appdata: Path) -> None:
    await _call(platform="jd", action="confirm")
    assert (await _call(platform="jd", action="status"))["status"] == "logged_in"

    payload = await _call(platform="jd", action="forget")
    assert payload["status"] == "unknown"
    assert (await _call(platform="jd", action="status"))["status"] == "unknown"
    # 撤销只影响这一个平台
    assert _record_file(isolated_appdata).is_file()
    assert json.loads(_record_file(isolated_appdata).read_text(encoding="utf-8"))["platforms"] == {}


async def test_list_shows_every_platform_so_the_user_can_see_and_revoke() -> None:
    await _call(platform="jd", action="confirm")

    payload = await _call(action="list")
    rows = {row["key"]: row for row in payload["platforms"]}
    assert set(rows) == {"jd", "taobao"}
    assert rows["jd"]["status"] == "logged_in"
    assert rows["jd"]["name"] == "京东"
    assert rows["taobao"]["status"] == "unknown"


# --------------------------------------------------------------------------- #
# 风控: 检测靠模型, 规范响应靠工具
# --------------------------------------------------------------------------- #


async def test_blocked_records_the_state_and_returns_the_canonical_message() -> None:
    """模型看到验证码页时调 blocked -> 拿到一段固定话术, 并要求它停下。"""
    payload = await _call(platform="jd", action="blocked")
    assert payload["status"] == "blocked"
    assert payload["message"]
    assert "不要自动重试" in payload["note"]


async def test_blocked_message_offers_both_ways_out() -> None:
    """话术必须同时给出两条出口: 手动过验证、以及**降级到截图**。"""
    message = (await _call(platform="jd", action="blocked"))["message"]
    assert "继续" in message  # ① 手动过验证后回复继续
    assert "截图" in message  # ② 降级到截图
    assert "不会自动重试" in message


async def test_blocked_survives_into_status_so_the_next_call_does_not_retry() -> None:
    """跨调用仍然有效的那一半: 下一轮 status 看到 blocked, 就知道别再试。"""
    await _call(platform="jd", action="blocked")

    payload = await _call(platform="jd", action="status")
    assert payload["status"] == "blocked"
    assert payload["rate_limit_message"]  # 话术再给一次
    assert "不要自动重试" in payload["note"]
    assert "换个入口" in payload["note"]


async def test_blocked_is_not_a_lock() -> None:
    """**刻意不做成锁**: 用户手动过了验证, 正常流程就该覆盖它, 不该再要一次确认。"""
    await _call(platform="jd", action="blocked")

    await _call(platform="jd", action="report", url="https://order.jd.com/center/list.action")
    assert (await _call(platform="jd", action="status"))["status"] == "logged_in"

    await _call(platform="jd", action="blocked")
    await _call(platform="jd", action="confirm")
    assert (await _call(platform="jd", action="status"))["status"] == "logged_in"


async def test_blocked_shows_up_in_the_list() -> None:
    await _call(platform="jd", action="blocked")
    rows = {row["key"]: row for row in (await _call(action="list"))["platforms"]}
    assert rows["jd"]["status"] == "blocked"
    assert rows["taobao"]["status"] == "unknown"


async def test_only_a_blocked_record_repeats_the_message() -> None:
    """`rate_limit_message` 只在 blocked 时出现 —— 否则每轮都塞一段无关话术。"""
    await _call(platform="jd", action="confirm")
    payload = await _call(platform="jd", action="status")
    assert payload["status"] == "logged_in"
    assert payload["rate_limit_message"] == ""


# --------------------------------------------------------------------------- #
# 容错与错误码
# --------------------------------------------------------------------------- #


async def test_corrupt_record_reads_as_empty_instead_of_crashing(isolated_appdata: Path) -> None:
    """丢了记录只是让用户被再问一次, 可恢复; 抛异常会挡住整条链路。"""
    path = _record_file(isolated_appdata)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    payload = await _call(platform="jd", action="status")
    assert payload["ok"] is True
    assert payload["status"] == "unknown"


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"action": "explode"}, "未知 action"),
        ({"platform": "拼多多", "action": "status"}, "未知平台"),
    ],
)
async def test_bad_input_returns_a_stable_error(kwargs: dict[str, Any], needle: str) -> None:
    payload = await _call(**kwargs)
    assert payload["ok"] is False
    assert needle in payload["reason"]


async def test_unknown_platform_lists_what_is_available() -> None:
    payload = await _call(platform="拼多多", action="status")
    assert "jd" in " ".join(payload["known"])
    assert "taobao" in " ".join(payload["known"])

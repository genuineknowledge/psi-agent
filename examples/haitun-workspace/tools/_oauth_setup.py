"""飞书授权的**环境自检与配置指导** —— 让 agent 能回答「我该怎么配」。

``_oauth_receiver`` 负责在授权时**选**通道, 本模块负责在授权之前**解释**这个选择:
当前算哪种部署形态、能不能免抄 code、不能的话确切缺哪一项、飞书后台该登记哪个
重定向 URL。这些判断的依据 (环境变量、端口占用、基址可达性) 本来就都在, 只是从未
暴露给 agent —— 于是用户问「重定向 URL 填什么」时, agent 只能猜。

一个真实的坑值得单独记: ``PSI_FEISHU_REDIRECT_URI`` 指向一个**早已失效**的地址
(如过期的临时隧道域名) 时, ``plan_receiver`` 会尊重它并一路判成 manual, 于是所有人
被静默打回手工贴码, 而只读环境变量根本看不出问题 —— 变量明明设着。所以这里要对
基址做一次真实探测: 只 GET ``/oauth/callback`` 且不带 code/state, 打的是本部署
自己的地址, 拿到任何 HTTP 响应即算可达。
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

import _oauth_receiver as _rx

# 探测超时给得短: 这是给用户的一次体检, 不该让对话卡住。
_PROBE_TIMEOUT = 5.0


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _in_container() -> bool:
    """是否跑在容器里 (容器内的 127.0.0.1 和用户浏览器的不是一回事)。"""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8", errors="replace") as fh:
            return "docker" in fh.read() or "containerd" in fh.read()
    except OSError:
        return False


def _host_is_loopback(host: str) -> bool:
    return host.lower() in ("127.0.0.1", "localhost", "::1")


def _host_is_private(host: str) -> bool:
    """是否内网地址 (只有同一内网的浏览器打得开)。"""
    import ipaddress  # noqa: PLC0415

    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def _port_in_use(port: int) -> bool:
    """端口是否已被占用 (与 ``_rx._port_is_free`` 互补, 语义正向便于报告)。"""
    return not _rx._port_is_free(port)


async def probe_base(base: str) -> dict[str, Any]:
    """探测回调基址是否真的可达。

    只请求 ``/oauth/callback`` 且**不带任何 code/state**: 该端点缺 state 时回 400,
    这正是「通了」的证据 —— 能拿到 HTTP 状态码就说明请求到达了处理器。连不上
    (DNS 失败/拒连/超时) 才是问题, 那意味着地址写着但形同虚设。
    """
    if not base:
        return {"probed": False, "reachable": False, "detail": "未配置基址"}
    import httpx  # noqa: PLC0415

    url = f"{base}{_rx._CALLBACK_PATH}"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(url)
    except Exception as exc:
        return {
            "probed": True,
            "reachable": False,
            "detail": f"{type(exc).__name__}: {str(exc)[:160]}",
        }
    return {"probed": True, "reachable": True, "http_status": resp.status_code}


def detect_deployment() -> dict[str, Any]:
    """判断部署形态: 本机开发 还是 服务器部署 (决定该给哪套建议)。

    两者的正确做法几乎相反: 本机开发浏览器和 agent 同机, 回环通道零配置就能用;
    服务器部署 (尤其接飞书多用户) 时用户在自己电脑/手机上点授权, 回环根本回不来,
    必须走 Gateway 通道并给一个**用户浏览器可达**的基址。
    """
    base = _rx.callback_base()
    host = (urlsplit(base).hostname or "") if base else ""
    containerized = _in_container()
    # 已配了非回环基址, 说明是奔着「别的机器上的浏览器也能回流」去的 -> 服务器部署。
    if base and not _host_is_loopback(host):
        kind = "server"
        reason = f"已配置非回环回调基址 ({host}), 说明面向其他机器上的浏览器"
    elif containerized:
        kind = "server"
        reason = "运行在容器内, 容器的 127.0.0.1 与用户浏览器的不是同一个回环"
    else:
        kind = "local"
        reason = "未配置回调基址且不在容器内, 按本机开发处理 (浏览器与 agent 同机)"
    exposure = ""
    if kind == "server" and host:
        if _host_is_private(host):
            exposure = "intranet"
        elif _host_is_loopback(host):
            exposure = "loopback"
        else:
            exposure = "public"
    return {
        "kind": kind,
        "reason": reason,
        "containerized": containerized,
        "callback_host": host,
        "exposure": exposure,
    }


def _probe_note(probe: dict[str, Any]) -> str:
    """探测结论的短注, 供拼进病因描述; 没探测过就不提。"""
    if not probe.get("probed"):
        return ""
    return " (探测: 可达)" if probe.get("reachable") else " (探测: 连不上)"


def _blockers(deployment: dict[str, Any], probe: dict[str, Any]) -> list[dict[str, str]]:
    """挡在「免抄 code」前面的确切原因, 每条都带可执行的下一步。

    刻意逐条区分而不是笼统报一句 mode=manual: manual 背后至少三种完全不同的病因
    (没配基址 / 显式 redirect 是死地址 / 回环端口被占), 对应的修法互不相同。
    """
    out: list[dict[str, str]] = []
    explicit = _env("PSI_FEISHU_REDIRECT_URI")
    if explicit:
        host = (urlsplit(explicit).hostname or "").lower()
        port = urlsplit(explicit).port
        if _host_is_loopback(host) and port and _port_in_use(port):
            out.append(
                {
                    "issue": f"PSI_FEISHU_REDIRECT_URI 指向回环端口 {port}, 但该端口已被占用",
                    "fix": f"腾出 {port} 端口, 或把 PSI_OAUTH_LOOPBACK_PORT 换成空闲端口并同步改后台登记",
                }
            )
        elif not _host_is_loopback(host):
            detail = _probe_note(probe)
            out.append(
                {
                    "issue": (
                        f"显式设了 PSI_FEISHU_REDIRECT_URI={explicit}{detail}; 它优先级最高, "
                        "且非回环地址一律判 manual -- 等于强制用户手抄 code"
                    ),
                    "fix": (
                        "删掉或注释掉 PSI_FEISHU_REDIRECT_URI, 改用 PSI_OAUTH_CALLBACK_BASE "
                        "(Gateway 通道, 才能自动回流)"
                    ),
                }
            )
    elif not _rx.callback_base():
        if deployment["kind"] == "server":
            out.append(
                {
                    "issue": "服务器部署但没配 PSI_OAUTH_CALLBACK_BASE, 回环通道对别的机器上的浏览器无效",
                    "fix": "设 PSI_OAUTH_CALLBACK_BASE 为用户浏览器能打开的 Gateway 地址, 再去飞书后台登记",
                }
            )
        elif _port_in_use(_rx.loopback_port()):
            out.append(
                {
                    "issue": f"本机回环端口 {_rx.loopback_port()} 被占用, 回环通道用不了",
                    "fix": f"腾出 {_rx.loopback_port()}, 或用 PSI_OAUTH_LOOPBACK_PORT 指定一个空闲端口",
                }
            )
    if probe.get("probed") and not probe.get("reachable") and _rx.callback_base():
        out.append(
            {
                "issue": (
                    f"回调基址 {_rx.callback_base()} 探测不可达: {probe.get('detail', '')} -- "
                    "地址配着但形同虚设, 用户点完授权回调会落空"
                ),
                "fix": "确认 Gateway/反向代理在监听该地址且用户浏览器所在网络能到达它",
            }
        )
    if not _env("PSI_FEISHU_APP_ID") or not _env("PSI_FEISHU_APP_SECRET"):
        out.append(
            {
                "issue": "飞书应用凭据不全 (PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET)",
                "fix": "在环境变量或 .env 里补齐这两项, 取值见飞书开放平台的应用凭证页",
            }
        )
    return out


async def env_check_impl(probe: bool = True) -> dict[str, Any]:
    """体检: 现在能不能免抄 code, 不能的话缺什么, 该登记哪个 URL。"""
    deployment = detect_deployment()
    base = _rx.callback_base()
    probe_result = await probe_base(base) if (probe and base) else {"probed": False}
    plan = _rx.plan_receiver(_env("PSI_FEISHU_REDIRECT_URI"))
    blockers = _blockers(deployment, probe_result)
    # app_secret 只报存在与否, 绝不回显 —— 体检结果会被原样发进聊天窗口。
    return {
        "ok": True,
        "auto_receive": plan.automatic,
        "mode": plan.mode,
        "redirect_uri_in_use": plan.redirect_uri,
        "deployment": deployment,
        "config": {
            "PSI_OAUTH_CALLBACK_BASE": base or "(未设)",
            "PSI_FEISHU_REDIRECT_URI": _env("PSI_FEISHU_REDIRECT_URI") or "(未设)",
            "PSI_OAUTH_LOOPBACK_PORT": _rx.loopback_port(),
            "app_id_set": bool(_env("PSI_FEISHU_APP_ID")),
            "app_secret_set": bool(_env("PSI_FEISHU_APP_SECRET")),
        },
        "callback_probe": probe_result,
        "blockers": blockers,
        "register_in_console": plan.redirect_uri,
        "message": _env_check_message(plan, deployment, blockers),
        "next_step": ("feishu_auth_start 就能直接用" if plan.automatic and not blockers else "feishu_auth_setup_guide"),
    }


def _env_check_message(plan: Any, deployment: dict[str, Any], blockers: list[dict[str, str]]) -> str:
    kind_cn = "本机开发" if deployment["kind"] == "local" else "服务器部署"
    head = f"部署形态: {kind_cn} ({deployment['reason']})."
    if plan.automatic and not blockers:
        return (
            f"{head}\n当前走 {plan.mode} 通道, **已经可以免抄 code**. "
            f"确认飞书后台的重定向 URL 里有这一条: {plan.redirect_uri}\n"
            "(后台没登记的话飞书会在跳转前就拒绝, 报错 20071.)"
        )
    if plan.automatic and blockers:
        lines = "\n".join(f"- {b['issue']}\n  -> {b['fix']}" for b in blockers)
        return (
            f"{head}\n通道选的是 {plan.mode} (理论上能自动回流), 但有隐患会让它实际失败:\n{lines}\n"
            f"该登记的重定向 URL: {plan.redirect_uri}"
        )
    lines = "\n".join(f"- {b['issue']}\n  -> {b['fix']}" for b in blockers) or "- 无自动接收通道可用"
    return (
        f"{head}\n当前是 manual: 用户必须自己从地址栏复制 code. 原因和修法:\n{lines}\n"
        "改完要重启 Gateway 让新环境变量生效. 想看分形态的完整步骤就调 feishu_auth_setup_guide."
    )


async def redirect_url_impl(probe: bool = True) -> dict[str, Any]:
    """直接回答「飞书后台的重定向 URL 该填什么」。"""
    deployment = detect_deployment()
    plan = _rx.plan_receiver(_env("PSI_FEISHU_REDIRECT_URI"))
    base = _rx.callback_base()
    probe_result = await probe_base(base) if (probe and base) else {"probed": False}
    # 除了当前生效的那条, 也给出另一条候选: 用户常常是在两种形态间做选择。
    candidates = []
    if base:
        candidates.append(
            {
                "url": _rx.gateway_redirect_uri(),
                "channel": "gateway",
                "note": "浏览器与 agent 可以不同机",
            }
        )
    candidates.append(
        {
            "url": _rx.loopback_redirect_uri(),
            "channel": "loopback",
            "note": "仅浏览器与 agent 同机时有效 (本机开发)",
        }
    )
    return {
        "ok": True,
        "register_this": plan.redirect_uri,
        "mode": plan.mode,
        "auto_receive": plan.automatic,
        "candidates": candidates,
        "deployment": deployment,
        "callback_probe": probe_result,
        "steps": [
            "打开飞书开放平台 open.feishu.cn -> 开发者后台 -> 选中本应用",
            "进「安全设置」-> 找到「重定向 URL」(有的版本叫回调地址)",
            f"把这条原样加进去 (不要改大小写、端口或末尾斜杠): {plan.redirect_uri}",
            "保存. 无需发版即可生效",
            "回来调 feishu_auth_start 发起一次授权验证",
        ],
        "message": (
            f"在飞书开放平台的「安全设置 -> 重定向 URL」里加这一条:\n{plan.redirect_uri}\n"
            f"(当前通道 {plan.mode}; 必须与授权和换 token 两步用的地址完全一致, 差一个字符飞书就报 20071.)"
        ),
    }


_LOCAL_STEPS = [
    "本机开发不用配任何东西: 未设 PSI_OAUTH_CALLBACK_BASE 时会自动用回环通道",
    f"去飞书后台「安全设置 -> 重定向 URL」登记: http://127.0.0.1:{_rx._DEFAULT_LOOPBACK_PORT}/oauth/callback",
    "端口被占就设 PSI_OAUTH_LOOPBACK_PORT 换一个, 并把后台登记同步改掉",
    "之后 feishu_auth_start 会报 mode=loopback / auto_receive=true, 用户点完即自动完成",
]

_INTRANET_STEPS = [
    "选一个内网用户浏览器能访问的地址和端口, 例如 http://192.168.x.x:8090",
    "不要把 Gateway 端口直接对外: /sessions 和 /chat/completions 能直接驱动 agent, "
    "而 /oauth/* 是无鉴权的 (安全性只靠 state 的熵)",
    "在前面放一个只放行 /oauth/callback 和 /oauth/code 两条路径的反向代理, 其余全部 404 "
    "(/oauth/code 也必须放行: 工具侧用同一基址轮询取件, 少放一条等于没接通)",
    "给 Gateway 设 PSI_OAUTH_CALLBACK_BASE=http://192.168.x.x:8090 (填代理的地址, 不是 Gateway 自己的)",
    "确认没有设 PSI_FEISHU_REDIRECT_URI —— 它优先级最高且非回环一律判 manual, 会把上面的努力全废掉",
    "重启 Gateway 使环境变量生效; 若代理与 Gateway 共享 network namespace, 重启 Gateway 后必须紧跟重启代理",
    "去飞书后台登记 http://192.168.x.x:8090/oauth/callback",
    "调 feishu_auth_env_check 复查, 应报 mode=gateway 且 blockers 为空",
]

_PUBLIC_STEPS = [
    "准备一个公网域名并配好 HTTPS (飞书可以接受 http, 但 code 会明文过网, 不建议)",
    "同样只放行 /oauth/callback 与 /oauth/code, 其余路径一律拒绝 —— 公网暴露 Gateway 等于把 agent 交出去",
    "设 PSI_OAUTH_CALLBACK_BASE=https://your.domain",
    "确认没有设 PSI_FEISHU_REDIRECT_URI",
    "重启 Gateway, 去飞书后台登记 https://your.domain/oauth/callback",
    "调 feishu_auth_env_check 复查",
]


async def setup_guide_impl(target: str = "") -> dict[str, Any]:
    """给出让「免抄 code」生效的配置步骤; ``target`` 为空则按当前环境自动判形态。"""
    deployment = detect_deployment()
    choice = (target or "").strip().lower()
    if choice not in ("local", "intranet", "public"):
        if deployment["kind"] == "local":
            choice = "local"
        else:
            choice = "public" if deployment.get("exposure") == "public" else "intranet"
    steps = {"local": _LOCAL_STEPS, "intranet": _INTRANET_STEPS, "public": _PUBLIC_STEPS}[choice]
    plan = _rx.plan_receiver(_env("PSI_FEISHU_REDIRECT_URI"))
    label = {"local": "本机开发", "intranet": "内网服务器部署", "public": "公网服务器部署"}[choice]
    return {
        "ok": True,
        "target": choice,
        "target_label": label,
        "steps": list(steps),
        "current_mode": plan.mode,
        "current_auto_receive": plan.automatic,
        "deployment": deployment,
        "register_in_console": plan.redirect_uri,
        "message": (
            f"按「{label}」的做法配 (当前实际是 mode={plan.mode}):\n"
            + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
            + "\n\n改完调 feishu_auth_env_check 复查. 无论哪种形态, 重定向 URL 都必须在飞书后台登记过."
        ),
        "security_note": (
            "/oauth/callback 与 /oauth/code 无鉴权, 安全性只靠一次性 state 的熵 (48 位十六进制, 600 秒 TTL); "
            "所以只放行这两条路径, 且别把授权链接转给第三方."
        ),
    }

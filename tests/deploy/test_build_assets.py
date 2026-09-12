"""`deploy/haitun/` 下构建资产的判据 —— 静态解析 Dockerfile 文本, 不跑 docker。

## 为什么这些判据存在

2026-09-10 实测: 境内 A 机(`47.100.84.197`)上 `find / -maxdepth 5 -iname 'Dockerfile*'` 只
找到 psi-cloud / psi-auth-impl / fmbuild 三份, **没有任何 psi-agent 的 Dockerfile**。搬机时
只搬了运行目录 `/srv/haitun/psi-agent`(compose + restart 脚本 + workspace), 没搬构建目录 ——
而构建目录此前是唯一的存放地, 全库无副本。后果是 A 机只能做 overlay 构建, 依赖一变就没法
全量 build; 同时发布文档里「用仓库里的 `Dockerfile` 全量 build」那句指向一个不存在的文件。

收进 git 解决了「文件会随机器消失」, 但**收进来的文件同样会静默腐烂**, 所以要有判据。这里
钉的都是「写错了不会报错、只会在某台机器上某个时刻变成故障」的那类:

- **镜像源必须是 `ARG`。** 同一个决策在境内境外**结论相反**: B 机(新加坡)实测
  `deb.debian.org` 比 `mirrors.aliyun.com` 快 146 倍、tuna 直接 403, 而 A 机(境内)实测
  aliyun 3.58 MB/s、deb.debian.org 126 KB/s、`pypi.org` 33 KB/s 连索引都下不完。谁把它写死,
  换机器时 build 就挂在装依赖那层, 报错长得像网络抽风。
- **前端产物必须在构建期落进镜像。** `feishu-web/dist/` 被 gitignore 排除, 而后端
  `_routes.py` 的 `add_static` 在目录不存在时**静默跳过** —— 页面 404, 日志只有一行 INFO,
  容器状态一切正常。此前靠镜像外手工 `npm run build` + 叠一层 `Dockerfile.fw`, 忘了做没有
  任何东西拦得住。
- **`.dockerignore` 里的 `dist/` 必须带前导斜杠。** 无锚点写法在任意层级匹配, 会把三棵前端
  的产物全部排出上下文, 触发的正是上面那个静默 404。

## 为什么不跑 docker

本仓 CI 没有 docker(见 `.github/workflows/`), 而这些缺陷全都是**文本层面**的: 源写死了、
COPY 顺序反了、锚点丢了。真构建的验证只能在目标机做, 已在 A 机实跑过一次(见 PR 正文的
实测数字), 但那不是每次提交能重复的事。所以这里守的是文本, 真构建靠发布流程的三层核验。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_HAITUN = Path(__file__).resolve().parents[2] / "deploy" / "haitun"
_DOCKERFILE = _HAITUN / "Dockerfile"
_OVERLAY = _HAITUN / "Dockerfile.overlay"
_IGNORE = _HAITUN / "Dockerfile.dockerignore"
_OVERLAY_IGNORE = _HAITUN / "Dockerfile.overlay.dockerignore"

#: 镜像内前端产物的落点。与 `gateway/feishu/_routes.py` 里 `add_static` 指的目录同一处 ——
#: 那边是 `Path(__file__).parent / "feishu-web" / "dist"`, 换算成镜像内绝对路径就是这个。
#: 两边任一侧改了包布局, 这个常量就得跟着改, 否则 `add_static` 静默跳过。
_DIST_IN_IMAGE = "/app/src/psi_agent/gateway/feishu/feishu-web/dist"


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _instructions(path: Path) -> list[str]:
    """把 Dockerfile 折成「一条指令一行」, 注释与空行去掉, 续行拼起来。

    不这么做的话续行会让 `COPY --from=...` 之类的断言碰不到真实指令; 而按裸行 grep
    又会把注释里提到的字符串当成真指令 —— 本文件的注释里恰好写了不少示例命令。
    """
    out: list[str] = []
    buf = ""
    for raw in _lines(path):
        line = raw.strip()
        if not buf and (not line or line.startswith("#")):
            continue
        # 续行内部的注释行(RUN 块里那种缩进注释)也要去掉, 否则拼进指令文本。
        if buf and line.startswith("#"):
            continue
        if line.endswith("\\"):
            buf += line[:-1].rstrip() + " "
            continue
        out.append((buf + line).strip())
        buf = ""
    if buf:
        out.append(buf.strip())
    return out


def _args(path: Path) -> dict[str, str]:
    """收集 `ARG NAME=default`。无默认值的记成空串。"""
    found: dict[str, str] = {}
    for ins in _instructions(path):
        m = re.match(r"^ARG\s+([A-Z_][A-Z0-9_]*)(?:=(.*))?$", ins)
        if m:
            found[m.group(1)] = (m.group(2) or "").strip()
    return found


# --------------------------------------------------------------------------
# 文件存在性 —— 这是整张卡的起点: 它们此前全都不在 git 里。
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [_DOCKERFILE, _OVERLAY, _IGNORE, _OVERLAY_IGNORE],
    ids=lambda p: p.name,
)
def test_build_assets_are_in_repo(path: Path) -> None:
    """四份构建资产都必须在仓库里。

    发布文档第 2 节写「用仓库里的 `Dockerfile` 全量 build」, 而在本轮之前全库无此文件
    (`git ls-tree -r origin/main | grep -i docker` 零命中)。这条判据让那句话有据可依 ——
    文件被谁删掉, 这里立刻红。
    """
    assert path.is_file(), f"{path.name} 不在 deploy/haitun/ 下"
    assert path.read_text(encoding="utf-8").strip(), f"{path.name} 是空文件"


# --------------------------------------------------------------------------
# 镜像源必须可换 —— 境内境外结论相反, 写死就会在换机器时炸。
# --------------------------------------------------------------------------


def test_apt_mirror_is_an_arg_not_hardcoded() -> None:
    """apt 源必须经 `ARG APT_MIRROR` 注入, 且默认值不是 tuna。

    tuna 对境外 IP 返回 **403**(卡 7c367 实测), 境内也只有 95 KB/s(A 机实测, 比 aliyun 慢
    37 倍)。它两边都不是最优, 写成默认值等于两边都埋一颗雷。
    """
    args = _args(_DOCKERFILE)
    assert "APT_MIRROR" in args, "apt 源没提成 ARG, 换机器时只能改镜像内容"
    assert "tuna" not in args["APT_MIRROR"], f"APT_MIRROR 默认值不该是 tuna: {args['APT_MIRROR']!r}"

    # sed 那行必须引用 ARG, 不能自己写死域名。
    sed_lines = [i for i in _instructions(_DOCKERFILE) if "sources.list.d/debian.sources" in i]
    assert len(sed_lines) == 1, f"换 apt 源的指令应恰好 1 条, 实际 {len(sed_lines)}"
    assert "${APT_MIRROR}" in sed_lines[0], f"换源指令没用 ARG: {sed_lines[0]!r}"


def test_apt_mirror_empty_value_means_no_rewrite() -> None:
    """`--build-arg APT_MIRROR=` 传空值时必须**不换源**, 而不是换成空域名。

    这是境外机唯一的正确姿势(B 机上 deb.debian.org 才是最快的)。少了这个 `if -n` 守卫,
    空值会把 sources 里的域名 sed 成空串 —— `apt-get update` 报的是一个语法层面的怪错,
    没人会想到是 build-arg 传空导致的。
    """
    sed_lines = [i for i in _instructions(_DOCKERFILE) if "sources.list.d/debian.sources" in i]
    guard = sed_lines[0]
    assert re.search(r'if\s+\[\s+-n\s+"\$\{APT_MIRROR\}"\s+\]', guard), f"换源指令缺 `if [ -n ]` 空值守卫: {guard!r}"


def test_pip_index_is_an_arg_with_reachable_default() -> None:
    """pip 源必须经 `ARG PIP_INDEX_URL` 注入, 默认值不能是 pypi.org。

    A 机实测 `pypi.org/simple/aiohttp/`: 200 之后以 **33 KB/s** 涓流, 3.95 MB 的索引 120 秒
    下不完。它不是 404、不是超时 —— 是"通但等于不通", 这种失败最难归因。境内默认必须给
    一个真的快的源(aliyun 实测 14.5 MB/s), 境外构建再显式传 pypi.org。
    """
    args = _args(_DOCKERFILE)
    assert "PIP_INDEX_URL" in args, "pip 源没提成 ARG"
    default = args["PIP_INDEX_URL"]
    assert default, "PIP_INDEX_URL 不该留空 —— 留空时 pip 走 pypi.org, 境内实质不可用"
    assert "pypi.org" not in default, f"PIP_INDEX_URL 默认值不该是 pypi.org: {default!r}"
    assert "tuna" not in default, f"PIP_INDEX_URL 默认值不该是 tuna: {default!r}"

    envs = [i for i in _instructions(_DOCKERFILE) if i.startswith("ENV") and "PIP_INDEX_URL" in i]
    assert len(envs) == 1, f"引用 PIP_INDEX_URL 的 ENV 应恰好 1 条, 实际 {len(envs)}"
    assert "${PIP_INDEX_URL}" in envs[0], f"ENV 没引用 ARG, 值被写死: {envs[0]!r}"


def test_base_image_is_an_arg_and_not_bare_dockerhub() -> None:
    """基础镜像可换, 且默认不是裸 docker.io。

    A 机实测 `registry-1.docker.io` 直连 **15 秒无响应**(curl 000), 而 daocloud 加速器与
    daemon 里配的 registry-mirrors 都能拉。写死加速器域名对没配 mirror 的机器也管用, 反过来
    写裸 docker.io 就赌目标机的 daemon 配置 —— 那个赌注在 A 机上会输。
    """
    args = _args(_DOCKERFILE)
    assert "BASE_IMAGE" in args, "基础镜像没提成 ARG"
    assert not args["BASE_IMAGE"].startswith(("docker.io/", "library/")), (
        f"BASE_IMAGE 默认值不该是裸 docker.io: {args['BASE_IMAGE']!r}"
    )
    assert "python:3.14" in args["BASE_IMAGE"], f"pyproject 要求 >=3.14, 基础镜像得对上: {args['BASE_IMAGE']!r}"


# --------------------------------------------------------------------------
# 前端产物必须在构建期落进镜像 —— 这是那个静默 404 的根。
# --------------------------------------------------------------------------


def test_frontend_is_built_inside_the_image() -> None:
    """Dockerfile 里必须有一个真的跑 `npm run build` 的阶段。

    此前是镜像外手工构建 + 叠 `Dockerfile.fw`(B 机 `/srv/haitun/build-34c73c65/Dockerfile.fw`
    就是那一层)。手工步骤漏做的后果是页面 404 而栈全绿, 没有任何判据能拦 —— 所以把它挪进
    镜像构建, 让前端产物与代码同生共死。
    """
    ins = _instructions(_DOCKERFILE)
    stages = [i for i in ins if i.startswith("FROM") and " AS " in i.upper()]
    assert stages, "没有多阶段构建, 前端产物只能来自镜像外"

    builds = [i for i in ins if re.match(r"^RUN\b.*npm run build", i)]
    assert len(builds) == 1, f"`npm run build` 应恰好 1 条, 实际 {len(builds)}: {builds}"


def test_frontend_deps_use_npm_ci_not_install() -> None:
    """装前端依赖必须用 `npm ci`。

    `npm install` 会在装不出 lockfile 记的版本时**悄悄改 lockfile 并接受漂移的版本**, 镜像
    里跑的前端于是可能不是 lockfile 那份 —— 而这不会有任何报错。`npm ci` 装不出就直接失败。
    """
    ins = _instructions(_DOCKERFILE)
    # `npm install -g tsx` 那条是运行镜像里装全局工具, 不是前端依赖, 放过。
    frontend_installs = [i for i in ins if re.match(r"^RUN\b.*npm\s+install\b", i) and "-g" not in i]
    assert not frontend_installs, f"前端依赖不该用 npm install: {frontend_installs}"
    assert any(re.match(r"^RUN\b.*npm\s+ci\b", i) for i in ins), "没有 `npm ci`"


def test_dist_copied_after_src_so_it_is_not_overwritten() -> None:
    """`COPY --from=<前端阶段> ... dist` 必须排在 `COPY src` **之后**。

    dist 在 src 子树里面。顺序反了的话, `COPY src ./src` 会把前端产物那一层盖掉 —— 而
    **构建仍然成功**, 镜像里就是少个目录, `add_static` 静默跳过, 页面 404。这是本文件里
    最值得存在的一条判据: 两条指令换个位置, 除了线上白屏之外没有任何信号。
    """
    ins = _instructions(_DOCKERFILE)
    src_at = [n for n, i in enumerate(ins) if re.match(r"^COPY\s+src\s+\./?src/?$", i)]
    dist_at = [n for n, i in enumerate(ins) if i.startswith("COPY --from=") and "feishu-web/dist" in i]
    assert len(src_at) == 1, f"`COPY src ./src` 应恰好 1 条, 实际 {len(src_at)}"
    assert len(dist_at) == 1, f"从前端阶段拷 dist 的 COPY 应恰好 1 条, 实际 {len(dist_at)}"
    assert dist_at[0] > src_at[0], f"拷 dist(第 {dist_at[0]} 条)排在 COPY src(第 {src_at[0]} 条)之前, 会被覆盖掉"


@pytest.mark.parametrize("path", [_DOCKERFILE, _OVERLAY], ids=lambda p: p.name)
def test_image_self_checks_dist_presence(path: Path) -> None:
    """两份 Dockerfile 都必须在构建期自检 dist 存在。

    这是兜底: 上面几条钉的是「指令写对了」, 这条钉的是「产物真在」。`COPY` 目标目录不存在
    时不报错(它会建目录), 所以只有显式 `test -f` 能在构建期把缺产物变红 —— 否则要等到有人
    打开页面才发现。overlay 那份尤其需要: 它的 dist 完全来自基础镜像, 基础镜像换错了就没了。
    """
    checks = [
        i for i in _instructions(path) if i.startswith("RUN") and "test -f" in i and f"{_DIST_IN_IMAGE}/index.html" in i
    ]
    assert checks, f"{path.name} 没有对 {_DIST_IN_IMAGE}/index.html 的构建期自检"


def test_dist_path_matches_add_static_target() -> None:
    """判据里那个镜像内路径必须与后端 `add_static` 真正读的目录一致。

    否则本文件全绿而线上 404: 自检的是 A 目录, `add_static` 读的是 B 目录。这条把
    `_DIST_IN_IMAGE` 与 `_routes.py` 的源码对上, 让包布局变动一定有一处变红。
    """
    routes = Path(__file__).resolve().parents[2] / "src" / "psi_agent" / "gateway" / "feishu" / "_routes.py"
    text = routes.read_text(encoding="utf-8")
    assert 'Path(__file__).parent / "feishu-web" / "dist"' in text, (
        "`_routes.py` 里 dist 目录的算法变了, _DIST_IN_IMAGE 要跟着改"
    )
    # `_routes.py` 位于 src/psi_agent/gateway/feishu/, 镜像里 src 挂在 /app/src ——
    # 于是 dist 的镜像内绝对路径就是 _DIST_IN_IMAGE。这里把这个换算写成断言而非注释。
    rel = routes.relative_to(routes.parents[4]).parent / "feishu-web" / "dist"
    derived = "/app/" + rel.as_posix()
    assert derived == _DIST_IN_IMAGE, f"_DIST_IN_IMAGE({_DIST_IN_IMAGE}) 与源码位置推出的路径({derived})不符"


# --------------------------------------------------------------------------
# overlay 的边界 —— 它不装依赖, 用错了是运行期 ImportError。
# --------------------------------------------------------------------------


def test_overlay_takes_base_image_as_arg() -> None:
    """overlay 的基础镜像必须是 ARG, 且在 `FROM` 之后重新声明一次。

    `FROM` 之前的 ARG **只对 `FROM` 行可见**, 阶段内引用到的是空串。漏了重声明的后果是
    报错信息里镜像名消失 —— 排查的人拿到一句没头没尾的错。
    """
    ins = _instructions(_OVERLAY)
    froms = [n for n, i in enumerate(ins) if i.startswith("FROM")]
    assert len(froms) == 1, f"overlay 应恰好 1 个 FROM, 实际 {len(froms)}"
    assert "${BASE_IMAGE}" in ins[froms[0]], f"FROM 没引用 ARG: {ins[froms[0]]!r}"

    redeclared = [n for n, i in enumerate(ins) if re.match(r"^ARG\s+BASE_IMAGE\b", i)]
    assert any(n > froms[0] for n in redeclared), "`FROM` 之后没重新声明 ARG BASE_IMAGE"


def test_overlay_does_not_install_dependencies() -> None:
    """overlay 里不许出现 `pip install` / `npm ci`。

    它的全部价值是秒级完成。真要装依赖就该走全量 build —— 在这里偷偷补一句 pip install,
    得到的是一个既慢又与全量构建结果不同的第三种镜像。
    """
    bad = [i for i in _instructions(_OVERLAY) if re.search(r"\b(pip install|npm ci)\b", i)]
    assert not bad, f"overlay 不该装依赖: {bad}"


# --------------------------------------------------------------------------
# .dockerignore 的锚点 —— 无锚点 dist/ 正是那个静默 404 的另一条路。
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", [_IGNORE, _OVERLAY_IGNORE], ids=lambda p: p.name)
def test_dist_ignore_rule_is_anchored(path: Path) -> None:
    """排除 Python 构建产物的 `dist/` 必须带前导斜杠。

    无锚点的 `dist/` 在**任意层级**匹配, 会把三棵前端的产物(desktop/spa, desktop/spa-v2,
    feishu/feishu-web)一起排出构建上下文。而 `add_static` 在目录不存在时静默跳过 ——
    栈显示健康、日志只有一行 INFO、页面 404。生产上踩过这个坑。
    """
    rules = [line.strip() for line in _lines(path) if line.strip() and not line.startswith("#")]
    assert "/dist/" in rules, f"{path.name} 缺 `/dist/` 这条"
    assert "dist/" not in rules, f"{path.name} 里有无锚点的 `dist/`, 会排掉三棵前端的产物"


@pytest.mark.parametrize("path", [_IGNORE, _OVERLAY_IGNORE], ids=lambda p: p.name)
def test_heavy_dirs_are_excluded(path: Path) -> None:
    """`node_modules/` 与 `.kanban/` 必须排掉。

    两者都在仓库内部且体积巨大: `.kanban/worktrees/` 下是整棵仓库的若干份副本。不排的后果
    不是构建失败, 而是上下文传输从秒级变成分钟级 —— overlay 存在的唯一理由就是快。
    """
    rules = [line.strip() for line in _lines(path) if line.strip() and not line.startswith("#")]
    for needed in ("node_modules/", ".kanban/", ".git/", "workspace/"):
        assert needed in rules, f"{path.name} 缺 `{needed}`"

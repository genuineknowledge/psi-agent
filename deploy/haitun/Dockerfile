# psi-agent gateway 部署镜像 (Linux/Debian)
#
# 这份此前只存在于目标机 `/srv/haitun/psi-agent/Dockerfile`, 全库无副本。后果是搬机时
# 只搬了运行目录、没搬构建目录: 2026-09-10 实测境内 A 机 47.100.84.197 上
# `find / -maxdepth 5 -iname 'Dockerfile*'` 只有 psi-cloud / psi-auth-impl / fmbuild 三份,
# 于是 A 机**只能做 overlay 构建**(换 /app/src), 一旦 pyproject.toml / uv.lock 变了就没法
# 全量 build —— 而发布文档当时写的是「用仓库里的 Dockerfile 全量 build」, 指向一个不存在
# 的文件。收进 git 就是为了让那句话成立。
#
# 用法(在仓库根, 不是在本目录):
#     docker build -f deploy/haitun/Dockerfile -t psi-agent-gateway:<commit> .
# 构建上下文必须是仓库根 —— 下面 COPY 的 pyproject.toml / src 都在根下。
# 排除规则见同目录 `dockerignore`(用法见该文件头部注释)。

# 基础镜像。默认走 DaoCloud 加速器: A 机 /etc/docker/daemon.json 虽已配 registry-mirrors,
# 但 mirror 只在拉 `docker.io/library/...` 时生效, 写死加速器域名对没配 mirror 的机器也管用。
# 2026-09-10 A 机实测三条路径: docker.m.daocloud.io 与 daemon mirror 兜的 docker.io 都能拉,
# registry-1.docker.io 直连超时(15s 无响应)。所以**不要**把这里改成裸 docker.io/library。
ARG BASE_IMAGE=docker.m.daocloud.io/library/python:3.14-slim-trixie

# ---------------------------------------------------------------------------
# 阶段 1: 构建 ToB 前端 (feishu-web)
# ---------------------------------------------------------------------------
# 为什么要有这一阶段: `feishu-web/dist/` 被 `.gitignore` 排除(见 feishu-web/.gitignore),
# 所以 clone 出来的构建上下文里**没有** dist。而后端 `_routes.py:652` 的 add_static 在目录
# 不存在时**静默跳过** —— 栈显示健康、日志只有一行 INFO, 页面却是 404。此前的补法是在镜像
# 外手工 `npm run build` 再叠一层 `Dockerfile.fw`(B 机 /srv/haitun/build-34c73c65/), 那是
# 一步纯人工步骤: 忘了做就是静默 404, 没有任何判据拦得住。放进镜像构建里, 前端产物就与
# 代码同生共死。
ARG NODE_IMAGE=node:22-slim
FROM ${NODE_IMAGE} AS feishu-web-build

# npm 源。境内机器上 registry.npmmirror.com 比官方快一个数量级
# (2026-09-10 A 机实测 TTFB: npmmirror 0.089s vs registry.npmjs.org 0.621s), 两者都通,
# 所以这里只是取快的那个, 不是可用性问题。境外机把它设成官方源即可。
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV npm_config_registry=${NPM_REGISTRY}

WORKDIR /fw
# 先只拷 lock 与 manifest, 让 npm ci 那层能被缓存(改前端源码不会重装依赖)。
COPY src/psi_agent/gateway/feishu/feishu-web/package.json \
     src/psi_agent/gateway/feishu/feishu-web/package-lock.json ./
# `npm ci` 而非 `npm install`: 前者严格照 lockfile 装, 装不出来就报错; 后者会悄悄改
# lockfile 并接受漂移的版本, 镜像里跑的前端就可能不是 lockfile 记的那份。
RUN npm ci

COPY src/psi_agent/gateway/feishu/feishu-web/ ./
# `npm run build` 内含 `tsc --noEmit`, 类型错会让**构建直接失败**而不是产出半成品 dist。
# 这是刻意的: 前端类型错误在这里变成红的构建, 好过变成线上一个坏页面。
RUN npm run build && test -f dist/index.html

# ---------------------------------------------------------------------------
# 阶段 2: 运行镜像
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE}

# apt 镜像源。空字符串 = 不换源, 用基础镜像自带的 deb.debian.org。
#
# 默认值按**境内**取, 因为终局目标机在境内(见 docs/deploy 的部署文档)。2026-09-10 A 机
# (47.100.84.197) 实测 trixie InRelease:
#     mirrors.aliyun.com               200  3.58 MB/s  0.039s   <- 默认取它
#     mirrors.cloud.aliyuncs.com       200  2.79 MB/s  0.050s   (阿里云内网源, 也快)
#     deb.debian.org                   200   126 KB/s  1.112s
#     mirrors.tuna.tsinghua.edu.cn     200    95 KB/s  1.483s
#
# 这与 B 机(新加坡)实测的结论**相反** —— 那边 deb.debian.org 比 aliyun 快 146 倍、tuna 直接
# 403, 所以境外那份 Dockerfile 是刻意不换源的。同一个决策在两地反向, 这就是它必须是 ARG
# 而不是写死的原因: 换机器时改一个 --build-arg, 不用再去 sed 镜像内容。
# 境外构建请显式传 `--build-arg APT_MIRROR=`(空值)。
ARG APT_MIRROR=mirrors.aliyun.com

# 系统依赖：
#  - nodejs/npm: fusion-flow (todo-check 定时任务调 npx tsx) 需要
#  - git: 部分工具/技能可能调用
#  - 字体/构建库: pymupdf、pillow 等运行期依赖
ENV DEBIAN_FRONTEND=noninteractive
RUN set -eux; \
    if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        git curl ca-certificates \
        nodejs npm \
        libgl1 libglib2.0-0 \
        fonts-noto-cjk \
    ; \
    rm -rf /var/lib/apt/lists/*

# pip 源。同样按境内取默认值, 2026-09-10 A 机实测 `simple/aiohttp/`(3.95 MB 索引):
#     mirrors.aliyun.com/pypi     200  14.5 MB/s  0.235s          <- 默认取它
#     pypi.tuna.tsinghua.edu.cn   200  1.57 MB/s  2.168s
#     pypi.org                    200  33 KB/s    120s 未下完      <- 实质不可用
#     mirrors.cloud.aliyuncs.com  000                              <- 该机无此 pypi 路径
#
# pypi.org 这条尤其要留意: 它**不是** 404 或超时, 而是 200 之后以 33 KB/s 涓流 —— 单个
# 索引 120 秒下不完。境外机上 pypi.org 是 2.70s 的最优选, 境内是死路。写死任一边, 换机器
# 时 build 都会挂在装依赖那一层, 而报错长得像网络抽风、不像配置问题。
# 境外构建传 `--build-arg PIP_INDEX_URL=https://pypi.org/simple`。
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先拷依赖清单，利用层缓存
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# 前端产物从阶段 1 取。放在 COPY src 之后 —— 反过来会被 src 覆盖掉(dist 在 src 子树里)。
COPY --from=feishu-web-build /fw/dist ./src/psi_agent/gateway/feishu/feishu-web/dist

# editable 安装：运行时直接用 /app/src 下的文件，
# 避开 hatch wheel 的 spa exclude/artifacts 交互不确定性，确保 SPA dist 存在。
# pywebview 等 GUI 依赖在无头环境仅安装不使用。
# .git 不在构建上下文里，hatch-vcs 拿不到版本号，用环境变量假装一个版本。
ENV SETUPTOOLS_SCM_PRETEND_VERSION=0.0.1
RUN pip install --no-cache-dir -e .

# fusion-flow 的 node 依赖（如果 workspace 里带了 package.json，运行期用挂载卷的 node_modules；
# 这里预装 tsx 以防挂载卷未带）
RUN npm install -g tsx@4 typescript@5 2>/dev/null || true

# 装完再自检一次前端产物在不在。上面那句 COPY 缺了目标目录也不会报错(COPY 建目录),
# 真正会静默的是 `pip install -e .` 之后有人改了包布局把 dist 挪走 —— 那时 add_static
# 静默跳过, 只有这一行 test 能在**构建期**把它变红。
RUN test -f /app/src/psi_agent/gateway/feishu/feishu-web/dist/index.html

# workspace 通过 bind mount 挂载到 /workspace，容器工作目录设为它
# gateway 用 Path.cwd() 决定 workspace 根
WORKDIR /workspace

EXPOSE 8848

# gateway 监听所有网卡；Linux 下必须带 http:// 前缀（裸地址会当成 unix socket）
CMD ["sh", "-c", "mkdir -p /tmp/psi && exec psi-agent run /workspace/config.yml"]

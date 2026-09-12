#!/usr/bin/env bash
# 构建 haitun ToB 栈的 gateway 镜像。发布流程 §2 用这个, 不要再手抄 Dockerfile.overlay。
#
# 用法(在仓库根跑):
#     deploy/haitun/build-image.sh overlay <commit>     # 只换 src, 秒级
#     deploy/haitun/build-image.sh full    <commit>     # 全量, 依赖变了必须用这个
#
# 环境变量(境外机器改这三个):
#     APT_MIRROR=       PIP_INDEX_URL=https://pypi.org/simple
#     NPM_REGISTRY=https://registry.npmjs.org
#     BASE_IMAGE=...    overlay 模式下是被叠的镜像, full 模式下是 python 基础镜像
#
# 为什么要有这个脚本, 而不是让文档写一串命令: 发布流程原先要求每次用 printf 现写一份
# Dockerfile.overlay(因为流程里的 `git clean -qfd` 会删掉未跟踪文件)。手抄两行本来风险不大,
# 但 tag 名怎么取、上下文取哪个目录、overlay 什么时候不能用, 这些都靠人记 —— 2026-09-10
# 实测的后果是 A 机压根没有 Dockerfile, 而文档还在说「用仓库里的 Dockerfile」。
# 现在这些判断落在代码里, 且 `deploy/` 是跟踪目录, `git clean` 不会删。

set -euo pipefail

MODE="${1:-}"
COMMIT="${2:-}"

if [[ "$MODE" != "overlay" && "$MODE" != "full" ]] || [[ -z "$COMMIT" ]]; then
    echo "用法: $0 {overlay|full} <commit>" >&2
    exit 2
fi

# 上下文必须是仓库根 —— Dockerfile 里 COPY 的 pyproject.toml / src 都在根下。
# 用脚本自身位置推, 而不是信 $PWD: 从别处调用时 $PWD 是错的, 而 COPY 找不到文件时
# 报的是 "not found", 不会说是上下文选错了。
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

TAG="psi-agent-gateway:${COMMIT:0:8}"

# 发布流程硬规则 1: 只从可指名 commit 的干净树构建(8-18 事故: 拿漂移目录构建, 丢功能两个月
# 无人发现)。这里把它变成构建前的闸门, 而不是文档里一句提醒。
HEAD_SHA="$(git rev-parse HEAD)"
if [[ "$HEAD_SHA" != "$COMMIT"* ]]; then
    echo "ERROR: HEAD($(git rev-parse --short HEAD)) 不是目标 commit(${COMMIT:0:8})。" >&2
    echo "       发布必须从干净 clone 的指定 commit 构建, 见发布文档 §1。" >&2
    exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: 工作树有未提交改动, 构建出来的镜像无法与任何 commit 对应。" >&2
    git status --porcelain >&2
    exit 1
fi

if [[ "$MODE" == "overlay" ]]; then
    # overlay 不跑 pip install, 依赖变了装不进去 —— 而表现是运行期 ImportError, 不是构建期
    # 报错。所以这里主动比对依赖清单: 与被叠的基础镜像里那份不同就拒绝, 让它在构建前就红。
    BASE="${BASE_IMAGE:-psi-agent-gateway:local}"
    for f in pyproject.toml uv.lock; do
        if ! docker run --rm --entrypoint sh "$BASE" -c "cat /app/$f" 2>/dev/null \
                | diff -q - "$f" > /dev/null 2>&1; then
            echo "ERROR: $f 与基础镜像 $BASE 里那份不同 —— overlay 装不进新依赖。" >&2
            echo "       改用: $0 full $COMMIT" >&2
            exit 1
        fi
    done
    exec docker build \
        -f deploy/haitun/Dockerfile.overlay \
        --build-arg "BASE_IMAGE=$BASE" \
        -t "$TAG" .
fi

# full 模式。三个源都可覆盖, 默认值按境内取(见 Dockerfile 里的实测数字)。
# `${VAR+--build-arg}` 的写法: 只有变量**被设置过**才传, 从而区分「没设」与「设成空串」——
# APT_MIRROR= 空串是境外机的正确姿势(不换源), 不能被当成「没设」而落回境内默认。
ARGS=()
if [[ -n "${APT_MIRROR+x}" ]]; then ARGS+=(--build-arg "APT_MIRROR=$APT_MIRROR"); fi
if [[ -n "${PIP_INDEX_URL+x}" ]]; then ARGS+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL"); fi
if [[ -n "${NPM_REGISTRY+x}" ]]; then ARGS+=(--build-arg "NPM_REGISTRY=$NPM_REGISTRY"); fi
if [[ -n "${BASE_IMAGE+x}" ]]; then ARGS+=(--build-arg "BASE_IMAGE=$BASE_IMAGE"); fi

exec docker build -f deploy/haitun/Dockerfile "${ARGS[@]}" -t "$TAG" .

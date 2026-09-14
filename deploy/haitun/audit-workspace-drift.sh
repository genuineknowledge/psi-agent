#!/usr/bin/env bash
# 审计生产 workspace/tools 与 git 的差异 —— 投放前必跑, 也是投放后的判据。
#
# 用法(在目标机上跑, 或本地 ssh 进去跑):
#     audit-workspace-drift.sh <git-ref> [workspace...]
#     audit-workspace-drift.sh origin/main                    # 审全部三份
#     audit-workspace-drift.sh origin/main workspace          # 只审 gateway 那份
#
# 为什么要有这个脚本, 而不是让文档写一串 md5 命令:
#
# `workspace/tools/` 那 241 个业务文件**不在镜像里**, 是 bind mount 到机器上的,
# 靠人手 cp 投放。2026-09-14 实测的后果: 生产 `_feishu_spec.py` 是新版而
# `_feishu_api_impl.py` 是旧版 —— 9-12 那次投放投了前者漏了后者, 于是 195 条飞书
# API 护栏规则一条都不生效, 唯一线索是一行 INFO 日志。漏投不报错、不变红、
# 功能表面跑通, 这种失败没有判据可言。
#
# 本脚本要回答的正是"差哪些文件", 且必须区分四类 —— 混成一个数字就再也分不开了:
#
#   同     md5 相等(LF 归一化后)。
#   落后   与 git 不同, 但 mtime 是**整秒** → 部署投放留下的旧版, 可安全覆盖。
#   领先   与 git 不同, 且 mtime **带纳秒** → 有人在生产上就地写入, 覆盖即丢代码。
#   缺失   git 有而生产没有。
#
# mtime 纳秒位这个判据来自 2026-09-12 的取证: 部署投放(tar/cp -p 保留源 mtime, 或
# CI 产物)落在整秒, 就地编辑落在带纳秒的时刻。uid 不是判据(投放和手改都可能是 root)。
#
# ⚠️ "领先"必须人工定归属, 不能自动覆盖。已知一例: `_card_dsl.py` 是未合并的
# PR #867 (fork Twin-Ghosts) 的代码再往前改的, 生产是该 PR 的超集。把它当"旧版"
# 覆盖会静默丢掉 607 行功能(原生 table 渲染 / bind-field 回写 / action_id 撞车防护)。
#
# ⚠️ 两台私有 workspace 各缺 73/71 个文件, 它们不是 gateway 的副本而是 8-07 的旧
# 快照。**不要**对它们做"补依赖闭包"式的增量投放: 补一条链会连带换新
# `_feishu_impl.py`, 而旧 `_feishu/bitable.py` 立刻让 40+ 文件断链, 工具数从
# 198 掉到 87(2026-09-12 实测并已回滚)。闭包的边界是整棵依赖树, 不是看得见的报错。
set -euo pipefail

REF="${1:-origin/main}"
shift || true
WORKSPACES=("$@")
[ ${#WORKSPACES[@]} -eq 0 ] && WORKSPACES=(workspace workspace-luolin workspace-chengxx)

PROD_ROOT="${PROD_ROOT:-/srv/haitun/psi-agent}"
SUBTREE="${SUBTREE:-agents/feishu/tools}"
REPO="${REPO:-}"

# 仓库位置: 显式给 REPO, 否则找一个能用的 clone。不 clone 新的 —— 目标机的 GitHub
# 是间歇故障(实测 TLS recv error -110), 静默拉半份比拉不到更糟。
if [ -z "$REPO" ]; then
  for c in /tmp/rel-check /tmp/rel-* .; do
    [ -d "$c/.git" ] && REPO="$c" && break
  done
fi
[ -n "$REPO" ] && [ -d "$REPO/.git" ] || { echo "找不到 git clone。用 REPO=<path> 指定。" >&2; exit 2; }

cd "$REPO"
SHA=$(git rev-parse --short "$REF" 2>/dev/null) || { echo "解析不出 ref: $REF" >&2; exit 2; }
echo "基准: $REF = $SHA  ($(git log -1 --format=%ad --date=short "$SHA"))"
echo "仓库: $REPO"
echo

# git 侧快照。用 archive 而非 checkout: 不动工作树, 也不受别的会话影响。
SNAP=$(mktemp -d)
trap 'rm -rf "$SNAP"' EXIT
git archive "$SHA" "$SUBTREE" | tar -x -C "$SNAP"
GITDIR="$SNAP/$SUBTREE"
[ -d "$GITDIR" ] || { echo "ref 里没有 $SUBTREE" >&2; exit 2; }

# 全树走, 不是顶层 glob。2026-09-14 实测: 顶层 glob 漏掉 6 个 `_feishu/` 私有子目录
# 文件, 其中 3 个的缺失直接导致工具加载失败。
( cd "$GITDIR" && find . -name '*.py' | sed 's|^\./||' ) | LC_ALL=C sort -u > "$SNAP/git_names"
echo "git 侧: $(wc -l < "$SNAP/git_names") 个 .py"
echo

RC=0
for W in "${WORKSPACES[@]}"; do
  T="$PROD_ROOT/$W/tools"
  [ -d "$T" ] || { echo "跳过 $W: $T 不存在"; echo; continue; }

  same=0; stale=0; ahead=0; missing=0
  : > "$SNAP/stale_$W"; : > "$SNAP/ahead_$W"; : > "$SNAP/missing_$W"

  while read -r f; do
    p="$T/$f"
    if [ ! -f "$p" ]; then
      missing=$((missing+1)); echo "$f" >> "$SNAP/missing_$W"; continue
    fi
    # LF 归一化: 生产是 LF、仓库检出可能是 CRLF, 裸比对会报几乎全不一致。
    a=$(tr -d '\r' < "$GITDIR/$f" | md5sum | cut -d' ' -f1)
    b=$(tr -d '\r' < "$p"         | md5sum | cut -d' ' -f1)
    if [ "$a" = "$b" ]; then
      same=$((same+1)); continue
    fi
    ns=$(stat -c %y "$p" | sed 's/.*\.//; s/ .*//')
    if [ "$ns" = "000000000" ]; then
      stale=$((stale+1)); echo "$f" >> "$SNAP/stale_$W"
    else
      ahead=$((ahead+1)); printf '%s  (%s)\n' "$f" "$(stat -c %y "$p" | cut -c1-19)" >> "$SNAP/ahead_$W"
    fi
  done < "$SNAP/git_names"

  ( cd "$T" && find . -name '*.py' | sed 's|^\./||' ) | LC_ALL=C sort -u > "$SNAP/prod_$W"
  # comm 要求两边都排过序, 且必须同一 collation —— LC_ALL=C 两处都加。不加会报
  # "not in sorted order" 并吐出自相矛盾的结果(同一文件同时出现在两侧), 实测踩过。
  comm -13 "$SNAP/git_names" "$SNAP/prod_$W" > "$SNAP/only_$W" || true
  only=$(wc -l < "$SNAP/only_$W")

  printf '=== %s\n' "$W"
  printf '    同=%-4s 落后=%-4s 领先=%-4s 缺失=%-4s 生产独有=%s\n' "$same" "$stale" "$ahead" "$missing" "$only"

  if [ "$ahead" -gt 0 ]; then
    echo "    ⚠ 领先(就地写入, 覆盖即丢代码 —— 必须人工定归属):"
    sed 's/^/      /' "$SNAP/ahead_$W"
    RC=1
  fi
  [ "$stale" -gt 0 ] && { echo "    落后(可安全覆盖):"; sed 's/^/      /' "$SNAP/stale_$W"; }
  [ "$missing" -gt 0 ] && { echo "    缺失:"; sed 's/^/      /' "$SNAP/missing_$W"; }
  [ "$only" -gt 0 ] && { echo "    生产独有(git 已删或从未有过):"; sed 's/^/      /' "$SNAP/only_$W"; }
  echo
done

# 退出码 1 = 有"领先"文件待定归属。CI/脚本据此停下, 而不是继续覆盖。
exit $RC

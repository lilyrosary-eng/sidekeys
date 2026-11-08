#!/usr/bin/env bash
# 把本地提交增量发布到远端 main：以远端 tree 为 base，父提交用远端 main 的 sha。
# 备用通道：只在 github.com 的 git 端点不可达时用（本项目早期长期 502，所以一直走这里）。
# 2026-11-08 起 git 端点可达、本地历史也已与远端对齐，日常请直接用 git push；
# 这个脚本留着应急，它的好处是逐 blob/逐 tree 校验，坏处是产生的 sha 与本地不同。
#
#   ./publish_via_api.sh            # 只发布 HEAD（要求其父 tree 与远端 tree 一致）
#   ./publish_via_api.sh 03b42d8    # 依序发布 03b42d8..HEAD 之间的每一个提交，保留各自提交信息
#
# 每一趟都重新向远端要 sha，并强制校验“远端 tree == 待发布提交的父 tree”，
# 不满足就中止：宁可少发，也不能把远端历史接错。
set -euo pipefail
cd "$(dirname "$0")"
REPO=lilyrosary-eng/sidekeys

remote_head() { timeout 60 gh api "repos/$REPO/commits/main" --jq .sha; }
remote_tree() { timeout 60 gh api "repos/$REPO/commits/main" --jq .commit.tree.sha; }

publish_one() { # $1 = 本地提交 sha
  local LOCAL=$1 LOCAL_PARENT LOCAL_PARENT_TREE RP RT ENTRIES NT LT MSG AUTHOR DATE NC
  LOCAL_PARENT=$(git rev-parse "$LOCAL^")
  LOCAL_PARENT_TREE=$(git rev-parse "$LOCAL_PARENT^{tree}")

  RP=$(remote_head)
  RT=$(remote_tree)
  echo "=== 发布 $(git log -1 --format='%h  %s' "$LOCAL")"
  echo "    远端 main $RP  tree $RT"
  if [ "$RT" != "$LOCAL_PARENT_TREE" ]; then
    echo "✗ 远端 tree 与 $LOCAL 的父 tree（$LOCAL_PARENT_TREE）不一致，增量发布不安全，中止"
    exit 1
  fi
  echo "    ✓ base tree 一致"

  # 删除与重命名不能用 base_tree 增量表达（要把 sha 置 null），这里拒绝而不是猜
  if git diff --name-status "$LOCAL_PARENT" "$LOCAL" | grep -qE '^(D|R[0-9])'; then
    echo "✗ 这个提交含删除/重命名，脚本只会做新增/修改，中止（请手工处理）"
    exit 1
  fi

  echo "    --- 建 blob ---"
  while read -r _status path; do
    # 内容必须取自“该提交里的版本”，不能读工作区文件：
    # 只发 HEAD 时两者恰好相同，改成按提交循环后就会错位
    # （实测第一个提交的 CHANGELOG.md 就对不上，脚本在 blob 校验处中止）。
    b64=$(git show "$LOCAL:$path" | base64 -w0)
    sha=$(jq -n --arg c "$b64" '{content:$c, encoding:"base64"}' \
          | timeout 60 gh api -X POST "repos/$REPO/git/blobs" --input - --jq .sha)
    want=$(git rev-parse "$LOCAL:$path")
    printf "      %-34s %s %s\n" "$path" "${sha:0:12}" "$([ "$sha" = "$want" ] && echo ✓ || echo ✗)"
    [ "$sha" = "$want" ] || exit 1
  done < <(git diff --name-status "$LOCAL_PARENT" "$LOCAL")

  echo "    --- 建 tree ---"
  ENTRIES=$(git diff --name-only "$LOCAL_PARENT" "$LOCAL" | while read -r p; do
    mode=$(git ls-tree "$LOCAL" -- "$p" | awk '{print $1}')
    sha=$(git rev-parse "$LOCAL:$p")
    printf '{"path":"%s","mode":"%s","type":"blob","sha":"%s"},' "$p" "$mode" "$sha"
  done | sed 's/,$//')
  NT=""
  for attempt in 1 2 3 4 5; do
    if NT=$(printf '%s' "$ENTRIES" | jq -n --arg base "$RT" --argjson t "[$ENTRIES]" '{base_tree:$base, tree:$t}' \
            | timeout 60 gh api -X POST "repos/$REPO/git/trees" --input - --jq .sha 2>/tmp/tree_err); then
      break
    fi
    # 实测过：刚建完 blob 立刻建 tree，GitHub 会回 “tree.sha … is not a valid blob”，
    # 但那个 sha 用 /git/blobs 查是合法的 blob —— git 后端还没同步完。重试即可，
    # 别把它当成内容错误去改打包逻辑。
    echo "      tree 第 $attempt 次失败：$(head -c 120 /tmp/tree_err)"
    NT=""
    sleep 3
  done
  [ -n "$NT" ] || { echo "✗ 建 tree 连续失败，中止（远端 main 未动）" >&2; exit 1; }
  LT=$(git rev-parse "$LOCAL^{tree}")
  printf "      远端算出 %s 本地 %s %s\n" "${NT:0:12}" "${LT:0:12}" "$([ "$NT" = "$LT" ] && echo ✓ || echo ✗)"
  [ "$NT" = "$LT" ] || { echo "✗ tree 不一致，中止"; exit 1; }

  echo "    --- 建 commit 并移动 ref ---"
  MSG=$(git log -1 --format=%B "$LOCAL")
  AUTHOR=$(git log -1 --format='%an|%ae' "$LOCAL")
  DATE=$(git log -1 --format=%cI "$LOCAL")
  NC=$(jq -n --arg m "$MSG" --arg t "$NT" --arg p "$RP" \
        --arg name "${AUTHOR%%|*}" --arg email "${AUTHOR##*|}" --arg date "$DATE" \
        '{message:$m, tree:$t, parents:[$p], author:{name:$name, email:$email, date:$date}}' \
       | timeout 60 gh api -X POST "repos/$REPO/git/commits" --input - --jq .sha)
  timeout 60 gh api -X PATCH "repos/$REPO/git/refs/heads/main" -f sha="$NC" \
    --jq '"      main → \(.object.sha)"'
}

if [ $# -ge 1 ]; then
  RANGE="$1..HEAD"
  # 传进来的必须是**本地**存在的提交：走 API 发布时远端 sha 与本地 sha 不同，
  # 拿远端 sha 当起点会让 git rev-list 报错。而 `for c in $(git rev-list ...)` 里
  # 命令替换失败并不触发 set -e —— 实测它就“发布了 0 个提交”然后打印完成，
  # 把失败说成成功。这里显式校验，宁可拒绝。
  if ! git rev-list --count "$RANGE" >/dev/null 2>&1; then
    echo "✗ 起点不是本地提交（或区间无提交）：$RANGE" >&2
    echo "  远端 sha 与本地 sha 不同，请用本地提交号或 HEAD^；查看本地序列：git log --oneline -8" >&2
    exit 1
  fi
  count=0
  for c in $(git rev-list --reverse "$RANGE"); do
    publish_one "$c"
    count=$((count + 1))
  done
  [ "$count" -gt 0 ] || { echo "✗ 区间里没有待发布的提交（$RANGE）—— 这不该被当成成功" >&2; exit 1; }
  echo "共发布 $count 个提交"
else
  publish_one "$(git rev-parse HEAD)"
fi
echo "完成（远端 commit SHA 可能与本地不同：GitHub 会规范化日期时区；内容一致以 tree SHA 为准）"

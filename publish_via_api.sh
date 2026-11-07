#!/usr/bin/env bash
# 把本地 HEAD 增量发布到远端 main：以远端 tree 为 base，父提交用远端 main 的 sha。
# 用于 github.com 的 git 端点不可达、只能走 api.github.com 的情况。
set -euo pipefail
cd "$(dirname "$0")"
REPO=lilyrosary-eng/sidekeys
LOCAL=$(git rev-parse HEAD)
LOCAL_PARENT=$(git rev-parse HEAD^)
LOCAL_PARENT_TREE=$(git rev-parse "$LOCAL_PARENT^{tree}")

RP=$(timeout 60 gh api "repos/$REPO/commits/main" --jq .sha)
RT=$(timeout 60 gh api "repos/$REPO/commits/main" --jq .commit.tree.sha)
echo "远端 main $RP  tree $RT"
echo "本地 HEAD $LOCAL  其父 tree $LOCAL_PARENT_TREE"
[ "$RT" = "$LOCAL_PARENT_TREE" ] || { echo "✗ 远端 tree 与本地父 tree 不一致，增量发布不安全，中止"; exit 1; }
echo "✓ base tree 一致，可以增量发布"

echo "--- 建 blob ---"
while read -r status path; do
  sha=$(gh api -X POST "repos/$REPO/git/blobs" \
        --input <(jq -n --arg c "$(base64 -w0 "$path")" '{content:$c, encoding:"base64"}') --jq .sha)
  want=$(git rev-parse "$LOCAL:$path")
  printf "  %-32s %s %s\n" "$path" "${sha:0:12}" "$([ "$sha" = "$want" ] && echo ✓ || echo ✗)"
  [ "$sha" = "$want" ] || exit 1
done < <(git diff --name-status "$LOCAL_PARENT" "$LOCAL")

echo "--- 建 tree ---"
ENTRIES=$(git diff --name-only "$LOCAL_PARENT" "$LOCAL" | while read -r p; do
  mode=$(git ls-tree "$LOCAL" -- "$p" | awk '{print $1}')
  sha=$(git rev-parse "$LOCAL:$p")
  printf '{"path":"%s","mode":"%s","type":"blob","sha":"%s"},' "$p" "$mode" "$sha"
done | sed 's/,$//')
NT=$(printf '%s' "$ENTRIES" | jq -n --arg base "$RT" --argjson t "[$ENTRIES]" '{base_tree:$base, tree:$t}' \
     | gh api -X POST "repos/$REPO/git/trees" --input - --jq .sha)
LT=$(git rev-parse "$LOCAL^{tree}")
printf "  tree 远端 %s 本地 %s %s\n" "${NT:0:12}" "${LT:0:12}" "$([ "$NT" = "$LT" ] && echo ✓ || echo ✗)"
[ "$NT" = "$LT" ] || { echo "✗ tree 不一致，中止"; exit 1; }

echo "--- 建 commit 并移动 ref ---"
MSG=$(git log -1 --format=%B "$LOCAL")
NC=$(jq -n --arg m "$MSG" --arg t "$NT" --arg p "$RP" '{message:$m, tree:$t, parents:[$p]}' \
     | gh api -X POST "repos/$REPO/git/commits" --input - --jq .sha)
gh api -X PATCH "repos/$REPO/git/refs/heads/main" -f sha="$NC" --jq '"main → \(.object.sha)"'
echo "完成：远端 commit $NC（本地 $LOCAL，SHA 可能因日期时区规范化而不同，tree 已验证一致）"

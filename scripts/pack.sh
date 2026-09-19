#!/usr/bin/env bash
# 打发行 zip。**从 git ls-files 正面构建，不用排除清单。**
#
# 为什么：排除清单会漂移。实测撞过一次——`.gstack/` 是浏览器工具在技能
# 目录里留下的日志（browse-network.log 61K，含访问过的每个 URL），它不在
# 排除清单里，于是整个进了发给试用者的 zip。和 archive.py 那张「哪些不是
# 档案」的黑名单是同一种失败：**清单和产生文件的东西分居两处，必然漂移。**
#
# 正面构建之后，没被 git 跟踪的东西物理上进不来。工作区脏就直接拒绝，
# 免得发出去一个和仓库对不上的版本。
#
# 跑法：bash scripts/pack.sh [输出路径]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
OUT="${1:-../$(basename "$PWD").zip}"

if [ -n "$(git status --porcelain)" ]; then
  echo "拒绝打包：工作区有未提交的改动。" >&2
  git status --short >&2
  echo "先提交（或 stash），否则发出去的 zip 和仓库对不上。" >&2
  exit 1
fi

# 仓库自己的门面文件不进包；LICENSE 要进——MIT 要求 included in all copies。
# 图标只在 SkillHub 网页后台上传用，技能运行用不到；SkillHub 对包里的文件类型有限制，不带进去。
STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT
NAME=$(basename "$PWD")
mkdir -p "$STAGE/$NAME"
git ls-files -z | while IFS= read -r -d '' f; do
  case "$f" in
    README.md|INSTALL.md|.gitignore|docs/*|assets/icon.*) continue ;;
  esac
  mkdir -p "$STAGE/$NAME/$(dirname "$f")"
  cp "$f" "$STAGE/$NAME/$f"
done

OUT_ABS=$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")
rm -f "$OUT_ABS"
(cd "$STAGE" && zip -r -q "$OUT_ABS" "$NAME")

N=$(unzip -l "$OUT_ABS" | tail -1 | awk '{print $2}')
echo "已打包：$OUT_ABS（$N 个文件）"
echo "来源：git ls-files —— 未跟踪的文件进不来。"

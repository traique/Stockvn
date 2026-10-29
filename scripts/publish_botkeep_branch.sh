#!/usr/bin/env bash
# Đẩy nội dung gói Botkeep (thư mục botkeep/ do scripts/build_botkeep_bundle.py tạo) lên một
# nhánh riêng (mặc định "botkeep"), để Botkeep deploy thẳng từ GitHub mà vẫn dưới giới hạn
# 250 mục / 4 MB. Nhánh chỉ chứa đúng các file của gói, cộng một .gitignore cho data/ và .env.
#
#   scripts/publish_botkeep_branch.sh dist/botkeep [remote] [nhánh]
#
# Chạy trong repo (commit nguồn = HEAD). Mỗi lần chạy thêm một commit lên nhánh nếu gói đổi,
# không bao giờ ép ghi đè lịch sử. GitHub Actions gọi script này sau mỗi lần main đổi.
set -euo pipefail

SRC=$(cd "${1:?cần thư mục gói, ví dụ dist/botkeep}" && pwd)
REMOTE=${2:-origin}
BRANCH=${3:-botkeep}
SHA=$(git rev-parse --short HEAD)
[ -f "$SRC/main_botkeep.py" ] || { echo "Không thấy $SRC/main_botkeep.py: chưa tạo gói?" >&2; exit 1; }

WT=$(mktemp -d)
cleanup() { git worktree remove --force "$WT" >/dev/null 2>&1 || true; rm -rf "$WT"; }
trap cleanup EXIT

if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
  git fetch -q --depth 1 "$REMOTE" "$BRANCH"
  git worktree add -q --detach "$WT" FETCH_HEAD
else
  git worktree add -q --detach "$WT" HEAD
  git -C "$WT" checkout -q --orphan "$BRANCH-moi"
fi

find "$WT" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$SRC"/. "$WT"/
cat > "$WT/.gitignore" <<'EOF'
# Dữ liệu và bí mật nằm trên server, không bao giờ vào nhánh này
.env
bot.env
data/
__pycache__/
*.pyc
EOF

git -C "$WT" add -A
if git -C "$WT" diff --cached --quiet; then
  echo "Nhánh $BRANCH đã đúng gói của $SHA, không đổi."
  exit 0
fi
INFO=""
if [ -f "$SRC/../botkeep.json" ]; then
  INFO=$(python3 - "$SRC/../botkeep.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
parts = [f"{name.capitalize()} {b['version']} ({b['build']})" for name, b in d["builds"].items()]
size = f"{d['bytes']:,}".replace(",", ".")
print(", ".join(parts) + f"; {d['files']} file, {d['dirs']} thư mục, {size} B")
PY
)
fi
git -C "$WT" commit -q -m "Gói Botkeep từ main $SHA" ${INFO:+-m "$INFO"}
git -C "$WT" push -q "$REMOTE" "HEAD:refs/heads/$BRANCH"
echo "Đã cập nhật nhánh $BRANCH: gói của $SHA${INFO:+ – $INFO}"

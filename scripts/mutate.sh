#!/usr/bin/env bash
# 变异测试：逐个回滚已修的 bug，确认测试套件真的抓得住。
#
# 为什么需要这个：写完 test_scripts.py 跑出 25 个绿，看起来很稳。
# 但其中 test_report_second_write_replaces_first 是个摆设 —— 它测的场景
# 已经被另一处修复（demote 把 ## 压成 ####）顺手挡掉了，把 report 的修复
# 回滚它照样绿。不做变异测试，永远发现不了这件事。
#
# 一个测试只有在「对应的代码坏掉时会红」的前提下才有意义。这个脚本
# 就是去证明那个前提。
#
# 跑法：bash scripts/mutate.sh
# 退出码 0 = 每个变异都被抓住；非 0 = 有测试是摆设，输出里会点名。

set -uo pipefail
SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
FAILED=0

mutate() {
  local name="$1" file="$2" from="$3" to="$4"
  local dir="$WORK/m"
  rm -rf "$dir"; cp -r "$SKILL" "$dir"

  python3 - "$dir/$file" "$from" "$to" <<'PY'
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path, encoding='utf-8').read()
if old not in s:
    print(f"SKIP_MARKER_NOT_FOUND")
    raise SystemExit(3)
open(path, 'w', encoding='utf-8').write(s.replace(old, new, 1))
PY
  if [ $? -eq 3 ]; then
    echo "  ⚠️  $name —— 变异锚点没找到，代码改过了，这条变异需要更新"
    FAILED=1
    return
  fi

  local reds
  reds=$(cd "$dir" && python3 scripts/test_scripts.py 2>&1 | grep -c "^FAIL:\|^ERROR:")
  if [ "$reds" -gt 0 ]; then
    echo "  ✓ $name —— $reds 条测试变红"
  else
    echo "  ✗ $name —— 测试仍全绿，这条修复没有被任何测试保护"
    FAILED=1
  fi
}

echo "变异测试：回滚每一个已修的 bug，看测试抓不抓得住"
echo

mutate "项目名精确键（严重·静默覆盖）" scripts/archive.py \
  '    for d in find_all():
        if d["project"] == project:
            return d
    return None' \
  '    for d in find_all():
        if d["project"] == project:
            return d
    for d in find_all():
        if project in d["project"] or d["project"] in project:
            return d
    return None'

mutate "pattern 未知类型不崩（严重·第0步崩溃）" scripts/pattern.py \
  'desc = KINDS.get(kind, "（手写类型，不在七类词表里）")' \
  'desc = KINDS[kind]'

mutate "pattern 不删手写小节（严重·静默删数据）" scripts/pattern.py \
  'for kind in list(KINDS) + [k for k in data if k not in KINDS]:' \
  'for kind in KINDS:'

mutate "断点取第一个未答（中·跳答后漏问）" scripts/archive.py \
  '    done, next_q = progress(body)' \
  '    done, next_q = answered, answered + 1'

# 这条要回滚两处：回落逻辑 + 写全局键。只回滚前者的话，脚本根本不写
# 全局键，回落打不着，变异是惰性的 —— 变异台第一次跑就是这么误报的。
mutate "save/report 用真进度（中·口径分裂）" scripts/archive.py \
  '    done, nxt = progress(body)
    write(doc, body, answered=done, status=doc["status"])' \
  '    done, nxt = max(doc["answered"], step), max(doc["answered"], step) + 1
    write(doc, body, answered=done, status=doc["status"])'

mutate "同意门不跨项目继承·回落逻辑（严重·隐私）" scripts/resources.py \
  'return meta.get(f"对接.{project}", "")' \
  'return meta.get(f"对接.{project}") or meta.get("对接") or ""'

mutate "同意门不跨项目继承·写全局键（严重·隐私）" scripts/resources.py \
  '    meta[f"对接.{args.project}"] = args.level
    write_doc(meta, body)' \
  '    meta[f"对接.{args.project}"] = args.level
    meta.setdefault("对接", args.level)
    write_doc(meta, body)'

mutate "anon 档拦社交账号（中·隐私·曾零覆盖）" scripts/resources.py \
  '    (re.compile(rf"(?:{_PLATFORM}){_JOIN}{_TOKEN}", re.I), "社交账号"),' \
  '    # 变异：整条规则移除'

mutate "anon 档暂停身份信息（严重·隐私）" scripts/resources.py \
  '    if level == "anon" and named and not args.deidentified:' \
  '    if False:'

mutate "off 档不写文件（严重·承诺落空）" scripts/resources.py \
  '    if args.level == "off":
        # 用户在同意门上读到的原话是「不留任何痕迹」' \
  '    if False:
        # 用户在同意门上读到的原话是「不留任何痕迹」'

mutate "slug 冲突不覆盖别人的档案（严重·丢数据）" scripts/archive.py \
  '    if cleaned != project.strip():' \
  '    if False:'

mutate "不方便的理由不落盘（隐私）" scripts/archive.py \
  '        note = "" if args.declined == "不方便" else args.answer.strip()' \
  '        note = args.answer.strip()'

mutate "敏感问题在 show 里浮出（行为自调优）" scripts/pattern.py \
  '    sens = read_sensitive()
    if sens:' \
  '    sens = {}
    if sens:'

mutate "多主体串档提醒（gstack 同款缺陷）" scripts/pattern.py \
  '    _multi_owner_warning()
    sens = read_sensitive()' \
  '    sens = read_sensitive()'

mutate "筛查与半途诊断可区分（体验）" scripts/archive.py \
  '        elif d["mode"] == "筛查":' \
  '        elif False:'

mutate "副业模式可区分（不套六问分母）" scripts/archive.py \
  '        if d["mode"] == "副业":' \
  '        if False:'

mutate "report 吃到文件末尾（严重·两份报告并存）" scripts/archive.py \
  'pattern = re.compile(r"^## 报告\s*\n.*\Z", re.MULTILINE | re.DOTALL)' \
  'pattern = re.compile(r"^## 报告\s*\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)'

mutate "写资源前必须有同意档位（严重·隐私）" scripts/resources.py \
  '    if not level:
        print("拒绝写入：这个项目还没有记录同意档位。", file=sys.stderr)' \
  '    if False:
        print("拒绝写入：这个项目还没有记录同意档位。", file=sys.stderr)'

echo
if [ "$FAILED" -eq 0 ]; then
  echo "全部变异都被抓住 —— 测试套件是有效的。"
else
  echo "有变异逃逸了。上面标 ✗ 的修复没有测试保护，补测试再说。"
fi
exit "$FAILED"

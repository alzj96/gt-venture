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

mutate "pattern/强项 不删手写小节（严重·静默删数据）" scripts/pattern.py \
  'for kind in list(kinds) + [k for k in data if k not in kinds]:' \
  'for kind in kinds:'

mutate "断点取第一个未答（中·跳答后漏问）" scripts/archive.py \
  '    done, next_q = progress(body, meta.get("模式", "诊断"))' \
  '    done, next_q = answered, answered + 1'

# 这条要回滚两处：回落逻辑 + 写全局键。只回滚前者的话，脚本根本不写
# 全局键，回落打不着，变异是惰性的 —— 变异台第一次跑就是这么误报的。
mutate "save/report 用真进度（中·口径分裂）" scripts/archive.py \
  '    done, nxt = progress(body, mode)
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

mutate "pattern 与 archive 口径一致（中·天天误报）" scripts/pattern.py \
  '                if _is_archive(p)]' \
  '                if p.name not in {PATTERN_FILE, SENSITIVE_FILE, SIGNAL_FILE}]'

# —— report_html.py：它的失败方式是"安静地产出一份坏文件，而那份文件
#    已经被转发出去了"，所以每一条都得有测试钉着。
mutate "报告 HTML 零外部请求（严重·转发即失效）" scripts/report_html.py \
  'CSS = """' \
  'CSS = """@import url("https://fonts.googleapis.com/css2?family=X");'

mutate "报告 HTML 转义内容（严重·结构被撑坏）" scripts/report_html.py \
  '    t = html_escape(t)' \
  '    pass'

mutate "有序列表不从 1 重来（中·两个「1.」）" scripts/report_html.py \
  'and start != 1 else' \
  'and False else'

mutate "表格渲染得出来（严重·丢整节）" scripts/report_html.py \
  '    head, body = cells[0], cells[2:]' \
  '    return ""'

mutate "还原档案里被降级的标题（严重·整份没有大标题）" scripts/report_html.py \
  '    return re.sub(r"^##(#{1,4})(?= )", r"\1", body, flags=re.MULTILINE)' \
  '    return body'

mutate "没报告时拒绝导出（中·空壳文件被转发）" scripts/report_html.py \
  '    if not report:' \
  '    if False:'

mutate "对接卡：我有为空不出卡（严重·network 门槛失守）" scripts/resources.py \
  '    if not entry["have"]:' \
  '    if False:'

mutate "对接卡：anon 不带联系方式（严重·隐私）" scripts/resources.py \
  '    if level == "anon" and args.contact:' \
  '    if False:'

mutate "对接卡：条目里的联系方式不分档位都拦（严重·替别人暴露）" scripts/resources.py \
  '    contacts = sorted({h for t in texts for h in sniff_contact(t)})' \
  '    contacts = []'

mutate "页脚 network 那句只给有东西可换的人（中·指进不去的门）" scripts/report_html.py \
  '    return bool(have and re.search(r"^- 类型:", have.group(1), re.MULTILINE))' \
  '    return True'

mutate "改成 off 要删掉已经存过的资源（严重·不留痕没兑现）" scripts/resources.py \
  '        new_body, n = section.subn("", body)' \
  '        new_body, n = body, 0'

mutate "跳过的问把补存命令递出去（中·下次重问）" scripts/archive.py \
  '        gaps = _unanswered_before(body, step, mode)' \
  '        gaps = []'

mutate "报告里的禁语要报出来（中·小模型拦不住）" scripts/archive.py \
  '    hits = [w for w in BANNED if w in report]' \
  '    hits = []'

mutate "点标题真的跳过去（严重·转发出去点不动）" scripts/report_html.py \
  "    var a=e.target.closest('a[href^=\"#\"]'); if(!a) return;" \
  "    var a=null; if(!a) return;"

# —— 副业和筛查这两条路，第一次真跑才发现全线按六问走。
#    MODE_STEPS 写了三轮没接线，下面四条就是那几根线。
mutate "副业按四问算进度（严重·把模型推去问第5问）" scripts/archive.py \
  '    return MODE_STEPS.get(mode, TOTAL_STEPS)' \
  '    return TOTAL_STEPS'

mutate "副业有自己的问题标题（严重·答案贴错标签）" scripts/archive.py \
  '    return SIDE_TITLES if mode == "副业" else STEP_TITLES' \
  '    return STEP_TITLES'

mutate "筛查不催着问第2问（严重·三项拖成半截六问）" scripts/archive.py \
  '    if mode == "筛查":' \
  '    if False:'

mutate "改模式不静默删有答案的小节（严重·丢数据）" scripts/archive.py \
  '        if _has_real_answer(m.group(1)):' \
  '        if False:'

mutate "页眉页脚跟着模式走（严重·筛查页上写着诊断）" scripts/report_html.py \
  '    kind, foot = CHROME.get(mode, CHROME["诊断"])' \
  '    kind, foot = CHROME["诊断"]'

mutate "点目录条目要收抽屉（严重·点了像没反应）" scripts/report_html.py \
  "    if(e.target.closest('a')) closeToc();" \
  "    if(false) closeToc();"

mutate "正文要用的工具必须在许可名单里（严重·真机上不弹）" SKILL.md \
  '  - AskUserQuestion' \
  '  - Glob'

mutate "SKILL.md 常驻体积不超过 5000 字（严重·每一轮都背着）" SKILL.md \
  '## 结束时报状态' \
  "$(python3 -c "print('## 结束时报状态\n\n' + '又一条顺手写进核心的教训。' * 300)")"

mutate "路由表的链接要真存在（严重·路由指空）" SKILL.md \
  '(references/declined.md)' \
  '(references/decline.md)'

# references 里的链接也要被扫到，不能只查 SKILL.md。
mutate "参考文件之间的链接要真存在（中·挪文件时断）" references/opening.md \
  '(asking.md#要用户选的时候怎么摆)' \
  '(ask.md#要用户选的时候怎么摆)'

# kb_lookup：每条变异拆掉一道守卫，对应的测试只能从那一条路命中。
mutate "关键词那一行要被读进去（严重·同义叫法查不到）" scripts/kb_lookup.py \
  '            heads[-1]["keywords"] += [w for w in KEYWORD_SPLIT.split(k.group(1)) if w]' \
  '            heads[-1]["keywords"] += []'

mutate "他的说法里含着关键词也算命中（严重·口语查不到）" scripts/kb_lookup.py \
  '        if k in t:      # 他的说法里含着关键词' \
  '        if False:      # 他的说法里含着关键词'

mutate "标题里含查的词算命中（中·标题查不到）" scripts/kb_lookup.py \
  '    if best[0] == 0 and t in norm(sec["title"]):' \
  '    if False:'

mutate "没命中要明说知识库没有（严重·查不到时编）" scripts/kb_lookup.py \
  '            print(f"「{term}」：知识库没有。\n")' \
  '            pass'

mutate "一个字的词不查（中·命中一大片）" scripts/kb_lookup.py \
  'MIN_LEN = 2 ' \
  'MIN_LEN = 1 '

mutate "一节读到下一个同级标题（中·读半节）" scripts/kb_lookup.py \
  '            if nxt["level"] <= h["level"] or h["level"] == 1:' \
  '            if True:'

mutate "卡的大标题只给开头一段（中·整份读）" scripts/kb_lookup.py \
  ' or h["level"] == 1:' \
  ':'

mutate "行号从 1 数（中·读错行）" scripts/kb_lookup.py \
  '    for i, line in enumerate(lines, 1):' \
  '    for i, line in enumerate(lines):'

mutate "编号开头的说明文件不当卡（中·把索引当卡）" scripts/kb_lookup.py \
  'if not META_FILE.match(p.name)]' \
  'if True]'

mutate "超过上限只报数不列（中·刷屏）" scripts/kb_lookup.py \
  '        for score, f, sec, why in hits[:args.limit]:' \
  '        for score, f, sec, why in hits:'

mutate "迁移前的 rules-*.md 也要查（中·规则查不到）" scripts/kb_lookup.py \
  '    files += sorted((root / "references").glob("rules-*.md"))' \
  '    files += []'

mutate "代码块里的 # 不算标题（中·命中模板）" scripts/kb_lookup.py \
  '            in_code = not in_code' \
  '            in_code = False'

mutate "术语卡开着时按钮让位（中·压在解释上）" scripts/report_html.py \
  "    document.body.classList.add('term-open');" \
  "    void 0;"

mutate "空的那一格也要打出来（严重·假装覆盖）" scripts/check_rules.py \
  '        if fname is None:' \
  '        if False:'

mutate "迁进 knowledge/ 的闸门规则文件要认得（严重·迁移后报假的缺口）" scripts/check_rules.py \
  '        elif (refs / fname).is_file():' \
  '        elif fname in {p.name for p in files}:'

mutate "说清楚类目只覆盖微信（中·平台偏向）" scripts/check_rules.py \
  '"只覆盖微信小程序，且只有高频那几类。' \
  '"覆盖主流渠道的类目要求。'

mutate "失败模式库也露名（被扫了却没人知道）" scripts/check_rules.py \
  '    if extra:' \
  '    if False:'

mutate "不把「新鲜」说成「有效」（严重·误导）" scripts/check_rules.py \
  'print(f"📅 拉取日期在保质期内 {len(fresh)} 条（{newest}–{oldest} 天前抄的）")' \
  'print(f"✓ 有效 {len(fresh)} 条（{newest}–{oldest} 天前核对）")'

# 知识卡按档位算保质期。
mutate "knowledge/ 下的卡也要扫（严重·过期没人知道）" scripts/check_rules.py \
  '    kb_files = sorted((refs / "knowledge").rglob("*.md")) if (refs / "knowledge").is_dir() else []' \
  '    kb_files = []'

mutate "没写档位按规则算（严重·迁移后变宽松）" scripts/check_rules.py \
  'DEFAULT_GRADE = "规则"' \
  'DEFAULT_GRADE = "公开数据"'

mutate "公开数据用自己的保质期（中·天天误报）" scripts/check_rules.py \
  '    limits = {None: max_age, "data": data_max_age}' \
  '    limits = {None: max_age, "data": max_age}'

mutate "表格一行一条地查（严重·一整张表不查）" scripts/check_rules.py \
  '                if "档位" in cells and "拉取日期" in cells:' \
  '                if False:'

mutate "经验估计不按日期过期、单独列（中·估计混进事实）" scripts/check_rules.py \
  '"经验估计": 0}' \
  '"经验估计": "data"}'

mutate "档位写错要报出来（中·静默放行）" scripts/check_rules.py \
  '                bad_grade.append((name, label, grade))' \
  '                pass'

mutate "追问说不方便不回退已答（严重·进度倒退）" scripts/archive.py \
  '        if m and _has_real_answer(m.group(1)):' \
  '        if m and m.group(1).strip() and UNANSWERED not in m.group(1):'

mutate "「未答」二字出现在答案里不算标记（中）" scripts/archive.py \
  '        if t.startswith(UNANSWERED):' \
  '        if UNANSWERED in t:'

mutate "邻居文件不被当成项目（严重·静默失败·踩过两次）" scripts/archive.py \
  'def is_archive(doc: dict) -> bool:
    return bool(_ARCHIVE_NAME.search(doc["path"].stem)) or doc["has_project_key"]' \
  'def is_archive(doc: dict) -> bool:
    return doc["path"].name not in {"资源.md", "模式.md"}'

mutate "手工档案不被正面判定关在门外（严重·丢档案）" scripts/archive.py \
  'return bool(_ARCHIVE_NAME.search(doc["path"].stem)) or doc["has_project_key"]' \
  'return bool(_ARCHIVE_NAME.search(doc["path"].stem))'

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
    _print_signals()' \
  '    _print_signals()'

mutate "强项写自己的文件（共用 render 后最易串）" scripts/pattern.py \
  'def signal_path() -> Path:
    return (_WORKSPACE or Path.cwd()) / ARCHIVE_DIR / SIGNAL_FILE' \
  'def signal_path() -> Path:
    return (_WORKSPACE or Path.cwd()) / ARCHIVE_DIR / PATTERN_FILE'

mutate "强项也挡联系方式和长文（隐私·不对称即无约束）" scripts/pattern.py \
  '    rc = _guard_note(note)
    if rc:
        return rc

    data = parse(signal_path())' \
  '    rc = 0
    if rc:
        return rc

    data = parse(signal_path())'

mutate "强项在 show 里浮出（只有优点的人会全空）" scripts/pattern.py \
  '    _multi_owner_warning()
    _print_signals()
    sens = read_sensitive()' \
  '    _multi_owner_warning()
    sens = read_sensitive()'

mutate "强项排在毛病前面（顺序决定他要不要接着聊）" scripts/pattern.py \
  'def cmd_show(_args) -> int:
    _multi_owner_warning()
    _print_signals()' \
  'def cmd_show(_args) -> int:
    import atexit
    atexit.register(_print_signals)
    _multi_owner_warning()'

# 这条和上一条是同一个坑的两个历史状态：上一条回滚到「第二轮之前的黑名单」
# （漏 SIGNAL_FILE），这一条回滚到「第二轮当时的黑名单」（漏 资源.md）。
# 两条都得红，否则说明测试只盯住了其中一个漏法。
mutate "强项.md 不算成一个项目（第二轮前的漏法）" scripts/pattern.py \
  '                if _is_archive(p)]' \
  '                if p.name not in {PATTERN_FILE, SENSITIVE_FILE}]'

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

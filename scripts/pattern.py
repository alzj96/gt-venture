#!/usr/bin/env python3
"""跨项目模式：同一个人在不同项目上反复犯的同一个错。

档案是按项目存的，所以第二个项目开始时，技能对这个人的认知回到零。
但诊断这件事有很强的跨项目规律——一个人在项目 A 高估需求，在项目 B 大概率
还会高估。把这个规律记下来，第三个项目就能在他还没说完的时候提醒他。

这是这个技能唯一能随时间变强的地方。档案让你记得「这个项目聊到哪」，
模式让你记得「这个人是什么样的人」。

**这两份记录是按工作空间存的，不是按人存的。**

差分测试在 gstack 身上看到了这个坑的后果：它的 builder profile 按机器用户存，于是会对第一次来的王姐说「欢迎回来，上次我们聊的是口算练习」——上一个被诊断的根本是另一个人。

我们按工作空间存，比按机器用户好，但同款风险还在：**一个顾问在同一个目录里给多个客户做诊断，模式和敏感问题会串**。一个客户"高估需求"，下一个客户会被当成惯犯；一个客户不方便答第 6 问，下一个客户会被跳过那一问。

所以 `show` 会在发现多个项目时出声提醒：**先确认这些模式是不是同一个人的**。串了就换目录，或者删掉重来。

**只记模式，不记内容。** 模式文件里不写项目细节、不写人名、不写渠道，
只写「倾向」和它第几次出现。理由：模式会在每次诊断开头被读进上下文，
而用户可能不希望 A 项目的细节出现在 B 项目的对话里。

只用标准库，Python 3.8+。

用法：
    python3 pattern.py show
    python3 pattern.py log --kind 高估需求 --note "第1问又是先说市场规模"
    python3 pattern.py --workspace /path/to/ws show
"""

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

ARCHIVE_DIR = "创业档案"
PATTERN_FILE = "模式.md"
SENSITIVE_FILE = "敏感问题.md"

# 只认这七类。自由文本会让统计失效，而这里的全部价值就在「第几次」。
KINDS = {
    "高估需求": "把「我觉得大家需要」当成需求证据，缺付费或花时间的信号",
    "说不出人": "第3问答不出一个具体的人，只给人群标签",
    "楔子砍不动": "最小版本里塞了三个以上功能，问到只留一个就卡住",
    "优势是通用能力": "第6问答的是执行力/努力/我是产品经理这类谁都有的东西",
    "跟风": "洞察是「AI 现在很火」这类所有人都看得到的",
    "低估合规": "对资质、备案、主体资格没概念，或想用变通绕过去",
    "低估竞品": "说「还没人做」或「现有的都不好用」，但没查过",
}

# 模式文件可能被别的项目的细节污染，这里在写入时挡一道
LEAK_PATTERNS = [
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "手机号"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "邮箱"),
]

# 行为级自调优：这个用户对哪几问明确说过「不方便」。
#
# 这是 skill 里唯一会改变下一次行为的记录，所以约束要比模式更严：
#   · 只记问号，不记理由 —— 理由本身往往正是他不想留下的东西
#   · 只在用户**明确表示**不方便时写，不由模型推测
#   · 「说不出」和「没想好」不写这里 —— 那两种要继续问 / 要记成发现
#
# 为什么值得单独做：不方便答的问题往往正是最关键的那个（在编身份、
# 合伙人、钱的来源）。硬问的结果不是拿到答案，是他不再来第二次。
# 而诊断价值可以不靠他开口拿到 —— 见 SKILL.md 的「换成自查表」。
_SENSITIVE_HINT = {
    1: "需求验证（付费/花时间的证据）",
    2: "现状替代品",
    3: "具体到绝望（说出一个真人）",
    4: "最窄楔子",
    5: "你观察到了什么",
    6: "你凭什么（个人资源与身份）",
}

_WORKSPACE = None


def today() -> str:
    return _dt.date.today().isoformat()


def pattern_path() -> Path:
    return (_WORKSPACE or Path.cwd()) / ARCHIVE_DIR / PATTERN_FILE


def sensitive_path() -> Path:
    return (_WORKSPACE or Path.cwd()) / ARCHIVE_DIR / SENSITIVE_FILE


def read_sensitive() -> dict:
    """{问号: 次数}。文件不在就是空。"""
    p = sensitive_path()
    if not p.is_file():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"- 第(\d)问\s*[｜|]\s*(\d+)\s*次", line.strip())
        if m:
            out[int(m.group(1))] = int(m.group(2))
    return out


def write_sensitive(data: dict) -> None:
    body = ("# 这个用户不方便回答的问题\n\n"
            "只记问号和次数，不记理由 —— 理由本身往往正是他不想留下的东西。\n"
            "下次诊断到这几问，改成自查表的问法（见 SKILL.md），不要再直接问。\n"
            f"\n最后更新: {today()}\n\n")
    for q in sorted(data):
        body += f"- 第{q}问　｜　{data[q]} 次　｜　{_SENSITIVE_HINT.get(q, '')}\n"
    p = sensitive_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def parse() -> dict:
    """返回 {类型: [(日期, 备注), ...]}。"""
    p = pattern_path()
    if not p.is_file():
        return {}
    out, kind = {}, None
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("## "):
            kind = s[3:].strip()
            out.setdefault(kind, [])
        elif s.startswith("- ") and kind:
            m = re.match(r"- (\d{4}-\d{2}-\d{2})\s*[｜|]\s*(.*)", s)
            if m:
                out[kind].append((m.group(1), m.group(2).strip()))
    return out


def render(data: dict) -> str:
    head = ("# 跨项目模式\n\n"
            "同一个人在不同项目上反复出现的倾向。只记倾向，不记项目细节。\n"
            f"\n最后更新: {today()}\n")
    body = ""
    # 先按词表顺序输出已知类型，再把手写的未知小节原样带回去。
    # 只遍历 KINDS 会让「跑一次 log 就静默删掉用户手写的那一节」，
    # 退出码还是 0 —— 手工模式是文档明确宣传的，不能这么对待它。
    for kind in list(KINDS) + [k for k in data if k not in KINDS]:
        hits = data.get(kind) or []
        if not hits:
            continue
        body += f"\n## {kind}\n\n"
        for d, note in hits:
            body += f"- {d}　｜　{note}\n"
    return head + body


def cmd_log(args) -> int:
    if args.kind not in KINDS:
        print("kind 必须是这七类之一：", file=sys.stderr)
        for k, v in KINDS.items():
            print(f"  {k} —— {v}", file=sys.stderr)
        return 1

    note = args.note.strip()
    for pat, name in LEAK_PATTERNS:
        if pat.search(note):
            print(f"拒绝写入：模式文件不存 {name}。", file=sys.stderr)
            print("这个文件每次诊断开头都会被读进上下文，只写倾向，不写细节。", file=sys.stderr)
            return 2
    if len(note) > 80:
        print(f"备注太长（{len(note)} 字）。模式只记一句话，细节留在项目档案里。", file=sys.stderr)
        return 1

    data = parse()
    data.setdefault(args.kind, []).append((today(), note))
    p = pattern_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(data), encoding="utf-8")

    n = len(data[args.kind])
    print(f"已记：{args.kind}（第 {n} 次）")
    if n >= 2:
        print(f"⚠️  这是第 {n} 次了。下次诊断开头就该提醒用户，别等他自己撞上。")
    return 0


def cmd_retire(args) -> int:
    """下架一个已改掉的倾向。

    没有退役机制的话，用户改掉了「高估需求」的毛病，那条记录永远在、
    ⚠️ 永远触发；七类全部 ≥2 次之后，第 0 步开场白会变成一份罪状清单。
    """
    data = parse()
    if args.kind not in data or not data[args.kind]:
        print(f"没有「{args.kind}」的记录，无需下架。", file=sys.stderr)
        return 1
    n = len(data.pop(args.kind))
    pattern_path().write_text(render(data), encoding="utf-8")
    print(f"已下架：{args.kind}（原有 {n} 次记录）")
    print("下次诊断不会再因为这一条提醒用户。改回来了再 log 一次即可。")
    return 0


def cmd_sensitive(args) -> int:
    """记一次「用户明确说不方便回答第 N 问」。"""
    if not 1 <= args.step <= 6:
        print("step 必须在 1..6 之间", file=sys.stderr)
        return 1
    data = read_sensitive()
    data[args.step] = data.get(args.step, 0) + 1
    write_sensitive(data)
    n = data[args.step]
    print(f"已记：第{args.step}问 用户不方便回答（第 {n} 次）")
    print("只记了问号，没记理由。")
    if n >= 2:
        print(f"⚠️  这是第 {n} 次了。下次诊断到这一问，直接用自查表的问法，别再问。")
    return 0


def _multi_owner_warning() -> None:
    """同一工作空间有多个项目档案时，提醒确认是不是同一个人。

    模式和敏感问题是「关于这个人」的记录，档案是「关于这个项目」的。
    一个人做多个项目 → 该串；一个顾问做多个客户 → 不该串，而脚本分不出来。
    分不出来的时候出声，比默认串或默认不串都好。
    """
    root = (_WORKSPACE or Path.cwd()) / ARCHIVE_DIR
    if not root.is_dir():
        return
    projects = [p.stem.split("-诊断-")[0] for p in root.glob("*.md")
                if p.name not in {PATTERN_FILE, SENSITIVE_FILE}]
    if len(set(projects)) >= 2:
        print(f"ℹ️  这个工作空间里有 {len(set(projects))} 个项目的档案。")
        print("   下面的模式和敏感问题是「关于这个人」的，不是关于项目的。")
        print("   如果这些项目属于不同的人（比如你在给多个客户做诊断），")
        print("   这些记录会串——换个目录，或者删掉重来。\n")


def cmd_show(_args) -> int:
    _multi_owner_warning()
    sens = read_sensitive()
    if sens:
        print("这个用户不方便回答的问题：\n")
        for q in sorted(sens):
            mark = "⚠️ " if sens[q] >= 2 else "   "
            print(f"{mark}第{q}问（{_SENSITIVE_HINT.get(q, '')}）—— {sens[q]} 次")
        print()
        print("这几问改用自查表的问法：把结论摆出来让他自己对照，不要求他开口。")
        print("见 SKILL.md「用户不方便回答时」。\n")
        print("━" * 52 + "\n")

    data = parse()
    if not data or not any(data.values()):
        print("还没有跨项目模式记录。")
        print("这是第一次诊断，或者之前没记录过。正常往下走。")
        return 0

    ranked = sorted(((k, v) for k, v in data.items() if v),
                    key=lambda kv: len(kv[1]), reverse=True)
    print("这个人过去的倾向：\n")
    for kind, hits in ranked:
        mark = "⚠️ " if len(hits) >= 2 else "   "
        print(f"{mark}{kind} —— {len(hits)} 次（最近 {hits[-1][0]}）")
        desc = KINDS.get(kind, "（手写类型，不在七类词表里）")
        print(f"     {desc}")
        for d, note in hits[-2:]:
            print(f"     · {d} {note}")
        print()

    repeat = [k for k, v in ranked if len(v) >= 2]
    if repeat:
        print("━" * 52)
        print(f"反复出现的：{'、'.join(repeat)}")
        print("在对应的那一问上多追一句，或者在开头就把这个倾向说出来——")
        print("用「你上次也是在这儿卡住」的方式，比等他再撞一次有用。")
        print("说的时候只说倾向，不要提另一个项目的细节。")
        print("━" * 52)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="跨项目模式")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="诊断开头读一次")

    p_r = sub.add_parser("retire", help="用户已改掉某个毛病，停止提醒")
    p_r.add_argument("--kind", required=True)

    p_s = sub.add_parser("sensitive", help="记一次「用户明确说不方便回答第N问」")
    p_s.add_argument("--step", type=int, required=True)

    p_l = sub.add_parser("log", help="诊断结束时记录本次观察到的倾向")
    p_l.add_argument("--kind", required=True, help=" / ".join(KINDS))
    p_l.add_argument("--note", required=True, help="一句话，≤80 字，不含细节")

    ap.add_argument("--workspace", type=Path, default=None,
                    help="用户工作空间路径。默认当前目录。")

    args = ap.parse_args()
    global _WORKSPACE
    _WORKSPACE = args.workspace.resolve() if args.workspace else None
    return {"show": cmd_show, "log": cmd_log, "retire": cmd_retire,
            "sensitive": cmd_sensitive}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""规则保质期检查。

规则库里的每条政策都带 `拉取日期`。这个脚本扫出所有日期，算出距今多久，
把过期的报出来——目的不是自动去抓新规则，而是让引用规则时能诚实地告诉用户
"这条最后核对于 X 日期"。

为什么不自动重抓：抓取要在作者的工坊里做，人工比对确认后再发新版。
在用户的会话里现搜，拿到的是搜索摘要而不是一手源，而监管规则是不能靠摘要下结论的。
规则文件里的 `状态: 待证` 标记同样会被报出来，那是尚未双源互证的条目。

只用标准库，Python 3.8+。

用法：
    python3 check_rules.py                # 默认阈值 90 天
    python3 check_rules.py --max-age 30
    python3 check_rules.py --refs ../references
"""

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

DATE_RE = re.compile(r"拉取日期[:：]\s*(\d{4}-\d{2}-\d{2})")
PENDING_RE = re.compile(r"状态[:：]\s*待证")

# 四道闸：任何行业、任何事都要过这四道，所以这张表不随行业变。
# (闸, 问的是什么, 对应的规则文件, 覆盖到什么程度)
GATES = [
    ("一、人", "这个人能不能做这件事", "rules-personal-eligibility.md",
     "覆盖：在编教师 / 公务员 / 事业单位 / 在校学生 / 在职副业 / 职务成果 / 个人接单的税。\n"
     "     未覆盖：医生、律师、会计师、军人、国企任职、外籍、失信被执行人。"),
    ("二、事", "这件事本身要不要许可", None,
     "整格空白 —— 食品经营、医疗器械、人力资源、旅行社、出版物、危化品……一条都没有。\n"
     "     遇到就说不知道：给类别名 + 市场监管部门/12345 的入口。"),
    ("三、地", "在哪个渠道上线，那个渠道要什么", "rules-miniprogram-categories.md",
     "只覆盖微信小程序，且只有高频那几类。\n"
     "     抖音/快手/支付宝/App商店/网站/公众号/视频号/线下，全没有。"),
    ("四、钱", "钱怎么收、要不要办执照", "rules-money.md",
     "覆盖：市场主体登记（已核）。待证：豁免那条线。未覆盖：发票开法、对公账户、社保、跨境。\n"
     "     另见 rules-prepaid.md（预付款，不挑行业）与 E9（个人接单的税）。"),
]
# 取标题行作为条目名，找不到就退回文件名
HEADING_RE = re.compile(r"^#{2,4}\s+(.+?)\s*$", re.MULTILINE)


def default_refs() -> Path:
    return (Path(__file__).resolve().parent.parent / "references")


def label_for(text: str, pos: int, fallback: str) -> str:
    """往前找最近的一个标题，作为这条规则的名字。

    标题行里常常内联着 `拉取日期: ...` / `状态: 待证` 这样的标记，
    它们属于元数据不属于条目名，剥掉之后报出来才读得通。"""
    last = fallback
    for m in HEADING_RE.finditer(text, 0, pos):
        last = m.group(1)
    return re.sub(r"`[^`]*`", "", last).strip(" `") or fallback


def scan(refs: Path, max_age: int) -> int:
    if not refs.is_dir():
        print(f"找不到 references 目录：{refs}", file=sys.stderr)
        return 1

    files = sorted(list(refs.glob("rules-*.md")) + list(refs.glob("failure-modes.md")))
    if not files:
        print(f"{refs} 下没有 rules-*.md / failure-modes.md，库为空。")
        return 0

    today = _dt.date.today()
    fresh, stale, pending = [], [], []

    for path in files:
        text = path.read_text(encoding="utf-8")

        for m in DATE_RE.finditer(text):
            raw = m.group(1)
            try:
                d = _dt.date.fromisoformat(raw)
            except ValueError:
                stale.append((path.name, f"日期无法解析：{raw}", -1))
                continue
            age = (today - d).days
            entry = (path.name, label_for(text, m.start(), path.stem), age)
            (stale if age > max_age else fresh).append(entry)

        for m in PENDING_RE.finditer(text):
            pending.append((path.name, label_for(text, m.start(), path.stem)))

    print(f"规则库：{refs}")
    print(f"今天 {today.isoformat()}，过期阈值 {max_age} 天\n")

    if stale:
        print(f"⚠️  过期 {len(stale)} 条 —— 引用这些条目时必须告诉用户核对日期：")
        for fname, label, age in stale:
            age_txt = "日期无效" if age < 0 else f"{age} 天前"
            print(f"  · [{fname}] {label} —— {age_txt}")
        print()

    if pending:
        print(f"🔍 待证 {len(pending)} 条 —— 尚未双源互证，引用时必须说明「这条我还没核实到一手源」：")
        for fname, label in pending:
            print(f"  · [{fname}] {label}")
        print()

    if fresh:
        newest = min(a for _, _, a in fresh)
        oldest = max(a for _, _, a in fresh)
        print(f"📅 拉取日期在保质期内 {len(fresh)} 条（{newest}–{oldest} 天前抄的）")
        print("   这只说明我们最近抄过它，不说明那条法规还在生效。")
        print("   实测里一条 2023 年就被废止的规章在这里显示为绿——四次独立诊断都撞到了它。")
        print("   要确认「还在不在」，只能去一手源看有没有被新文件废止或修订。")

    if not stale:
        print("（日期都在保质期内）")
    else:
        print("\n刷新方式：回到各条目的来源链接重抓，人工比对确认后更新规则文件的拉取日期。")
        print("不要在对话里现搜现用——搜索摘要不能当一手源。")

    # 这一段是这个脚本最容易被误读的地方，必须每次都打。
    # 实测中它对一个规则库根本不覆盖的行业（宠物上门、港美股）报了「✓ 有效 10 条」，
    # 读的人很容易把「日期新鲜」当成「这行查过了」。
    print()
    print("━" * 52)
    print("注意：上面回答的只有一件事——「这条我们多久没抄过了」。")
    print("它不回答「这条法规还在不在」，也不回答「你这行覆盖没覆盖」。")
    # 按「闸门」报覆盖度，不按文件名报。
    #
    # 原先这里打的是文件名列表，那回答的是"我们有哪几个文件"，
    # 而用户真正要知道的是"我这门生意要过的闸，你覆盖了哪几道"。
    # 四道闸不挑行业（人 / 事 / 地 / 钱），内容永远不全，但框架必须完整——
    # 所以空的那一格也要打出来，而且要打得比有的那几格更显眼。
    print("按四道闸看覆盖度（框架见 references/gates.md）：")
    present = {p.name for p in files}
    for gate, question, fname, note in GATES:
        if fname is None:
            print(f"  ✗ {gate}：{question}")
            print(f"     {note}")
        elif fname in present:
            print(f"  · {gate}：{question}")
            print(f"     {note}")
        else:
            print(f"  ✗ {gate}：{question} —— 规则文件不在（{fname}）")
    # 失败模式库不是闸门，但它同样会过期（反例失效、竞品收费了、法规变了），
    # 所以它在保质期扫描里，得在这儿露个名字，否则没人知道它也被扫了。
    extra = sorted(p.name for p in files
                   if p.name not in {g[2] for g in GATES if g[2]})
    if extra:
        print(f"  （不属于闸门、但同样在扫描内：{'、'.join(extra)}）")
    print()
    print("用户的业务不在覆盖范围内时，正确做法是明说不知道并给出查询链接，")
    print("也不要因为日期是绿的就以为那条法规还有效——那是两件事。")
    print("━" * 52)

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="检查规则库条目是否过期")
    ap.add_argument("--max-age", type=int, default=90, help="过期阈值（天），默认 90")
    ap.add_argument("--refs", type=Path, default=None, help="references 目录路径")
    args = ap.parse_args()
    return scan(args.refs or default_refs(), args.max_age)


if __name__ == "__main__":
    sys.exit(main())

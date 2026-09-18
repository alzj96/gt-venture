#!/usr/bin/env python3
"""知识库查询：传几个关键词，返回命中的文件和章节（带行号）。

为什么要这个：知识库要覆盖全行业（20 个门类、97 个大类，外加深卡和横向规则卡），
整份读进对话本身就会拖慢之后的每一轮——读进来的东西会一直留在上下文里。
所以模型**先调这个脚本，再只读命中的那一两节**。

查的是什么：
    references/knowledge/ 下每份卡的标题，和标题下面那一行 `关键词：a、b、c`
    references/rules-*.md 的标题（迁移进 knowledge/ 之前的规则库）
    00-索引.md、01-怎么用.md 这种编号开头的说明文件不算卡，不返回
    代码块里的 # 不算标题（模板和示例里全是）

返回的行号：这一节从标题读到下一个同级标题之前（子标题算在里面）；
卡的大标题只给开头那一段，不给整份。

怎么算命中（同一节只报一次，按最强的那种排）：
    1. 关键词和查的词一模一样         「茶饮」= 关键词「茶饮」
    2. 他的说法里含着一个关键词       「开奶茶店」含关键词「奶茶」
       或者查的词是某个关键词的一部分  「喂猫」⊂ 关键词「上门喂猫」
    3. 标题里含着查的词               「预付」⊂「预付式消费」

同义叫法靠卡里的 `关键词：` 那一行：「奶茶」「茶饮」「饮品店」要落到同一节，
写卡的人就三个都写上（见 docs/知识库写卡规范.md）。

只用标准库。

用法：
    python3 kb_lookup.py 上门喂猫 宠物
    python3 kb_lookup.py 奶茶 --limit 5
"""

import argparse
import re
import sys
import unicodedata
from pathlib import Path

MIN_LEN = 2          # 一个字的词（「店」「猫」）会命中一大片，等于没查
DEFAULT_LIMIT = 8    # 一个词最多列几节。列太多，模型就会整份读，那这个脚本白写了

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
KEYWORD_RE = re.compile(r"^\s*关键词[:：]\s*(.+?)\s*$")
KEYWORD_SPLIT = re.compile(r"[、，,；;／/\s]+")
META_FILE = re.compile(r"^\d{2}-")


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip().casefold()


def clean_title(raw: str) -> str:
    """标题行里内联的 `档位: …` `拉取日期: …` 是元数据，不算标题。"""
    return re.sub(r"`[^`]*`", "", raw).strip()


def default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def card_files(root: Path):
    kb = root / "references" / "knowledge"
    files = []
    if kb.is_dir():
        files += [p for p in sorted(kb.rglob("*.md")) if not META_FILE.match(p.name)]
    files += sorted((root / "references").glob("rules-*.md"))
    return files


def sections(path: Path):
    """一份卡拆成节：标题、级别、起止行、祖先链、关键词。代码块里的 # 不算标题。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    heads = []
    in_code = False
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = HEADING_RE.match(line)
        if m:
            heads.append({"level": len(m.group(1)), "title": clean_title(m.group(2)),
                          "start": i, "keywords": []})
        elif heads and (k := KEYWORD_RE.match(line)):
            heads[-1]["keywords"] += [w for w in KEYWORD_SPLIT.split(k.group(1)) if w]

    stack = []
    for idx, h in enumerate(heads):
        # 这一节读到下一个同级或更高级的标题之前；子标题算这一节的一部分。
        # 例外：卡的大标题（一级）只给开头那一段——什么时候读、来源、关键词。
        # 给整份的行号，模型就整份读了，那这个脚本白写。
        end = len(lines)
        for nxt in heads[idx + 1:]:
            if nxt["level"] <= h["level"] or h["level"] == 1:
                end = nxt["start"] - 1
                break
        h["end"] = end
        while stack and stack[-1]["level"] >= h["level"]:
            stack.pop()
        h["path"] = [s["title"] for s in stack] + [h["title"]]
        stack.append(h)
    return heads


def match(term: str, sec) -> tuple:
    """返回 (分数, 理由)。0 分是没命中。"""
    t = norm(term)
    best = (0, "")
    for kw in sec["keywords"]:
        k = norm(kw)
        if len(k) < MIN_LEN:
            continue
        if k == t:
            return (3, f"关键词「{kw}」")
        if k in t:      # 他的说法里含着关键词：「开奶茶店」⊃「奶茶」
            best = max(best, (2, f"关键词「{kw}」"))
        elif t in k:    # 查的词是关键词的一部分：「喂猫」⊂「上门喂猫」
            best = max(best, (2, f"关键词「{kw}」"))
    if best[0] == 0 and t in norm(sec["title"]):
        best = (1, f"标题含「{term}」")
    return best


def lookup(root: Path, term: str):
    hits = []
    for f in card_files(root):
        for sec in sections(f):
            score, why = match(term, sec)
            if score:
                hits.append((score, f, sec, why))
    hits.sort(key=lambda h: (-h[0], str(h[1]), h[2]["start"]))
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="查知识库：返回命中的文件和章节，只读那几节。")
    ap.add_argument("terms", nargs="+", help="关键词，可以传几个")
    ap.add_argument("--root", type=Path, default=None, help="技能目录（默认是这个脚本的上一级）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"每个词最多列几节（默认 {DEFAULT_LIMIT}）")
    args = ap.parse_args(argv)
    root = (args.root or default_root()).resolve()

    missed = []
    for term in args.terms:
        if len(norm(term)) < MIN_LEN:
            print(f"「{term}」太短，一个字会命中一大片。换成两个字以上的叫法（比如「奶茶店」「上门喂猫」）。\n")
            missed.append(term)
            continue
        hits = lookup(root, term)
        if not hits:
            print(f"「{term}」：知识库没有。\n")
            missed.append(term)
            continue
        print(f"「{term}」命中 {len(hits)} 节：")
        for score, f, sec, why in hits[:args.limit]:
            rel = f.relative_to(root)
            print(f"  {rel}  第 {sec['start']}–{sec['end']} 行  {' › '.join(sec['path'])}  （{why}）")
        if len(hits) > args.limit:
            print(f"  ……还有 {len(hits) - args.limit} 节没列。换个更具体的叫法再查，别整份读。")
        print()

    if missed:
        print(f"没查到的：{'、'.join(missed)}")
        print("下一步：按 references/knowledge/01-怎么用.md 里「联网」那一节查（先脱敏，搜到的只算线索，不是规则），")
        print(f"并在报告里记一行「知识库缺口：{'、'.join(missed)}」。")
    if len(missed) < len(args.terms):
        print("只读上面列出的那几行，不要整份读。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

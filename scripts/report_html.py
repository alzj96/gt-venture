#!/usr/bin/env python3
"""把档案里的报告渲染成一个能转发的 HTML 文件。

**为什么要有这个**：报告的全部意义是「能被转发给合伙人看」（见
references/report-format.md 第一句）。而一段 markdown 贴在聊天框里，
转给一个不用 AI 的人，他看到的是一堆 ## 和 **。

**三条设计约束，每条都有理由：**

1. **单文件、零外部依赖。** 样式内联，不引 CDN、不引字体、不带 JS。
   转发出去的文件会在断网的手机上、在微信内置浏览器里、在打印预览里
   被打开——任何一个外部请求都可能让它变成裸文本。
2. **从档案生成，不从对话生成。** 唯一真相是 `创业档案/` 里那份档案，
   所以这个脚本随时可以重跑，也能给几个月前的旧档案补一份 HTML。
3. **它是附加的，不是唯一的。** 宿主可能不让用户拿到生成的文件（这一点
   我们没有在 WorkBuddy 上验证过）。所以 markdown 那份照样要发进对话，
   HTML 是锦上添花——**不要因为生成了 HTML 就不发正文**。

**这个 markdown 子集是刻意小的**：标题、粗体、行内代码、引用、无序/有序
列表、表格、分隔线、链接。报告格式里用到的就这些。遇到不认识的语法，
按原样当段落输出，**不要报错**——报告出不来比排版难看严重得多。

只用标准库，Python 3.8+。

用法：
    python3 report_html.py --workspace /path/to/ws --project 王姐优选
    python3 report_html.py --workspace /path/to/ws --project 王姐优选 --out /tmp/a.html
"""

import argparse
import datetime as _dt
import html
import re
import sys
from pathlib import Path

ARCHIVE_DIR = "创业档案"
SKIP_FILES = {"模式.md", "强项.md", "敏感问题.md", "资源.md"}

CSS = """
:root{--ink:#1a1a1a;--dim:#5b5b5b;--line:#e3e3e3;--bg:#fff;--accent:#8a5a2b;--mark:#fff7e8}
*{box-sizing:border-box}
body{margin:0;background:#f6f5f3;color:var(--ink);
 font:16px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
 -webkit-text-size-adjust:100%}
.page{max-width:720px;margin:0 auto;background:var(--bg);padding:40px 28px 56px}
h1{font-size:26px;line-height:1.35;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:19px;margin:38px 0 12px;padding-top:18px;border-top:1px solid var(--line)}
h2:first-of-type{border-top:0;padding-top:0}
h3{font-size:16px;margin:24px 0 8px}
h4{font-size:15px;margin:18px 0 6px;color:var(--dim)}
p{margin:0 0 14px}
ul,ol{margin:0 0 14px;padding-left:1.4em}
li{margin:0 0 7px}
li>ul,li>ol{margin-top:7px}
strong{font-weight:600}
code{background:#f2f1ee;padding:.12em .38em;border-radius:3px;font-size:.9em;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
a{color:var(--accent)}
blockquote{margin:0 0 14px;padding:10px 16px;background:var(--mark);
 border-left:3px solid var(--accent);color:#4a3a22}
blockquote p:last-child{margin-bottom:0}
hr{border:0;border-top:1px solid var(--line);margin:30px 0}
.meta{color:var(--dim);font-size:14px;margin:0 0 26px}
.tablewrap{overflow-x:auto;margin:0 0 16px}
table{border-collapse:collapse;width:100%;font-size:14.5px}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{background:#faf9f7;font-weight:600}
.foot{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);
 color:var(--dim);font-size:13px;line-height:1.7}
@media(max-width:560px){.page{padding:26px 18px 40px}h1{font-size:22px}body{font-size:15.5px}}
@media print{body{background:#fff}.page{max-width:none;padding:0}}
"""


def _inline(t: str) -> str:
    """行内标记。**先转义再替换**，否则报告里的 < > & 会被当成标签。"""
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
               r'<a href="\2" rel="noopener">\1</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    return t


def _table(rows: list) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    # 第二行是 |---|---| 这种分隔行，没有它就不是表
    if len(cells) < 2 or not all(set(c) <= set("-: ") and c for c in cells[1]):
        return ""
    head, body = cells[0], cells[2:]
    out = ['<div class="tablewrap"><table><thead><tr>']
    out += [f"<th>{_inline(c)}</th>" for c in head]
    out.append("</tr></thead><tbody>")
    for row in body:
        row = (row + [""] * len(head))[:len(head)]
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def md_to_html(md: str) -> str:
    """刻意小的 markdown 子集。认不出来的按段落原样输出，绝不抛异常。"""
    out, buf, lst, quote, table = [], [], None, [], []

    def flush_p():
        if buf:
            out.append("<p>" + "<br>".join(_inline(x) for x in buf) + "</p>")
            buf.clear()

    def flush_list():
        nonlocal lst
        if lst:
            tag, items, start = lst
            # 有序列表要带 start：报告里「1. …」下面常挂一串 - 子项，
            # 子项会把 ol 截断，后面的「2. …」于是又从 1 开始——
            # 一份转发给合伙人的报告里出现两个「1.」，比排版难看严重。
            attr = f' start="{start}"' if tag == "ol" and start != 1 else ""
            out.append(f"<{tag}{attr}>" +
                       "".join(f"<li>{_inline(i)}</li>" for i in items) +
                       f"</{tag}>")
            lst = None

    def flush_quote():
        if quote:
            out.append("<blockquote>" +
                       "".join(f"<p>{_inline(x)}</p>" for x in quote if x.strip()) +
                       "</blockquote>")
            quote.clear()

    def flush_table():
        if table:
            rendered = _table(table)
            # 不是合法表格就按普通段落放回去，别把内容吞掉
            out.append(rendered or "<p>" + "<br>".join(_inline(r) for r in table) + "</p>")
            table.clear()

    def flush_all():
        flush_p(); flush_list(); flush_quote(); flush_table()

    for raw in md.splitlines():
        line = raw.rstrip()
        s = line.strip()

        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            flush_p(); flush_list(); flush_quote()
            table.append(s)
            continue
        flush_table()

        if not s:
            flush_p(); flush_list(); flush_quote()
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_all()
            lvl = min(len(m.group(1)), 4)
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            continue

        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s):
            flush_all()
            out.append("<hr>")
            continue

        if s.startswith(">"):
            flush_p(); flush_list()
            quote.append(s.lstrip(">").strip())
            continue
        flush_quote()

        m = re.match(r"^[-*+]\s+(.*)$", s)
        if m:
            flush_p()
            if lst and lst[0] != "ul":
                flush_list()
            lst = ("ul", (lst[1] if lst else []) + [m.group(1)], 1)
            continue
        m = re.match(r"^(\d+)[.)]\s+(.*)$", s)
        if m:
            flush_p()
            if lst and lst[0] != "ol":
                flush_list()
            n0 = int(m.group(1)) if not lst else lst[2]
            lst = ("ol", (lst[1] if lst else []) + [m.group(2)], n0)
            continue
        flush_list()

        buf.append(s)

    flush_all()
    return "\n".join(out)


def archive_root(ws) -> Path:
    return (ws or Path.cwd()) / ARCHIVE_DIR


def find_archive(ws, project: str) -> Path:
    root = archive_root(ws)
    if not root.is_dir():
        return None
    hits = [p for p in root.glob("*.md") if p.name not in SKIP_FILES]
    for p in hits:
        text = p.read_text(encoding="utf-8")
        m = re.search(r"^项目:\s*(.+)$", text, re.MULTILINE)
        if m and m.group(1).strip() == project.strip():
            return p
    for p in hits:                      # 退一步按文件名前缀找
        if p.stem.split("-诊断-")[0] == project.strip():
            return p
    return None


def extract_report(text: str) -> str:
    """取出 `## 报告` 到文件末尾，并把 demote 过的标题还原两级。"""
    m = re.search(r"^## 报告\s*\n(.*)\Z", text, re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    body = m.group(1).strip()
    return re.sub(r"^##(#{1,4})(?= )", r"\1", body, flags=re.MULTILINE)


def render(title: str, report_md: str, project: str) -> str:
    body = md_to_html(report_md)
    today = _dt.date.today().isoformat()
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">
<p class="meta">gt-venture · 创业诊断　｜　{html.escape(project)}　｜　导出于 {today}</p>
{body}
<div class="foot">
由 <strong>gt-venture · 创业诊断</strong> 生成。正文里每个判断都配了「什么能推翻它」——
<strong>你手上有它不知道的信息时，请推翻它。</strong>
</div>
</div>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="把档案里的报告导出成单文件 HTML")
    ap.add_argument("--workspace", type=Path, default=None)
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    ws = args.workspace.resolve() if args.workspace else None
    src = find_archive(ws, args.project)
    if not src:
        print(f"没找到「{args.project}」的档案。", file=sys.stderr)
        print(f"（查找位置：{archive_root(ws)}）", file=sys.stderr)
        return 1

    report = extract_report(src.read_text(encoding="utf-8"))
    if not report:
        print(f"档案里还没有报告：{src}", file=sys.stderr)
        print("先用 archive.py report 把报告存进去，再导出。", file=sys.stderr)
        return 2

    title = f"诊断：{args.project}"
    m = re.search(r"^#\s+(.+)$", report, re.MULTILINE)
    if m:
        title = m.group(1).strip()

    out = args.out or src.with_suffix(".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(title, report, args.project), encoding="utf-8")
    print(f"报告已导出：{out}")
    print("这是一个单文件 HTML，可以直接发给别人，断网也能打开。")
    print("⚠️  正文照样要发进对话——宿主不一定让用户拿得到生成的文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

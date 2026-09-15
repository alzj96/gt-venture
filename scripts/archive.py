#!/usr/bin/env python3
"""创业档案读写。

档案是这个技能唯一的记忆：用户下次回来时，它让对话从上次断掉的地方接着走，
而不是把六个问题重问一遍。格式说明见 references/archive-format.md。

宿主不让跑脚本时可以照那份文档手工操作，但注意两处脚本行为文档没法替你做：
报告和闸门内容写入前会被 demote() 降两级标题；断点按正文里第一个「未答」算，
不是按 frontmatter 的「已答」。手工写时照着做即可。

只用标准库，Python 3.8+。档案写在当前工作空间内的 创业档案/ 下，不碰家目录——
多数宿主的沙箱以工作空间为边界，写到外面会反复触发越权确认。

用法：
    python3 archive.py find
    python3 archive.py show    --project 名字
    python3 archive.py save    --project 名字 --step 1 --answer "用户原话"
    python3 archive.py report  --project 名字 --file /tmp/report.md
    python3 archive.py gate    --project 名字 --file /tmp/gate.md

所有命令都接受 --workspace <用户工作空间>，不传则用当前目录。
"""

import argparse
import datetime as _dt
import hashlib
import re
import sys
from pathlib import Path

ARCHIVE_DIR = "创业档案"
TOTAL_STEPS = 6
STEP_TITLES = {
    1: "需求是真的吗",
    2: "现状替代品是什么",
    3: "具体到绝望",
    4: "最窄的楔子",
    5: "你观察到了什么",
    6: "你凭什么",
}
UNANSWERED = "未答"
# 「没答」有三种，性质完全不同，混成一种就会把边界当成逃避。
MODES = {"副业": "副业体检：四问，卖的是自己的时间和手艺，不走六问",
         "筛查": "轻量筛查：只查三项硬伤，未发现不等于可以做",
         "诊断": "全面诊断：六问 + 失败模式 + 闸门 + 报告"}
# 副业走四问，进度分母和六问不同
MODE_STEPS = {"副业": 4, "筛查": 6, "诊断": 6}
DECLINE_KINDS = {
    "说不出": "给了一个听起来像答案的非答案，他没意识到自己没答 → 追问",
    "没想好": "他知道自己没想清楚 → 这本身是最有价值的发现，记下来给动作",
    "不方便": "他明确不想说 → 这是边界，记「选择不答」不记内容，下次别再问",
}
_ILLEGAL = r'[/\\:*?"<>|\s]+'


def today() -> str:
    return _dt.date.today().isoformat()


_WORKSPACE = None


def archive_root() -> Path:
    """档案落在用户的工作空间，不是 skill 的安装目录。

    这个区分是真踩过的坑：SKILL.md 里写 `python3 scripts/archive.py`，
    读的人容易以为要先 cd 到 skill 目录，结果档案被悄悄写进安装目录，
    下次 find 返回空、六个问题重问一遍——正好是这个技能要防的那个失败。
    所以调用方可以用 --workspace 显式指定，不靠 cwd 碰运气。"""
    return (_WORKSPACE or Path.cwd()) / ARCHIVE_DIR


def slugify(project: str) -> str:
    """项目名转成安全的文件名片段，冲突时带一段项目名的哈希。

    slug 是有损的：「宠物 寄养」和「宠物-寄养」都会变成「宠物-寄养」。
    查找键改精确之后，存储键仍然有损，于是两个不同项目落到同一个文件路径，
    write() 整份覆盖，退出码 0 —— 修了查找没修存储，bug 在一个字符之外活着。
    （那条声称修掉它的测试恰好挑了两个 slug 不同的名字，所以从没碰过这条路径。）

    所以只要 slug 和原名不是一一对应，就把原名的哈希缀上去。
    """
    cleaned = re.sub(_ILLEGAL, "-", project.strip()).strip("-") or "未命名"
    if cleaned != project.strip():
        digest = hashlib.sha1(project.strip().encode("utf-8")).hexdigest()[:6]
        cleaned = f"{cleaned}-{digest}"
    return cleaned


def parse(path: Path) -> dict:
    """读一份档案。frontmatter 缺字段时给默认值，不抛异常——
    档案可能是用户手工写的，宽容一点比严格更有用。"""
    text = path.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = parts[2]
    try:
        answered = int(meta.get("已答", 0))
    except ValueError:
        answered = 0
    # frontmatter 的「已答」是写入顺序的最大值，跳问时会说谎。真相在正文里。
    done, next_q = progress(body)
    return {
        "path": path,
        # find_all 用它做正面判定：手工写的档案文件名可能不规范，但有这个键。
        "has_project_key": "项目" in meta,
        "project": meta.get("项目", path.stem),
        "created": meta.get("创建", ""),
        "updated": meta.get("更新", ""),
        "status": meta.get("状态", "进行中"),
        "mode": meta.get("模式", "诊断"),
        "answered": done,
        "next_q": next_q,
        "body": body,
    }


# 同目录下的邻居文件不是诊断档案。漏掉会让 find 报出
# 「资源 进行中 已答 0/6」这种假项目，下次开场就去续问一个不存在的项目 ——
# 这正是 SKILL.md 自称最该避免的那种静默失败。
#
# 这里曾经是一张黑名单 `{"资源.md", "模式.md"}`，**它漂移过两次**：
# 后来加的 `敏感问题.md` 和 `强项.md` 都没人记得回来补一行。
# 黑名单和写文件的那几个脚本分居两处，必然漂移，所以改成正面判定。
#
# 认两样，满足一样就是档案：
#   · 文件名是 `<项目>-诊断-YYYYMMDD.md` —— 脚本写出来的都长这样
#   · frontmatter 里有「项目」字段 —— 手工写的档案，文件名可能不规范
# 邻居文件两样都不满足：模式/强项/敏感问题没有 frontmatter，
# 资源.md 有 frontmatter 但键是「对接.<项目>」，没有「项目」。
_ARCHIVE_NAME = re.compile(r"-诊断-\d{8}$")


def is_archive(doc: dict) -> bool:
    return bool(_ARCHIVE_NAME.search(doc["path"].stem)) or doc["has_project_key"]


def find_all() -> list:
    root = archive_root()
    if not root.is_dir():
        return []
    return sorted(
        (d for d in (parse(p) for p in root.glob("*.md")) if is_archive(d)),
        key=lambda d: d["updated"],
        reverse=True,
    )


def similar(project: str) -> list:
    """名字相近的档案，只用来提示，绝不用来自动选中。"""
    return [d for d in find_all()
            if d["project"] != project
            and (project in d["project"] or d["project"] in project)]


def match(project: str):
    """按项目名精确找档案。找不到就是找不到。

    曾经这里有个子串回退，理由是「用户很少把项目名打得一字不差」。
    后果是「宠物寄养」和「宠物寄养小程序」被当成同一个项目，后者的第1问
    静默覆盖前者的答案，退出码还是 0。项目名是跨会话找回档案的唯一键，
    唯一键不能是模糊键 —— resources.py 的同意档位也是这个道理。
    名字像的档案由 similar() 报给用户，让人来认，不由脚本猜。
    """
    for d in find_all():
        if d["project"] == project:
            return d
    return None


def blank(project: str) -> str:
    sections = "\n\n".join(
        f"## 第{i}问 {STEP_TITLES[i]}\n\n> {UNANSWERED}" for i in range(1, TOTAL_STEPS + 1)
    )
    return (
        f"---\n项目: {project}\n创建: {today()}\n更新: {today()}\n"
        f"状态: 进行中\n已答: 0\n---\n\n{sections}\n\n## 闸门检查\n\n## 报告\n"
    )


def write(doc: dict, body: str, *, answered: int, status: str) -> None:
    created = doc["created"] or today()
    header = (
        f"---\n项目: {doc['project']}\n创建: {created}\n更新: {today()}\n"
        f"模式: {doc.get('mode', '诊断')}\n状态: {status}\n已答: {answered}\n---\n"
    )
    doc["path"].parent.mkdir(parents=True, exist_ok=True)
    # 纵深防御：哈希后撞名的概率极低，但覆盖别人整份档案的代价是不可逆的，
    # 所以落盘前再确认一次这个文件不属于另一个项目。
    if doc["path"].is_file():
        owner = parse(doc["path"])["project"]
        if owner != doc["project"]:
            raise SystemExit(
                f"拒绝写入：{doc['path']} 属于项目「{owner}」，不是「{doc['project']}」。\n"
                "两个项目名撞到了同一个文件路径。换一个项目名重试。")
    doc["path"].write_text(header + "\n" + body.strip() + "\n", encoding="utf-8")


def progress(body: str) -> tuple:
    """(已答数, 下一个未答的问号)。唯一真相来源，三处命令共用。

    曾经 cmd_find 看正文、cmd_save 和 frontmatter 看 max(step)，
    同一份档案两个答案：save 说「六问已答完」，find 说「下一问是第 1 问」。
    半修比不修更危险 —— 读到哪个就信哪个。
    """
    done, nxt = 0, TOTAL_STEPS + 1
    for i in range(1, TOTAL_STEPS + 1):
        m = re.search(rf"^## 第{i}问[^\n]*\n(.*?)(?=^## |\Z)", body,
                      re.MULTILINE | re.DOTALL)
        answered = bool(m and m.group(1).strip() and UNANSWERED not in m.group(1))
        if answered:
            done += 1
        elif nxt > TOTAL_STEPS:
            nxt = i
    return done, nxt


def demote(text: str) -> str:
    """把一段 markdown 的标题整体压到 ## 之下。

    档案的骨架是「六问 / 闸门检查 / 报告」三段 ##。报告和闸门内容自己也是
    markdown，原样灌进去，它们的 # 和 ## 会跟 ## 第N问 变成同级，结构塌掉。
    降两级而不是一级：只降一级的话报告的 H1 仍然停在 ##，还是同级。
    """
    return re.sub(r"^(#{1,4})(?= )", r"##\1", text, flags=re.MULTILINE)


def load_or_create(project: str) -> dict:
    doc = match(project)
    if doc:
        return doc
    near = similar(project)
    if near:
        print(f"⚠️  没有叫「{project}」的档案，将新建。但有名字相近的：", file=sys.stderr)
        for d in near:
            print(f"     · {d['project']}（已答 {d['answered']}/{TOTAL_STEPS}）", file=sys.stderr)
        print("   如果用户指的是上面某一个，用那个确切的名字重跑，别新建。", file=sys.stderr)
    path = archive_root() / f"{slugify(project)}-诊断-{today().replace('-', '')}.md"
    return {
        "path": path,
        "project": project,
        "created": today(),
        "updated": today(),
        "status": "进行中",
        "mode": "诊断",
        "answered": 0,
        "body": blank(project).split("---", 2)[2],
    }


def cmd_find(_args) -> int:
    docs = find_all()
    if not docs:
        root = archive_root()
        if root.is_dir():
            print(f"{root} 存在，但里面没有诊断档案（只有资源/模式这类邻居文件）。")
        else:
            print("没有找到 创业档案/，这是一个新项目。")
        print(f"（查找位置：{root}）")
        print("如果之前在别的目录诊断过，请用户提供那个目录的路径。")
        return 0
    print(f"在 {archive_root()} 找到 {len(docs)} 份档案：\n")
    for d in docs:
        if d["mode"] == "副业":
            state = "副业体检（四问）"
        elif d["mode"] == "筛查":
            state = ("轻量筛查已做（只查了三项硬伤，没做全面诊断）"
                     if d["answered"] else "轻量筛查进行中")
        elif d["status"] == "已完成":
            state = "已完成（报告已出）"
        else:
            nq, done = d["next_q"], d["answered"]
            state = (f"进行中，已答 {done}/{TOTAL_STEPS}，下一问是第 {nq} 问"
                     if nq <= TOTAL_STEPS else f"六问已答完（{done}/{TOTAL_STEPS}），未出报告")
        print(f"· {d['project']}　[{state}]　更新于 {d['updated'] or '未知'}")
        print(f"  {d['path']}")
    return 0


def cmd_show(args) -> int:
    doc = match(args.project)
    if not doc:
        print(f"没有找到项目「{args.project}」的档案。", file=sys.stderr)
        return 1
    print(doc["path"].read_text(encoding="utf-8"))
    return 0


def cmd_save(args) -> int:
    step = args.step
    if not 1 <= step <= TOTAL_STEPS:
        print(f"step 必须在 1..{TOTAL_STEPS} 之间，收到 {step}", file=sys.stderr)
        return 1

    doc = load_or_create(args.project)
    if args.declined:
        if args.declined not in DECLINE_KINDS:
            print(f"--declined 只能是：{' / '.join(DECLINE_KINDS)}", file=sys.stderr)
            for k, v in DECLINE_KINDS.items():
                print(f"  {k} —— {v}", file=sys.stderr)
            return 1
        # 「不方便」只记状态不记内容 —— 用户说的理由本身可能就是他不想留下的东西
        note = "" if args.declined == "不方便" else args.answer.strip()
        args.answer = f"{UNANSWERED}（{args.declined}）" + (f" {note}" if note else "")
    answer = args.answer.strip() or UNANSWERED
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in answer.splitlines())

    heading = f"## 第{step}问 {STEP_TITLES[step]}"
    pattern = re.compile(
        rf"^{re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
    )

    def _build(m):
        prior = m.group(1).strip()
        # 追问会产生同一问的第二个答案。覆盖等于把追问逼出来的东西丢掉，
        # 而追问拿到的往往比原答案值钱（实测：「5人各出50块」被追问后
        # 才暴露那是奖金池不是付费）。所以 --append 把两段都留着。
        if args.append and prior and UNANSWERED not in prior:
            return f"{heading}\n\n{prior}\n\n> —— 追问后 ——\n\n{quoted}\n\n"
        return f"{heading}\n\n{quoted}\n\n"

    body, n = pattern.subn(_build, doc["body"], count=1)
    replacement = f"{heading}\n\n{quoted}\n\n"
    if n == 0:  # 手工写的档案可能没有这一节，补在末尾而不是报错
        body = doc["body"].rstrip() + "\n\n" + replacement

    done, nxt = progress(body)
    write(doc, body, answered=done, status=doc["status"])
    appended = args.append and n > 0 and "—— 追问后 ——" in body
    print(f"{'已追加（原答案保留）' if appended else '已存'}：第{step}问 → {doc['path']}")
    if nxt <= TOTAL_STEPS:
        print(f"进度 {done}/{TOTAL_STEPS}，下一问是第 {nxt} 问")
    else:
        print(f"六问已答完（{done}/{TOTAL_STEPS}），可以做闸门检查和出报告了")
    return 0


def cmd_report(args) -> int:
    src = Path(args.file)
    if not src.is_file():
        print(f"报告文件不存在：{src}", file=sys.stderr)
        return 1
    doc = load_or_create(args.project)
    report = demote(src.read_text(encoding="utf-8").strip())

    # 吃到文件末尾，不是吃到下一个 ## —— 报告本身通篇都是 ## 标题，
    # 用 (?=^## ) 收尾会让新报告插在旧报告上面，两份矛盾的报告并存。
    pattern = re.compile(r"^## 报告\s*\n.*\Z", re.MULTILINE | re.DOTALL)
    replacement = f"## 报告\n\n{report}\n"
    body, n = pattern.subn(lambda _m: replacement, doc["body"], count=1)
    if n == 0:
        body = doc["body"].rstrip() + "\n\n" + replacement

    done, _ = progress(body)
    write(doc, body, answered=done, status="已完成")
    print(f"报告已存入：{doc['path']}")
    if done < TOTAL_STEPS:
        print(f"注意：只答了 {done}/{TOTAL_STEPS} 问，报告里必须标明哪几问未答")
    return 0


def cmd_mode(args) -> int:
    """标记模式。

    不标的话，一次完成的轻量筛查在档案里长得像一次半途而废的全面诊断
    （都是「已答 1/6」），下次开场会说「上次聊到第2问，接着来？」——
    而用户根本没打算走全面诊断。
    """
    doc = load_or_create(args.project)
    doc["mode"] = args.set
    write(doc, doc["body"], answered=doc["answered"], status=doc["status"])
    print(f"已标记：{args.project} → {args.set}（{MODES[args.set]}）")
    return 0


def cmd_gate(args) -> int:
    """写入闸门检查结果。没有这个命令，档案模板里的 ## 闸门检查 永远是空的。"""
    src = Path(args.file)
    if not src.is_file():
        print(f"闸门检查文件不存在：{src}", file=sys.stderr)
        return 1
    doc = load_or_create(args.project)
    gate = demote(src.read_text(encoding="utf-8").strip())

    # 降级必须先做：内容里的 ## 不降下去，下面这个 (?=^## ) 会停在
    # 闸门内容自己的第一个二级标题上，新旧两份并存 —— cmd_report 踩过同样的坑。
    pattern = re.compile(r"^## 闸门检查\s*\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    replacement = f"## 闸门检查\n\n{gate}\n\n"
    body, n = pattern.subn(lambda _m: replacement, doc["body"], count=1)
    if n == 0:
        body = doc["body"].rstrip() + "\n\n" + replacement

    write(doc, body, answered=doc["answered"], status=doc["status"])
    print(f"闸门检查已存入：{doc['path']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="创业档案读写")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("find", help="列出当前工作空间的所有档案")

    p_show = sub.add_parser("show", help="打印某个项目的档案全文")
    p_show.add_argument("--project", required=True)

    p_save = sub.add_parser("save", help="保存某一问的回答")
    p_save.add_argument("--project", required=True)
    p_save.add_argument("--step", type=int, required=True)
    p_save.add_argument("--answer", required=True, help="用户原话，不要填概括")
    p_save.add_argument("--append", action="store_true",
                        help="追问后的补充答案：保留原答案，追加在后面，不覆盖")
    p_save.add_argument("--declined", choices=list(DECLINE_KINDS),
                        help="用户没答，标明是哪一种：说不出 / 没想好 / 不方便。"
                             "「不方便」只记状态不记内容")

    p_report = sub.add_parser("report", help="写入诊断报告并标记完成")
    p_report.add_argument("--project", required=True)
    p_report.add_argument("--file", required=True, help="报告 markdown 文件路径")

    p_m = sub.add_parser("mode", help="标记这次是轻量筛查还是全面诊断")
    p_m.add_argument("--project", required=True)
    p_m.add_argument("--set", required=True, choices=list(MODES))

    p_gate = sub.add_parser("gate", help="写入闸门检查结果")
    p_gate.add_argument("--project", required=True)
    p_gate.add_argument("--file", required=True, help="闸门检查 markdown 文件路径")

    parser.add_argument("--workspace", type=Path, default=None,
                        help="用户工作空间路径（档案写在这里）。默认当前目录。")

    args = parser.parse_args()
    global _WORKSPACE
    _WORKSPACE = args.workspace.resolve() if args.workspace else None
    return {
        "find": cmd_find,
        "show": cmd_show,
        "save": cmd_save,
        "report": cmd_report,
        "gate": cmd_gate,
        "mode": cmd_mode,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

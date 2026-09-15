#!/usr/bin/env python3
"""资源档案：记录同意档位、存「我有/我缺」、做本地匹配。

诊断问到第 6 问时，用户已经把自己的底牌说出来了。这个脚本把它结构化存下来，
让「谁有什么、谁缺什么」变成可检索的东西。规则见 references/resource-matching.md。

两件事这个脚本会替你拦住：

1. 没有记录过同意档位就写资源 —— 直接拒绝。用户答第 6 问时想的是「我在回答诊断
   问题」，不是「我在提交一份可被检索的资料」，这两件事的知情程度不一样。
2. anon 档下写入疑似联系方式 —— 直接拒绝。脱敏不能靠调用方自觉。

只用标准库，Python 3.8+。档案写在当前工作空间的 创业档案/ 下。

用法：
    python3 resources.py consent --project 名字 --level full|anon|off
    python3 resources.py add     --project 名字 --side have|need --type 渠道 --detail "描述"
    python3 resources.py show
    python3 resources.py match

所有命令都接受 --workspace <用户工作空间>，不传则用当前目录。
"""

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

ARCHIVE_DIR = "创业档案"
RESOURCE_FILE = "资源.md"
LEVELS = {"full": "完整存（含联系方式，可被对接）",
          "anon": "脱敏存（只存行业与资源类型）",
          "off": "不存"}
# 统一词表。自创类型会让匹配失效，所以这里硬拦。
TYPES = ["渠道", "客户", "内容", "技术", "资金", "产能", "资质", "经验"]

# anon 档的联系方式嗅探。
#
# 这条规则改过两次，两次都改错了方向，值得把教训写在这里：
#   v1 用 [\w-]{5,}，而 Python3 的 \w 吃中文 —— 「一个微信群里有三百多个宝妈」
#      被当成账号拒收，而那正是本技能推荐的脱敏写法。最谨慎的用户最写不进东西。
#   v2 收紧成只认「微信号:」一种连接词 —— 于是「微信号是 zhangsan_2020」
#      「我的微信叫 xiaoli8888」「抖音号 lisi2024」全部漏网。
#
# 正确的偏向是过度拦截：anon 档误报的代价是让人改写一句话，
# 漏报的代价是账号泄露。所以平台词表放宽、连接词放宽、token 只要像账号就拦。
_PLATFORM = (r"微信|weixin|wechat|wx|vx|v信|QQ|qq|扣扣|抖音|douyin|快手|"
             r"小红书|xhs|微博|支付宝|alipay|钉钉|飞书|telegram|whatsapp|line|skype")
# 平台词和账号之间可能夹着「号/是/叫/加我/搜/ID/：」等任意零散连接字
# 连接词可以连着出现好几个：「微信号是」=号+是，「我的微信叫」=的+叫。
# 只允许一个的话 `微信号是 zhangsan_2020` 就漏了 —— 实测漏过。
_JOIN = r"(?:[^\S\n]{0,3}(?:号|名|是|叫|为|加|搜|找|的|ID|id|账号|昵称|[:：])){0,4}[^\S\n]{0,3}"
# 像账号的 token：4 位以上的拉丁字母数字下划线点号短横
_TOKEN = r"[A-Za-z0-9][A-Za-z0-9_.-]{3,}"

CONTACT_PATTERNS = [
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "手机号"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "邮箱"),
    (re.compile(rf"(?:{_PLATFORM}){_JOIN}{_TOKEN}", re.I), "社交账号"),
    # 联系意图词后面跟着像账号的 token，即使没点名平台
    (re.compile(rf"(?:联系方式|联系我|私信我|加我|我的账号){_JOIN}{_TOKEN}", re.I),
     "联系方式"),
    (re.compile(r"(?<![\d.])\d{8,}(?![\d.])"), "疑似长串数字（账号或电话）"),
]


# resource-matching.md 的脱敏表逐条点名要挡住：真人姓名、具体群名/小区名/
# 公司全名、学校名。而 CONTACT_PATTERNS 只认数字和账号 —— 于是
# 「豆豆妈妈和 Vivian，都在实验二小三年级五班家长群」原样写进了 anon 档。
#
# 但第一版修得过头了：把「同一个小学班级的家长群」「一位一线数学老师」
# 也拦了。那比漏网更糟 —— anon 档如果每句话都拒收，用户会去选 full 或 off，
# 隐私反而更差。所以要分清专名和泛称。
#
# 判据：泛称前面有量词或指示词（一个/一位/两位/我们/同一个/某…），
# 专名前面是另一个名词或者句首。用代码做，不硬塞进正则的定宽后顾。

_ENTITY_TAIL = (r"小学|中学|大学|幼儿园|医院|诊所|银行|公司|集团|事务所|"
                r"工作室|门店|分店|小区|花园|苑|府|湾|群|班")
_TITLE_TAIL = r"妈妈|爸爸|老师|医生|律师|总|姐|哥|叔|阿姨"
# 前面出现这些，说明是泛称不是专名
_GENERIC_LEAD = ("一个", "一位", "两位", "三位", "几位", "一些", "一群", "一批",
                 "我们", "同一个", "同一", "某", "这个", "那个", "这", "那",
                 "每个", "各", "的", "人的", "线")
# 这些整词本身就是类别，不是专名
_GENERIC_WHOLE = ("业主群", "家长群", "微信群", "同学群", "宝妈群", "车友群",
                  "客户群", "用户群", "小区", "公司", "医院", "小学", "中学",
                  "大学", "班", "群")


def _is_generic(text: str, m) -> bool:
    """命中的是泛称还是专名。

    判据：把类别后缀剥掉，看剩下的部分。剩下空的、或者是量词指示词，
    那就是泛称（「一个业主群」「同一个小学」「一位一线数学老师」）；
    剩下具体的东西，那就是专名（「实验二小」「三年级五班家长群」「城西花园」）。
    """
    frag = m.group(0)
    # 前文是量词或指示词 → 泛称。不看 head 长度，「一位一线数学老师」的
    # head 是四个字，加长度条件就漏。
    before = text[max(0, m.start() - 4):m.start()].strip()
    if any(before.endswith(g) for g in _GENERIC_LEAD):
        return True

    tail = m.group(m.lastindex) if m.lastindex else ""
    head = frag[:-len(tail)] if tail and frag.endswith(tail) else frag
    for whole in sorted(_GENERIC_WHOLE, key=len, reverse=True):
        if frag.endswith(whole):
            head = frag[:-len(whole)]
            break
    head = head.strip()
    while head:
        hit = [g for g in _GENERIC_LEAD if head.endswith(g)]
        if not hit:
            break
        head = head[:-max(len(g) for g in hit)]
    return not head


_NAMED_ENTITY = [
    # 称谓人名：X妈妈 / X老师 / 老X。不加尾部否定后顾 —— 加了就匹配不到
    # 句子中间的「老张在城西花园」，而那恰恰是要拦的。
    (re.compile(rf"[一-龥]{{1,4}}({_TITLE_TAIL})"), "疑似称谓人名", "cn"),
    (re.compile(r"(?<![一-龥])老([一-龥])(?=[^一-龥]|在|的|是|说|有)"),
     "疑似称谓人名", "cn"),
    (re.compile(rf"[一-龥]{{2,8}}({_ENTITY_TAIL})"),
     "具名机构、小区或群组", "cn"),
    # 拉丁人名：孤立的首字母大写词。中文语境里冒出一个 Michael 基本就是人名，
    # 所以不走泛称判定，只走平台/技术词白名单。
    (re.compile(r"(?<![A-Za-z])(?!WeChat|Excel|Word|Python|Java|SaaS|App|APP|AI|"
                r"CEO|CTO|COO|IT|ID|OK|MVP|SKU|GMV|KOL|ROI|PPT|PDF|CRM|ERP|SEO)"
                r"([A-Z][a-z]{2,11})(?![A-Za-z])"), "疑似拉丁人名", "latin"),
]


def sniff_named(text: str) -> list:
    hits = []
    for pat, name, kind in _NAMED_ENTITY:
        for m in pat.finditer(text):
            if kind == "latin" or not _is_generic(text, m):
                if name not in hits:
                    hits.append(name)
                break
    return hits


def today() -> str:
    return _dt.date.today().isoformat()


_WORKSPACE = None


def resource_path() -> Path:
    """和 archive.py 同一个约定：写用户的工作空间，不写 skill 安装目录。"""
    return (_WORKSPACE or Path.cwd()) / ARCHIVE_DIR / RESOURCE_FILE


def read_doc() -> tuple:
    """返回 (meta, body)。文件不存在时给空壳，让调用方不必处理两种情况。"""
    p = resource_path()
    if not p.is_file():
        return {}, ""
    text = p.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = parts[2]
    return meta, body.strip()


def write_doc(meta: dict, body: str) -> None:
    meta["更新"] = today()
    head = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n"
    p = resource_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(head + "\n" + body.strip() + "\n", encoding="utf-8")


def consent_of(meta: dict, project: str) -> str:
    """同意档位严格按项目取，没有就是没有。

    绝不回落到「别的项目选过的档位」。一个用户可能愿意公开 A 项目的渠道，
    同时完全不想让人知道他在做 B。继承会让一个从没被问过的项目直接写入，
    那正是同意门存在要防的事。"""
    return meta.get(f"对接.{project}", "")


def sniff_contact(text: str) -> list:
    return [name for pat, name in CONTACT_PATTERNS if pat.search(text)]


def cmd_consent(args) -> int:
    if args.level not in LEVELS:
        print(f"level 必须是 {'/'.join(LEVELS)}，收到 {args.level}", file=sys.stderr)
        return 1
    if args.level == "off":
        # 用户在同意门上读到的原话是「不留任何痕迹」。写一行
        # 「对接.秘密项目: off」就破了这个承诺 —— 项目名本身往往
        # 正是他不想留痕的那个东西。所以 off 什么都不写。
        # 代价：下次会重新问一次。这个代价该由产品承担，不该由用户承担。
        existing, body = read_doc()
        key = f"对接.{args.project}"
        if key in existing:
            existing.pop(key)
            write_doc(existing, body)
            print(f"已清除 {args.project} 的对接记录。")
        print(f"已记录：{args.project} → off（不存）")
        print("不写任何文件——用户选的是「不留任何痕迹」。")
        print("代价是下次会重新问一次同意门，这个代价该产品承担。")
        return 0
    meta, body = read_doc()
    meta[f"对接.{args.project}"] = args.level
    write_doc(meta, body)
    print(f"已记录：{args.project} → {args.level}（{LEVELS[args.level]}）")
    return 0


def cmd_add(args) -> int:
    meta, body = read_doc()
    level = consent_of(meta, args.project)

    if not level:
        print("拒绝写入：这个项目还没有记录同意档位。", file=sys.stderr)
        print("先按 references/resource-matching.md 的同意门问一次用户，", file=sys.stderr)
        print("再运行 resources.py consent 记录选择。", file=sys.stderr)
        return 2
    if level == "off":
        print(f"拒绝写入：{args.project} 的档位是 off，用户明确说了不存。", file=sys.stderr)
        return 2
    if args.type not in TYPES:
        print(f"type 必须是这八个之一：{' '.join(TYPES)}", file=sys.stderr)
        print("自创类型会让匹配失效，请归到最近的一个。", file=sys.stderr)
        return 1

    hits = sniff_contact(args.detail)
    named = sniff_named(args.detail)
    if level == "anon" and hits:
        # 联系方式是可靠可测的，直接拒，没有商量
        print(f"拒绝写入：anon 档不能含 {'、'.join(hits)}。", file=sys.stderr)
        print(f"原文：{args.detail}", file=sys.stderr)
        print("删掉联系方式再存。", file=sys.stderr)
        return 2
    if level == "anon" and named and not args.deidentified:
        # 中文命名实体没法用正则可靠判定 —— 收紧会把「同一个小学班级的家长群」
        # 也拦掉，而 anon 档一旦每句话都拒收，用户就会去选 full 或 off，
        # 隐私反而更差。所以这里不做永久拒收，而是要求一次显式确认：
        # 把「默认放行」变成「刻意动作」。这正是同意门本来的逻辑。
        print(f"暂停：这条可能含 {'、'.join(named)}。", file=sys.stderr)
        print(f"原文：{args.detail}", file=sys.stderr)
        print("", file=sys.stderr)
        print("anon 档承诺过不存人名、群名、学校名、公司名。请先改写：", file=sys.stderr)
        print("  「豆豆妈妈和 Vivian，都在实验二小三年级五班家长群」", file=sys.stderr)
        print("  →「两位家长，同一个小学班级的家长群里」", file=sys.stderr)
        print("  保留数量和关系，去掉谁是谁。", file=sys.stderr)
        print("", file=sys.stderr)
        print("确认已经脱敏（或判定这是误报）之后，加 --deidentified 重跑。", file=sys.stderr)
        return 2
    if level == "full" and named:
        # 第三方脱敏在 full 档也没有例外 —— 用户能决定暴露自己的联系方式，
        # 不能替「豆豆妈妈」决定。这条脚本拦不死（分不清是不是用户本人），
        # 但至少每次都要出声，而不是只在数字命中时才出声。
        print(f"⚠️  这条含 {'、'.join(named)}。", file=sys.stderr)
        print("   第三方的身份信息在 full 档也不该存 —— 用户能决定暴露自己的，", file=sys.stderr)
        print("   不能替别人决定。是用户自己的就继续，是别人的请改写。", file=sys.stderr)
    if level == "full" and hits:
        # full 档允许存用户自己的联系方式，所以不能拦。但脚本分不清这个号码
        # 是用户的还是「豆豆妈妈」的 —— 用户能决定暴露自己的，不能替第三方决定。
        # 拦会误伤合法写入，不吭声又会让第三方信息静默流出，所以出声不拦。
        print(f"⚠️  这条含 {'、'.join(hits)}。", file=sys.stderr)
        print("   如果这是第三方（用户提到的某个人）的，必须删掉——", file=sys.stderr)
        print("   用户可以决定暴露自己的联系方式，不能替别人决定。", file=sys.stderr)
        print("   是用户本人的就继续。", file=sys.stderr)

    side_label = "我有" if args.side == "have" else "我缺"
    proj_head = f"## {args.project}"
    side_head = f"### {side_label}"
    entry = f"- 类型: {args.type}　｜　{args.detail.strip()}"

    lines = body.splitlines()
    # 找到项目段；没有就整段新建，避免把条目挂到别的项目下面
    try:
        pi = lines.index(proj_head)
    except ValueError:
        block = f"\n{proj_head}\n\n{side_head}\n{entry}\n"
        write_doc(meta, (body + "\n" + block).strip())
        print(f"已存（新建项目段）：{side_label} / {args.type}")
        return 0

    # 项目段的范围到下一个 ## 为止
    end = next((i for i in range(pi + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    try:
        si = lines.index(side_head, pi, end)
        insert = next((i for i in range(si + 1, end) if lines[i].startswith("#")), end)
        while insert > si + 1 and not lines[insert - 1].strip():
            insert -= 1
        lines.insert(insert, entry)
    except ValueError:
        lines.insert(end, f"\n{side_head}\n{entry}")

    write_doc(meta, "\n".join(lines))
    print(f"已存：{args.project} / {side_label} / {args.type}")
    return 0


def parse_entries(body: str) -> dict:
    """解析成 {项目: {'have': [(类型, 描述)], 'need': [...]}}。"""
    out, proj, side = {}, None, None
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("## "):
            proj = s[3:].strip()
            out[proj] = {"have": [], "need": []}
            side = None
        elif s.startswith("### "):
            label = s[4:].strip()
            side = "have" if "有" in label else ("need" if "缺" in label else None)
        elif s.startswith("- 类型:") and proj and side:
            m = re.match(r"- 类型:\s*(\S+)\s*[｜|]\s*(.+)", s)
            if m:
                out[proj][side].append((m.group(1), m.group(2).strip()))
    return out


def cmd_show(_args) -> int:
    p = resource_path()
    if not p.is_file():
        print("还没有资源档案。")
        print("走完诊断、问过同意门之后才会建立，见 references/resource-matching.md。")
        return 0
    print(p.read_text(encoding="utf-8"))
    return 0


def cmd_match(_args) -> int:
    meta, body = read_doc()
    if not body:
        print("还没有资源档案，没什么可匹配的。")
        return 0

    data = parse_entries(body)
    projects = [p for p in data if consent_of(meta, p) != "off"]
    if len(projects) < 2:
        print(f"当前只有 {len(projects)} 个项目的资源记录。")
        print("本地匹配要至少两个项目才有意义。")
        print("真正的价值要等有服务端、池子里有别人的档案之后——现在先把数据攒着。")
        return 0

    hits = []
    for a in projects:
        for ntype, ndetail in data[a]["need"]:
            for b in projects:
                if a == b:
                    continue
                for htype, hdetail in data[b]["have"]:
                    if htype == ntype:
                        hits.append((a, ndetail, b, hdetail, htype))

    if not hits:
        print(f"{len(projects)} 个项目，没有找到类型对得上的缺口和资源。")
        return 0

    print(f"在 {len(projects)} 个项目之间找到 {len(hits)} 处可能的对接：\n")
    for a, nd, b, hd, t in hits:
        print(f"· 「{a}」缺 {t}：{nd}")
        print(f"  「{b}」有 {t}：{hd}\n")
    print("这是按类型做的粗匹配，对不对得上要你自己判断。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="资源档案与对接")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_c = sub.add_parser("consent", help="记录同意档位（写资源前必须先做）")
    p_c.add_argument("--project", required=True)
    p_c.add_argument("--level", required=True, choices=list(LEVELS))

    p_a = sub.add_parser("add", help="添加一条「我有」或「我缺」")
    p_a.add_argument("--project", required=True)
    p_a.add_argument("--side", required=True, choices=["have", "need"])
    p_a.add_argument("--type", required=True, help=" / ".join(TYPES))
    p_a.add_argument("--detail", required=True)
    p_a.add_argument("--deidentified", action="store_true",
                     help="anon 档：确认这条已经脱敏（或身份信息告警是误报）")

    sub.add_parser("show", help="打印资源档案")
    sub.add_parser("match", help="在已有项目之间做本地匹配")

    ap.add_argument("--workspace", type=Path, default=None,
                    help="用户工作空间路径（档案写在这里）。默认当前目录。")

    args = ap.parse_args()
    global _WORKSPACE
    _WORKSPACE = args.workspace.resolve() if args.workspace else None
    return {"consent": cmd_consent, "add": cmd_add,
            "show": cmd_show, "match": cmd_match}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

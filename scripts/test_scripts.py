#!/usr/bin/env python3
"""四个脚本的回归测试。

每一条用例都对应一个真实发生过的 bug —— 全部是实测或对抗评审挖出来的，
不是想象的边界情况。用例名后面标着当初的严重度。

这个文件存在的理由：这个技能对用户说「没有证据，不给判断」，
那它自己的一致性断言也得有证据。没有测试的时候，我在 archive.py 的
docstring 里写过「文档和实现完全一致」—— 那句话当时已经不成立了。

跑法（不依赖 pytest，标准库 unittest 即可）：
    python3 scripts/test_scripts.py
    python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ARCHIVE = SCRIPTS / "archive.py"
RESOURCES = SCRIPTS / "resources.py"
PATTERN = SCRIPTS / "pattern.py"
CHECK = SCRIPTS / "check_rules.py"


def run(script: Path, *args, ws: Path = None):
    cmd = [sys.executable, str(script)]
    if ws is not None:
        cmd += ["--workspace", str(ws)]
    cmd += [str(a) for a in args]
    return subprocess.run(cmd, capture_output=True, text=True)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def archive_text(self, project: str) -> str:
        hits = list((self.ws / "创业档案").glob(f"{project}*.md"))
        self.assertTrue(hits, f"没有找到 {project} 的档案")
        return hits[0].read_text(encoding="utf-8")


class TestArchive(Base):

    def test_no_neighbour_file_is_ever_listed_as_a_project(self):
        """[严重·静默失败] find 曾把邻居文件报成「敏感问题 进行中 已答 0/6」。

        这个坑踩过两次：排除名单原先只有 资源.md / 模式.md，后来加的
        敏感问题.md 和 强项.md 都没人记得回来补一行。名单和写文件的脚本
        分居两处，必然漂移。

        所以这条测试**不写文件名**——它把会产出邻居文件的命令全跑一遍，
        再断言 find 只看得见真项目。以后再加邻居文件，这条自动覆盖。
        """
        run(ARCHIVE, "save", "--project", "真项目", "--step", "1",
            "--answer", "真的", ws=self.ws)
        run(PATTERN, "log", "--kind", "跟风", "--note", "x", ws=self.ws)
        run(PATTERN, "signal", "--kind", "砍得动", "--note", "x", ws=self.ws)
        run(PATTERN, "sensitive", "--step", "6", ws=self.ws)
        run(RESOURCES, "consent", "--project", "真项目", "--level", "anon", ws=self.ws)
        run(RESOURCES, "add", "--project", "真项目", "--side", "have",
            "--type", "渠道", "--detail", "两位家长", ws=self.ws)

        produced = {p.name for p in (self.ws / "创业档案").glob("*.md")}
        self.assertGreater(len(produced), 2, f"邻居文件没生成出来，这条测试是空的：{produced}")

        out = run(ARCHIVE, "find", ws=self.ws).stdout
        self.assertIn("找到 1 份档案", out, f"除了真项目还列出了别的：\n{out}")
        for name in produced:
            if "-诊断-" in name:
                continue
            self.assertNotIn(name[:-3], out, f"邻居文件 {name} 被当成项目列出来了")

    def test_handwritten_archive_with_odd_filename_still_found(self):
        """正面判定不能把手工档案关在门外——那比多列一个假项目更糟。

        archive-format.md 明说脚本跑不了时可以手工读写。手工写的文件名
        可能不带 `-诊断-YYYYMMDD`，但按文档一定有 frontmatter 的「项目」键。
        """
        d = self.ws / "创业档案"
        d.mkdir(parents=True)
        (d / "随手起的名字.md").write_text(
            "---\n项目: 手工项目\n创建: 2026-09-01\n更新: 2026-09-01\n"
            "状态: 进行中\n已答: 1\n---\n\n## 第1问\n\n手工写的答案\n",
            encoding="utf-8")
        out = run(ARCHIVE, "find", ws=self.ws).stdout
        self.assertIn("手工项目", out, f"手工档案被漏掉了：\n{out}")

    def test_append_declined_does_not_roll_back_an_answered_question(self):
        """[严重·进度倒退] 追问里说「不方便」，把已答过的那一问退回未答。

        实测：第 5 问答完（5/6），再 `--append --declined 不方便` 之后
        变成 4/6、下一问倒退回第 5 问——诊断师会把答过的问题重问一遍，
        正是这个技能最想避免的事。

        根因：progress() 用「整段里有没有出现『未答』」判断，而 append
        把标记追加在真答案下面，两者同时存在。
        """
        for i in range(1, 6):
            run(ARCHIVE, "save", "--project", "回退", "--step", i,
                "--answer", f"第{i}问的答案", ws=self.ws)
        self.assertIn("已答 5/6", run(ARCHIVE, "find", ws=self.ws).stdout)

        run(ARCHIVE, "save", "--project", "回退", "--step", "5", "--append",
            "--declined", "不方便", "--answer", "这个不太好说", ws=self.ws)
        out = run(ARCHIVE, "find", ws=self.ws).stdout
        self.assertIn("已答 5/6", out, f"答过的一问被退回未答：\n{out}")

    def test_the_word_unanswered_inside_a_real_answer_is_not_a_marker(self):
        """[中] 用户原话或手工补的说明里带「未答」二字，整问被判成没答。

        实测里踩到过：脚本出错后手工改档案，说明文字里写了「未答」，
        `find` 当场把那一问算成空的。判据应该是「除掉标记还剩不剩内容」，
        不是「整段里有没有这两个字」。
        """
        run(ARCHIVE, "save", "--project", "字面", "--step", "1",
            "--answer", "上次那个问题我未答完，这次补上", ws=self.ws)
        self.assertIn("已答 1/6", run(ARCHIVE, "find", ws=self.ws).stdout)

    def test_a_genuinely_declined_question_still_counts_as_unanswered(self):
        """修完上面两条不能把真·未答也放过去——那会让报告漏标。"""
        run(ARCHIVE, "save", "--project", "真未答", "--step", "1",
            "--answer", "答了", ws=self.ws)
        run(ARCHIVE, "save", "--project", "真未答", "--step", "2",
            "--declined", "不方便", "--answer", "别问了", ws=self.ws)
        out = run(ARCHIVE, "find", ws=self.ws).stdout
        self.assertIn("已答 1/6", out)
        self.assertIn("下一问是第 2 问", out)

    def test_project_name_is_exact_key(self):
        """[严重] 子串回退曾让「宠物寄养小程序」静默覆盖「宠物寄养」的答案。"""
        run(ARCHIVE, "save", "--project", "宠物寄养", "--step", "1",
            "--answer", "A项目的答案", ws=self.ws)
        run(ARCHIVE, "save", "--project", "宠物寄养小程序", "--step", "1",
            "--answer", "B项目的答案", ws=self.ws)

        files = sorted((self.ws / "创业档案").glob("*.md"))
        self.assertEqual(len(files), 2, "名字相近的项目必须各自建档，不能合并")
        self.assertIn("A项目的答案", self.archive_text("宠物寄养-"))
        self.assertIn("B项目的答案", self.archive_text("宠物寄养小程序"))

    def test_slug_collision_does_not_overwrite(self):
        """[严重·静默丢数据] 查找键改精确了，存储键还是有损的。

        「宠物 寄养」和「宠物-寄养」slug 相同 → 同一个文件路径 → write()
        整份覆盖，退出码 0。而 test_project_name_is_exact_key 恰好挑了两个
        slug 不同的名字，所以绿灯通过，bug 在一个字符之外活着。
        这条专挑 slug 相同的。
        """
        run(ARCHIVE, "save", "--project", "宠物 寄养", "--step", "1",
            "--answer", "A项目：去年两个客户各付8000", ws=self.ws)
        run(ARCHIVE, "save", "--project", "宠物-寄养", "--step", "1",
            "--answer", "B项目：完全不同的东西", ws=self.ws)

        files = sorted((self.ws / "创业档案").glob("*.md"))
        self.assertEqual(len(files), 2, "slug 撞车导致整份档案被覆盖")
        allbody = "".join(f.read_text(encoding="utf-8") for f in files)
        self.assertIn("各付8000", allbody, "A 项目的答案丢了")
        self.assertIn("完全不同的东西", allbody)

    def test_screening_mode_distinguishable_from_abandoned_diagnosis(self):
        """[体验] 一次完成的轻量筛查和一次半途而废的诊断，档案里都是「已答 1/6」。

        不区分的话，下次开场会说「上次聊到第2问，接着来？」——
        而用户根本没打算走全面诊断。
        """
        run(ARCHIVE, "mode", "--project", "快看看", "--set", "筛查", ws=self.ws)
        run(ARCHIVE, "save", "--project", "快看看", "--step", "1", "--answer", "x", ws=self.ws)
        run(ARCHIVE, "save", "--project", "正经项目", "--step", "1", "--answer", "y", ws=self.ws)
        out = run(ARCHIVE, "find", ws=self.ws).stdout
        self.assertIn("轻量筛查已做", out)
        self.assertIn("下一问是第 2 问", out, "全面诊断那份仍应显示断点")
        screening_line = [l for l in out.splitlines() if "快看看" in l][0]
        self.assertNotIn("下一问", screening_line, "筛查不该被当成半途而废的诊断")

    def test_side_hustle_mode_distinguishable(self):
        """副业走四问不走六问，进度分母不同，不能显示成「已答 1/6」。"""
        run(ARCHIVE, "mode", "--project", "接单", "--set", "副业", ws=self.ws)
        run(ARCHIVE, "save", "--project", "接单", "--step", "1", "--answer", "x", ws=self.ws)
        out = run(ARCHIVE, "find", ws=self.ws).stdout
        self.assertIn("副业体检", out)
        self.assertNotIn("/6", out, "副业不该套六问的分母")

    def test_three_modes_all_accepted(self):
        for mode in ("副业", "筛查", "诊断"):
            r = run(ARCHIVE, "mode", "--project", f"P{mode}", "--set", mode, ws=self.ws)
            self.assertEqual(r.returncode, 0, f"{mode} 被拒了")

    def test_mode_defaults_to_diagnosis(self):
        """没标模式时默认全面诊断 —— 保守的那一边。"""
        run(ARCHIVE, "save", "--project", "T", "--step", "1", "--answer", "x", ws=self.ws)
        self.assertIn("模式: 诊断", self.archive_text("T"))

    def test_similar_name_warns_on_stderr(self):
        """新建时若有相近档案要出声，让人来认，不由脚本猜。"""
        run(ARCHIVE, "save", "--project", "宠物寄养", "--step", "1",
            "--answer", "x", ws=self.ws)
        r = run(ARCHIVE, "save", "--project", "宠物寄养小程序", "--step", "1",
                "--answer", "y", ws=self.ws)
        self.assertIn("名字相近", r.stderr)

    def test_append_keeps_original_answer(self):
        """[严重] 追问的答案曾直接覆盖原答案，落差证据丢失。"""
        run(ARCHIVE, "save", "--project", "T", "--step", "1",
            "--answer", "5个人每人出50块", ws=self.ws)
        r = run(ARCHIVE, "save", "--project", "T", "--step", "1", "--append",
                "--answer", "那是凑奖金池，我一分没拿", ws=self.ws)
        body = self.archive_text("T")
        self.assertIn("5个人每人出50块", body)
        self.assertIn("那是凑奖金池", body)
        self.assertIn("追问后", body)
        self.assertIn("已追加", r.stdout, "追加和普通保存的输出要能分辨")

    def test_breakpoint_is_first_unanswered_not_max_step(self):
        """[中] 只答第6问时，断点曾显示「下一问是第 7 问」，前五问再也不会被问。"""
        run(ARCHIVE, "save", "--project", "T", "--step", "6",
            "--answer", "只答了最后一问", ws=self.ws)
        r = run(ARCHIVE, "find", ws=self.ws)
        self.assertIn("下一问是第 1 问", r.stdout)
        self.assertNotIn("第 7 问", r.stdout)

    def test_progress_consistent_across_all_commands(self):
        """[中] 曾经 save 说「六问已答完」、frontmatter 写 6、find 说「下一问是第1问」。

        同一份档案四个来源两种答案 —— 评审原话：半修比不修更危险，
        读到哪个就信哪个。现在三处共用 progress()。
        """
        r_save = run(ARCHIVE, "save", "--project", "X", "--step", "6",
                     "--answer", "只答了最后一问", ws=self.ws)
        self.assertIn("进度 1/6", r_save.stdout)
        self.assertIn("下一问是第 1 问", r_save.stdout)

        self.assertIn("已答: 1", self.archive_text("X"))

        r_find = run(ARCHIVE, "find", ws=self.ws)
        self.assertIn("已答 1/6", r_find.stdout)
        self.assertIn("下一问是第 1 问", r_find.stdout)

    def test_report_warns_when_questions_unanswered(self):
        """[中] 跳答后这条兜底曾完全失效 —— 而跳答是 SKILL.md 明确允许的路径。"""
        run(ARCHIVE, "save", "--project", "X", "--step", "6",
            "--answer", "只答了最后一问", ws=self.ws)
        f = self.ws / "r.md"
        f.write_text("# 诊断\n## 一句话\nx\n", encoding="utf-8")
        r = run(ARCHIVE, "report", "--project", "X", "--file", f, ws=self.ws)
        self.assertIn("只答了 1/6", r.stdout)
        self.assertIn("必须标明哪几问未答", r.stdout)

    def test_report_second_write_replaces_first(self):
        """[严重] 第二次写报告曾插在旧报告上面，两份矛盾报告并存。

        变异测试暴露过：这条用例原先是摆设。报告内容经 demote() 压成 ####
        之后已经不含 ## ，所以把 `.*\\Z` 回滚成 `.*?(?=^## |\\Z)` 它也照样绿。
        真正要钉住的不变量是「从 ## 报告 吃到文件末尾」，所以先在报告后面
        人为插一个 ## 小节，再写第二份 —— 那一段必须被一起吞掉。
        """
        run(ARCHIVE, "save", "--project", "T", "--step", "1", "--answer", "x", ws=self.ws)
        f = self.ws / "r.md"
        f.write_text("# 诊断\n## 一句话\n第一版\n", encoding="utf-8")
        run(ARCHIVE, "report", "--project", "T", "--file", f, ws=self.ws)

        # 模拟档案末尾混进了一个二级小节（手工编辑、旧版本残留都可能造成）
        path = sorted((self.ws / "创业档案").glob("T*.md"))[0]
        path.write_text(path.read_text(encoding="utf-8").rstrip()
                        + "\n\n## 旧版遗留小节\n\n残留内容\n", encoding="utf-8")

        f.write_text("# 诊断\n## 一句话\n第二版\n", encoding="utf-8")
        run(ARCHIVE, "report", "--project", "T", "--file", f, ws=self.ws)

        body = self.archive_text("T")
        self.assertNotIn("第一版", body)
        self.assertNotIn("残留内容", body,
                         "报告替换必须吃到文件末尾，否则旧内容会和新报告并存")
        self.assertEqual(body.count("第二版"), 1)

    def test_demote_pushes_content_below_section_headings(self):
        """demote() 是 report/gate 两处正则安全的前提，单独钉住它。"""
        run(ARCHIVE, "save", "--project", "T", "--step", "1", "--answer", "x", ws=self.ws)
        f = self.ws / "g.md"
        f.write_text("# 一级\n## 二级\n### 三级\n内容\n", encoding="utf-8")
        run(ARCHIVE, "gate", "--project", "T", "--file", f, ws=self.ws)
        body = self.archive_text("T")
        for line in body.splitlines():
            if line.startswith("## "):
                self.assertTrue(
                    line.startswith("## 第") or line in ("## 闸门检查", "## 报告"),
                    f"写入内容的标题泄漏到了顶层：{line}")
        self.assertIn("#### 二级", body)

    def test_gate_second_write_replaces_and_keeps_report(self):
        """[严重] cmd_gate 曾把 cmd_report 已修的两个坑原样再踩一遍。"""
        run(ARCHIVE, "save", "--project", "T", "--step", "1", "--answer", "x", ws=self.ws)
        f = self.ws / "g.md"
        f.write_text("## 主体资格\n第一版闸门\n", encoding="utf-8")
        run(ARCHIVE, "gate", "--project", "T", "--file", f, ws=self.ws)
        f.write_text("## 主体资格\n第二版闸门\n", encoding="utf-8")
        run(ARCHIVE, "gate", "--project", "T", "--file", f, ws=self.ws)
        r = self.ws / "r.md"
        r.write_text("# 诊断\n## 一句话\n报告内容\n", encoding="utf-8")
        run(ARCHIVE, "report", "--project", "T", "--file", r, ws=self.ws)

        body = self.archive_text("T")
        self.assertNotIn("第一版闸门", body)
        self.assertIn("第二版闸门", body)
        self.assertIn("报告内容", body, "写报告不能吃掉闸门检查，反之亦然")

    def test_report_headings_demoted_below_section(self):
        """[中] 报告的 ## 曾和 ## 第1问 同级，档案骨架塌掉。"""
        run(ARCHIVE, "save", "--project", "T", "--step", "1", "--answer", "x", ws=self.ws)
        f = self.ws / "r.md"
        f.write_text("# 诊断：T\n## 一句话\n内容\n", encoding="utf-8")
        run(ARCHIVE, "report", "--project", "T", "--file", f, ws=self.ws)
        body = self.archive_text("T")
        tops = [l for l in body.splitlines() if l.startswith("## ")]
        self.assertEqual(len(tops), 8, f"顶层只该有六问+闸门+报告，实际 {tops}")

    def test_find_excludes_neighbour_files(self):
        """[中] find 曾把 资源.md / 模式.md 当成「进行中 0/6」的假项目列出。"""
        run(ARCHIVE, "save", "--project", "真项目", "--step", "1", "--answer", "x", ws=self.ws)
        run(RESOURCES, "consent", "--project", "真项目", "--level", "anon", ws=self.ws)
        run(PATTERN, "log", "--kind", "高估需求", "--note", "x", ws=self.ws)
        r = run(ARCHIVE, "find", ws=self.ws)
        self.assertIn("找到 1 份档案", r.stdout)
        self.assertNotIn("资源", r.stdout)
        self.assertNotIn("模式", r.stdout)

    def test_workspace_isolation(self):
        """[严重] 不传 --workspace 时档案曾被写进 skill 安装目录，静默失败。"""
        run(ARCHIVE, "save", "--project", "T", "--step", "1", "--answer", "x", ws=self.ws)
        self.assertTrue((self.ws / "创业档案").is_dir())
        self.assertFalse((SCRIPTS.parent / "创业档案").exists(),
                         "技能目录被污染了")

    def test_step_out_of_range_rejected(self):
        r = run(ARCHIVE, "save", "--project", "T", "--step", "9",
                "--answer", "x", ws=self.ws)
        self.assertEqual(r.returncode, 1)


    def test_declined_reason_not_stored_for_sensitive(self):
        """[隐私] 「不方便」只记状态不记内容 —— 理由本身往往正是用户不想留下的。"""
        run(ARCHIVE, "save", "--project", "T", "--step", "6", "--declined", "不方便",
            "--answer", "涉及合伙人分钱的事他不想说", ws=self.ws)
        body = self.archive_text("T")
        self.assertIn("未答（不方便）", body)
        self.assertNotIn("合伙人", body, "不方便的理由被写进档案了")

    def test_declined_reason_kept_for_not_thought_through(self):
        """「没想好」要记内容 —— 那是最有价值的发现之一，不是边界。"""
        run(ARCHIVE, "save", "--project", "T", "--step", "1", "--declined", "没想好",
            "--answer", "他说还没想清楚怎么验证", ws=self.ws)
        body = self.archive_text("T")
        self.assertIn("未答（没想好）", body)
        self.assertIn("还没想清楚怎么验证", body)

    def test_declined_does_not_count_as_answered(self):
        """三种未答都不能算已答，否则断点会跳过它们。"""
        run(ARCHIVE, "save", "--project", "T", "--step", "1", "--declined", "没想好",
            "--answer", "x", ws=self.ws)
        r = run(ARCHIVE, "find", ws=self.ws)
        self.assertIn("已答 0/6", r.stdout)
        self.assertIn("下一问是第 1 问", r.stdout)

    def test_declined_kind_must_be_valid(self):
        r = run(ARCHIVE, "save", "--project", "T", "--step", "1",
                "--declined", "懒得说", "--answer", "x", ws=self.ws)
        self.assertNotEqual(r.returncode, 0)


class TestResources(Base):

    def test_write_without_consent_refused(self):
        """[严重] 同意门是这个模块唯一的隐私保障。"""
        r = run(RESOURCES, "add", "--project", "T", "--side", "have",
                "--type", "渠道", "--detail", "x", ws=self.ws)
        self.assertEqual(r.returncode, 2)
        self.assertIn("还没有记录同意档位", r.stderr)

    def test_consent_never_inherited_across_projects(self):
        """[严重] 全局回落曾让从没被问过的新项目继承 full 档直接写入。"""
        run(RESOURCES, "consent", "--project", "A", "--level", "full", ws=self.ws)
        r = run(RESOURCES, "add", "--project", "B", "--side", "have",
                "--type", "技术", "--detail", "x", ws=self.ws)
        self.assertEqual(r.returncode, 2, "B 从没被问过，不能继承 A 的档位")

    def test_handwritten_global_consent_key_is_not_inherited(self):
        """[严重·隐私] 手工模式是文档支持的，用户可能手写一个全局 `对接: full`。

        consent_of 若回落到全局键，这个从没被问过的项目就会带着联系方式
        直接写进共享池。变异测试暴露过：只回滚 consent_of 抓不住这条，
        因为脚本自己不再写全局键 —— 但手写的文件会。
        """
        d = self.ws / "创业档案"
        d.mkdir(parents=True)
        (d / "资源.md").write_text(
            "---\n对接: full\n更新: 2026-09-15\n---\n\n## 别的项目\n",
            encoding="utf-8")
        r = run(RESOURCES, "add", "--project", "从没问过的项目", "--side", "have",
                "--type", "渠道", "--detail", "我微信 13900001111", ws=self.ws)
        self.assertEqual(r.returncode, 2, "手写的全局档位不能被当成这个项目的同意")
        self.assertIn("还没有记录同意档位", r.stderr)

    def test_no_bare_global_consent_key_is_ever_written(self):
        """[严重·隐私·纵深防御] 脚本自己绝不能写裸的 `对接:` 键。

        单写一个没人读的全局键当下无害，但它是一颗定时炸弹：哪天有人给
        consent_of 加回一行回落，泄漏就悄悄复活，而且文件里那行 `对接: full`
        会让读的人以为它本来就该全局生效。不变量定在这里，比定在读取侧更稳。
        """
        run(RESOURCES, "consent", "--project", "A", "--level", "full", ws=self.ws)
        run(RESOURCES, "consent", "--project", "B", "--level", "anon", ws=self.ws)
        text = (self.ws / "创业档案" / "资源.md").read_text(encoding="utf-8")
        front = text.split("---")[1]
        for line in front.strip().splitlines():
            key = line.split(":", 1)[0].strip()
            self.assertNotEqual(key, "对接",
                                f"写出了全局同意键，泄漏隐患：{line}")

    def test_off_tier_refuses_write(self):
        run(RESOURCES, "consent", "--project", "T", "--level", "off", ws=self.ws)
        r = run(RESOURCES, "add", "--project", "T", "--side", "have",
                "--type", "技术", "--detail", "x", ws=self.ws)
        self.assertEqual(r.returncode, 2)

    def test_anon_blocks_real_contact(self):
        run(RESOURCES, "consent", "--project", "T", "--level", "anon", ws=self.ws)
        r = run(RESOURCES, "add", "--project", "T", "--side", "have",
                "--type", "渠道", "--detail", "我微信 13900001111", ws=self.ws)
        self.assertEqual(r.returncode, 2)

    def test_anon_blocks_social_accounts(self):
        """[中·隐私] 社交账号正则曾两次改错方向，而且阻断侧零测试覆盖 ——
        把整条规则删掉，28 条测试照样全绿。这里把它钉死。

        anon 档的偏向是过度拦截：误报的代价是让人改写一句话，
        漏报的代价是账号泄露。
        """
        run(RESOURCES, "consent", "--project", "T", "--level", "anon", ws=self.ws)
        must_block = [
            "微信号是 zhangsan_2020",
            "我的微信叫 xiaoli8888",
            "加我 wx 是 abc12345",
            "抖音号 lisi2024",
            "联系方式 zhangsan_2020",
            "微信：abc12345",
            "vx abc12345",
            "QQ 1234567",
            "小红书 ID xhs_user01",
            "他的微信号 wang_2024",
        ]
        for detail in must_block:
            r = run(RESOURCES, "add", "--project", "T", "--side", "have",
                    "--type", "渠道", "--detail", detail, ws=self.ws)
            self.assertEqual(r.returncode, 2, f"账号漏网了：{detail}")

    def test_anon_pauses_on_identity_info(self):
        """[严重·隐私] anon 档承诺不存人名/群名/学校名，实现却只挡数字串 ——
        「豆豆妈妈和 Vivian，都在实验二小三年级五班家长群」原样写进过档案。

        设计取舍：中文命名实体没法用正则可靠判定，收紧会把合法的脱敏写法
        也拦掉，而 anon 一旦每句话都拒收，用户会去选 full 或 off，隐私反而更差。
        所以不做永久拒收，而是暂停并要求显式 --deidentified —— 把默认放行
        变成刻意动作。误报的代价是多一次确认，漏报的代价是身份泄露。
        """
        run(RESOURCES, "consent", "--project", "T", "--level", "anon", ws=self.ws)
        must_pause = [
            "豆豆妈妈和 Vivian，都在实验二小三年级五班家长群",
            "老张在城西花园有套房",
            "在阳光雅苑小区做过",
            "同事 Michael 帮忙介绍",
            "认识实验二小的李老师",
            "跟宏图科技公司谈过",
        ]
        for detail in must_pause:
            r = run(RESOURCES, "add", "--project", "T", "--side", "have",
                    "--type", "渠道", "--detail", detail, ws=self.ws)
            self.assertEqual(r.returncode, 2, f"身份信息直接放行了：{detail}")
            self.assertIn("--deidentified", r.stderr)

    def test_deidentified_flag_lets_confirmed_entry_through(self):
        """确认之后要能写进去，否则这道门就成了死路。"""
        run(RESOURCES, "consent", "--project", "T", "--level", "anon", ws=self.ws)
        r = run(RESOURCES, "add", "--project", "T", "--side", "have", "--type", "渠道",
                "--detail", "一位家长，同一个班级的家长群里", "--deidentified", ws=self.ws)
        self.assertEqual(r.returncode, 0)

    def test_off_tier_writes_nothing(self):
        """[严重·承诺落空] 同意门对用户的原话是「不留任何痕迹」，
        而 off 档曾写下一行「对接.秘密项目: off」—— 项目名本身往往
        正是用户不想留痕的那个东西。"""
        r = run(RESOURCES, "consent", "--project", "秘密项目", "--level", "off", ws=self.ws)
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.ws / "创业档案" / "资源.md").exists(),
                         "off 档不能留下任何文件")

    def test_anon_allows_chinese_channel_descriptions(self):
        """[中] \\w 匹配中文，曾把「一个微信群里有三百多个宝妈」当成社交账号拒收 ——
        而那正是 resource-matching.md 推荐的脱敏写法。"""
        run(RESOURCES, "consent", "--project", "T", "--level", "anon", ws=self.ws)
        for detail in ("一个微信群里有三百多个宝妈",
                       "一个 800 人的业主群",
                       "自有资金 200000 元",
                       "在微信生态里做了三年",
                       "小红书上有粉丝基础",
                       "做过抖音本地生活",
                       "认识一批做快手电商的人",
                       "微信小程序开发经验五年"):
            r = run(RESOURCES, "add", "--project", "T", "--side", "have",
                    "--type", "渠道", "--detail", detail, ws=self.ws)
            self.assertEqual(r.returncode, 0, f"误拦了合法的脱敏描述：{detail}\n{r.stderr}")

    def test_full_tier_warns_but_allows_contact(self):
        """full 档允许用户自己的联系方式，但要出声提醒第三方的不行。"""
        run(RESOURCES, "consent", "--project", "T", "--level", "full", ws=self.ws)
        r = run(RESOURCES, "add", "--project", "T", "--side", "have",
                "--type", "渠道", "--detail", "我微信 13900001111", ws=self.ws)
        self.assertEqual(r.returncode, 0)
        self.assertIn("第三方", r.stderr)

    def test_type_whitelist_enforced(self):
        run(RESOURCES, "consent", "--project", "T", "--level", "anon", ws=self.ws)
        r = run(RESOURCES, "add", "--project", "T", "--side", "have",
                "--type", "人脉", "--detail", "x", ws=self.ws)
        self.assertEqual(r.returncode, 1)


class TestPattern(Base):

    def test_show_survives_unknown_handwritten_kind(self):
        """[严重] 未知 kind 曾让 show 抛 KeyError —— 而它是第 0 步的命令。"""
        d = self.ws / "创业档案"
        d.mkdir(parents=True)
        (d / "模式.md").write_text(
            "# 跨项目模式\n\n## 手工补记的观察\n\n- 2026-08-02　｜　手写的一条\n",
            encoding="utf-8")
        r = run(PATTERN, "show", ws=self.ws)
        self.assertEqual(r.returncode, 0, f"崩了：{r.stderr}")
        self.assertIn("手工补记的观察", r.stdout)

    def test_log_preserves_handwritten_section(self):
        """[严重] render 只遍历 KINDS，跑一次 log 就静默删掉手写小节。"""
        d = self.ws / "创业档案"
        d.mkdir(parents=True)
        (d / "模式.md").write_text(
            "# 跨项目模式\n\n## 手工补记的观察\n\n- 2026-08-02　｜　别删我\n",
            encoding="utf-8")
        run(PATTERN, "log", "--kind", "高估需求", "--note", "新记录", ws=self.ws)
        body = (d / "模式.md").read_text(encoding="utf-8")
        self.assertIn("别删我", body, "手写内容被静默删除了")
        self.assertIn("新记录", body)

    def test_repeat_triggers_warning(self):
        run(PATTERN, "log", "--kind", "高估需求", "--note", "第一次", ws=self.ws)
        r = run(PATTERN, "log", "--kind", "高估需求", "--note", "第二次", ws=self.ws)
        self.assertIn("第 2 次", r.stdout)

    def test_retire_removes_pattern(self):
        """没有退役机制的话，改掉的毛病会永远触发提醒。"""
        run(PATTERN, "log", "--kind", "高估需求", "--note", "x", ws=self.ws)
        r = run(PATTERN, "retire", "--kind", "高估需求", ws=self.ws)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("高估需求", run(PATTERN, "show", ws=self.ws).stdout)

    def test_leak_and_length_guards(self):
        r = run(PATTERN, "log", "--kind", "跟风", "--note", "他微信13900001111", ws=self.ws)
        self.assertEqual(r.returncode, 2)
        r = run(PATTERN, "log", "--kind", "跟风", "--note", "很长" * 45, ws=self.ws)
        self.assertEqual(r.returncode, 1)


class TestSensitive(Base):

    def test_sensitive_records_step_not_reason(self):
        """[隐私] 敏感问题只记问号和次数。这是唯一会改变下次行为的记录，约束要更严。

        断言落在条目行上，不在说明文字上 —— 文件头本来就写着「不记理由」。
        真正的不变量是：`sensitive` 根本不接受理由参数，所以条目行里
        除了问号、次数和固定提示，不可能有用户内容。
        """
        run(PATTERN, "sensitive", "--step", "6", ws=self.ws)
        body = (self.ws / "创业档案" / "敏感问题.md").read_text(encoding="utf-8")
        entries = [l for l in body.splitlines() if l.strip().startswith("- 第")]
        self.assertEqual(len(entries), 1)
        self.assertIn("第6问", entries[0])
        self.assertIn("1 次", entries[0])
        # 条目只由三段组成，没有自由文本的位置
        self.assertEqual(len(entries[0].split("｜")), 3, f"条目结构变了：{entries[0]}")

    def test_sensitive_takes_no_reason_argument(self):
        """命令行层面就没有存理由的口子 —— 比靠调用方自觉可靠。"""
        r = run(PATTERN, "sensitive", "--step", "6", "--reason", "涉及合伙人", ws=self.ws)
        self.assertNotEqual(r.returncode, 0, "sensitive 不该接受理由参数")

    def test_sensitive_warns_on_second_time(self):
        """≥2 次才提醒下次换自查表 —— 一次可能是偶然。"""
        run(PATTERN, "sensitive", "--step", "6", ws=self.ws)
        r = run(PATTERN, "sensitive", "--step", "6", ws=self.ws)
        self.assertIn("第 2 次", r.stdout)
        self.assertIn("自查表", r.stdout)

    def test_sensitive_surfaces_in_show(self):
        """第 0 步的 show 必须带出来，否则这条记录永远不会影响行为。"""
        run(PATTERN, "sensitive", "--step", "3", ws=self.ws)
        r = run(PATTERN, "show", ws=self.ws)
        self.assertIn("不方便回答", r.stdout)
        self.assertIn("第3问", r.stdout)

    def test_multi_project_workspace_warns_about_mixed_owners(self):
        """模式是「关于人」的，档案是「关于项目」的，脚本分不出多项目属不属于同一个人。

        差分测试在 gstack 上看到了这个坑的后果：它的 builder profile 按机器用户存，
        于是会对第一次来的用户说「欢迎回来，上次我们聊的是<另一个人的项目>」。
        分不出来的时候出声，比默认串或默认不串都好。
        """
        run(ARCHIVE, "save", "--project", "客户A", "--step", "1", "--answer", "x", ws=self.ws)
        run(ARCHIVE, "save", "--project", "客户B", "--step", "1", "--answer", "y", ws=self.ws)
        run(PATTERN, "log", "--kind", "高估需求", "--note", "x", ws=self.ws)
        r = run(PATTERN, "show", ws=self.ws)
        self.assertIn("2 个项目", r.stdout)
        self.assertIn("会串", r.stdout)

    def test_single_project_workspace_stays_quiet(self):
        """只有一个项目时不该唠叨。"""
        run(ARCHIVE, "save", "--project", "只有一个", "--step", "1", "--answer", "x", ws=self.ws)
        run(PATTERN, "log", "--kind", "高估需求", "--note", "x", ws=self.ws)
        r = run(PATTERN, "show", ws=self.ws)
        self.assertNotIn("会串", r.stdout)

    def test_sensitive_step_out_of_range(self):
        r = run(PATTERN, "sensitive", "--step", "9", ws=self.ws)
        self.assertEqual(r.returncode, 1)



class TestSignals(Base):
    """正向信号。和「模式」对称的另一半，约束必须一样严。

    加这一组的理由不是好听：模式库七类全是毛病，只记毛病的话，
    一个人来第三次时开场白就是一份罪状清单——而这个技能的存亡标准
    是他会不会来第四次。
    """

    def test_pattern_and_archive_agree_on_what_counts_as_a_project(self):
        """[中·天天误报] 两个脚本各存一份「哪些不是档案」，必然各自漂移。

        pattern.py 那份漏了 `资源.md`：只有一个项目的工作空间被报成
        「有 2 个项目」，然后劝用户换目录——一条只该在多客户场景出现的
        警告，对单用户天天响。archive.py 那份漏了 `敏感问题.md` 和 `强项.md`。

        这条测试不写文件名，跑真实命令比对两个脚本的口径。
        """
        run(ARCHIVE, "save", "--project", "只有一个", "--step", "1",
            "--answer", "x", ws=self.ws)
        run(RESOURCES, "consent", "--project", "只有一个", "--level", "anon", ws=self.ws)
        run(RESOURCES, "add", "--project", "只有一个", "--side", "have",
            "--type", "渠道", "--detail", "两位家长", ws=self.ws)
        run(PATTERN, "log", "--kind", "跟风", "--note", "x", ws=self.ws)
        run(PATTERN, "signal", "--kind", "砍得动", "--note", "x", ws=self.ws)
        run(PATTERN, "sensitive", "--step", "6", ws=self.ws)

        self.assertIn("找到 1 份档案", run(ARCHIVE, "find", ws=self.ws).stdout)
        self.assertNotIn("会串", run(PATTERN, "show", ws=self.ws).stdout,
                         "只有一个项目却报了多主体串档警告")

    def test_multi_owner_warning_still_fires_for_real_second_project(self):
        """修完误报不能顺手把真警告也关掉——这条是隐私相关的。"""
        for name in ("客户A", "客户B"):
            run(ARCHIVE, "save", "--project", name, "--step", "1",
                "--answer", "x", ws=self.ws)
        run(PATTERN, "log", "--kind", "跟风", "--note", "x", ws=self.ws)
        self.assertIn("会串", run(PATTERN, "show", ws=self.ws).stdout)

    def test_signal_writes_its_own_file(self):
        """强项和模式不共用文件，两边互不覆盖。

        parse/render 被这两条命令共用，共用之后最容易出的事是
        一个命令把另一个的文件写没了。
        """
        run(PATTERN, "log", "--kind", "高估需求", "--note", "甲甲甲", ws=self.ws)
        run(PATTERN, "signal", "--kind", "说得出人", "--note", "乙乙乙", ws=self.ws)
        d = self.ws / "创业档案"
        self.assertIn("甲甲甲", (d / "模式.md").read_text(encoding="utf-8"))
        self.assertIn("乙乙乙", (d / "强项.md").read_text(encoding="utf-8"))
        self.assertNotIn("乙乙乙", (d / "模式.md").read_text(encoding="utf-8"))
        self.assertNotIn("甲甲甲", (d / "强项.md").read_text(encoding="utf-8"))

    def test_signal_has_the_same_leak_and_length_guards_as_log(self):
        """[隐私] 只在 log 那边设闸的话，强项就是绕过去的口子。

        两个文件都会在每次诊断开头被读进上下文，约束不对称等于没有约束。
        """
        r = run(PATTERN, "signal", "--kind", "说得出人",
                "--note", "他微信13900001111", ws=self.ws)
        self.assertEqual(r.returncode, 2, "强项文件放行了手机号")
        r = run(PATTERN, "signal", "--kind", "说得出人",
                "--note", "很长" * 45, ws=self.ws)
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.ws / "创业档案" / "强项.md").exists(),
                         "被拒的写入不该留下文件")

    def test_signal_rejects_freeform_kind(self):
        """自由文本会让「第几次」失效，而这里的全部价值就在第几次。"""
        r = run(PATTERN, "signal", "--kind", "很有想法", "--note", "x", ws=self.ws)
        self.assertNotEqual(r.returncode, 0)

    def test_show_puts_signals_before_faults(self):
        """顺序是刻意的，不是排版。倒过来就回到了罪状清单。"""
        run(PATTERN, "log", "--kind", "高估需求", "--note", "毛病记录", ws=self.ws)
        run(PATTERN, "signal", "--kind", "砍得动", "--note", "优点记录", ws=self.ws)
        out = run(PATTERN, "show", ws=self.ws).stdout
        self.assertIn("做对过什么", out)
        self.assertLess(out.index("优点记录"), out.index("毛病记录"),
                        "毛病排在了强项前面")

    def test_show_surfaces_signals_even_with_no_faults(self):
        """[严重] cmd_show 在没有模式记录时提前 return 0。

        强项打印放在那个 return 之后的话，只有优点没有毛病的用户
        —— 也就是表现最好的那个 —— 反而什么都看不到。
        """
        run(PATTERN, "signal", "--kind", "拿得出证据", "--note", "只有优点", ws=self.ws)
        r = run(PATTERN, "show", ws=self.ws)
        self.assertEqual(r.returncode, 0)
        self.assertIn("只有优点", r.stdout)

    def test_homework_done_changes_the_opening(self):
        """这一条有行为后果：做了作业的人，开场该问结果，不该从头来。"""
        run(PATTERN, "signal", "--kind", "上次的作业做了",
            "--note", "上次让他找的三个人都聊了", ws=self.ws)
        self.assertIn("开场", run(PATTERN, "show", ws=self.ws).stdout)

    def test_signal_file_is_not_counted_as_a_project(self):
        """[中] find 曾把 资源.md / 模式.md 当成诊断项目列出来。同款 bug。

        强项.md 被算成项目的话，单项目工作空间会误报「多个客户会串」。
        """
        run(ARCHIVE, "save", "--project", "只有一个", "--step", "1",
            "--answer", "x", ws=self.ws)
        run(PATTERN, "signal", "--kind", "砍得动", "--note", "x", ws=self.ws)
        self.assertNotIn("会串", run(PATTERN, "show", ws=self.ws).stdout)

    def test_signal_preserves_handwritten_section(self):
        """手工模式是文档明确宣传的，render 不能静默删掉手写小节。"""
        d = self.ws / "创业档案"
        d.mkdir(parents=True)
        (d / "强项.md").write_text(
            "# 这个人做对过什么\n\n## 手写的观察\n\n- 2026-08-02　｜　别删我\n",
            encoding="utf-8")
        run(PATTERN, "signal", "--kind", "砍得动", "--note", "新记录", ws=self.ws)
        body = (d / "强项.md").read_text(encoding="utf-8")
        self.assertIn("别删我", body)
        self.assertIn("新记录", body)


class TestCheckRules(unittest.TestCase):

    def test_separates_freshness_from_coverage(self):
        """[中] 对根本没覆盖的行业曾报「✓ 有效 N 条」绿灯，被误读成「这行查过了」。

        断言落在语义上不落在原句上——措辞改过一次，钉死原句只会让
        下一次改写变成"改文案顺手改测试"。
        """
        r = run(CHECK)
        self.assertEqual(r.returncode, 0)
        self.assertIn("覆盖没覆盖", r.stdout, "没说清楚它不回答覆盖范围")
        self.assertIn("规则库覆盖的范围", r.stdout)

    def test_never_claims_the_rules_are_still_in_force(self):
        """[严重·误导] 「✓ 有效 N 条」被读成「这些法规还生效」，而它只量拉取日期。

        实测撞到了后果：规则库 E4 引的规章 2023 年就被废止，而这里
        一直报绿——四次独立诊断全都指出了这一点。脚本没法知道一条
        法规活着没有，那它就不能用"有效"这个词。
        """
        out = run(CHECK).stdout
        import re
        self.assertIsNone(re.search(r"[✓有]\s*效\s*\d+\s*条", out),
                          f"又出现了「有效 N 条」这种说法：\n{out}")
        self.assertIn("不说明那条法规还在生效", out, "没有把「新鲜」和「仍然有效」分开")

    def test_failure_modes_included_in_freshness_scan(self):
        """失败模式库也会过期 —— 反例失效、竞品收费了、法规变了都会让它失准。"""
        r = run(CHECK)
        self.assertIn("failure-modes.md", r.stdout)

    def test_side_hustle_reference_exists_and_has_four_questions(self):
        """副业四问是独立的问题集，不是六问的子集。少一问就不成立。"""
        f = SCRIPTS.parent / "references" / "side-hustle.md"
        self.assertTrue(f.is_file(), "side-hustle.md 不存在")
        t = f.read_text(encoding="utf-8")
        import re as _re
        qs = _re.findall(r"^## 第 (\d) 问", t, flags=_re.MULTILINE)
        self.assertEqual(qs, ["1", "2", "3", "4"], f"应有四问，实际 {qs}")
        # 第2问必须靠前 —— 在职冲突要早于谈钱
        self.assertLess(t.index("## 第 2 问"), t.index("## 第 3 问"))
        self.assertIn("竞业", t)
        self.assertIn("职务发明", t)

    def test_every_failure_mode_has_counterexample(self):
        """[方法论] 没有反例的机制不许入库 —— 这是基数谬误的解药。

        失败案例库只收录失败者。不强制找反例的话，每条机制都会长成
        「这样做会死」，而真相往往是「这样做很难，但有人靠 X 做成了」。
        后者对用户有用，前者只会让他瘫痪。
        """
        import re as _re
        text = (SCRIPTS.parent / "references" / "failure-modes.md").read_text(encoding="utf-8")
        blocks = _re.split(r"^## F\d+ ", text, flags=_re.MULTILINE)[1:]
        self.assertTrue(blocks, "一条失败模式都没有")
        for b in blocks:
            name = b.splitlines()[0]
            for field in ("**诊断测试**", "**为什么致死**", "**反例**", "**出路**"):
                self.assertIn(field, b, f"F「{name}」缺 {field}")

    def test_every_failure_mode_appears_in_index(self):
        """[上下文预算] 库长到 14 条后改成按索引读，新加条目必须同步进索引。

        漏进索引的条目等于不存在 —— 模型只读索引和命中的那几条，
        永远不会读到它。这比条目写错更隐蔽。
        """
        import re as _re
        t = (SCRIPTS.parent / "references" / "failure-modes.md").read_text(encoding="utf-8")
        head = t[:t.index("## F1 ")]
        ids = _re.findall(r"^## (F\d+) ", t, flags=_re.MULTILINE)
        self.assertTrue(ids)
        for fid in ids:
            # 必须整词匹配：F1 的正则会把 F11 F12 一起吃掉，反过来也一样
            self.assertRegex(head, rf"{fid}(?![0-9])",
                             f"{fid} 没有出现在索引表里，模型永远读不到它")

    def test_failure_mode_index_stays_small(self):
        """索引本身不能长成第二个全文 —— 它存在的意义就是别全读。"""
        t = (SCRIPTS.parent / "references" / "failure-modes.md").read_text(encoding="utf-8")
        head = t[:t.index("## F1 ")]
        self.assertLess(len(head), len(t) * 0.45,
                        "索引超过全文 45%，该分组或精简了")

    def test_flags_pending_verification(self):
        r = run(CHECK)
        self.assertIn("待证", r.stdout)

    def test_stale_and_bad_dates(self):
        with tempfile.TemporaryDirectory() as t:
            refs = Path(t)
            (refs / "rules-x.md").write_text(
                "# 测试库\n## 陈年 `拉取日期: 2020-01-01`\n## 烂日期 `拉取日期: 2026-13-45`\n",
                encoding="utf-8")
            r = run(CHECK, "--refs", refs)
            self.assertIn("过期", r.stdout)
            self.assertIn("日期无效", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

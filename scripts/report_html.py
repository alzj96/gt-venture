#!/usr/bin/env python3
"""把档案里的报告渲染成一个能转发的 HTML 文件。

**为什么要有这个**：报告的全部意义是「能被转发给合伙人看」（见
references/report-format.md 第一句）。而一段 markdown 贴在聊天框里，
转给一个不用 AI 的人，他看到的是一堆 ## 和 **。

**三条设计约束，每条都有理由：**

1. **单文件、零外部依赖。** 样式和脚本都内联，不引 CDN、不引字体。
   转发出去的文件会在断网的手机上、在微信内置浏览器里、在打印预览里
   被打开——任何一个外部请求都可能让它变成裸文本。
   内联的那点 JS 只做增强（目录高亮、术语卡、锚点跳转）：**关掉 JS
   这份报告照样从头读到尾**，一个字都不少。
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
import html as _html
import re
import sys
from pathlib import Path


def html_escape(t: str) -> str:
    """模块名 html 被 apply_glossary/build_toc 的参数名占了，统一走这个。"""
    return _html.escape(t, quote=False)


ARCHIVE_DIR = "创业档案"
# 页眉页脚曾经三种模式共用一句「gt-venture · 创业诊断」。
# 在一份通篇写着「这不是诊断」的筛查报告上，页眉和页脚各说一次「诊断」——
# **这份报告最重要的那句话，被它自己的页面装修拆了台。**
# 落款还承诺「正文里每个判断都配了什么能推翻它」，那是六问诊断的写法，
# 体检和筛查的骨架里根本没这一节：一句兑现不了的承诺。
CHROME = {
    "诊断": ("创业诊断",
             "正文里每个判断都配了「什么能推翻它」——"
             "<strong>你手上有它不知道的信息时，请推翻它。</strong>"),
    "副业": ("副业体检",
             "体检看的是四件事：你卖什么、和本职冲不冲突、时薪多少、钱怎么收。"
             "<strong>没写进来的，就是这次没看。</strong>"),
    "筛查": ("轻量筛查",
             "<strong>这是筛查，不是诊断。</strong>"
             "它只查了三样能一票否决的东西，三样都没中也不等于这事能做——"
             "没查的那几项，正文「没查的是这些」里列着。"),
}
SKIP_FILES = {"模式.md", "强项.md", "敏感问题.md", "资源.md"}

CSS = """
/* 排印语言借自一篇长文的网页版：serif 正文、sans 标题、章节上边线、
   左侧目录脊。状态色取自 dataviz 的固定状态盘，且**永远配图标和文字**
   —— 颜色不单独承载意义（色觉障碍、打印、强制高对比下都要读得出）。 */
:root{
 --serif:Georgia,"Songti SC","Source Han Serif SC","Noto Serif CJK SC",serif;
 --sans:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
 --maxw:708px; --toc-w:212px;
 --ink:#1a1a1a; --soft:#3a3a3a; --muted:#86868b; --bg:#fff; --rule:#e7e7e7;
 --accent:#0066cc; --mark:#fffaf0;
 --good:#0ca30c; --warn:#fab219; --serious:#ec835a; --crit:#d03b3b;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--serif);
 font-size:17px;line-height:1.85;-webkit-font-smoothing:antialiased;
 text-rendering:optimizeLegibility;-webkit-text-size-adjust:100%}
.layout{display:flex;gap:40px;justify-content:center;padding:44px 24px 80px}
.doc{max-width:var(--maxw);width:100%;min-width:0}

/* ── 顶栏 ── */
.eyebrow{font-family:var(--sans);font-size:12px;letter-spacing:.08em;
 color:var(--muted);margin:0 0 10px}
h1{font-family:var(--sans);font-size:31px;font-weight:760;line-height:1.28;
 letter-spacing:-.01em;margin:0 0 8px}
.sub{font-family:var(--sans);font-size:13.5px;color:var(--muted);margin:0 0 26px}

/* ── 概览：结论 + 四道闸 ── */
.verdict{font-family:var(--sans);font-size:20px;font-weight:700;line-height:1.5;
 margin:0 0 18px;padding:16px 20px;background:var(--mark);
 border-left:3px solid var(--accent);border-radius:0 4px 4px 0}
.gates{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 8px}
.gate{font-family:var(--sans);border:1px solid var(--rule);border-radius:6px;
 padding:11px 12px;min-width:0}
.gate .g-n{font-size:12px;color:var(--muted);letter-spacing:.04em}
.gate .g-s{font-size:14.5px;font-weight:650;margin-top:3px;display:flex;
 align-items:center;gap:5px;line-height:1.3}
.g-i{flex:none;width:14px;height:14px}
.gate.ok{border-color:rgba(12,163,12,.34)} .gate.ok .g-s{color:var(--good)}
.gate.bad{border-color:rgba(208,59,59,.34)} .gate.bad .g-s{color:var(--crit)}
.gate.unknown{border-color:rgba(250,178,25,.5)} .gate.unknown .g-s{color:#9a6a00}
.gate.na .g-s{color:var(--muted)}
.gates-note{font-family:var(--sans);font-size:12px;color:var(--muted);margin:0 0 30px}

/* ── 标题栏：上边线 + 大留白，章节切割靠它 ── */
h2 a.anchor{color:inherit;text-decoration:none}
h2 a.anchor:hover::after{content:"#";color:var(--muted);font-weight:400;
 font-size:.66em;margin-left:.34em;vertical-align:.12em}
h2{font-family:var(--sans);font-size:23px;font-weight:750;line-height:1.35;
 margin:54px 0 6px;padding-top:26px;border-top:1px solid var(--rule);
 scroll-margin-top:20px}
h3{font-family:var(--sans);font-size:17.5px;font-weight:700;margin:32px 0 4px}
h4{font-family:var(--sans);font-size:15.5px;font-weight:650;color:var(--soft);margin:22px 0 4px}
p{margin:0 0 15px}
ul,ol{margin:0 0 15px;padding-left:1.35em}
li{margin:0 0 8px}
li>ul,li>ol{margin-top:8px}
strong{font-weight:700}
em{font-style:italic;color:var(--soft)}
code{background:#f3f3f1;padding:.1em .38em;border-radius:3px;font-size:.88em;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
a{color:var(--accent);text-underline-offset:2px}
blockquote{margin:0 0 15px;padding:12px 18px;background:#fafaf8;
 border-left:3px solid var(--rule);color:var(--soft)}
blockquote p:last-child{margin-bottom:0}
hr{border:0;border-top:1px solid var(--rule);margin:28px 0}
.tablewrap{overflow-x:auto;margin:0 0 16px}
table{border-collapse:collapse;width:100%;font-family:var(--sans);font-size:13.5px;line-height:1.6}
th,td{border:1px solid var(--rule);padding:9px 11px;text-align:left;vertical-align:top}
th{background:#faf9f7;font-weight:650}

/* ── 术语卡：复刻自那篇长文的实现 ──
   桌面 hover 走 CSS，**点击/触屏走 JS —— 手机没有 hover，必须能点**。
   第一版我只做了 hover，等于术语卡在手机上完全没用，而报告最常
   被打开的地方就是手机。卡片用 --dx 做视口边缘避让，贴底时翻到上方。 */
.term{position:relative;border-bottom:1px dashed rgba(0,102,204,.5);cursor:pointer;
 transition:border-color .15s,background .15s}
.term:hover,.term.open{background:rgba(0,102,204,.07);border-bottom-color:var(--accent)}
.term:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}
.term-card{position:absolute;left:0;bottom:calc(100% + 9px);
 transform:translateX(var(--dx,0px));width:min(330px,76vw);
 background:var(--ink);color:#fff;font-family:var(--sans);font-size:12.5px;
 line-height:1.68;font-weight:400;text-align:left;padding:11px 13px;border-radius:7px;
 opacity:0;visibility:hidden;transition:opacity .16s;z-index:30;cursor:text;
 box-shadow:0 8px 28px rgba(0,0,0,.2)}
.term-card.up{bottom:auto;top:calc(100% + 9px)}
.term:hover .term-card,.term.open .term-card{opacity:1;visibility:visible}
@media(max-width:700px){
 /* 手机上不做边缘避让，直接贴着正文宽度铺开，点一下出、再点收 */
 .term-card{position:fixed;left:12px;right:12px;bottom:14px;top:auto;width:auto;
  transform:none;font-size:13.5px;padding:14px 16px;box-shadow:0 -6px 30px rgba(0,0,0,.22)}
 .term:hover .term-card{opacity:0;visibility:hidden}
 .term.open .term-card{opacity:1;visibility:visible}
}

/* ── 下一步：可勾的清单 ── */
.todo{list-style:none;padding:0;margin:0 0 16px}
.todo li{display:flex;gap:11px;align-items:flex-start;padding:11px 13px;
 border:1px solid var(--rule);border-radius:6px;margin-bottom:8px;
 font-family:var(--sans);font-size:14.5px;line-height:1.65}
.todo input{margin:5px 0 0;flex:none;width:15px;height:15px;accent-color:var(--accent)}
.todo li:has(input:checked){color:var(--muted);background:#fafaf8}
.todo li:has(input:checked) .t-x{text-decoration:line-through}

/* ── 目录脊 ── */
.toc{position:sticky;top:44px;width:var(--toc-w);flex:none;align-self:flex-start;
 border-left:1.5px solid var(--rule);padding-left:16px;font-family:var(--sans)}
.toc-h{font-size:11.5px;letter-spacing:.07em;color:var(--muted);margin:0 0 10px}
.toc a{display:block;color:#b9b9b9;text-decoration:none;font-size:12.5px;
 padding:4px 0;line-height:1.5;transition:color .15s}
.toc a:hover{color:var(--ink)}
.toc a.on{color:var(--accent)}
.toc a.on::before{content:"";position:absolute;left:-20px;margin-top:.55em;
 width:5px;height:5px;border-radius:50%;background:var(--accent)}
.toc a{position:relative}

.foot .net{margin:10px 0 0}
.foot{margin-top:52px;padding-top:16px;border-top:1px solid var(--rule);
 font-family:var(--sans);color:var(--muted);font-size:12.5px;line-height:1.75}

/* 窄屏不是把目录藏起来，是换形态。
   报告转发出去多半在手机或窄面板里打开——把导航藏掉，"分模块"这件事
   就只在宽屏成立，而宽屏恰恰是最少见的那个场景。

   形态照搬那篇长文：**左下角一个悬浮按钮，点开是从左边滑出的抽屉**。
   之前做的是顶部横排一行，够得着，但它占着正文最值钱的第一屏，
   而且章节一多就折成两三行。悬浮按钮不占版面，手也够得到——
   拇指在屏幕下方，不在顶部。 */
#menuBtn,#scrim{display:none}
@media(max-width:940px){
 .layout{display:block;padding:30px 18px 76px}
 .doc{max-width:none}
 .toc{position:fixed;top:0;left:0;height:100vh;width:286px;max-width:84vw;
  background:var(--bg);z-index:60;transform:translateX(-106%);
  transition:transform .25s ease;overflow-y:auto;border-left:0;
  box-shadow:0 0 48px rgba(0,0,0,.16);padding:60px 22px 40px}
 .toc.open{transform:none}
 .toc-h{margin:0 0 14px}
 .toc a{padding:9px 0;color:var(--soft);font-size:14px;line-height:1.5}
 .toc a.on{color:var(--accent);font-weight:650}
 .toc a.on::before{left:-12px}
 #menuBtn{display:inline-flex;align-items:center;gap:6px;position:fixed;
  bottom:18px;left:18px;z-index:65;border:none;cursor:pointer;color:#fff;
  background:var(--ink);font-family:var(--sans);font-size:13px;font-weight:600;
  height:42px;padding:0 18px;line-height:1;border-radius:30px;
  box-shadow:0 4px 16px rgba(0,0,0,.22)}
 #scrim{position:fixed;inset:0;background:rgba(0,0,0,.34);z-index:55}
 #scrim.show{display:block}
 /* 术语卡在手机上是贴底的抽屉，和这个按钮抢同一块地方。
    卡片开着的时候把按钮让出去，别压在解释文字上。 */
 body.term-open #menuBtn{opacity:0;pointer-events:none;transition:opacity .18s}
}
@media(max-width:560px){body{font-size:16px}h1{font-size:25px}h2{font-size:20px}
 .gates{grid-template-columns:repeat(2,1fr)}.verdict{font-size:17px;padding:13px 15px}}
@media print{.toc{display:none}.layout{padding:0}.term-card{display:none}
 .h2 a.anchor::after{content:none}
 .todo li{break-inside:avoid}}
"""



# 目录高亮。**没有它页面照样能读**，所以不做任何兜底——
# 这份文件的第一约束是"断网、微信内置浏览器、打印预览都能打开"，
# 任何依赖 JS 才成立的信息都不该放进来。
JS = """<script>
(function(){
  // ── 目录高亮：用 IntersectionObserver，rootMargin 下沿 -82%。
  //    复刻自那篇长文——比监听 scroll 算 offsetTop 稳，
  //    而且"当前在哪一节"的手感是那个 -82% 调出来的。
  var links=[].slice.call(document.querySelectorAll('.toc a'));
  var map={}; links.forEach(function(a){ map[a.getAttribute('href').slice(1)]=a; });
  var heads=[].slice.call(document.querySelectorAll('h2[id]'));
  if(links.length&&heads.length&&window.IntersectionObserver){
    var io=new IntersectionObserver(function(es){
      es.forEach(function(e){
        if(e.isIntersecting){
          links.forEach(function(l){l.classList.remove('on');});
          var a=map[e.target.id]; if(a){a.classList.add('on');}
        }
      });
    },{rootMargin:'0px 0px -82% 0px',threshold:0});
    heads.forEach(function(h){io.observe(h);});
  }

  // ── 点标题/点目录要真的跳过去。
  //    直接开本地文件、或挂在网上时，href="#sN" 原生就能跳，所以本地一测就过。
  //    但这份报告是拿来转发的：从微信、邮件附件、系统预览器打开时，页面地址常是
  //    data: 或 blob:，浏览器会拦掉片段跳转——点了没反应。
  //    所以自己 scrollIntoView，几种打开方式下行为一致。
  document.addEventListener('click',function(e){
    var a=e.target.closest('a[href^="#"]'); if(!a) return;
    var id=a.getAttribute('href').slice(1); if(!id) return;
    var el=document.getElementById(id); if(!el) return;
    e.preventDefault();
    el.scrollIntoView({behavior:'smooth',block:'start'});
    if(history.replaceState) try{history.replaceState(null,'','#'+id);}catch(_){}
  });

  // ── 窄屏的目录抽屉（复刻自那篇长文：按钮 / 遮罩 / 点条目就收）
  var toc=document.getElementById('toc'),
      btn=document.getElementById('menuBtn'),
      scrim=document.getElementById('scrim');
  function closeToc(){ if(toc){toc.classList.remove('open');}
                       if(scrim){scrim.classList.remove('show');} }
  if(btn&&toc){
    btn.addEventListener('click',function(){
      toc.classList.toggle('open');
      if(scrim) scrim.classList.toggle('show');
    });
  }
  if(scrim) scrim.addEventListener('click',closeToc);
  // 点了条目就收起来 —— 上面那个锚点处理器已经把页面滚过去了，
  // 抽屉还盖着的话，用户看到的是自己刚点的那一栏，不是跳到的那一节。
  if(toc) toc.addEventListener('click',function(e){
    if(e.target.closest('a')) closeToc();
  });
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape') closeToc();
  });

  // ── 术语卡：桌面 hover 走 CSS，点击/触屏走这里（手机没有 hover，必须能点）
  var openTerm=null, openAtY=0;
  function narrow(){ return matchMedia('(max-width:700px)').matches; }
  function place(t){
    var c=t.querySelector('.term-card'); if(!c) return;
    c.classList.remove('up'); c.style.setProperty('--dx','0px');
    if(narrow()) return;
    var r=c.getBoundingClientRect(), m=12, dx=0;
    if(r.right>innerWidth-m) dx=innerWidth-m-r.right;
    if(r.left+dx<m) dx=m-r.left;
    c.style.setProperty('--dx',dx+'px');
    if(r.bottom>innerHeight-m && t.getBoundingClientRect().top>r.height+m) c.classList.add('up');
  }
  function closeTerm(){
    if(openTerm){ openTerm.classList.remove('open'); openTerm=null;
                  document.body.classList.remove('term-open'); }
  }
  document.addEventListener('click',function(e){
    if(e.target.closest('.term-card')) return;        // 卡片内可选中文字
    var t=e.target.closest('.term');
    if(!t){ closeTerm(); return; }
    e.preventDefault();
    if(t===openTerm){ closeTerm(); return; }
    closeTerm(); place(t); t.classList.add('open'); openTerm=t; openAtY=scrollY;
    document.body.classList.add('term-open');
  });
  document.addEventListener('mouseover',function(e){
    var t=e.target.closest('.term'); if(t&&t!==openTerm&&!narrow()) place(t);
  });
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape') closeTerm();
    if((e.key==='Enter'||e.key===' ')&&document.activeElement&&
       document.activeElement.classList.contains('term')){
      e.preventDefault(); document.activeElement.click();
    }
  });
  // 滚开一屏才收（小幅滚动是读者在看卡片本身，别抢在他前面关掉）
  addEventListener('scroll',function(){
    if(openTerm&&!narrow()&&Math.abs(scrollY-openAtY)>120) closeTerm();
  },{passive:true});
})();
</script>"""


# 报告里全是行话。第一次创业的人读到「类目资质」「最窄楔子」就卡住，
# 而他恰恰是这份报告的目标读者。术语卡是首次出现时挂一个悬停解释——
# 比任何图表都值钱，因为它解决的是"读不懂"，不是"看不清"。
GLOSSARY = {
    "主体资格": "这个人能不能做这件事——身份上的限制，比如在编教师、公务员。主体不合格时，证办得再全也没用。",
    "类目资质": "平台（比如微信小程序）按业务类型要求的证照。选错类目，后面整段结论都是错的。",
    "最窄的楔子": "砍到最小、但还有人要的那个版本。楔子窄，第一批用户才会满意而不是失望。",
    "最窄楔子": "砍到最小、但还有人要的那个版本。楔子窄，第一批用户才会满意而不是失望。",
    "预付式消费": "先收钱、之后分次兑付（充值、会员卡、次卡、存杯）。2024-07-01 起收预付款必须与消费者订立书面合同。",
    "市场主体登记": "就是办营业执照。个体工商户也在《市场主体登记管理条例》的「市场主体」名单上。",
    "四道闸": "任何行业都要过的四道：人（谁能做）、事（要不要许可）、地（哪个渠道）、钱（怎么收）。",
    "可托付税": "把一件事交给 AI 且没人盯着也敢用，要额外付的成本 = 错误代价 × 漏检率 × 不可预测性。",
    "失败模式": "杀死项目的机制，不是死掉的公司名单。每条都带「谁有这个病却活下来了、靠什么」。",
    "对抗评审": "报告定稿前，找一个只看报告、不看对话的独立视角挑毛病——看了对话就会被推理过程带着走。",
}

GATE_ICON = {
    # 状态永远是「图标 + 文字 + 颜色」三件套。少了前两件，色觉障碍、
    # 打印和强制高对比模式下这一栏就变成了空白。
    "ok": ('<svg class="g-i" viewBox="0 0 16 16" fill="none" aria-hidden="true">'
           '<path d="M3 8.5l3.2 3.2L13 5" stroke="currentColor" stroke-width="2.1" '
           'stroke-linecap="round" stroke-linejoin="round"/></svg>'),
    "bad": ('<svg class="g-i" viewBox="0 0 16 16" fill="none" aria-hidden="true">'
            '<path d="M8 4v5.2M8 12.2v.1" stroke="currentColor" stroke-width="2.1" '
            'stroke-linecap="round"/><circle cx="8" cy="8" r="6.3" stroke="currentColor" '
            'stroke-width="1.5"/></svg>'),
    "unknown": ('<svg class="g-i" viewBox="0 0 16 16" fill="none" aria-hidden="true">'
                '<circle cx="8" cy="8" r="6.3" stroke="currentColor" stroke-width="1.5" '
                'stroke-dasharray="2.6 2.4"/><path d="M8 11.4v.1" stroke="currentColor" '
                'stroke-width="2.1" stroke-linecap="round"/><path d="M6.2 6.2a1.8 1.8 0 113 1.4'
                'c-.7.5-1.2.8-1.2 1.6" stroke="currentColor" stroke-width="1.6" '
                'stroke-linecap="round"/></svg>'),
    "na": ('<svg class="g-i" viewBox="0 0 16 16" fill="none" aria-hidden="true">'
           '<path d="M4 8h8" stroke="currentColor" stroke-width="2.1" stroke-linecap="round"/>'
           '</svg>'),
}
STATE = {"过": "ok", "有问题": "bad", "未查": "unknown", "不适用": "na"}


def apply_glossary(html: str) -> str:
    """只挂首次出现的那一个，而且只在文本节点里挂。

    挂满全文会让正文变成一片虚线，读起来更累——术语卡是帮读者过第一道坎，
    不是给每个词都配一本词典。

    **实现上有个坑**：第一版用 `(?<![>\\w])` 想避开标签内部，但 Python 的
    `\\w` 匹配中文，于是中文正文里几乎每一次出现都被挡掉，一个卡都没挂上。
    正确做法是按标签切开，只在文本段里替换。
    """
    parts = re.split(r"(<[^>]+>)", html)
    used = set()
    skip = False
    for k, seg in enumerate(parts):
        if seg.startswith("<"):
            low = seg.lower()
            if low.startswith(("<style", "<script", "<title")):
                skip = True
            elif low.startswith(("</style", "</script", "</title")):
                skip = False
            continue
        if skip or not seg.strip():
            continue
        for term, desc in GLOSSARY.items():
            if term in used or term not in seg:
                continue
            card = (f'<span class="term" tabindex="0" role="button">{term}'
                    f'<span class="term-card">{html_escape(desc)}</span></span>')
            parts[k] = seg = seg.replace(term, card, 1)
            used.add(term)
    return "".join(parts)


def parse_overview(md: str) -> tuple:
    """抽出「## 概览」那三行，返回 (结论, 四道闸, 已答, 剩下的 markdown)。

    格式对不上就整块当普通内容渲染——**报告出得来比画得好看重要**。
    """
    m = re.search(r"^## 概览\s*\n(.*?)(?=^## |\Z)", md, re.MULTILINE | re.DOTALL)
    if not m:
        return None, None, None, md
    block = m.group(1)
    verdict = gates = answered = None
    for line in block.splitlines():
        t = line.strip().lstrip("-*").strip()
        if t.startswith("结论：") or t.startswith("结论:"):
            verdict = t.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif t.startswith("闸门：") or t.startswith("闸门:"):
            raw = t.split("：", 1)[-1].split(":", 1)[-1]
            gates = []
            for part in raw.split("/"):
                if "=" in part:
                    name, st = part.split("=", 1)
                    gates.append((name.strip(), st.strip()))
        elif t.startswith("已答：") or t.startswith("已答:"):
            answered = t.split("：", 1)[-1].split(":", 1)[-1].strip()
    if not verdict:
        return None, None, None, md
    return verdict, gates, answered, md[:m.start()] + md[m.end():]


def render_overview(verdict: str, gates, answered) -> str:
    out = [f'<p class="verdict">{_inline(verdict)}</p>']
    if gates:
        out.append('<div class="gates">')
        for name, st in gates:
            cls = STATE.get(st, "unknown")
            out.append(f'<div class="gate {cls}"><div class="g-n">{html_escape(name)}</div>'
                       f'<div class="g-s">{GATE_ICON[cls]}{html_escape(st)}</div></div>')
        out.append("</div>")
        if any(STATE.get(st) == "unknown" for _, st in gates):
            out.append('<p class="gates-note">标「未查」的那几格是本报告没覆盖的，'
                       '<strong>不等于没问题</strong>——正文里写了去哪儿查。</p>')
        else:
            out.append('<p class="gates-note">四道闸逐格都给了状态，一格都没省。</p>')
    return "\n".join(out)


def build_toc(html: str) -> tuple:
    """给每个 h2 挂 id，生成左侧目录脊。"""
    items = []

    def tag(m):
        n = len(items) + 1
        text = re.sub(r"<[^>]+>", "", m.group(1))
        items.append((f"s{n}", text))
        return (f'<h2 id="s{n}"><a class="anchor" href="#s{n}">'
                f'{m.group(1)}</a></h2>')

    html = re.sub(r"<h2>(.*?)</h2>", tag, html, flags=re.DOTALL)
    if len(items) < 3:
        return html, ""
    links = "".join(f'<a href="#{i}">{html_escape(t)}</a>' for i, t in items)
    return html, f'<nav class="toc" id="toc"><p class="toc-h">本报告</p>{links}</nav>'


def _inline(t: str) -> str:
    """行内标记。**先转义再替换**，否则报告里的 < > & 会被当成标签。"""
    t = html_escape(t)
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
            if tag == "todo":
                # 「下一步」是这份报告里唯一要用户动手的一节，
                # 做成能勾掉的清单，比一段话更容易真的被执行。
                out.append('<ul class="todo">' + "".join(
                    f'<li><input type="checkbox"{" checked" if c else ""}>'
                    f'<span class="t-x">{_inline(x)}</span></li>' for c, x in items
                ) + "</ul>")
                lst = None
                return
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

        m = re.match(r"^[-*+]\s+\[([ xX])\]\s+(.*)$", s)
        if m:
            flush_p()
            if lst and lst[0] != "todo":
                flush_list()
            lst = ("todo", (lst[1] if lst else []) + [(m.group(1).lower() == "x", m.group(2))], 1)
            continue
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


def has_resources(ws, project: str) -> bool:
    """这个项目的「我有」是不是不空，而且没选 off。

    GT network 的门槛就是手上有能拿出来换的东西。页脚那句只给过门槛的人看——
    「我有」空着的人属于还在学怎么想的那一拨，给他看一句「加入 network」，
    是在指一扇他进不去的门。直接读资源档案，不 import resources.py：
    这个脚本要能单独拷走用。
    """
    f = archive_root(ws) / "资源.md"
    if not f.is_file():
        return False
    text = f.read_text(encoding="utf-8")
    if re.search(rf"^对接\.{re.escape(project)}:\s*off\s*$", text, re.MULTILINE):
        return False
    sec = re.search(rf"^## {re.escape(project)}\s*\n(.*?)(?=^## |\Z)", text,
                    re.MULTILINE | re.DOTALL)
    if not sec:
        return False
    have = re.search(r"^### 我有\s*\n(.*?)(?=^###? |\Z)", sec.group(1),
                     re.MULTILINE | re.DOTALL)
    return bool(have and re.search(r"^- 类型:", have.group(1), re.MULTILINE))


# 放名字不放链接：报告会被转发，落在谁手里、在哪个平台打开都不知道，
# 带链接的东西在抖音、微信里都可能被判成引流。
NETWORK_LINE = ("<strong>GT network</strong>：手上有资源或技能想交换的创业者，"
                "用 gt-venture 跑完诊断、生成对接卡加入。")


def extract_report(text: str) -> str:
    """取出 `## 报告` 到文件末尾，并把 demote 过的标题还原两级。"""
    m = re.search(r"^## 报告\s*\n(.*)\Z", text, re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    body = m.group(1).strip()
    return re.sub(r"^##(#{1,4})(?= )", r"\1", body, flags=re.MULTILINE)


def render(title: str, report_md: str, project: str, mode: str = "诊断",
           network: bool = False) -> str:
    verdict, gates, answered, rest = parse_overview(report_md)

    # H1 和紧随其后的日期行从正文里摘出来单独排，剩下的走通用渲染
    head = ""
    m = re.match(r"\s*#\s+(.+?)\n(?:\s*(日期[^\n]*)\n)?", rest)
    if m:
        head = m.group(2) or ""
        rest = rest[m.end():]

    body = md_to_html(rest)
    body, toc = build_toc(body)
    body = apply_glossary(body)

    top = ""
    if verdict:
        top = render_overview(verdict, gates, answered)

    # 报告的日期行常常自己就写了「已答 N/6」，概览块里也有一份——
    # 两边都拼上去，副标题会出现两次已答。
    bits = [head.strip()]
    if answered and "已答" not in head:
        bits.append(f"已答 {answered}")
    sub = " ｜ ".join(x for x in bits if x)
    kind, foot = CHROME.get(mode, CHROME["诊断"])
    drawer = ('<button id="menuBtn" aria-label="打开目录">☰ 目录</button>'
              '<div id="scrim"></div>') if toc else ""
    today = _dt.date.today().isoformat()
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html_escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="layout">
{toc}
<main class="doc">
<p class="eyebrow">gt-venture · {kind}　|　{html_escape(project)}　|　导出于 {today}</p>
<h1>{html_escape(title)}</h1>
{f'<p class="sub">{_inline(sub)}</p>' if sub else ''}
{top}
{body}
<div class="foot">
由 <strong>gt-venture · {kind}</strong> 生成。{foot}
{f'<p class="net">{NETWORK_LINE}</p>' if network else ''}
</div>
</main>
</div>
{drawer}
{JS}
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

    raw = src.read_text(encoding="utf-8")
    mm = re.search(r"^模式:\s*(\S+)\s*$", raw, re.MULTILINE)
    mode = mm.group(1) if mm and mm.group(1) in CHROME else "诊断"

    title = f"诊断：{args.project}"
    m = re.search(r"^#\s+(.+)$", report, re.MULTILINE)
    if m:
        title = m.group(1).strip()

    out = args.out or src.with_suffix(".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    network = has_resources(ws, args.project)
    out.write_text(render(title, report, args.project, mode, network), encoding="utf-8")
    print(f"报告已导出：{out}")
    print("这是一个单文件 HTML，可以直接发给别人，断网也能打开。")
    print("⚠️  正文照样要发进对话——宿主不一定让用户拿得到生成的文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

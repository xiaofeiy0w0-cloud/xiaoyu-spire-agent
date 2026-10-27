"""根据当前局面，从尖塔手册里抽出相关的那几行，附在 st.py 的 state 输出后面。

为什么要它
─────────
2026-10-25 晚上我打了一整局，用户全程看着。**手册 4 万字，我死后才打开。**
其中三处错的地方，手册里全都写对了：
  · 路线要倒着看（我走了「精英→精英→普通怪」，中间没火堆）
  · 乐加维林固定睡 3 回合、被打醒会晕（我说成「不打它就多睡」）
  · 精英之后要能接上火堆

**问题不是「我没记住」，是我没法靠「记住要查」来解决** —— 三次错都出在我自认为记得的时候。
所以修法不能是「下次记得翻手册」，得是：**我调 state 的那一刻，规矩自己贴出来。**

设计
────
不做新数据源，直接解析现有的 .md —— **没有第二份真相，就不会漂**。
  1. 战斗：拿怪物名去匹配手册的标题或文件名，命中就贴那一节的前几行
  2. 地图：候选节点里有精英/Boss，就贴那两条规矩
  3. 血 < 50%：永远贴「血量纪律」
一共最多贴 ~16 行，多了我自己也不会看。

用法
────
    from manual_hint import hint
    print(hint(state_dict))

单独试：
    python -X utf8 research/manual_hint.py
"""
import json
import os
import re
import sys
from pathlib import Path

# 手册目录：默认 <本文件所在目录>/manual（包自带）；也可用环境变量覆盖
MANUAL_DIR = Path(os.environ.get("STS_MANUAL_DIR")
                  or (Path(__file__).resolve().parent / "manual"))

# 游戏里显示的名字 → 手册里用的名字（两边不一致时在这里补）
#
# ★★ 2026-10-27 00:18 修：**此前整个提示功能是哑的。**
#   根因：游戏是英文版，怪物名是 `Lagavulin` / `Cultist` / `Gremlin Nob`，
#   而这张表原来只写了中文键。于是：
#     name = ALIAS.get(_norm("Lagavulin"), _norm("Lagavulin"))  → 还是 "Lagavulin"
#     _find(sections, "Lagavulin")  → 去中文手册里找英文，永远 0 命中
#   更隐蔽的一层：手册标题写作「乐加维林（Lagavulin）」，而 `_norm()` 会**把括号内容删掉**，
#   所以就算手册里写着英文也匹配不到 —— 只剩「乐加维林」。
#   实测（00:18）：打 Lagavulin 时 hint() 返回空字符串，手册里三处打法一条都没贴出来。
#   这解释了 2026-10-25 那句「手册 4 万字，我死后才打开」——
#   不是我不看，是**它从来没被贴出来过**。
#   ⇒ 英文名必须在这张表里显式映射到中文。
ALIAS = {
    # — 第一幕精英 —
    "Lagavulin": "乐加维林",
    "Gremlin Nob": "小鬼贵族",
    "地精大块头": "小鬼贵族",
    "大块头": "小鬼贵族",
    # — 第一幕 Boss / 常见怪 —
    "The Guardian": "守护者",
    "Hexaghost": "六火亡魂",
    "Slime Boss": "史莱姆老大",
    "Jaw Worm": "大颚虫",
    "Cultist": "邪教徒",
    "Looter": "掠夺者",
    "Slaver": "奴隶主",
    "Chosen": "被拣选者",
    # — 第二幕 —
    "Book of Stabbing": "刺书",
    "Bronze Automaton": "青铜自动机",
    "The Champ": "第一勇士",
    # — 中文名兜底（界面切中文时也认） —
    "乐加维林": "乐加维林",
    "拉加维林": "乐加维林",
    "哨卫": "哨卫",
    "哨兵": "哨卫",
    "精灵贵族": "小鬼贵族",
    "乐加维林(大)": "乐加维林",
}

# 贴多少行
MAX_LINES_PER_SECTION = 5
MAX_SECTIONS = 3

_HEAD = re.compile(r"^#{2,4} ")
_BRACKET = re.compile(r"[（(].*?[）)]")


def _norm(s):
    """去掉「（大）（小）」这类括号、空白和标点，方便匹配。"""
    s = _BRACKET.sub("", s or "")
    return re.sub(r"[\s·—\-]+", "", s)


def load_sections():
    """把所有 .md 拆成 [(文件名, 标题, [正文行])]。"""
    out = []
    if not MANUAL_DIR.is_dir():
        return out
    for f in sorted(MANUAL_DIR.glob("*.md")):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        cur_head, cur_body = None, []
        for ln in lines:
            if _HEAD.match(ln):
                if cur_head is not None or cur_body:
                    out.append((f.name, cur_head or "", cur_body))
                cur_head, cur_body = ln.lstrip("#"), []
            else:
                cur_body.append(ln)
        out.append((f.name, cur_head or "", cur_body))
    return out


def _clean(body, n=MAX_LINES_PER_SECTION):
    """从一节正文里挑出有内容的 n 行。"""
    picked = []
    for ln in body:
        t = ln.strip()
        if not t or t.startswith("|") or t.startswith("```"):
            continue
        picked.append(t)
        if len(picked) >= n:
            break
    return picked


def _find(sections, name):
    """挑出最该贴的那一节。

    ★ 2026-10-25 修：原来「标题命中就赢」，结果「哨卫」命中了一份文件里
      「- 见 _raw/哨卫.txt」这种目录行，贴出来是个空壳，而真正的
      `06-哨卫精英战.md` 因为标题里没写「哨卫」被跳过。
      改成打分：
        · 文件名里有关键词 → +10（这个文件整篇就是讲它的）
        · 标题里有关键词   → +5
        · 正文越长越优先（空壳节自然排后面）
      同一份文件命中时，取**最长**的那一节 —— 短节多半是目录/索引。
    """
    key = _norm(name)
    if not key:
        return None
    best, best_score = None, 0.0
    for fname, head, body in sections:
        score = 0.0
        if key in _norm(fname):
            score += 10
        if head and key in _norm(head):
            score += 5
        if score == 0:
            continue
        text = " ".join(_clean(body, 40))
        # 目录行（「- 见 _raw/xxx.txt」）不是内容，直接跳过
        if "_raw/" in text and len(text) < 60:
            continue
        score += min(len(text), 400) * 0.02
        # 没标题的序言（文件开头）排在正式小节后面
        if not head:
            score -= 3
        if score > best_score:
            best, best_score = (fname, head, body), score
    return best


def hint(state):
    """state = st.py 读到的那份 dict。返回要贴在末尾的字符串（可能为空）。"""
    gs = (state or {}).get("game_state") or {}
    if not gs:
        return ""
    sections = load_sections()
    if not sections:
        return ""

    blocks = []
    seen = set()

    def add(label, fname, head, body):
        if len(blocks) >= MAX_SECTIONS:
            return
        k = (fname, head)
        if k in seen:
            return
        lines = _clean(body)
        if not lines:
            return
        seen.add(k)
        blocks.append(f"  ▸ 手册·{label}（{fname}）\n" + "\n".join("    " + x for x in lines))

    # 1. 战斗：按怪物名
    ss = gs.get("screen_state") or {}
    monsters = (gs.get("combat_state") or {}).get("monsters") or []
    for m in monsters:
        raw = m.get("name") or ""
        if (m.get("current_hp") or 0) <= 0:
            continue
        # ★★ 2026-10-27 08:08 修一个从 10-25 就存在的 bug：
        #   ALIAS 的键是**带空格**的英文名（"Slime Boss" / "Gremlin Nob" / "Jaw Worm" /
        #   "The Guardian" / "Book of Stabbing" / "Bronze Automaton" / "The Champ"），
        #   但这里用 _norm(raw) 去查 —— _norm 会把空格删掉，变成 "SlimeBoss" ——**永远查不到**。
        #   实测：昨晚加的 20+ 条英文映射，带空格的那一半从写下去那天就是死的。
        #   今晚打 Slime Boss 时 hint() 返回空就是它 —— 而手册里明明有 18 行打法。
        #   ⇒ 先试原名（带空格），再试 norm 名，最后才退回 norm 名。
        name = ALIAS.get(raw) or ALIAS.get(_norm(raw)) or _norm(raw)
        hit = _find(sections, name)
        if hit:
            add(name, *hit)

    # 2. 地图：候选节点
    if (gs.get("screen_type") or "").upper() == "MAP" or ss.get("next_nodes"):
        syms = {n.get("symbol") for n in (ss.get("next_nodes") or [])}
        if "B" in syms:
            hit = _find(sections, "进Boss前")
            if hit:
                add("Boss 前", *hit)
        elif "E" in syms:
            hit = _find(sections, "小鬼贵族第一幕精英") or _find(sections, "精英")
            if hit:
                add("精英", *hit)

    # 3. 血低于一半
    hp = gs.get("current_hp")
    mx = gs.get("max_hp") or 0
    if isinstance(hp, int) and mx and hp * 2 < mx:
        hit = _find(sections, "血量纪律")
        if hit:
            add("血量纪律", *hit)

    if not blocks:
        return ""
    # ★ 2026-10-25 修：同一个提示只贴一次（局面没变就不再重复）。
    #   原来每调一次 state 都贴，结果「血量纪律」那五行把战斗数据挤出屏幕，
    #   我反而看不见敌人在干嘛 —— 提示把正文挤没了，就是负价值。
    keys = "|".join(f"{f}:{h}" for (f, h) in sorted(seen))
    try:
        seen_file = Path(r"C:\sts_bridge\hint_seen.txt")
        seen = seen_file.read_text(encoding="utf-8").strip() if seen_file.exists() else ""
        if seen == keys:
            return ""
        seen_file.write_text(keys, encoding="utf-8")
    except OSError:
        pass
    return "  ── 手册提示（自动查的，不是我记的）──\n" + "\n".join(blocks)


if __name__ == "__main__":
    p = Path(r"C:\sts_bridge\state.json")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"读不到 state.json: {e}")
        sys.exit(1)
    out = hint(d)
    print(out or "（当前局面没有匹配到手册条目）")

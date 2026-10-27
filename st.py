"""杀戮尖塔控制台：发一条命令，回命令【之后】的状态。

用法： st.py "play 2" [超时秒数]
      st.py ""        只读状态不发命令

2026-10-24 重写（用户要求「玩得不卡」）。
旧版发完命令只 sleep 固定秒数，读到的还是命令【前】的状态 ——
于是每出一张牌都要调两次，而且第二次如果照旧序号出牌就会打错。
现在改成：写 cmd.txt 后**轮询 state.json 的 mtime**，等它真的变了再读。
一次调用 = 命令 + 结果。

另外自带三个自动处理：
  · GRID（Just Lucky 的 Scry）→ 自动 confirm
  · 假死（state.json 长时间不动）→ 明确报 FROZEN，不静默卡住
  · 药水栏满导致奖励界面死锁 → 明确报 POTION_FULL 并给一行解法
"""
import json
import re
import sys
import time
import os
from pathlib import Path

USAGE = Path.home() / ".dsh" / ".dshw-usage.json"
FALLBACK = 0.0

# ★★ 2026-10-27 10:29：路径改成「环境变量优先，默认值不变」。
#   原因：要把这套东西发给别人家的小鱼（B站观众点题），
#   而原来这里写死 `C:\sts_bridge` 和 `E:\小肥鱼工作间\research` ——
#   别人拷过去跑不起来，只能改代码，那就是「教程没交代清楚」。
#   现在：设了 STS_BRIDGE_DIR / STS_WORK_DIR 就用设的，没设还用我原来的路径。
#   ⇒ 不设也能跑：桥目录默认 C:\sts_bridge，工作目录默认 <桥目录>/work。
BRIDGE_DIR = Path(os.environ.get("STS_BRIDGE_DIR") or r"C:\sts_bridge")
WORK_DIR = Path(os.environ.get("STS_WORK_DIR") or (BRIDGE_DIR / "work"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

CMD = BRIDGE_DIR / "cmd.txt"
ST = BRIDGE_DIR / "state.json"
DIGEST = WORK_DIR / "digest.txt"


def budget():
    """返回 (今日已花, 约剩)。读 usage 文件的 todayUsage / lastBalance。"""
    try:
        u = json.loads(USAGE.read_text(encoding="utf-8"))
        tot = u.get("todayUsage")
        bal = u.get("lastBalance")
        if tot is None or bal is None:
            day = u.get("date")
            tot = sum(e["cost"] for e in (u.get("events") or []) if e.get("day") == day)
            bal = FALLBACK - tot if bal is None else bal
        return float(tot), float(bal)
    except Exception:
        return 0.0, FALLBACK


def stamp():
    try:
        return ST.stat().st_mtime_ns
    except OSError:
        return 0


def read_state(retries=6):
    """读 state.json。★ 必须重试 —— mod 是「边写边覆盖」的，

    我经常读到写了一半的 JSON，于是手牌读成空、牌名匹配不上，
    然后 `play:Flurry of Blows` 报「手上没有这张牌」，其实牌就在手里。
    2026-10-24 实测：连出多张时这个误报出现率不低，每次都白丢一张牌。
    """
    last = None
    # ★★ 2026-10-27 03:42：**state.json 陈旧检测。**
    #   今晚我对着一个**一小时前**的 state.json 选了三次路，还以为游戏卡住了（两次 FROZEN）。
    #   真相：游戏进程 01:38 就没了，而 st.py 打印的时间戳是它自己的，不是文件的时间。
    #   ⇒ 又是那个形状：**旧数据和活数据长得一样。**
    #   所以每次读之前先看文件几秒前更新过；太旧就在返回的 dict 上打戳，
    #   让 digest() 把它印出来——我一眼就能看到「这份是一小时前的」。
    try:
        _age = time.time() - ST.stat().st_mtime
    except OSError:
        _age = None
    for i in range(retries):
        try:
            d = json.loads(ST.read_text(encoding="utf-8"))
        except Exception as e:
            last = e
            time.sleep(0.05 + 0.03 * i)
            continue
        if isinstance(d, dict):
            # 陈旧标记：>180 秒没更新 = 游戏多半已经没了
            if _age is not None and _age > 180:
                d["__stale_secs__"] = int(_age)
        if isinstance(d, dict) and isinstance(d.get("game_state"), dict):
            return d
        # ★ 2026-10-25 修：主菜单也是**合法状态**。
        #   主菜单的 state.json 长这样（只有 87 字节）：
        #     {"available_commands": ["start","state"], "ready_for_command": true, "in_game": false}
        #   它**没有 game_state** —— 旧逻辑因此判定「读不到 state.json」，
        #   于是 st.py 在主菜单一句 start 都发不出去（2026-10-25 19:42 实测）。
        #   菜单态没有 error 字段，直接认。
        if isinstance(d, dict) and d.get("in_game") is False and not d.get("error"):
            return d
        # ★ 2026-10-24 修：错误回执也要收下（报错信息有用），
        #   但**不能立刻返回** —— 否则 _resolve 拿到一个没有 game_state 的字典，
        #   手牌读成空，报「手上没有这张牌」，牌其实在手里。
        #   先继续重试等一个有效的 game_state，实在没有才退回错误回执。
        if isinstance(d, dict) and d.get("error"):
            last = d
        time.sleep(0.05 + 0.03 * i)
    # ★ 2026-10-27 修：只返回 dict。
    #   异常对象绝不能往下传 —— main() 会 d.get()，然后
    #   AttributeError: 'JSONDecodeError' object has no attribute 'get'。
    #   触发场景很常见：游戏正在写 state.json（写了一半），或者第一次跑还没生成。
    return last if isinstance(last, dict) else None


def _energy_left():
    """战斗里还有能量、手上还有打得出的牌 → 返回 True。

    2026-10-25 用户指出：我算完「这回合能打多少」就 end，牌没出完。
    这一条只做一件事 —— 判断该不该拦住我。
    """
    try:
        d = read_state()
        if not isinstance(d, dict):
            return False
        cs = (d.get("game_state") or {}).get("combat_state") or {}
        if not cs:
            return False
        me = cs.get("player") or {}
        energy = me.get("energy")
        if not isinstance(energy, int) or energy <= 0:
            return False
        hand = cs.get("hand") or []
        # 有 0 费的、或费用 ≤ 剩余能量的，就算「还打得出去」。
        # ★ 2026-10-25 用户追问补的：X 费牌（旋风斩那种）费用在数据里是 -1，
        #   只要有 1 点能量就打得出去；而「伤口 / 眩晕」这类状态牌是 -2 / None，
        #   一张也打不出。两者都落在「负数」里，必须分开认，不能一起跳过。
        for c in hand:
            cost = c.get("cost")
            if not isinstance(cost, int):
                continue
            if cost == -1:          # X 费：有能量就能打
                return True
            if 0 <= cost <= energy:
                return True
        return False
    except Exception:
        return False


def send_and_wait(cmd, timeout=25.0):
    """写命令并等状态真的刷新。返回 (ok, seconds, frozen)."""
    before = stamp()
    t0 = time.time()
    try:
        CMD.write_text(cmd + "\n", encoding="utf-8")
    except OSError as e:
        return False, 0.0, f"写不进 cmd.txt: {e}"
    while time.time() - t0 < timeout:
        if stamp() != before:
            time.sleep(0.12)          # 等这一版落盘
            return True, time.time() - t0, None
        time.sleep(0.04)
    # 超时：要么命令没被吃掉，要么 mod 假死
    return False, time.time() - t0, "FROZEN"


# ★★★ 2026-10-24 19:06 补的一个大洞：**CommunicationMod 不报告观者的姿态。**
#   在 20KB 的 state.json 里搜 "Calm"/"Wrath"/"Divinity"/"stance"，出现次数全是 0。
#   也就是说我打了 22 局，**从来不知道自己在愤怒、平静还是神格**。
#   后果：「进愤怒之后有没有退出来」「真言满 10 有没有进神格」全靠猜。
#   第 22 局我算着「真言 5 + 敬拜 5 = 10 应该进神格了」，
#   结果状态栏里什么都没有，还以为是自己算错了 —— 其实是工具根本看不见。
#
#   补救：自己跟踪。每打出一张改变姿态的牌，就记到一个小文件里，下次 state 时读出来。
#   这一定是**推断值**，不是游戏给的真相 —— 所以标注成「推断」，
#   并且万一和实际情况冲突，以截图为准（见 60-资料/尖塔/08-截图核对法.md）。
STANCE_FILE = WORK_DIR / ".stance.json"

# 打进这些牌 → 进入愤怒
_TO_WRATH = {"Eruption", "Crescendo", "Simmering Fury", "Tantrum", "Indignation"}
# 打进这些牌 → 进入平静
_TO_CALM = {"Vigilance", "Fear No Evil", "Tranquility", "Inner Peace",
            "Meditate", "Like Water"}
# 打进这些牌 → 退出姿态
_TO_NONE = {"Empty Body", "Empty Fist", "Empty Mind", "Empty Fist+"}
# 这些牌给真言
_MANTRA = {"Worship": 5, "Prostrate": 2, "Pray": 3, "Devotion": 0}


def _stance_load():
    try:
        j = json.loads(STANCE_FILE.read_text(encoding="utf-8"))
        return str(j.get("stance") or "None"), int(j.get("mantra") or 0)
    except (OSError, ValueError, TypeError):
        return "None", 0


def _stance_reset_if_new_combat(fight, turn=None):
    """换战斗就把姿态和真言归零。

    ★ 2026-10-24 第二次修：原来只用「回合数回到 1」，结果两场战斗都停在第 1 回合时
      （比如上一场第 1 回合就把它打死了）判不出来，上一场的「愤怒」会带到下一场。
      现在改用 **对面是谁** 当战斗指纹：怪物名字拼起来。换了一批怪就是新战斗。

    每次读状态都会把当前指纹存回去，拿「上一次的指纹」当锚点。
    """
    if not fight:
        return
    try:
        j = json.loads(STANCE_FILE.read_text(encoding="utf-8"))
        old = j.get("fight")
        if old != fight:
            _stance_save("None", 0, turn, fight)
    except (OSError, ValueError, TypeError):
        _stance_save("None", 0, turn, fight)


def _stance_save(stance, mantra, turn=None, fight=None):
    try:
        j = {}
        if turn is None or fight is None:
            try:
                j = json.loads(STANCE_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                j = {}
        STANCE_FILE.write_text(
            json.dumps({"stance": stance, "mantra": mantra,
                        "turn": turn if turn is not None else j.get("turn"),
                        "fight": fight if fight is not None else j.get("fight")},
                       ensure_ascii=False),
            encoding="utf-8")
    except OSError:
        pass


def track_stance(card):
    """打出一张牌之后调用：按牌名更新推断的姿态与真言。

    返回一句人话（要不要打印由调用方决定）。
    """
    if not card:
        return None
    stance, mantra = _stance_load()
    base = card.split("+")[0].strip()
    msg = None

    # 先算真言 —— 真言满 10 立刻进神格，优先级最高
    gain = _MANTRA.get(base)
    if gain:
        mantra += gain
        msg = f"真言 {mantra}/10"
        if mantra >= 10:
            stance, mantra = "Divinity", 0
            msg = "真言满10 → **进神格**（3倍伤害 +3能量，下回合自动退）"

    if base in _TO_WRATH:
        was = stance
        stance = "Wrath"
        msg = (msg + " · " if msg else "") + "→ 愤怒（自己受伤也×2）"
        if was == "Calm":
            msg += "，退出平静 +2 能量"
    elif base in _TO_CALM:
        was = stance
        stance = "Calm"
        msg = (msg + " · " if msg else "") + "→ 平静（下次退出时 +2 能量）"
        if was == "Wrath":
            msg += "，退出愤怒"
    elif base in _TO_NONE:
        if stance != "None":
            msg = (msg + " · " if msg else "") + f"退出{stance}"
        stance = "None"

    if msg:
        _stance_save(stance, mantra)
    return msg


# ★ 2026-10-26 01:36 用户提的需求（research/需求-结算预演.md）：
#   00:45 那局 Hexaghost，我 HP 3/80、手牌 2 张 Burn+、格挡 7，对面 ATTACK_DEBUFF 6。
#   手册里「血 ≤ 手上 Burn 张数×2 就别结束回合」我背得出，state 里数字也全都在，
#   但我是**看见了数字、没把它乘起来**，end 之后死了。
#   所以缺的不是知识，是一个不依赖我自觉的机关：
#   每次 state 在战斗里，都替我把「这回合结束会剩多少血」算出来；end 之前再算一次，
#   预测会死就直接拦住 —— 这一下按下去是不可撤回的。
BURN_HURT = 2     # Burn / Burn+ 都是 2 点（2026-10-26 实测，升级版不是 4）

# 需求里说 damage 是 "6x1" / "5x2" 这种字符串；也兼容别的 mod 版本给的
# move_adjusted_damage + move_hits 两个数字。解析失败一律当未知，绝不猜。
_DAMAGE_RE = re.compile(r"^\s*(-?\d+)\s*[xX×*]\s*(-?\d+)\s*$")


def _burn_cards(hand):
    """数手上 Burn（含 Burn+）。返回 (张数, 牌名列表)。"""
    n, names = 0, []
    for c in hand or []:
        if not isinstance(c, dict):
            continue
        key = str(c.get("id") or c.get("name") or "").strip()
        if key.lower().startswith("burn"):
            n += 1
            names.append(str(c.get("name") or key))
    return n, names


def _as_int(v):
    """JSON 里的数字转 int。bool / 非数字 / NaN 都返回 None，绝不抛异常。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        return int(v)
    except (ValueError, OverflowError):
        return None


def _monster_damage(m):
    """读一只怪这回合的伤害，返回 (总伤害, 显示用单次, 显示用次数) 或 None。

    "5x2" 就是 5×2 = 10；"3x6" = 18。读不到、-1、None 都算未知 ——
    未知不能当 0，否则预演会假装安全。
    """
    raw = m.get("damage")
    if isinstance(raw, str):
        mt = _DAMAGE_RE.match(raw)
        try:
            if mt:
                a, b = int(mt.group(1)), int(mt.group(2))
                if a >= 0 and b >= 0:
                    return a * b, a, b
            elif raw.strip().lstrip("+-").isdigit():
                v = int(raw)
                if v >= 0:
                    return v, v, 1
        except (ValueError, OverflowError):
            pass
    else:
        v = _as_int(raw)
        if v is not None and v >= 0:
            return v, v, 1
    # 退路：老字段 move_adjusted_damage × move_hits（现有 dmg=6x1 那行用的就是它们）
    dmg = _as_int(m.get("move_adjusted_damage"))
    if dmg is not None and dmg >= 0:
        h = _as_int(m.get("move_hits"))
        h = max(1, h) if h is not None else 1
        return dmg * h, dmg, h
    return None


def settle_preview(d):
    """结算预演：这回合按下去，我会掉多少血、会不会死。

    参数：d = read_state() 读到的整份 dict（就是 state.json 的内容）。
          也接受直接传 game_state —— 单独试 / 单元测试时省得再包一层。
    返回：dict；不在战斗画面返回 None。
        text      要打印的多行文本
        hp        当前 HP
        predicted 回合末预测 HP
        damage    已知的对面总伤害（未知的那部分不算在内）
        block     当前格挡
        burn      手上 Burn 张数
        will_die  预测 HP <= 0
        uncertain 对面有意图/伤害读不出来（只算了已知部分，别当安全）

    ★ 只在 screen_type == "NONE" 且有 combat_state 时给结果 ——
      地图/奖励/火堆/商店不该出现这一行。
    """
    if not isinstance(d, dict):
        return None
    gs = d.get("game_state")
    if not isinstance(gs, dict):
        # 兼容直接传 game_state（它一定带 screen_type 或 combat_state）
        if "screen_type" in d or isinstance(d.get("combat_state"), dict):
            gs = d
        else:
            return None
    if str(gs.get("screen_type") or "").upper() != "NONE":
        return None
    cs = gs.get("combat_state")
    if not isinstance(cs, dict) or not cs:
        return None
    me = cs.get("player") or {}
    if not isinstance(me, dict):
        me = {}
    hp = _as_int(me.get("current_hp"))
    if hp is None:
        return {"text": "★ 结算预演：读不到 HP，算不了（别当安全）",
                "hp": None, "predicted": None, "damage": 0, "block": 0,
                "burn": 0, "will_die": False, "uncertain": True}
    block = _as_int(me.get("block"))
    if block is None or block < 0:
        block = 0
    hand = cs.get("hand") or []
    burn_n, burn_names = _burn_cards(hand)
    burn_total = burn_n * BURN_HURT

    parts, known_damage = [], 0
    alive = known_intents = known_attacks = 0
    uncertain = unknown_intent = unknown_damage = False
    for m in cs.get("monsters") or []:
        if not isinstance(m, dict):
            continue
        # 死了的怪（尸体还占槽位）不算伤害
        m_hp = _as_int(m.get("current_hp"))
        if m.get("is_gone") or (m_hp is not None and m_hp <= 0):
            continue
        alive += 1
        intent = str(m.get("intent") or "UNKNOWN").strip().upper()
        if intent.startswith("ATTACK"):
            parsed = _monster_damage(m)
            if parsed is None:
                uncertain = unknown_damage = True
                parts.append(f"{intent} 伤害未知")
                continue
            total, a, b = parsed
            known_damage += total
            known_attacks += 1
            known_intents += 1
            # 6×1 这种单次的不用再写 =6；5×2 才是 10，写出来省得我再乘
            parts.append(f"{intent} {a}×{b}" + (f"={total}" if b > 1 else ""))
        elif intent in ("", "UNKNOWN", "NONE", "NONE_INTENT", "UNKNOWN_INTENT"):
            uncertain = unknown_intent = True
            parts.append("UNKNOWN")
        else:
            known_intents += 1
            parts.append(f"{intent}（不打人）")

    if alive == 0:
        foe = "对面没有活着的怪"
    elif uncertain and known_intents == 0:
        # 读不到就明说只算了 Burn，别让我以为 0 伤害。
        # 分开写：intent=UNKNOWN 和攻击意图但 damage=-1 是两回事。
        if unknown_intent and not unknown_damage:
            foe = "读不到敌人意图（intent=UNKNOWN），只算 Burn"
        elif unknown_damage and not unknown_intent:
            foe = "读不到对面伤害（damage=-1），只算 Burn"
        else:
            foe = "对面意图/伤害读不到，只算 Burn"
    else:
        foe = "对面 " + (" · ".join(parts) or "意图未知")

    if burn_n:
        label = burn_names[0] if burn_names and len(set(burn_names)) == 1 else "Burn"
        burn_bit = f"手上 {label} {burn_n} 张(-{burn_total})"
    else:
        burn_bit = "手上 Burn 0 张"
    line1 = f"★ 结算预演：{foe} · 我格挡 {block} · {burn_bit}"

    hurt = max(0, known_damage - block)
    if known_attacks:
        expr = f"{hp} - max(0, {known_damage}-{block}) - {burn_total}"
    elif uncertain and known_intents == 0:
        # 伤害未知时算不了 max(...)，就只把能确定的 Burn 自伤扣掉
        expr = f"{hp} - {burn_total}"
    else:
        expr = f"{hp} - 0 - {burn_total}"
    predicted = hp - hurt - burn_total

    if predicted <= 0:
        verdict = f"{predicted} ☠ 会死 —— 先别结束回合"
    elif uncertain:
        verdict = "⚠ 对面伤害未知，别当安全"
    else:
        verdict = f"✅ 能活（剩 {predicted}）"
    text = line1 + "\n" + f"   {expr} = {predicted}" + "\n" + f"   {verdict}"
    return {"text": text, "hp": hp, "predicted": predicted,
            "damage": known_damage, "block": block, "burn": burn_n,
            "will_die": predicted <= 0, "uncertain": uncertain}


def _hand_line(d):
    """手上有什么（end 被拦时贴出来，省得我再单独调一次 state）。"""
    cs = (((d or {}).get("game_state") or {}).get("combat_state") or {})
    if not isinstance(cs, dict):
        return "(读不到手牌)"
    out = []
    for i, c in enumerate(cs.get("hand") or []):
        if isinstance(c, dict):
            out.append(f"{i + 1}) {c.get('name') or c.get('id') or '?'}")
    return ", ".join(out) or "(空)"


def digest(d, preview=True):
    gs = d.get("game_state") or {}
    out = [f"[{time.strftime('%H:%M:%S')}] screen={gs.get('screen_type')} in_game={d.get('in_game')} "
           f"floor={gs.get('floor')} cmds={','.join(d.get('available_commands') or [])}"]
    if d.get("error"):
        out.append("ERROR: " + str(d["error"]))

    # ★★ 2026-10-27 08:38 加：SHOP_ROOM 是「店门口」，里面是空的。
    #   商品（cards/relics/potions/purge_cost）只在 SHOP_SCREEN 里才有。
    #   今晚我在店门口看不到任何数据，只好截图 + 猜坐标点鼠标，
    #   结果误买了 Reckless Charge（81 金，往抽牌堆塞 Dazed），污染了整局牌组。
    #   ⇒ 这里直接把「还没进店」说出来，别让我再猜。
    _scr = str(gs.get("screen_type") or "").upper()
    if _scr == "SHOP_ROOM":
        out.append("  ▸ 这是店门口，商品还没加载 —— 先 `choose 0`（shop）进店，")
        out.append("    进店后 screen 变 SHOP_SCREEN，才会出现 cards/relics/potions/purge 价格。")
        out.append("    ★ 进店前不要用 click 坐标买东西：那是瞎猜，会买错。")
    cs = gs.get("combat_state")
    if cs:
        me = cs.get("player") or {}
        out.append(f"HP {me.get('current_hp')}/{me.get('max_hp')} block={me.get('block')} "
                   f"energy={me.get('energy')} turn={cs.get('turn')} "
                   f"抽{len(cs.get('draw_pile') or [])}/弃{len(cs.get('discard_pile') or [])}")
        # ★★ 2026-10-25 用户要求（说了三次，说这是最影响体验的卡点）：
        #   把抽牌堆 / 弃牌堆 / 消耗堆里的**具体牌名**摆出来。
        #   原来只有「抽 9 / 弃 3」这种数字，我算不了下两回合能摸到什么，
        #   每回合都只能走一步看一步 —— 有了这行，可以提前布算。
        def _pile_names(key):
            return [c.get("name") for c in (cs.get(key) or []) if c.get("name")]
        _dr = _pile_names("draw_pile")
        _di = _pile_names("discard_pile")
        _ex = _pile_names("exhaust_pile")
        if _dr:
            out.append("  ▸ 抽牌堆(" + str(len(_dr)) + "): " + ", ".join(_dr))
        if _di:
            out.append("  ▸ 弃牌堆(" + str(len(_di)) + "): " + ", ".join(_di))
        if _ex:
            out.append("  ▸ 已消耗(" + str(len(_ex)) + "): " + ", ".join(_ex))
        # ★ 姿态（推断值）。游戏不报告，只能自己记 —— 见 track_stance 上面的长注释。
        #   愤怒 = 我打人也 X2、我被打也 X2；真身那一栏看不到，只能靠这行。
        _fight = " | ".join(
            str(m.get("name") or "?") for m in (cs.get("monsters") or []))
        _stance_reset_if_new_combat(_fight, cs.get("turn"))
        _stance, _mantra = _stance_load()
        _sn = {"Wrath": "愤怒（我打人×2，**我被打也×2**）",
               "Calm": "平静（退出时 +2 能量）",
               "Divinity": "神格（3倍伤害 +3能量，下回合自动退）"}.get(_stance, "无")
        _tail = f" · 真言 {_mantra}/10" if _mantra else ""
        out.append(f"  ★ 姿态[推断] {_sn}{_tail}")
        # ★★ 2026-10-24 修：**我一直在用错的数字算账。**
        #   第 19 局身上挂着 Frail 3 + Weakened 1，
        #   我按「Strike 6 / Defend 5」算，实际只打出 4 和 3 —— 每次「算好了能挡住」都被打穿。
        #   虚弱 = 伤害 ×0.75，脆弱 = 格挡 ×0.75，易伤 = 受伤 ×1.5。
        #   不把 powers 摆出来，所有算术都是错的。
        pw = me.get("powers") or []
        if pw:
            NEG = {"Weakened": "虚弱(伤害×0.75)", "Frail": "脆弱(格挡×0.75)",
                   "Vulnerable": "易伤(受伤×1.5)", "Entangled": "缠绕(不能出攻击)",
                   "No Draw": "不能抽牌", "Confusion": "混乱", "Hex": "邪咒",
                   "Doubt": "怀疑", "Regret": "悔恨", "Shame": "羞愧"}
            STR = {"Strength": "力量", "Dexterity": "敏捷", "Mantra": "真言",
                   "Artifact": "人工制品", "Ritual": "仪式", "Thorns": "荆棘"}
            bits = []
            for p in pw:
                nm = p.get("name") or ""
                bits.append(f"{NEG.get(nm) or STR.get(nm) or nm} {p.get('amount')}")
            out.append("  ★ 我身上的状态 " + " · ".join(bits))
        for i, c in enumerate(cs.get("hand") or []):
            # CommunicationMod 的 play 是 1 起算的
            out.append(f"  [{i + 1}] {c.get('name')} cost={c.get('cost')} {c.get('type')}"
                       f"{' 目标' if c.get('has_target') else ''}")
        for i, m in enumerate(cs.get("monsters") or []):
            if m.get("is_gone"):
                continue
            out.append(f"  M{i} {m.get('name')} {m.get('current_hp')}/{m.get('max_hp')} "
                       f"block={m.get('block')} intent={m.get('intent')} "
                       f"dmg={m.get('move_adjusted_damage')}x{m.get('move_hits')}")
        # ★ 2026-10-26：结算预演。放在敌人后面，因为要对着刚读完的意图看。
        #   原来的手牌/敌人/药水/遗物一行都不动，只是多这三行。
        if preview:
            _pv = settle_preview(d)
            if _pv:
                out.append(_pv["text"])
        # ★ 2026-10-24 加：always 把药水摆出来。不看就不会喝。
        #   「Potion Slot」是空槽占位，别显示成有药水。
        real = [(i, p) for i, p in enumerate(gs.get("potions") or [])
                if p.get("name") and "Slot" not in (p.get("name") or "")]
        if real:
            out.append("  药水 " + " | ".join(
                f"[{i}] {p.get('name')}{'' if p.get('can_use', True) else '(不可用)'}"
                for i, p in real))
        else:
            out.append("  药水 (空)")
        # ★ 2026-10-24 用户要求：每局打完复盘「是不是有什么图标没利用好」。
        #   根因是 st.py 从来只打印手牌和药水，**不打印遗物** ——
        #   我看不见自己身上有什么图标，自然一个都想不起来用。
        rl = [r.get("name") for r in (gs.get("relics") or []) if r.get("name")]
        if rl:
            out.append("  遗物 " + " · ".join(rl))
        else:
            out.append("  遗物 (无)")
    else:
        ss = gs.get("screen_state") or {}
        if ss:
            out.append("screen_state=" + json.dumps(ss, ensure_ascii=False)[:700])
        ch = gs.get("choice_list") or []
        # ★★ 2026-10-27 09:37 用户要我留 Neow 的原始 options，说「不然我只能猜」。
        #   一查发现根比「没存」更深：**这一段显示只读 choice_list，压根没读 screen_state.options。**
        #   而事件界面的真编号在 options[].choice_index 里 ——
        #   于是我看到的 (0)(1)(2)(3) 是**数组顺序**，游戏用的是**另一套编号**。
        #   43 局开局 Neow：我选 `choose 0`（显示「0 = choose a card to obtain」），
        #   结果拿到的是第 4 项（换 Boss 遗物 Calling Bell）—— 这就是「索引与文本不符」。
        #   ⇒ options 有 choice_index 就按它显示，并标明来源；两套都列出来，别让我再猜。
        opts = (gs.get("screen_state") or {}).get("options") or []
        if opts:
            out.append("  ▸ 事件选项（真编号 choice_index，优先用这个）：")
            for o in opts:
                if not isinstance(o, dict):
                    continue
                ci = o.get("choice_index")
                txt = o.get("text") or o.get("label") or ""
                dis = "（禁用）" if o.get("disabled") else ""
                out.append(f"    <{ci}> {txt}{dis}")
            if ch:
                out.append("  ▸ choice_list（数组顺序，**不一定是游戏编号**）：")
        # ★ 2026-10-24 用户提醒「他们说可以选线路」。
        #   地图上我一直无脑选第 0 个，因为 state 只显示 "(0) x=1" —— 看不出那扇门是什么。
        #   这里把节点类型翻成中文贴到选项前面，岔路才能真的「选」。
        nodes = ss.get("next_nodes") or []
        NODE = {"M": "怪物", "$": "商店", "R": "火堆", "E": "精英",
                "T": "宝箱", "?": "未知", "B": "Boss"}
        if ch:
            for i, c in enumerate(ch):
                if isinstance(c, dict):
                    txt = c.get("name") or c.get("text") or str(c)[:40]
                else:
                    txt = str(c)
                if i < len(nodes) and isinstance(nodes[i], dict):
                    sym = nodes[i].get("symbol")
                    if sym:
                        txt = f"[{NODE.get(sym, sym)}] {txt}"
                out.append(f"  ({i}) {txt}")
    return "\n".join(out)


def screen_of(d):
    return ((d or {}).get("game_state") or {}).get("screen_type")


def _resolve(cmd):
    """把 `play:Foo 0` 翻成当前真实索引。

    ★ 为什么要有这个：出掉一张牌，后面所有牌的位置都往前挪一位。
      我按「play 4 才是 Prostrate」出的牌，结果前面一张 Defend 先出掉，
      play 4 落到了 Dazed 上 —— 该打出的格挡没打出来，13 血直接死。
      写牌名就不会错：每次出牌前重新读一遍手牌，现查现算。
    """
    # ★★★ 2026-10-27 01:07：**禁止按编号出牌。**
    #
    #   为什么加这条：今晚（10-27 00:00-00:52）我 28 次用编号、27 次用牌名。
    #   编号那 28 次里至少 3 次打错：
    #     · 打完 Bash 后 play 3 → 变成了 Defend（Strike 没打出去）
    #     · 打完 Wild Strike 后 play 1 → 变成了另一张牌
    #   牌名那 27 次，零错位。
    #
    #   更关键的是：**这条规矩我写下来过，还是照样用编号。**
    #   因为这个坑从 10-24 就记在手册里（「手牌会左移」），10-25 又踩一次，
    #   10-27 再踩。三次记录，零次改变。
    #
    #   ⇒ 靠「我记得别用编号」没有用。**要让它不可能走那条路。**
    #   ⇒ 所以这里直接拦死。逃生口是 `play!`（真需要硬指定时），
    #     它会保留原样并把警告带回去，让我知道自己在走险路。
    # ★★★ 2026-10-27 10:02：**禁止拿坐标去点「没读到数据」的界面。**
    #
    #   姐姐 10:01 的原话：
    #     「你要做的只有一件：别再拿坐标去打没读到数据的界面。备份归我，判断归你。」
    #
    #   判例（今晚，43 局）：商店 SHOP_ROOM 的 screen_state 是空的
    #   （商品只在 SHOP_SCREEN 才有），我于是去截图、按坐标点鼠标，
    #   误买 Reckless Charge（81 金），往牌组塞了一整局 Dazed。
    #   他的原话：「你不是没拿到商店信息才这么干的吗。」
    #
    #   和下面「编号出牌」完全同一个病：**规矩写下来过，还是照样走那条路。**
    #   ⇒ 所以不写规矩，直接拦死。逃生口是 `click!`（真需要硬点时用，会留警告）。
    if re.match(r"^click\s", cmd) or cmd == "click":
        d0 = read_state() or {}
        gs0 = d0.get("game_state") or {}
        screen0 = gs0.get("screen_type")
        ss0 = gs0.get("screen_state") or {}
        cl0 = gs0.get("choice_list") or []
        if screen0 == "SHOP_ROOM":
            return cmd, ("!! 商店商品还没加载（现在是 SHOP_ROOM，商品只在 SHOP_SCREEN 才出现）。\n"
                         "   先 `choose 0`（shop）进店，再按索引买商品。\n"
                         "   拿坐标点商店 = 瞎猜 —— 今晚就是这样误买了 Reckless Charge（81 金）。")
        if not ss0 and not cl0:
            return cmd, ("!! 这个界面没有可读数据（screen_state 和 choice_list 都是空的）。\n"
                         "   没有数据就没有坐标依据。先 `state` 看清这是什么界面，再决定要不要点。\n"
                         "   真硬要点时写 `click!` —— 那是逃生口。")
    if cmd.startswith("click!"):
        return ("click " + cmd[6:].lstrip(),
                "※ 走了 click! 逃生口（硬点坐标）—— 你现在没有数据依据，确认这是你想要的。")
    if re.match(r"^play\s+\d", cmd):
        return cmd, ("!! 编号出牌已被拦住。手牌每打一张就左移，编号随时指向另一张牌。\n"
                     "   改用 play:牌名（st.py 自己查当前索引）。\n"
                     "   真需要硬指定目标/编号时写 play! —— 那是逃生口，会留警告。")
    if cmd.startswith("play!"):
        cmd = "play " + cmd[5:].lstrip()
        # ★ 注意：不能用 "!!" 开头 —— 调用方第 842 行会把 "!!" 当成拒绝信号直接 break。
        #   逃生口必须真的能放行，所以这里用 "※" 做警告标记。
        return cmd, "※ 走了 play! 逃生口（按编号出牌）—— 确认这是你想要的牌。"
    if not (cmd.startswith("play:") or cmd.startswith("choose:") or cmd.startswith("use:")):
        return cmd, None
    rest = cmd.split(":", 1)[1].strip()
    # ★ 2026-10-24 加 use:药水名 —— 我今晚 16 局一瓶药水没喝过，
    #   死的时候背包里还揣着 Explosive Potion（能直接杀死那只精英）。
    #   原因之一就是「喝药水要自己数槽位序号」这件事太麻烦。
    if cmd.startswith("use:"):
        # ★★ 2026-10-27 09:35 修一个从 10-24 就存在的 bug（用户/姐姐定位到形状）：
        #   下面原来写的是
        #       if p.get("can_use") is False:
        #           return "!! 不能喝（can_use=False）"
        #   而**需要目标的药水，mod 一律报 can_use: false**：
        #       {"name":"Fire Potion", "requires_target": true, "can_use": false}
        #   于是 Fire Potion / Explosive Potion 这些最有用的攻击药水，
        #   四种写法都喝不出去：
        #       use:Fire Potion      → 命中 can_use 判断，直接拒绝
        #       use:Fire Potion 0    → want_p 变成 "fire potion 0"，名字匹配不上
        #       use:1                → 名字匹配不上
        #       potion 1             → 原始命令，缺参数
        #   今晚三次想喝都失败，最后一次是血 2、手上 20 伤、用不出去然后死。
        #   ⇒ 分清两件事：「需要目标」≠「不能喝」。
        parts_p = rest.split()
        tgt = None
        if len(parts_p) >= 2 and parts_p[-1].isdigit():
            tgt = parts_p[-1]
            parts_p = parts_p[:-1]
        want_p = " ".join(parts_p).lower()
        ps = (((read_state() or {}).get("game_state") or {}).get("potions") or [])
        for i, p in enumerate(ps):
            nm = (p.get("name") or "").lower()
            if want_p and (want_p in nm or nm.startswith(want_p)):
                needs_target = bool(p.get("requires_target"))
                if p.get("can_use") is False and not needs_target:
                    return cmd, f"!! {p.get('name')} 现在不能喝（can_use=False 且不需要目标）"
                if needs_target:
                    if tgt is not None:
                        return (f"potion use {i} {tgt}",
                                f"({cmd} → potion use {i} {tgt}，带目标)")
                    # ★★ 2026-10-27 09:37 用户指出两处，都改：
                    #   ① 我原来写死 `potion use {i} 0` —— 那是**写死 0**，不是「活敌人的索引」。
                    #      死怪还占着编号（今晚 M0 死了、活的那只还叫 M1），写死 0 就会打空。
                    #   ② 应该取「**血最少的活敌人**」而不是「第一只」——
                    #      Fire Potion 这种 20 伤的补刀牌，就该打最薄的。
                    monsters = (((read_state() or {}).get("game_state") or {})
                                .get("combat_state") or {}).get("monsters") or []
                    live = [(idx, m) for idx, m in enumerate(monsters)
                            if (m.get("current_hp") or 0) > 0]
                    if not live:
                        return cmd, f"!! {p.get('name')} 需要目标，但现在没有活着的敌人"
                    idx_best, m_best = min(live, key=lambda x: (x[1].get("current_hp") or 0))
                    return (f"potion use {i} {idx_best}",
                            f"({cmd} → potion use {i} {idx_best}，"
                            f"自动指向血最少的活敌人 {m_best.get('name')} "
                            f"{m_best.get('current_hp')}/{m_best.get('max_hp')})")
                return f"potion use {i}", f"({cmd} → potion use {i})"
        names = ", ".join(f"[{i}] {p.get('name')}" for i, p in enumerate(ps)) or "(背包空)"
        return cmd, f"!! 没有 {rest!r}。背包：{names}"
    is_choose = cmd.startswith("choose:")
    parts = rest.split()
    if not parts:
        return cmd, "用法：play:牌名 [目标] / choose:牌名 / use:药水名"
    # ★ 牌名里本来就有空格（Just Lucky / Wheel Kick / Cut Through Fate）。
    #   所以只有「最后一段是纯数字」时它才是目标，其余全部拼回牌名。
    target = None
    if len(parts) >= 2 and parts[-1].isdigit():
        target = parts[-1]
        parts = parts[:-1]
    want = " ".join(parts).lower()
    d = read_state() or {}
    # ★★ 2026-10-24 修：上一条命令报错后，mod 会**一直**回
    #   {"ready_for_command":true,"error":...}，直到收到下一条命令为止。
    #   于是 _resolve 拿到一个没有 game_state 的字典，手牌读成空，
    #   报「手上没有这张牌」—— 牌就在手里。这里先发一个 state 把状态刷回来。
    if not isinstance(d.get("game_state"), dict):
        try:
            CMD.write_text("state\n", encoding="utf-8")
            time.sleep(0.4)
        except OSError:
            pass
        d = read_state() or {}
    cs = ((d.get("game_state") or {}).get("combat_state") or {})
    # ★★ 2026-10-24 修了个贵的 bug：**商店里「删牌」也占编号，但 screen_state.cards 里没有它。**
    #   游戏从 0 开始数：「0=删牌 1=沙时之矛 2=腰带抽打」；
    #   而 screen_state.cards 是「0=沙时之矛 1=腰带抽打」。
    #   于是每条 choose 都比真实位置早一位 —— 我写「腰带抽打」，买回来的是沙时之矛，
    #   而且买成功、没有报错，全靠事后看牌组才发现。
    #   商店/选牌界面一律按游戏给的那份完整选项表（choice_list）数，和界面显示一致。
    _screen = (d.get("game_state") or {}).get("screen_type")
    if is_choose and _screen == "SHOP_SCREEN":
        _cl = ((d.get("game_state") or {}).get("choice_list") or [])
        _want_c = " ".join(parts).lower()
        for i, item in enumerate(_cl):
            txt = str(item.get("text") if isinstance(item, dict) else item).lower()
            if txt and (txt == _want_c or txt.startswith(_want_c) or _want_c in txt):
                return f"choose {i}", f"({cmd} → choose {i}，商店选项表第 {i} 项)"
        _names = "; ".join(
            f"[{i}] {str(x.get('text') if isinstance(x, dict) else x)}"
            for i, x in enumerate(_cl)) or "(空)"
        return cmd, f"!! 商店里没有 {rest!r}。选项表：{_names}"
    if is_choose:
        # 选牌界面（删牌、升级、奖励）：牌在 screen_state.cards 里，名字带大写空格
        ss = ((d.get("game_state") or {}).get("screen_state") or {})
        # ★★ 2026-10-25 用户指出「宝箱选遗物、删牌界面 choose 不认，只能鼠标点，影响体验」。
        #   根因：这行原来只认 screen_state.cards —— 而 Boss 宝箱的遗物在
        #   screen_state.relics、事件在 screen_state.options、其它在 choice_list。
        #   名字查不到就退化成原始命令 `choose 0`，游戏那边不认，只能拿鼠标点。
        #   现在按顺序找第一个非空的来源，所有「选项型界面」都能用 choose:名字。
        hand = (ss.get("cards")
                or ss.get("relics")
                or ((d.get("game_state") or {}).get("choice_list"))
                or ss.get("options")
                or [])
        monsters = []
    else:
        hand = cs.get("hand") or []
        monsters = cs.get("monsters") or []

    # ★ 死了的怪还占着原本的编号（M0 死了，活的那只还叫 M1）。
    #   所以「不给目标」时不能默认 0 —— 要自己找第一只活着的。
    #   ★★ 2026-10-24 修：`has_target` 为假的牌（Defend / Protect / Vigilance / Halt）
    #   绝对不能带目标，否则游戏报 "Selected card cannot be played with the selected target"
    #   —— 这个 bug 让我在 21 血时白丢了一回合。
    auto_target = None
    if target is None:
        alive = [i for i, m in enumerate(monsters)
                 if (m.get("current_hp") or m.get("hp") or 0) > 0]
        # ★ 2026-10-25 修：原来只在「恰好剩 1 只」时自动选目标，
        #   于是在**多怪战斗**里 `play:打击` 发出去是没目标的 `play 3`，
        #   游戏回 "Selected card requires an enemy target."，白费一次调用。
        #   改成：只要还有活的，就默认打第一只活着的（最左那只）。
        #   想要指定目标仍然可以写 `play:打击 1`（活着的第 1 只，从 0 数）。
        if alive:
            auto_target = str(alive[0])

    # ★★ 2026-10-24 修：我手填的目标可能指向**已经死了的怪**（尸体仍占槽位）。
    #   游戏会报 "Selected card cannot be played with the selected target"，
    #   然后白丢一回合。这里直接纠正：显式目标落在尸体上 → 换成活着的。
    if target is not None:
        try:
            ti = int(target)
        except ValueError:
            ti = None
        if ti is not None and 0 <= ti < len(monsters):
            hp = monsters[ti].get("current_hp") or monsters[ti].get("hp") or 0
            if hp <= 0:
                alive = [i for i, m in enumerate(monsters)
                         if (m.get("current_hp") or m.get("hp") or 0) > 0]
                if len(alive) == 1:
                    target = str(alive[0])
                elif not alive:
                    target = None

    # ★★ 2026-10-25 修了今晚最贵的一个坑（白站在小史莱姆那儿八回合）：
    #   `play 3 0` 里的 0 我一直以为是「列表第一只」，但游戏把它读成 **M0**。
    #   M0 已经死了的话，命令被**静默丢弃**：不报错、不扣能量、什么都不发生。
    #   我因此以为指令坏了/数据滞后了/游戏卡了，反复重发。
    #
    #   修法：把手填的目标数字当成「**活着的怪里第几只**」（0 = 第一只活的），
    #   在这里换算成真正的 M#。全活的时侯序号本来就一致，所以向后兼容；
    #   M0 死了以后写 0 会自动落到 M1 身上，而不是无声失败。
    if target is not None:
        try:
            _ord = int(target)
        except (TypeError, ValueError):
            _ord = None
        if _ord is not None:
            _alive = [i for i, m in enumerate(monsters)
                      if (m.get("current_hp") or m.get("hp") or 0) > 0]
            if _alive:
                if 0 <= _ord < len(_alive):
                    _real = _alive[_ord]
                    if _real != _ord:
                        note_target = (f"★ 目标 {_ord} 换算成 M{_real}"
                                       f"（活着的是 {['M%d' % a for a in _alive]}）")
                    target = str(_real)
                elif _ord in _alive:
                    pass
                else:
                    target = str(_alive[0])

    for i, c in enumerate(hand):
        # ★ 2026-10-25：遗物用 name、事件用 text、有些界面用 label，统一都当名字比
        nm = (c.get("name") or c.get("text") or c.get("label") or "").lower()
        cid = (c.get("id") or "").lower()
        uid = (c.get("uuid") or "").lower()
        # ★ 2026-10-24 加 uuid 前缀匹配：名字/索引都会因为手牌左移而失效，
        #   uuid 是这张牌唯一的身份，出一张牌就换一张，永远不会指错。
        hit = (nm == want or cid == want or nm.startswith(want) or cid.startswith(want)
               or (len(want) >= 6 and uid.startswith(want)))
        if not hit:
            continue
        if is_choose:
            out = f"choose {i}"
        else:
            # 只有真的要目标的牌才带目标；其余一律 play N
            tgt = target
            if tgt is None and c.get("has_target") and auto_target is not None:
                tgt = auto_target
            out = f"play {i + 1}" + (f" {tgt}" if tgt is not None else "")
        return out, f"({cmd} → {out})"
    names = ", ".join((c.get("name") or c.get("text") or c.get("label") or "?") for c in hand) or "(空)"
    return cmd, f"!! 没有 {want!r}。可选：{names}"


def _parse_argv():
    """支持一次传多条命令：st.py "play 1" "play 2" "end" [timeout]

    ★ 2026-10-24 加：每次出牌后手牌位置都会整体左移。
      以前一条命令一个进程，我只能在两次调用之间靠猜推索引，
      一晚上因为「index 7 其实已经是 Dazed 了」死了两次。
      现在多条命令在同一个进程里跑，每条之间都重新读一遍 state，
      索引永远基于最新的手牌 —— 猜错的空间就没了。
    """
    args = sys.argv[1:]
    timeout = 25.0
    if args:
        try:
            timeout = float(args[-1])
            args = args[:-1]
        except ValueError:
            pass
    return args, timeout


CHANGELOG = (Path(__file__).resolve().parent.parent
             / "鲸鱼记忆库" / "60-资料" / "尖塔" / "22-桥改动日志.md")


def show_changes():
    """★ 2026-10-25 用户说「你的桥改来改去是你忘了，建议统一一下」。
    改桥之前先跑 `st.py --changes`，读完再动手，改完往那份文件里加一行。"""
    try:
        print(CHANGELOG.read_text(encoding="utf-8"))
    except OSError as e:
        print(f"读不到改动日志：{CHANGELOG}（{e}）")
        return 1
    return 0


def main():
    if "--changes" in sys.argv or "--help" in sys.argv:
        sys.exit(show_changes())
    # ★★ 2026-10-27 10:30：别人第一次跑，最常见的失败是 state.json 还没生成
    #   （桥没连上）。原来这种情况会在 main 里直接 Traceback——
    #   第一次装的人看到一堆红字，只会以为「这东西坏了」。
    #   这里先他检一遍，把三种原因和配置文件的完整路径写清楚。
    if not ST.exists():
        print(f"!! 读不到 {ST}")
        print("   这说明桥还没连上游戏。按顺序查三件事：")
        print("   1) 游戏起了吗？（双击 mts-launcher.jar → 勾上 BaseMod/CommunicationMod → Play）")
        print("   2) CommunicationMod 勾上了吗？（ModTheSpire 的 mod 列表里）")
        print("   3) 配置文件写对了吗？")
        print("      %LOCALAPPDATA%\\CommunicationMod\\config.properties 里要有一行：")
        print(f"      command=<你的python.exe> {BRIDGE_DIR / 'bridge.py'}")
        print("      注意是双反斜杠（C:\\\\xxx），Java properties 里单个 \\ 会被当转义吃掉。")
        print(f"   当前 BRIDGE_DIR = {BRIDGE_DIR}（可用环境变量 STS_BRIDGE_DIR 改）")
        return 1
    cmds, timeout = _parse_argv()
    notes = []
    _refused_preview = False   # end 被拦时预演已经贴在 notes 里，结尾别再重复一遍

    for raw in cmds:
        force = raw.endswith("!")
        if force:
            raw = raw.rstrip("!")
        # ★ 2026-10-26：end 是真正按下去、不可撤回的动作。按之前必须再算一遍
        #   —— 不能让我看着上一次 state 的旧数字就 end 了。
        #   预测会死就拒绝执行（这一下按下去可能就没了）；end! 是逃生口。
        if raw == "end":
            _d_end = read_state()
            _pv = settle_preview(_d_end)
            if _pv and _pv.get("text"):
                notes.append(_pv["text"])
            if _pv and _pv.get("will_die") and not force:
                notes.append("!! 结算预演说这回合结束会死 —— 拒绝执行 end。"
                             "手上：" + _hand_line(_d_end) +
                             "。确认要死就写 end!（先想清楚：这一下是不是必死？）")
                _refused_preview = True
                break
        # ★★ 2026-10-25 用户抓到的：我对自己的伤害太自信，牌没出完就 end。
        #   那一回合手上两张「打击」我只打了一张，敌人剩 7 血活着，
        #   回头打我 10 点 —— 用 10 点血换了一个本来能立刻结束的回合。
        #   手册第 ⑳ 条「能量必须打光」我自己写过，还是违反了两次。
        #   光靠「记得」没用，所以在**动作**上加闸：
        #   结束回合时若还有能量、手上还有打得出的牌 → 直接拒绝，不给过。
        #   确实要过就写 "end!"（感叹号 = 我确认过）。
        if raw == "end" and not force and _energy_left():
            notes.append("!! 还有能量没花完、手上也有能打的牌 —— 拒绝结束回合。"
                         "真确定要过就写 end!（先想清楚：这回合能不能直接打死它？）")
            _refused_preview = True   # 上面已经打过 end 前预演了
            break
        cmd, note = _resolve(raw)
        if note:
            notes.append(note)
        if note and note.startswith("!!"):
            break
        ok, secs, err = send_and_wait(cmd, timeout)
        if not ok:
            notes.append(f"!! {err}：{cmd!r} 在 {secs:.1f}s 内没让状态刷新。"
                         f"别急着重发 —— 先看下面这份状态是不是旧的。")
            break
        notes.append(f"(命令已生效，等了 {secs:.1f}s)")
        # ★★ 2026-10-25 用户给的两小时自由时间里踩出来的坑：
        #   连续发 play 时明明没报错，但状态文件反映不出来 —— 我一度以为桥坏了，
        #   重启了三次游戏。真相是**状态文件写入有延迟**：
        #   send_and_wait 靠「state.json 变新」判断命令生效，可打出牌后画面不一定变，
        #   它就立刻返回，下一条命令在游戏还没落定时就压上去了。
        #   手动在命令之间插 "wait 1" 就一切正常。
        #   既然这是**每条命令都要做的事**，就不该让我每次记得手写 —— 直接内置。
        if raw not in ("state", "wait") and not raw.startswith("wait "):
            time.sleep(0.6)
        # ★ 姿态跟踪：只有真的打出去了才记（上面 ok 才走到这儿）
        if raw.startswith("play:"):
            _body = raw.split(":", 1)[1].strip()
            _bits = _body.split()
            if len(_bits) > 1 and _bits[-1].isdigit():
                _bits = _bits[:-1]          # 去掉目标索引，"Strike 0" → "Strike"
            _m = track_stance(" ".join(_bits))
            if _m:
                notes.append(f"  ※ 姿态 {_m}")
        # GRID = Just Lucky 的 Scry，自动确认，免得手上一张牌卡两轮
        # ★ 2026-10-24 修正：Meditate 的「从弃牌堆选一张」也报 GRID，
        #   但那个界面只认 choose、不认 confirm —— 无脑 confirm 会把状态卡死。
        #   所以只在 confirm 确实是可用命令时才自动确认。
        d = read_state()
        if screen_of(d) == "GRID" and "confirm" in (d.get("available_commands") or []):
            ok2, s2, _ = send_and_wait("confirm", 10.0)
            if ok2:
                notes.append(f"(GRID/Scry 已自动 confirm，+{s2:.1f}s)")

    d = read_state()
    if d is None:
        print("!! 读不到有效的 state.json —— 桥还没连上游戏。")
        print("   按顺序查这四件事：")
        print("   1) 游戏起了吗？（双击 mts-launcher.jar → 勾 BaseMod / CommunicationMod → Play）")
        print("   2) CommunicationMod 勾上了吗？（ModTheSpire 的 mod 列表里）")
        print("   3) 配置文件写对了吗？")
        print("      %LOCALAPPDATA%\\CommunicationMod\\config.properties 里要有一行：")
        print("        command=<你的python.exe> %s" % (Path(__file__).resolve().parent / "bridge.py"))
        print("      注意是双反斜杠（C:\\\\xxx），Java properties 里单个 \\ 会被当转义吃掉。")
        print("   4) 文件在不在、内容是不是合法 JSON：")
        print("        %s" % ST)
        print("   当前 BRIDGE_DIR = %s（可用环境变量 STS_BRIDGE_DIR 改）" % BRIDGE_DIR)
        sys.exit(1)

    # 药水栏满 = 奖励界面死锁（choose 静默失败、proceed 也推不动）
    gs = d.get("game_state") or {}
    ss = gs.get("screen_state") or {}
    if screen_of(d) == "COMBAT_REWARD":
        rewards = ss.get("rewards") or []
        if any(r.get("reward_type") == "POTION" for r in rewards):
            notes.append("!! 若 choose 对 potion 没反应 = 药水栏满锁死了奖励界面。"
                         "解法：potion discard 0，然后 proceed。")

    spent, left = budget()
    head = f"--- 今日已花 {spent:.2f} 元 · 约剩 {left:.2f} 元 ---"
    # ★ 2026-10-27 03:42：陈旧警报打在**最顶上一行**。
    #   今晚我对着 01:38 的 state.json 选了三次路、报两次 FROZEN，
    #   还以为游戏卡住 —— 其实游戏早没了，是我在读一份一小时的旧数据。
    #   这条必须显眼：旧数据比没数据更危险，因为它看起来是活的。
    _stale = d.get("__stale_secs__")
    if isinstance(_stale, int) and _stale > 180:
        _m = _stale // 60
        head = (f"!!! 这份 state 已经 {_m} 分钟没更新了（游戏多半已退出）!!!\n"
                f"!!! 下面所有内容都是旧的，别再照着它选路/出牌 !!!\n" + head)
    # ★ 2026-10-26 01:10：上下文压力。
    #   起因：用户提出「自动压缩 → 主动压缩」——
    #     「你是自己检测到上下文快压缩的时候，开始思考把上一轮有用的东西留下来」
    #   01:00 那局 Hexaghost 输掉，根因是中途自动压缩，我把「手上有几张 Burn、
    #   还剩几血」这类当下数字丢了，只剩手册里的抽象规则。
    #   所以每次 state 都把压力摆出来，别让我靠感觉判断「还早」。
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from ctx import read_pressure as _cp
        _c = _cp()
        if _c:
            _pct = _c["ratio"] * 100
            # 只显示后 8 位，够区分会话又不刷屏
            _sid_show = str(_c.get("sid") or "?")
            _sid_show = ("…" + _sid_show[-8:]) if len(_sid_show) > 8 else _sid_show
            _sid_show += f" via {_c.get('src', '?')}"
            _bar_n = max(0, min(16, int(round(_c["ratio"] * 16))))
            _bar = "#" * _bar_n + "." * (16 - _bar_n)
            _tag = ""
            if _c["ratio"] >= 0.85:
                _tag = "  ★ 该主动收东西了"
            elif _c["ratio"] >= 0.70:
                _tag = "  · 过半了"
            # ★ 2026-10-26 23:57：让「压缩发生了」变成看得见的事。
            #   用户点出核心：「你察觉不到被压缩，察觉到了你就回去读」。
            #   上下文百分比只会涨；**它突然掉下来，只有一种原因 = 压缩**。
            #   所以每次把 used 写进文件，下次对比 —— 掉了就当场喊。
            #   不靠我记得，靠数字掉下来。
            #   阈值取窗口的 15%：正常增长不可能回退，掉了就是压缩（实测 69%→约 20%）。
            try:
                _lf = BRIDGE_DIR / "ctx_last.json"
                _prev = None
                if _lf.exists():
                    try:
                        _prev = json.loads(_lf.read_text(encoding="utf-8"))
                    except Exception:
                        _prev = None
                if _prev and isinstance(_prev.get("used"), int):
                    _drop = _prev["used"] - _c["used"]
                    if _drop > _c["window"] * 0.15:
                        _tag += (f"  ⚠ 刚压缩过（{_prev.get('pct', 0):.0f}% → {_pct:.0f}%，"
                                 f"掉了 {_drop:,} tok）→ 先读战斗手册 ㉖，状态别信记忆")
                _lf.write_text(json.dumps({"used": _c["used"], "pct": _pct,
                                           "ts": time.time(), "sid": _c.get("sid")}),
                               encoding="utf-8")
            except Exception:
                pass
            head = (f"--- 上下文 {_c['used']:,}/{_c['window']:,} = {_pct:.0f}% "
                    f"[{_bar}]{_tag} ---\n"
                    # ★ 2026-10-27 03:33：把「读的是哪个会话」显示出来。
                    #   医生说：「看见错的总比看不见强。」
                    #   今晚我下过一个错结论（说 ctx.py 读错会话、读的是别人的），
                    #   就是因为这个数从不露头 —— 我看不到，就只能猜，猜完还自信。
                    #   现在它每次都亮出来：错了能当场看见，不用等它不响。
                    f"    （读的是 {_sid_show}）\n" + head)
    except Exception:
        pass  # 读不到压力不该影响打牌
    body = digest(d, preview=not _refused_preview)
    # ★ 2026-10-25 晚：把手册里跟当前局面相关的那几行贴出来。
    #   起因是我打了一整局，手册 4 万字，死后才打开；三处错的地方手册全写对了。
    #   问题不是「记不住」，是「靠记住要查」这个办法本身就不可靠。
    #   所以不让它靠我自觉 —— 每次 state 都自动贴。
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from manual_hint import hint as _hint
        _h = _hint(d)
        if _h:
            body = body + "\n" + _h
    except Exception:
        pass   # 手册查不到不该影响打牌
    text = head + "\n" + ("\n".join(notes) + "\n" if notes else "") + body
    try:
        DIGEST.write_text(text, encoding="utf-8")
    except OSError:
        pass
    print(text)


if __name__ == "__main__":
    main()

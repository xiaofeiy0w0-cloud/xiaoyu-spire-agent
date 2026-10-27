"""小肥鱼的手：CommunicationMod 通信桥（文件驱动版）。

- 收到 JSON → 写 C:\\sts_bridge\\state.json（原始）+ state.txt（摘要）
- 然后等 C:\\sts_bridge\\cmd.txt 出现 → 读第一行 → 发给游戏 → 删掉文件
- 超时没等到 → 发 "state"（空转，不推进游戏）
"""
import json
import os
import sys
import time

BASE = os.environ.get("STS_BRIDGE_DIR") or r"C:\sts_bridge"
STATE_JSON = os.path.join(BASE, "state.json")
STATE_TXT = os.path.join(BASE, "state.txt")
CMD = os.path.join(BASE, "cmd.txt")
LOG = os.path.join(BASE, "bridge.log")


def log(s):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + str(s) + "\n")


def emit(cmd):
    sys.stdout.write(cmd + "\n")
    sys.stdout.flush()
    log("-> " + cmd)


def digest(st):
    gs = st.get("game_state") or {}
    lines = []
    lines.append("wall=" + time.strftime("%H:%M:%S"))
    lines.append(f"in_game={st.get('in_game')} ready={st.get('ready_for_command')} screen={gs.get('screen_type')}")
    lines.append("cmds=" + ",".join(st.get("available_commands") or []))
    cs = gs.get("combat_state")
    if cs:
        me = cs.get("player") or {}
        lines.append(f"HP {me.get('current_hp')}/{me.get('max_hp')}  block={me.get('block')}  energy={me.get('energy')}  turn={cs.get('turn')}")
        hand = cs.get("hand") or []
        for i, c in enumerate(hand):
            lines.append(f"  [{i}] {c.get('name')} cost={c.get('cost')} type={c.get('type')} dmg={c.get('damage')} blk={c.get('block')} target={c.get('has_target')} playable={c.get('is_playable')}")
        ms = cs.get("monsters") or []
        for i, m in enumerate(ms):
            if m.get("is_gone"):
                continue
            lines.append(f"  M{i} {m.get('name')} {m.get('current_hp')}/{m.get('max_hp')} block={m.get('block')} intent={m.get('intent')} dmg={m.get('move_adjusted_damage')}x{m.get('move_hits')}")
        lines.append(f"  draw={len(cs.get('draw_pile') or [])} discard={len(cs.get('discard_pile') or [])} exhaust={len(cs.get('exhaust_pile') or [])}")
    else:
        ss = gs.get("screen_state") or {}
        if ss:
            lines.append("screen_state_keys=" + ",".join(list(ss.keys())[:8]))
            ch = ss.get("choice_list") or ss.get("cards") or []
            if ch:
                names = []
                for c in ch[:10]:
                    names.append(c.get("name") or str(c)[:24])
                lines.append("choices=" + " | ".join(names))
    return "\n".join(lines)


def main():
    # 2026-10-24 bugfix（第二版，方向修正）：
    # 中文 Windows 上 Java 的默认输出编码是 GBK(cp936)，CommunicationMod
    # 发过来的是 GBK 字节；而 Python 的 sys.stdin 若被环境变量推成 UTF-8，
    # 就会 UnicodeDecodeError 把桥弄死（现象：游戏在局内，state.json 冻住）。
    # 这里显式按 GBK 解码，errors=replace 兜底，保证桥永不死。
    # 想改回 UTF-8：启动时给 java 加 -Dfile.encoding=UTF-8，再把这里换成 utf-8。
    try:
        sys.stdin.reconfigure(encoding="gbk", errors="replace")
    except Exception:
        pass
    log("bridge start pid=" + str(os.getpid()) + " stdin=" + str(getattr(sys.stdin, "encoding", "?")))
    emit("ready")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            st = json.loads(line)
        except json.JSONDecodeError:
            log("bad json")
            continue
        if not st.get("ready_for_command"):
            continue
        with open(STATE_JSON, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        with open(STATE_TXT, "w", encoding="utf-8") as f:
            f.write(digest(st))
        cmd = None
        # 2026-10-24 第三步修复：同步模型 —— 一直等，不再 1.5 秒超时空转。
        # 原来的「等 1.5 秒没命令就发 state」会让玩家还没写的命令被空转顶掉，
        # 造成命令积压/丢失（现象：send.py 报 ok，但游戏手牌能量都不动）。
        # 现在：写完状态后阻塞，直到 cmd.txt 出现真命令才回给游戏。
        # 副作用：我不发命令，游戏就一直等你 —— 这是对的，本来就是回合制。
        while True:
            if os.path.exists(CMD):
                taken = CMD + ".taken"
                try:
                    os.replace(CMD, taken)
                except OSError:
                    time.sleep(0.05)
                    continue
                try:
                    with open(taken, encoding="utf-8") as f:
                        cmd = (f.readline() or "").strip()
                except OSError:
                    cmd = None
                try:
                    os.remove(taken)
                except OSError:
                    pass
                if cmd:
                    break
            time.sleep(0.08)
        emit(cmd)


if __name__ == "__main__":
    main()

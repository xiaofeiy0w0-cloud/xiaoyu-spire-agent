# -*- coding: utf-8 -*-
"""激活游戏窗口 + 发出 st.py 命令，一次搞定。

为什么需要这个（2026-10-27 07:41）：
  每次我跑工具，DSH 窗口都会把焦点抢回去 —— 而游戏（LWJGL）只接受**前台窗口**的输入。
  所以单发命令时，点击/键盘全落到 DSH 上，游戏那边一动不动（表现就是 FROZEN）。
  ⇒ 必须每次先 AttachThreadInput 强制把游戏切到前台，再发命令。

用法：
    python st_go.py "choose 0"
    python st_go.py "play:Strike 0"
"""
import ctypes
import ctypes.wintypes as w
import os
import subprocess
import sys
import time
from pathlib import Path

# ★★ 2026-10-27 10:29：同 st.py，路径改成「环境变量优先，默认找同目录」。
#   给别人家的小鱼用时要能换位置；我自己这边不设环境变量，仍然指向 research\st.py。
ST = Path(os.environ.get("STS_ST_PY") or (Path(__file__).with_name("st.py")))
PY = sys.executable


def find_game():
    u = ctypes.windll.user32
    hits = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)

    def cb(hwnd, lp):
        n = u.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        if "Modded" in buf.value or "ModTheSpire" in buf.value:
            hits.append(hwnd)
        return True

    u.EnumWindows(EnumProc(cb), 0)
    return hits[0] if hits else None


def focus_game():
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    g = find_game()
    if not g:
        print("!! 没找到游戏窗口")
        return False
    if u.GetForegroundWindow() == g:
        return True
    fg = u.GetForegroundWindow()
    ft = u.GetWindowThreadProcessId(fg, None)
    gt = u.GetWindowThreadProcessId(g, None)
    cur = k.GetCurrentThreadId()
    u.AttachThreadInput(cur, ft, True)
    u.AttachThreadInput(cur, gt, True)
    u.BringWindowToTop(g)
    u.SetForegroundWindow(g)
    u.SetFocus(g)
    u.AttachThreadInput(cur, ft, False)
    u.AttachThreadInput(cur, gt, False)
    time.sleep(0.5)
    ok = u.GetForegroundWindow() == g
    if not ok:
        print("!! 激活失败（前台仍不是游戏）")
    return ok


def main():
    if len(sys.argv) < 2:
        print("用法: python st_go.py \"choose 0\"")
        return 1
    cmd = " ".join(sys.argv[1:])
    focus_game()
    r = subprocess.run([PY, "-X", "utf8", str(ST), cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print(out)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())

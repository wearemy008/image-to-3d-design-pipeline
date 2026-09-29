#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
activate_window.py — 把 SketchUp 主窗口激活到前台
================================================================================
为什么需要：SketchUp 窗口在后台/最小化时，OpenGL 上下文可能不可用，
`write_image` 会显著变慢甚至长时间不返回。出图前先置前可避免这类"假死"。

用法:
    python activate_window.py                 # 激活标题含 SketchUp 的窗口
    python activate_window.py --title 我的模型  # 只激活标题含指定文字的窗口

注意：Windows 对 SetForegroundWindow 有前台进程限制，
这里用 HWND_TOPMOST → NOTOPMOST 的闪一下技巧绕过，属于常规做法。
"""

import argparse
import ctypes
import ctypes.wintypes as wt

u = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def find_windows(keyword):
    """返回 [(hwnd, pid, title), ...]，按 PID 分组返回可见窗口"""
    hits = []

    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if keyword not in title:
            return True
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hits.append((hwnd, pid.value, title))
        return True

    u.EnumWindows(EnumProc(cb), 0)
    return hits


def activate(hwnd):
    u.ShowWindow(hwnd, 9)                     # SW_RESTORE
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)   # HWND_TOPMOST, NOSIZE|NOMOVE
    u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)   # 回 NOTOPMOST
    u.SetForegroundWindow(hwnd)
    u.BringWindowToTop(hwnd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="SketchUp", help="窗口标题关键字")
    ap.add_argument("--skip-blank", action="store_true", default=True,
                    help="跳过「无标题」的空文档窗口（默认开）")
    args = ap.parse_args()

    wins = find_windows(args.title)
    if not wins:
        print(f"[X] 未找到标题含「{args.title}」的可见窗口")
        return 1

    done = 0
    for hwnd, pid, title in wins:
        if args.skip_blank and title.strip().startswith("无标题"):
            print(f"  (跳过空文档窗口) PID {pid} «{title}»")
            continue
        activate(hwnd)
        print(f"[OK] 已置前 PID {pid}  hwnd {hwnd}  «{title}»")
        done += 1

    if done == 0:
        print("[!] 只找到空文档窗口，未置前任何窗口")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

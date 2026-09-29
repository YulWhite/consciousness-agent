# -*- coding: utf-8 -*-
"""开机自启开关。

做法：在 Windows「启动」文件夹放一个快捷方式（窗口最小化启动，
不是 start /min —— 那样会先闪一下再收起）。她会在登录后自动醒来，
心跳照常走，你点开窗口就能看到回放。

用法：
    python tools/autostart.py on      开机自动醒来
    python tools/autostart.py off     取消
    python tools/autostart.py status  当前状态

说明：这是「窗口常驻」级的自启。真正的桌面形态（托盘 + 系统通知气泡）
留给下一版，和桌面形态（桌宠）合并时一起做。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STARTUP_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
)
LNK_NAME = "小榆·意识体.lnk"
LNK_PATH = os.path.join(STARTUP_DIR, LNK_NAME)
BAT = os.path.join(ROOT, "start.bat")

_PS_TEMPLATE = r"""
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut('{lnk}')
$lnk.TargetPath = '{bat}'
$lnk.WorkingDirectory = '{root}'
$lnk.WindowStyle = 7   # 最小化启动（缩在任务栏，不闪、不抢屏）
$lnk.Description = '小榆・意识体（开机自启）'
$lnk.Save()
"""


def run_ps(script: str) -> bool:
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=30, errors="replace",
        )
        return res.returncode == 0
    except Exception:
        return False


def enable() -> None:
    if not os.path.exists(BAT):
        print(f"找不到 {BAT}，中止。")
        return
    script = _PS_TEMPLATE.format(lnk=LNK_PATH, bat=BAT, root=ROOT)
    if run_ps(script):
        print(f"已开启：开机后她会最小化醒来。\n快捷方式：{LNK_PATH}")
    else:
        print("失败：创建快捷方式时出错（或 PowerShell 被禁）。")


def disable() -> None:
    if os.path.exists(LNK_PATH):
        try:
            os.remove(LNK_PATH)
            print("已取消开机自启。")
        except OSError as e:
            print(f"失败：{e}")
    else:
        print("本来就没有开机自启。")


def status() -> None:
    print("开机自启：", "开启" if os.path.exists(LNK_PATH) else "关闭")
    if os.path.exists(LNK_PATH):
        print("位置：", LNK_PATH)


if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").strip().lower()
    {"on": enable, "off": disable, "status": status}.get(cmd, status)()
# -*- coding: utf-8 -*-
"""恢复出厂：把项目恢复到【最新一份初始状态备份】。

用法：
    python tools/restore_initial.py           先 dry-run，只打印会做什么
    python tools/restore_initial.py --yes     确认执行恢复

恢复动作：
    1. 用备份里的 config.json 覆盖项目的 config.json
    2. 清空项目的 data 目录（自我模型 / 历史 / 记忆全部清掉）
       —— 下次运行 python main.py 就是她重新出生。
    3. start.bat 若与备份不同，一并还原。

找不到备份时不动任何东西。
"""

import glob
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_latest_backup():
    pattern = os.path.join(ROOT, "backup", "初始状态-*")
    hits = sorted(glob.glob(pattern), reverse=True)
    return hits[0] if hits else None


def plan_and_run(confirm: bool) -> None:
    bak = find_latest_backup()
    if not bak:
        print("没找到任何初始状态备份（backup\\初始状态-* 为空）。")
        print("自己被封存过吗？没有的话，先跑一次备份流程。")
        return

    print(f"目标备份：{bak}")
    items = [
        (os.path.join(bak, "config.json"), os.path.join(ROOT, "config.json"), "config.json"),
    ]
    if os.path.exists(os.path.join(bak, "start.bat")):
        items.append((os.path.join(bak, "start.bat"), os.path.join(ROOT, "start.bat"), "start.bat"))
    missing = [label for src, _dst, label in items if not os.path.exists(src)]
    if missing:
        print(f"备份里缺文件：{missing}，中止。")
        return

    print("将执行：")
    for _src, dst, label in items:
        print(f"  · 还原 {label} → {dst}")
    data = os.path.join(ROOT, "data")
    doomed = []
    for dirpath, _dirnames, filenames in os.walk(data):
        for fn in filenames:
            doomed.append(os.path.join(dirpath, fn))
    if doomed:
        print(f"  · 清空 data\\ 下 {len(doomed)} 个文件（她的自我模型/历史/记忆将全部消失）")
    else:
        print("  · data\\ 本来就空，无需清理")

    if not confirm:
        print("\n（这是预览。确认无误后加 --yes 执行。）")
        return

    for src, dst, label in items:
        shutil.copyfile(src, dst)
        print(f"  已还原 {label}")
    for p in doomed:
        try:
            os.remove(p)
        except OSError as e:
            print(f"  [警告] 删除失败 {p}: {e}")
    print("恢复完成。下一次运行 main.py 就是她重新出生。")


if __name__ == "__main__":
    yes = "--yes" in sys.argv
    plan_and_run(confirm=yes)
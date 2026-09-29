# -*- coding: utf-8 -*-
"""备份恢复：列出 backup\\ 下所有封存（初始状态 / 快照），按选择或最新恢复。

用法：
    python tools/restore_backup.py               列出所有封存并交互选择（dry-run 预览）
    python tools/restore_backup.py --list        只列出
    python tools/restore_backup.py --latest --yes 恢复最新一份
    python tools/restore_backup.py <目录名> --yes 恢复指定目录（backup\\ 下的名字）

恢复动作：用封存里的 config.json 覆盖项目配置，data\\ 整体替换为封存里的 data。
（self_model / history / 记忆 / 内核状态 / 心跳档案一起回到那个时刻）
"""

import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def list_backups():
    hits = sorted(glob.glob(os.path.join(ROOT, "backup", "*")), key=os.path.getmtime, reverse=True)
    return [os.path.basename(h) for h in hits if os.path.isdir(h)]


def restore(name: str, confirm: bool) -> None:
    src = os.path.join(ROOT, "backup", name)
    if not os.path.isdir(src):
        print(f"没找到封存：{name}")
        print_files = list_backups()
        print("现有的封存：", ", ".join(print_files) if print_files else "（无）")
        return
    cfg_src = os.path.join(src, "config.json")
    data_src = os.path.join(src, "data")
    if not os.path.exists(cfg_src) or not os.path.isdir(data_src):
        print(f"{name} 不是完整封存（缺 config.json 或 data\\），中止。")
        return

    target_cfg = os.path.join(ROOT, "config.json")
    target_data = os.path.join(ROOT, "data")
    print(f"即将恢复「{name}」：")
    print(f"  · {cfg_src} → {target_cfg}")
    print(f"  · {data_src} → {target_data}（当前 data 将被替换）")
    if not confirm:
        print("（预览模式。确认后加 --yes 执行。）")
        return

    import shutil

    shutil.copyfile(cfg_src, target_cfg)
    if os.path.isdir(target_data):
        shutil.rmtree(target_data)
    shutil.copytree(data_src, target_data)
    print("恢复完成。下一次运行，她醒来时就是那个时刻的她。")


if __name__ == "__main__":
    argv = sys.argv[1:]
    names = list_backups()
    if "--list" in argv:
        print("封存清单（新→旧）：")
        for n in names:
            print("  ", n)
        sys.exit(0)
    if "--latest" in argv:
        if not names:
            print("没有任何封存。")
            sys.exit(1)
        restore(names[0], confirm="--yes" in argv)
        sys.exit(0)
    if argv and not argv[0].startswith("--"):
        restore(argv[0], confirm="--yes" in argv)
        sys.exit(0)
    # 交互
    if not names:
        print("没有任何封存。用 /快照 先存一份，或等初始状态封存。")
        sys.exit(0)
    for i, n in enumerate(names, 1):
        print(f"{i}. {n}")
    try:
        pick = input("选一个（回车=最新）: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)
    if not pick:
        restore(names[0], confirm="--yes" in argv)
    elif pick.isdigit() and 1 <= int(pick) <= len(names):
        restore(names[int(pick) - 1], confirm="--yes" in argv)
    else:
        print("选择无效。")
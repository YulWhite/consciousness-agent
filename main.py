"""意识体 v0.8 —— 入口。

三层结构：外界（终端输入）→ 中间层（SelfModel / 反思 / 皮层）
                      ↑ 体感注入
                内核（情绪 / 需求 / 记忆 / 精力，不可触及）

用法：
    python main.py          # 第一次运行 = 她的出生
    pip 不需要装任何东西    # 只依赖 Python 标准库
"""

import sys


def _prepare_console() -> None:
    """Windows 控制台编码兜底（UTF-8，出错就替换，不崩）。"""
    for stream in (sys.stdout, sys.stdin):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main() -> None:
    _prepare_console()
    from agent.shell import Agent

    Agent().run()


if __name__ == "__main__":
    main()
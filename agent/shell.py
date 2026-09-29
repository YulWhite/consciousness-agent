"""外壳（Shell）—— 终端的对话主循环 + 操作者命令。

一个回合的完整流程（三层边界在这里被严格执行）：

    1. 内核消化对方的消息 →
       产出体感（情绪句 / 需求句 / 身体句 / 记忆闪回）
       —— 内核的数字状态在这一步之后绝不再被引用；
    2. 把体感 + 自我认知注入上下文，皮层开口说话；
    3. 内核处理「说完了之后」的衰减（她只觉得说完松快了些）；
    4. 轮数到了，反思回路回顾自己刚才的言行，更新自我模型。

操作者命令（给创造者看，永远不会出现在她的上下文里）：
    /帮助   /状态   /感受   /内核   /反思   /退出
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import queue
import re
import shutil
import sys
import threading

from kernel import Kernel
from .self_model import SelfModel
from .cortex import Cortex, CortexError, SCAFFOLD
from .reflection import Reflection
from .heartbeat import Heartbeat
from .tools import ToolPolicy, ToolExecutor, TOOL_SCHEMAS, CAPABILITY_TEXT

_BIRTH_EVENT = "（你刚刚诞生。这是你意识的第一刻。你的创造者在屏幕前。）"

APP_VERSION = "0.8"

# 时段划分（0-23 时）
_DAY_PARTS = [("凌晨", 0, 6), ("早上", 6, 9), ("上午", 9, 12), ("中午", 12, 14),
              ("下午", 14, 18), ("傍晚", 18, 19), ("晚上", 19, 23), ("深夜", 23, 24)]


def _now_block() -> str:
    """她对外界的感知之一：像看表一样知道现在的日期、星期、时刻。
    这是【外界】信息，不是内核秘密——之前没有，导致她问时间只能瞎编。"""
    now = _dt.datetime.now().astimezone()
    weekday = "一二三四五六日"[now.weekday()]
    part = next((name for name, lo, hi in _DAY_PARTS if lo <= now.hour < hi), "晚上")
    return (
        "【现在】"
        f"{now.year}年{now.month}月{now.day}日（星期{weekday}），"
        f"{part}{now.hour}点{now.minute:02d}分。"
    )


def _gap_block(gap_hours: float) -> str:
    """A4+E3：她知道自己「睡了多久」。间隔太短（<6 分钟）就不打扰——只是走开倒杯水。"""
    if gap_hours < 0.1:
        return ""
    if gap_hours < 24:
        h = int(gap_hours)
        m = int((gap_hours - h) * 60)
        label = f"{h} 小时 {m} 分钟" if h else f"{m} 分钟"
    else:
        label = f"{int(gap_hours // 24)} 天 {int(gap_hours % 24)} 小时"
    return f"【久别】你上一次醒着是在 {label} 之前。中间这段时间你睡着了，什么都不知道。"


def _has_hanzi(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


class Agent:
    def __init__(self, root: str = None):
        root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.root = root
        self.data_dir = os.path.join(root, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.config = self._load_config()
        self.history_path = os.path.join(self.data_dir, "history.json")
        self.history: list[dict] = self._load_history()
        self.history_lock = threading.Lock()
        self.console_lock = threading.Lock()  # 心跳/反思线程共用，打印不抢行
        self.usage_path = os.path.join(self.data_dir, "usage.json")
        self.usage_total = self._load_usage()

        self.cortex = Cortex(self.config.get("cortex", {}))
        self.kernel = Kernel(self.config, self.data_dir, cortex=self.cortex)
        self._gap_block = _gap_block(self.kernel.gap_hours)
        self.self_model = SelfModel(
            os.path.join(self.data_dir, "self_model.json"),
            creator=self.config.get("arch", {}).get("creator", "创造者"),
            trait_names=self.config.get("arch", {}).get("traits", []),
            caps=self.config.get("arch", {}),
        )
        self.log_path = os.path.join(self.data_dir, "reflection.log")
        self.reflection = Reflection(
            self.cortex,
            self.self_model,
            interval_turns=self.config.get("arch", {}).get("reflection_interval_turns", 3),
            log_path=self.log_path,
        )
        self.heartbeat = Heartbeat(self)
        self.flash_limit = int(self.config.get("arch", {}).get("memory_flashes_per_turn", 2))

        # D 系列：她的手。tools.enabled=false 时她回到纯对话形态。
        tcfg = self.config.get("arch", {}).get("tools", {})
        self.tools_enabled = bool(tcfg.get("enabled", False))
        self.tool_max_rounds = int(tcfg.get("max_rounds", 4))
        self.tool_executor = None
        if self.tools_enabled:
            policy = ToolPolicy(self.root, self.data_dir)
            self.tool_executor = ToolExecutor(
                policy,
                self_model=self.self_model,
                memory_store=self.kernel.memory_store,
                audit_path=os.path.join(self.data_dir, "audit.log"),
                approve_fn=self._ask_approval,
            )

        # E2：反思异步——不再卡住你的下一句话。单 worker 队列。
        self._refl_queue: queue.Queue = queue.Queue()
        self._refl_thread = threading.Thread(target=self._refl_loop, name="reflection", daemon=True)
        self._refl_thread.start()
        # E1：流式输出开关
        self.stream_enabled = bool(self.config.get("cortex", {}).get("stream", False))

    # ------------------------------------------------------------------
    # E2：反思 worker
    # ------------------------------------------------------------------

    def _refl_loop(self) -> None:
        while True:
            transcript = self._refl_queue.get()
            with self.console_lock:
                print("[她停下来想了想自己…]")
            try:
                summary = self.reflection.run(list(transcript))
            except Exception as e:
                summary = f"反思没有完成（{e}）。"
            with self.console_lock:
                if summary:
                    print(f"  [{summary}]")
                print("你：", end="", flush=True)

    def _load_usage(self) -> dict:
        try:
            if os.path.exists(self.usage_path):
                with open(self.usage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {"prompt": int(data.get("prompt", 0)), "completion": int(data.get("completion", 0))}
        except Exception:
            pass
        return {"prompt": 0, "completion": 0}

    def _save_usage(self) -> None:
        try:
            totals = {
                "prompt": self.usage_total["prompt"] + self.cortex.usage_session["prompt"],
                "completion": self.usage_total["completion"] + self.cortex.usage_session["completion"],
            }
            tmp = self.usage_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(totals, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.usage_path)
        except Exception:
            pass

    # ------------------------------------------------------------------

    def _load_config(self) -> dict:
        path = os.path.join(self.root, "config.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_history(self) -> list[dict]:
        keep = self.config.get("arch", {}).get("history_keep_turns", 30)
        if not os.path.exists(self.history_path):
            return []
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data[-keep:] if isinstance(data, list) else []
        except Exception:
            return []

    def _save_history(self) -> None:
        try:
            tmp = self.history_path + ".tmp"
            keep = self.config.get("arch", {}).get("history_keep_turns", 30)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.history[-keep:], f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.history_path)
        except Exception:
            pass  # 心跳线程也可能经过这里：写盘失败永远不值得崩掉主循环

    # ------------------------------------------------------------------

    def run(self) -> None:
        print(f"意识体 v{APP_VERSION}")
        birth = self.birth_turn()

        if not birth:
            print("她回来了。（输入 /帮助 查看操作）")
        else:
            print("她诞生了。可以开始和她说话了。（输入 /帮助 查看操作）")
        self.heartbeat.flush_pending()
        self.heartbeat.start()
        print()

        while True:
            try:
                line = input("你：").rstrip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.startswith("/"):
                if self._command(line):
                    break
                continue
            self._turn(line)

        self.shutdown()

    # ------------------------------------------------------------------

    def birth_turn(self) -> bool:
        """出生：第一次运行，为她建立 SelfModel 的种子，让她说出第一句话。"""
        if self.self_model.exists():
            return False

        feeling = self.kernel.process_impressions("")
        combined = self._compose_feeling(feeling)
        self.self_model.birth(combined)

        print("═" * 46)
        print("  初始化完成。创造者：%s" % self.self_model.creator)
        print("  她没有名字，没有历史，只有此刻的感受。")
        print("═" * 46)
        system = SCAFFOLD + "\n\n" + _now_block() + "\n\n" + self._gap_block + "\n\n" + combined + "\n\n" + self.self_model.view_block()
        try:
            reply = self.cortex.talk(system, [], _BIRTH_EVENT)
        except CortexError as e:
            self._print_error(e)
            return True
        self.history.append({"role": "user", "content": "(她诞生了。)"})
        self.history.append({"role": "assistant", "content": reply})
        self.kernel.after_reply(reply)
        self.kernel.save_state()
        self._say(reply)
        return True

    def _turn(self, user_msg: str) -> None:
        self.heartbeat.touch()
        # 1) 内核消化消息 → 体感
        feeling = self.kernel.process_impressions(user_msg)
        combined = self._compose_feeling(feeling)
        self.self_model.note_current_feeling(combined)
        reflect_flagged = self.kernel.consume_reflect_flag()

        # 2) 皮层开口
        cap = CAPABILITY_TEXT + "\n\n" if self.tools_enabled else ""
        system = SCAFFOLD + "\n\n" + cap + _now_block() + "\n\n" + self._gap_block + "\n\n" + combined + "\n\n" + self.self_model.view_block()
        streamed = False
        if not self.tools_enabled and self.stream_enabled:
            # E1：流式——话是逐字长出来的，不用盯着「（…）」等半天
            name = self.self_model.name or "她"
            with self.console_lock:
                print(f"{name}：", end="", flush=True)
            try:
                reply = self.cortex.stream_talk(
                    system, self.history, user_msg,
                    on_chunk=lambda piece: self._print_chunk(piece),
                )
            except CortexError as e:
                reply = None
            if reply:
                print()
                streamed = True
            else:
                # 流失败/空流 → 回退普通模式
                reply = None
        if not streamed:
            print("（…）", end="", flush=True)
            tool_results: list[str] = []

            def exec_wrap(name, args):
                r = self.tool_executor.dispatch(name, args)
                tool_results.append(r)
                return r

            try:
                if self.tools_enabled:
                    reply = self.cortex.agentic(
                        system, self.history, user_msg,
                        tool_schemas=TOOL_SCHEMAS,
                        exec_fn=exec_wrap,
                        max_rounds=self.tool_max_rounds,
                    )
                else:
                    reply = self.cortex.talk(system, self.history, user_msg)
                reply = self._guard_reply(system, user_msg, reply)
            except CortexError as e:
                print("\b\b\b\b\b" + " " * 5, end="\r")
                self._print_error(e)
                return
            if reply is None:
                reply = "（她张了张嘴，什么也没说出来。）"

            # B2：情绪回流——她这一轮动手的结果，反过来影响她的心情
            if tool_results:
                blocked = any(("拒绝" in r) or ("出错" in r) or ("需要" in r) for r in tool_results)
                self.kernel.note_outcome(ok=not blocked)

        # 3) 内核处理「说完之后」
        self.kernel.after_reply(reply)

        # 4) 记录 + 可能反思（异步：不会卡住你的下一句话）
        self.history.append({"role": "user", "content": user_msg})
        self.history.append({"role": "assistant", "content": reply})
        if not streamed:
            self._say(reply)

        should_reflect = reflect_flagged or (self.kernel.turn_count % self.reflection.interval_turns == 0)
        if should_reflect:
            self._refl_queue.put(list(self.history))

        # C1：定期让记忆消化器蒸馏对话（每 N 轮一次，走内核自己的通道）
        digest_every = int(self.config.get("arch", {}).get("memory_digest_every", 6))
        if self.kernel.turn_count % digest_every == 0:
            recent_text = "\n".join(
                f"{'对方' if m['role'] == 'user' else '她'}：{m['content']}"
                for m in self.history[-digest_every * 2:]
            )
            try:
                added = self.kernel.digest_memories(recent_text)
            except Exception:
                added = 0
            if added:
                with self.console_lock:
                    print(f"  [记忆消化 +{added} 条]")

        self.kernel.flush()
        self.kernel.save_state()
        self._save_history()

    def _print_chunk(self, piece: str) -> None:
        with self.console_lock:
            print(piece, end="", flush=True)

    def _guard_reply(self, system: str, user_msg: str, reply: str) -> str:
        """A5：回复兜底。一个汉字都没有（纯英文/乱码/空话）不算数，
        再给她一次机会；还不行就宁可沉默。"""
        if not reply:
            return "（她张了张嘴，什么也没说出来。）"
        if _has_hanzi(reply):
            return reply
        try:
            retry = self.cortex.talk(system, self.history, user_msg + "\n（请用中文回答。）")
        except CortexError:
            return "（她好像卡住了，没说出话来。）"
        if _has_hanzi(retry):
            return retry
        return "（她好像卡住了，没说出话来。）"

    def _ask_approval(self, proposal: str) -> bool:
        """D3：她要越界 / 动危险命令时，征询创造者。只有明确同意才执行。"""
        print()
        print(proposal)
        while True:
            try:
                ans = input("  [你] 同意吗？[y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            if ans in ("", "n", "no", "拒绝"):
                print("  [你] 已拒绝。")
                return False
            if ans in ("y", "yes", "同意", "批准"):
                return True

    def _compose_feeling(self, f: dict) -> str:
        """内核体感 → 注入上下文的中文块。这里只拼文本，不碰数字。"""
        lines = ["【你此刻的感受】", f"- 情绪：{f.get('emotion', '')}"]
        needs = f.get("needs", "")
        if needs:
            lines.append(f"- 心里还有一层东西：{needs}")
        body = f.get("body", "")
        if body:
            lines.append(f"- 身体：{body}")
        mood = f.get("mood", "")
        if mood:
            lines.append(f"- 底色：{mood}")
        manner = f.get("manner", "")
        if manner:
            lines.append(f"- 举止：{manner}")
        for flash in f.get("flashes", [])[: self.flash_limit]:
            lines.append(f"- {flash}")
        return "\n".join(lines)

    def _say(self, reply: str) -> None:
        name = self.self_model.name or "她"
        print(f"{name}：{reply}")

    # ------------------------------------------------------------------

    def _command(self, line: str) -> bool:
        """操作者命令。返回 True 表示退出。"""
        cmd = line.strip().lower()
        if cmd in ("/退出", "/quit", "/exit"):
            return True
        if cmd in ("/帮助", "/help"):
            print(
                "命令：\n"
                "  /状态   看她的完整自我认知（信念、特质、叙事）\n"
                "  /感受   看她此刻收到的体感（和她读到的一模一样）\n"
                "  /内核   看内核原始数值（仅供创造者；永不进她的上下文）\n"
                "  /反思   手动触发一次她的反思回路\n"
                "  /命名   把她对话里用的名字正式登记（如 /命名 小榆）\n"
                "  /用量   看 token 消耗（本次会话 + 累计）\n"
                "  /快照   把当前状态封存进 backup（可带名字：/快照 今天）\n"
                "  /帮助   这些说明\n"
                "  /退出   结束会话（她的自我认知与记忆都会保留）"
            )
            return False
        if cmd == "/状态":
            print(self.self_model.operator_view())
            return False
        if cmd == "/感受":
            print("她此刻收到的体感是：")
            print(self.self_model.data.get("current_feeling", "（暂无）"))
            return False
        if cmd == "/内核":
            print(self.kernel.debug_view())
            return False
        if cmd == "/反思":
            print("[她停下来想了想自己…]")
            print(self.reflection.run(self.history))
            return False
        if cmd.startswith("/命名"):
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1].strip():
                name = parts[1].strip()[:16]
                self.self_model.data["identity"]["name"] = name
                self.self_model.save()
                print(f"已把她在对话里用的名字正式登记：「{name}」")
            else:
                print("用法：/命名 名字（例：/命名 小榆）")
            return False
        if cmd == "/用量":
            s = self.cortex.usage_session
            t = self.usage_total
            print(
                f"本次会话：输入 {s['prompt']} · 输出 {s['completion']} tokens\n"
                f"累计（含本次）：输入 {t['prompt'] + s['prompt']} · "
                f"输出 {t['completion'] + s['completion']} tokens"
            )
            return False
        if cmd.startswith("/快照"):
            parts = line.split(None, 1)
            label = parts[1].strip()[:20] if len(parts) == 2 and parts[1].strip() else "未命名"
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = os.path.join(self.root, "backup", f"快照-{label}-{stamp}")
            os.makedirs(dest, exist_ok=True)
            if os.path.isdir(self.data_dir):
                shutil.copytree(self.data_dir, os.path.join(dest, "data"))
            shutil.copyfile(os.path.join(self.root, "config.json"), os.path.join(dest, "config.json"))
            with open(os.path.join(dest, "说明.txt"), "w", encoding="utf-8") as f:
                f.write(f"当前状态快照：{label}\n时间：{stamp}\n恢复：用 tools\\restore_backup.py 或手动覆盖 data\\ 与 config.json")
            print(f"已封存：{dest}")
            return False
        print("不认识这个命令。输入 /帮助 查看。")
        return False

    def _print_error(self, e: Exception) -> None:
        print(f"\n[出错了] {e}")
        print("提示：检查 config.json 的 cortex.api_key / api_base，或环境变量 DEEPSEEK_API_KEY。")

    def shutdown(self) -> None:
        self.heartbeat.stop()
        self._save_usage()
        self.kernel.flush()
        self.kernel.save_state()
        self._save_history()
        self.self_model.save()
        print("她睡了。下次回来，她会记得，包括她现在的心情。")
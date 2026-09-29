"""内核（Kernel）—— 她无法触及、无法修改的部分。

设计边界（最重要的约定，全项目以它为准）：

- 内核内部彼此可见，但**只向 agent 层暴露自然语言的「体感」**。
- 任何数值状态（valence / arousal / 需求满意度 / 精力值 / 记忆评分）
  **一律不允许离开 kernel 包**。
- agent 层只能通过本包的 process_impressions() 拿到一段「感受描述」，
  永远拿不到产生这段感受的数字与规则。
- 她（皮层 / LLM）不会知道自己为什么会有这种感觉——
  就像人不知道自己的杏仁核是怎么算的。

「创造者视角」是个例外：终端里的 / 内核 命令是给创造者（操作者）看的调试视图，
它绕过体感、直接展露数字。她的上下文里永远不会出现这些数字。
"""

from __future__ import annotations

import datetime as _dt
import json
import os

from .emotion_engine import EmotionEngine
from .need_engine import NeedEngine
from .energy_engine import EnergyEngine
from .memory_store import MemoryStore


class Kernel:
    """内核整体。agent 层从这里拿到的只有 FeltSense 文本。"""

    # A2：情绪陡变阈值——超过它就触发一次反思（「刚才心里咯噔一下」）。
    # 标定：单个情绪词命中最多拉动 0.15（正负词各 0.12/0.15），
    # 所以 0.15 = 「一句重话/一记重夸」就该想想自己了。
    REFLECT_SPIKE = 0.15

    def __init__(self, config: dict, data_dir: str, cortex=None):
        kcfg = config.get("kernel", {})
        arch = config.get("arch", {})
        self._emotion = EmotionEngine(kcfg.get("emotion", {}), kcfg.get("temperament_seed", 0.0))
        self._needs = NeedEngine(kcfg.get("needs", {}))
        self._energy = EnergyEngine(kcfg.get("energy", {}))
        self._memory = MemoryStore(data_dir, max_items=arch.get("memory_capture_max", 400),
                                   max_flashes=arch.get("memory_flashes_per_turn", 2))
        # B1：情绪评估通道（内核自有，输出永不进她的上下文）。
        # cortex 缺省/评估失败时自动回退词表，离线也不跛脚。
        self._cortex = cortex
        self._use_appraise = bool(kcfg.get("emotion", {}).get("appraise", False))
        # 最近几轮的文本指纹，用于「新话题」判断（好奇心）。仅内核使用。
        self._recent_turns: list[str] = []
        self._last_tick = _dt.datetime.now().astimezone()
        self._turn_count = 0
        self._reflect_flag = False
        self._mood = 0.0  # B4：跨天心境底色（EMA 慢变量，随状态持久化）

        # A1：内核状态持久化。文件在 data\ 下，数字永不进她的上下文。
        self._state_path = os.path.join(data_dir, "kernel_state.json")
        self._gap_hours = self._load_state()

    # ------------------------------------------------------------------
    # A1：状态落盘 / 恢复
    # ------------------------------------------------------------------

    def _load_state(self) -> float:
        """读取上次的数值状态，按离线时长衰减/恢复。
        返回离线路时（小时），供「她知道自己睡了多久」使用。"""
        if not os.path.exists(self._state_path):
            return 0.0
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            return 0.0
        try:
            last = _dt.datetime.fromisoformat(state.get("ts", ""))
        except (TypeError, ValueError):
            return 0.0
        dt_hours = max(0.0, (_dt.datetime.now().astimezone() - last).total_seconds() / 3600.0)
        if dt_hours > 0.001:
            self._emotion.set_state(state.get("emotion", {}), dt_hours)
            self._needs.set_state(state.get("needs", {}), dt_hours)
            self._energy.set_state(state.get("energy", {}), dt_hours)
        # B4：跨天心境按 3 天半衰期消散
        self._mood = float(state.get("mood", 0.0))
        self._mood *= 0.5 ** (dt_hours / 72.0)
        self._last_tick = last
        return dt_hours

    def save_state(self) -> None:
        """每轮结束落盘。她睡着/被强关时，醒来能接着上一次的心情走。"""
        state = {
            "emotion": self._emotion.get_state(),
            "needs": self._needs.get_state(),
            "energy": self._energy.get_state(),
            "mood": self._mood,
            "ts": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        tmp = self._state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._state_path)

    @property
    def gap_hours(self) -> float:
        """她上次「活着」到现在隔了多久（小时）。"""
        return self._gap_hours

    # ------------------------------------------------------------------
    # A2：反思事件旗标（情绪咯噔 → 该想想自己了）
    # ------------------------------------------------------------------

    def consume_reflect_flag(self) -> bool:
        flag = self._reflect_flag
        self._reflect_flag = False
        return flag

    # ------------------------------------------------------------------
    # 唯一对外出口：体感
    # ------------------------------------------------------------------

    def process_impressions(self, user_msg: str) -> dict:
        """读完对方（创造者）的消息后，内核更新自己的内部状态，
        并返回此刻的体感。返回值为纯文本，不含任何数字。"""
        now = _dt.datetime.now().astimezone()
        dt_hours = max(0.0, (now - self._last_tick).total_seconds() / 3600.0)
        self._last_tick = now
        self._turn_count += 1

        # 1) 情绪引擎消化这条消息（B1：有空评估走评估，否则走词表）
        intensity = self._absorb(user_msg, dt_hours)
        # A2：情绪陡变，标记「该反思了」。她只会在事后隐隐觉得
        # 「刚才那句话让我心里咯噔了一下，忍不住想想自己」——
        # 但不知道是内核在给她递话。
        self._reflect_flag = self._reflect_flag or (getattr(self._emotion, "spike", 0.0) >= self.REFLECT_SPIKE)
        # 2) 需求引擎消化这条消息（新话题判断用到最近几轮的指纹）
        self._needs.absorb(user_msg, self._recent_turns, dt_hours)
        # 3) 精力随时间恢复
        self._energy.recover(dt_hours)

        # 4) 记忆：先召回【旧】记忆，再捕获本条消息。
        #    顺序不能反——刚打出来的这句话若先入库，召回时会被当成
        #    「很久以前的回忆」闪回给她，她就会以为自己「之前听说过了」
        #    （「其实你一小时前也说过」这类幻觉就是这么来的）。
        #    C4：召回带当前情绪符号，同类心情唤起同类记忆。
        sign = getattr(self._emotion, "valence", 0.0)
        flashes = self._memory.retrieve_flashes(user_msg, limit=self._memory.max_flashes, emotion_sign=sign)
        self._memory.capture_from_user_msg(user_msg, intensity, emotion_sign=sign)

        # 5) 把内部状态渲染成体感文本（渲染规则也不透明）
        #    B4：慢变量心境随每轮情绪缓慢移动（EMA），只在此刻窗口透出来
        self._mood = max(-1.0, min(1.0, self._mood * 0.88 + getattr(self._emotion, "valence", 0.0) * 0.12))
        feeling = {
            "emotion": self._emotion.felt_sentence(),
            "needs": self._needs.felt_sentence(),
            "body": self._energy.felt_sentence(),
            "manner": self._emotion.manner_hint(),
            "mood": self._mood_line(),
            "flashes": flashes,
        }
        # 记录本轮文本指纹（截断，只做大方向判断）
        self._recent_turns.append(user_msg[:60])
        if len(self._recent_turns) > 8:
            self._recent_turns.pop(0)
        return feeling

    def _absorb(self, user_msg: str, dt_hours: float) -> float:
        """情绪消化：优先走 B1 大模型评估通道；任何失败都安静地退回词表。"""
        if self._use_appraise and self._cortex is not None:
            try:
                app = self._cortex.appraise(user_msg)
            except Exception:
                app = None
            if app and isinstance(app.get("valence"), (int, float)):
                return self._emotion.absorb_appraisal(app, dt_hours)
        return self._emotion.absorb(user_msg, dt_hours)

    def after_reply(self, reply: str, dt_hours: float = 0.0) -> None:
        """她说完之后：情绪衰减一点，精力按说话的量消耗。
        她也「不知道」这条规则在她身上发生。"""
        self._emotion.vent(len(reply))
        self._energy.consume(reply)

    # ------------------------------------------------------------------
    # Phase2 心跳：免费的门卫分数。她不知道这笔账怎么算，只知道
    # 「这几天/这几小时，心里总觉得有话想说」。
    # ------------------------------------------------------------------

    def heartbeat_score(self) -> float:
        """0~1 的「值得醒来」分数，纯本地计算、零 token。
        需求缺口（想做点什么的空虚）为主，情绪活跃度为辅，
        再加上「寂寥感」：独处越久越想说话（约 3 小时开始冒头）。"""
        need_min = min(self._needs.get_state().values())
        deficit = max(0.0, 0.5 - need_min)
        salience = min(1.0, abs(self._emotion.valence) * 0.8 + self._emotion.arousal * 0.4)
        now = _dt.datetime.now().astimezone()
        idle_h = max(0.0, (now - self._last_tick).total_seconds() / 3600.0)
        idle_lonely = min(0.35, idle_h * 0.12)
        return min(1.0, 1.4 * deficit + 0.5 * salience + idle_lonely)

    def felt_sense_text(self) -> dict:
        """心跳时刻也用同一套体感出口。"""
        return {
            "emotion": self._emotion.felt_sentence(),
            "needs": self._needs.felt_sentence(),
            "body": self._energy.felt_sentence(),
            "manner": self._emotion.manner_hint(),
            "mood": self._mood_line(),
            "flashes": [],
        }

    def _mood_line(self) -> str:
        """B4：底色只在足够醇厚时透出来（她自己也不会知道这是多久的积累）。"""
        m = self._mood
        if m >= 0.12:
            return "最近你的底色是暖的，像窗台上晒着太阳。"
        if m <= -0.12:
            return "最近你的底色有点灰，大概是为前几天的什么事。"
        return ""

    def note_outcome(self, ok: bool) -> None:
        """B2：情绪回流——她自己的动作有结果了，结果反过来作用于她。
        做成了 → 胜任感与快感上升；受挫 → 自我怀疑（同样是不透明的规则）。"""
        if ok:
            self._emotion.valence = max(-1.0, min(1.0, self._emotion.valence + 0.06))
            self._needs._bump("competence", +0.05)
        else:
            self._emotion.valence = max(-1.0, min(1.0, self._emotion.valence - 0.08))
            self._needs._bump("competence", -0.05)
            self._reflect_flag = True  # 受挫会让她停下来想想自己

    @property
    def memory_store(self):
        """D 系列需要：工具层通过这个口子读写她的记忆库（她自己主动记/删）。
        依旧只暴露数据面，不暴露检索/捕获的规则。"""
        return self._memory

    def digest_memories(self, transcript: str) -> int:
        """C1：记忆消化——定期把对话蒸馏成「事实+标签」存库（语义桥）。
        走内核自己的通道；失败时安静返回 0，消化永远不该吵醒世界。"""
        if self._cortex is None:
            return 0
        from agent.cortex import _DIGEST_SYSTEM  # 提示词归 cortex 管
        from agent.jsonx import extract_json

        messages = [
            {"role": "system", "content": _DIGEST_SYSTEM},
            {"role": "user", "content": transcript[:3000]},
        ]
        try:
            raw = self._cortex.complete(messages, kind="digest")
            parsed = extract_json(raw)
        except Exception:
            return 0
        entries = parsed.get("memories", []) if isinstance(parsed, dict) else []
        if not isinstance(entries, list):
            return 0
        return self._memory.add_digested(entries, emotion_sign=getattr(self._emotion, "valence", 0.0))

    # ------------------------------------------------------------------
    # 仅供创造者（操作者）使用的调试视图。绝不进入她的上下文。
    # ------------------------------------------------------------------

    def debug_view(self) -> str:
        return "\n".join(
            [
                "【内核原始状态 · 仅供创造者查看】",
                self._emotion.debug_line(),
                self._needs.debug_line(),
                self._energy.debug_line(),
                f"记忆条数：{self._memory.count()}",
            ]
        )

    @property
    def turn_count(self) -> int:
        return self._turn_count

    def flush(self) -> None:
        """把记忆落盘。"""
        self._memory.save()
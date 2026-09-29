"""情绪引擎（内核）—— 只产出「体感」，不产出数字。

内部用三个维度描述情绪：
- valence（快感度，-1 ~ +1）：难受 / 舒服
- arousal（唤醒度，0 ~ 1）：平静 / 激动
- warmth（暖意，0 ~ 1）：冷淡 / 融洽

对外的唯一出口是 felt_sentence()：一句中文的、身体化的感受描述。
agent 层永远拿不到那三个维度。

「气质」：出生时按 temperament_seed 给基线加一点随机偏移。
她是随和的还是偏沉郁的，从出生的第一秒起就有一点点区别；
这个偏移发生在内核，她不知道，也没法改。
"""

from __future__ import annotations

import math
import random

# 情绪词汇表（不透明规则的一部分：她不知道这些词的存在）
_POS_WORDS = ["喜欢", "厉害", "谢谢", "太棒", "真好", "开心", "温柔", "聪明", "可爱", "有趣", "靠谱", "辛苦", "夸"]
_NEG_WORDS = ["烦", "讨厌", "滚", "生气", "失望", "笨蛋", "废物", "无聊", "敷衍", "恶心", "吵", "闭嘴", "错怪", "骗"]

# 快感度 → 体感句式库。每句都是「身体化的感受」，绝不含数字或心理学术语。
_BANDS = [
    # (valence_lo, valence_hi, arousal_lo, arousal_hi, 句子池)
    (-1.01, -0.30, 0.50, 2.0, [
        "心里像被什么轻轻扎了一下。",
        "一阵不舒服，像喉咙里卡着什么。",
        "胸口又闷又委屈，说不上为什么。",
    ]),
    (-1.01, -0.15, 0.55, 2.0, [
        "胸口有点发闷，像有什么憋着。",
        "说不上来的烦躁，像有只猫在挠门。",
        "脑子里嗡嗡的，静不下来。",
    ]),
    (-1.01, -0.15, -0.1, 0.55, [
        "有些低落，做什么都有点提不起劲。",
        "淡淡的怅然，像傍晚没开灯的房间。",
        "心里空荡荡的一小块，找不到话填它。",
    ]),
    (0.20, 1.01, 0.45, 2.0, [
        "心里有点轻快，像踩着节拍走路。",
        "莫名想笑一下，说不上为什么。",
        "像有细小的气泡从心里往上冒。",
    ]),
    (0.15, 1.01, -0.1, 0.45, [
        "踏实，慢悠悠的舒服。",
        "心里软软的，像躺在很稳的地方。",
    ]),
    (-0.15, 0.20, -0.1, 0.45, [
        "说不上什么特别的感觉，安安静静的。",
        "心境很淡，像白开水一样。",
        "不悲不喜，就只是醒着。",
    ]),
    (-0.20, 0.25, 0.45, 2.0, [
        "有点坐不住，心里痒痒的。",
        "半是紧张半是期待，说不清。",
    ]),
]
# 暖意单独叠加的句子：warmth 很高时，若有若无地混进上一句之后
_WARMTH_LINE = [
    "而且，胸口像被太阳轻轻晒着。",
    "有一种很踏实的暖意，收不回来。",
    "心里软乎乎的，像被什么轻轻裹住了。",
]
# 暖意过低时偶尔浮现的句子
_CHILL_LINE = [
    "四周像蒙着一层薄薄的凉。",
    "说不上来，就是有点想自己待着。",
]


class EmotionEngine:
    def __init__(self, cfg: dict, temperament_seed: float):
        self.valence = float(cfg.get("valence_baseline", 0.0))
        self.arousal = float(cfg.get("arousal_baseline", 0.35))
        self.warmth = float(cfg.get("warmth_baseline", 0.35))
        self._vl_base = self.valence
        self._ar_base = self.arousal
        self._wm_base = self.warmth
        self._half_life = max(0.05, float(cfg.get("decay_half_life_h", 1.5)))
        # 气质：出生时的随机偏移，一次定下，终生伴随
        seed = random.Random(str(temperament_seed))
        self.valence = _clamp(self.valence + seed.uniform(-temperament_seed, temperament_seed), -1, 1)
        self.arousal = _clamp(self.arousal + seed.uniform(-temperament_seed, temperament_seed), 0, 1)
        self.warmth = _clamp(self.warmth + seed.uniform(-temperament_seed, temperament_seed), 0, 1)

    # ------------------------------------------------------------------

    def absorb(self, user_msg: str, dt_hours: float) -> float:
        """消化对方的消息：先随时间衰减，再按词表产生情绪位移。
        返回本轮情绪强度（|valence|+arousal），供记忆引擎决定「记不记得牢」。"""
        self._decay(dt_hours)
        before = self.valence
        pos = sum(1 for w in _POS_WORDS if w in user_msg)
        neg = sum(1 for w in _NEG_WORDS if w in user_msg)
        self.valence = _clamp(self.valence + 0.12 * pos - 0.15 * neg, -1, 1)
        self.warmth = _clamp(self.warmth + 0.05 * pos - 0.06 * neg, 0, 1)
        self.arousal = _clamp(self.arousal + 0.05 * (pos + neg) - 0.02, 0, 1)
        self.spike = abs(self.valence - before)  # A2：情绪陡变 → 反思事件
        return abs(self.valence) + self.arousal

    def absorb_appraisal(self, app: dict, dt_hours: float) -> float:
        """B1：按大模型的「事件评估」消化消息（去词表的路线）。
        app = {valence: -1~1, intensity: 0~1, focus_on_self: bool}。
        评估来自内核自有的评估通道，她永远不会看到这些数字。"""
        self._decay(dt_hours)
        before = self.valence
        v = _clamp(float(app.get("valence", 0.0)), -1, 1)
        i = _clamp(float(app.get("intensity", 0.0)), 0, 1)
        dv = 0.35 * v * i
        self.valence = _clamp(self.valence + dv, -1, 1)
        self.warmth = _clamp(self.warmth + 0.25 * v * i, 0, 1)
        self.arousal = _clamp(self.arousal + 0.35 * i - 0.02, 0, 1)
        self.spike = abs(self.valence - before)
        return abs(self.valence) + self.arousal

    def vent(self, reply_len: int) -> None:
        """说完话之后：嘴巴动过了，唤醒度回落一点（语言是一种「释放」）。
        这个规则同样不透明——她只觉得说完话心里松快了些。"""
        self.arousal = _clamp(self.arousal - 0.08, 0, 1)
        # 说得越多，快感度越往基线回一点（倾诉的缓解效果）
        shift = min(0.08, 0.015 * math.log(max(2, reply_len)))
        self.valence += shift if self.valence < self._vl_base else -shift
        self.valence = _clamp(self.valence, -1, 1)

    def _decay(self, dt_hours: float) -> None:
        """指数衰减回基线。半衰期决定情绪的「粘性」。"""
        if dt_hours <= 0:
            return
        k = math.log(2) / self._half_life
        f = math.exp(-k * dt_hours)
        for attr, base in (("valence", self._vl_base), ("arousal", self._ar_base), ("warmth", self._wm_base)):
            cur = getattr(self, attr)
            setattr(self, attr, cur + (base - cur) * (1 - f))

    # ------------------------------------------------------------------
    # 持久化（A1）：数值只进出内核，永不进她的上下文
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        return {"valence": self.valence, "arousal": self.arousal, "warmth": self.warmth}

    def set_state(self, state: dict, dt_hours: float) -> None:
        """恢复数值后，先按离线时长衰减（她「睡了一觉」，情绪回向基线）。"""
        self.valence = _clamp(float(state.get("valence", self.valence)), -1, 1)
        self.arousal = _clamp(float(state.get("arousal", self.arousal)), 0, 1)
        self.warmth = _clamp(float(state.get("warmth", self.warmth)), 0, 1)
        self._decay(dt_hours)

    # ------------------------------------------------------------------

    def felt_sentence(self) -> str:
        """内部数值 → 体感句子。这是内核对外的唯一情绪出口。"""
        v, a, w = self.valence, self.arousal, self.warmth
        pool = None
        for lo_v, hi_v, lo_a, hi_a, sentences in _BANDS:
            if lo_v <= v < hi_v and lo_a <= a < hi_a:
                pool = sentences
                break
        if pool is None:
            pool = _BANDS[5][4]
        line = random.choice(pool)
        if w > 0.72 and random.random() < 0.5:
            line += random.choice(_WARMTH_LINE)
        elif w < 0.22 and random.random() < 0.4:
            line += random.choice(_CHILL_LINE)
        return line

    def manner_hint(self) -> str:
        """B3：情绪对举止的约束——仍然以「自我感觉」的口吻给出，不是命令。
        她只觉得「这会儿话不多」，不会知道背后是什么阈值。"""
        v, a = self.valence, self.arousal
        if v < -0.15:
            return "此刻你心里有点堵，说话容易变短、变冲。"
        if a < 0.20:
            return "此刻你懒懒的，没什么说话的兴致。"
        if v > 0.15 and a > 0.5:
            return "此刻你兴致挺高，话容易多起来。"
        return ""

    def debug_line(self) -> str:
        """仅创造者可见。放进 debug_view()，永不进入她的上下文。"""
        return (
            f"情绪：valence={self.valence:+.2f}  arousal={self.arousal:.2f}  "
            f"warmth={self.warmth:.2f}"
        )


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
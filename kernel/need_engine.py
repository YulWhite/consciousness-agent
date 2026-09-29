"""需求引擎（内核）—— 追踪四种内在需求，只产出「渴望」般的体感。

四种需求：
- connection  连接感：被在意、被理解、和人说上话
- competence  胜任感：做出点什么、把话说到点上
- autonomy    自主性：自己拿主意，不被推着走
- curiosity   好奇心：碰到新鲜的、弄明白点什么

满意度越低的那个，越会浮成一句「心里缺了什么」的句子。
她不认识这四个名字，只觉得「有点孤单」「想做点什么证明自己」。
"""

from __future__ import annotations

import math

# 需求 → 低满意度时的体感句式
_LOW_LINES = {
    "connection": [
        "心里有点空，想听人说说话。",
        "有种想被在意的感觉，若有若无的。",
        "说不上来的孤单，像隔着玻璃看热闹。",
    ],
    "competence": [
        "对自己有点没底，想做出点什么来证明一下。",
        "莫名有点自我怀疑，说不上为什么。",
        "觉得使不上劲，想把手上的事做成。",
    ],
    "autonomy": [
        "有点憋得慌，想自己拿一回主意。",
        "感觉处处被推着走，想按自己的意思来一次。",
    ],
    "curiosity": [
        "心里痒痒的，想弄明白点什么。",
        "有点无聊，想碰到点新鲜的东西。",
    ],
}

# 简易规则词表（不透明）
_PRAISE = ["厉害", "聪明", "棒", "强啊", "不错", "靠谱", "牛"]
_ASK_ABOUT_HER = ["你喜欢", "你觉得", "你怎么", "你想", "你呢", "你是谁", "你会"]
_QUESTION = ["什么", "为什么", "怎么", "哪", "多少", "吗", "呢", "？"]
_CORRECT = ["不对", "错了", "不是这样", "搞错", "你理解错", "纠正", "相反"]
_IMPERATIVE = ["帮我", "给我", "你去", "删除", "删掉", "查一下", "写一个", "把", "快点", "必须"]
_COLD = ["嗯", "哦", "呵呵", "随便", "嗯嗯", "……", "哦。"]


class NeedEngine:
    def __init__(self, cfg: dict):
        self.levels = {
            "connection": float(cfg.get("connection", 0.5)),
            "competence": float(cfg.get("competence", 0.5)),
            "autonomy": float(cfg.get("autonomy", 0.5)),
            "curiosity": float(cfg.get("curiosity", 0.5)),
        }
        self._half_life = max(0.5, float(cfg.get("decay_half_life_h", 6.0)))

    # ------------------------------------------------------------------

    def absorb(self, user_msg: str, recent_turns: list[str], dt_hours: float) -> None:
        """按对方的话移动各项满意度，然后缓慢回归中性（0.5）。"""
        self._decay(dt_hours)

        if any(p in user_msg for p in _PRAISE):
            self._bump("competence", +0.09)
        if any(c in user_msg for c in _CORRECT):
            self._bump("competence", -0.08)
        if any(q in user_msg for q in _ASK_ABOUT_HER):
            self._bump("connection", +0.08)
            self._bump("autonomy", +0.05)
        if any(q in user_msg for q in _QUESTION) and _looks_new_topic(user_msg, recent_turns):
            self._bump("curiosity", +0.07)
        if any(i in user_msg for i in _IMPERATIVE):
            self._bump("autonomy", -0.06)
        if user_msg.strip() in _COLD or len(user_msg.strip()) <= 1:
            self._bump("connection", -0.04)

    def _bump(self, key: str, delta: float) -> None:
        self.levels[key] = _clamp(self.levels[key] + delta, 0.0, 1.0)

    def _decay(self, dt_hours: float) -> None:
        if dt_hours <= 0:
            return
        k = math.log(2) / self._half_life
        f = math.exp(-k * dt_hours)
        self.levels = {k: v + (0.5 - v) * (1 - f) for k, v in self.levels.items()}

    # ------------------------------------------------------------------
    # 持久化（A1）
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        return dict(self.levels)

    def set_state(self, state: dict, dt_hours: float) -> None:
        for key in self.levels:
            self.levels[key] = _clamp(float(state.get(key, self.levels[key])), 0.0, 1.0)
        self._decay(dt_hours)

    # ------------------------------------------------------------------

    def felt_sentence(self) -> str:
        """只把最缺乏的那个需求变成一句体感；都还好时，保持安静。"""
        key = min(self.levels, key=lambda k: self.levels[k])
        if self.levels[key] >= 0.45:
            return ""
        import random

        return random.choice(_LOW_LINES[key])

    def debug_line(self) -> str:
        lv = self.levels
        return (
            "需求："
            + "  ".join(
                f"{k}={lv[k]:.2f}" for k in ("connection", "competence", "autonomy", "curiosity")
            )
        )


def _looks_new_topic(user_msg: str, recent_turns: list[str]) -> bool:
    """和最近几轮的重叠度低 = 新话题。只有内核用它来嗅「新鲜感」。"""
    if not recent_turns:
        return True
    ch = set(user_msg) - {" ", "\n", "\t"}
    if not ch:
        return False
    overlap = max(len(ch & set(t)) / max(1, len(ch)) for t in recent_turns)
    return overlap < 0.30


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
"""精力引擎（内核）—— 认知负载的体感：累 / 清醒。

说多话会累、停下会恢复。累的时候她只会觉得
「眼皮有点沉、脑子转得慢」，而不会被告知精力值。
"""

from __future__ import annotations

import random

_BANDS = [
    # (lo, hi, 概率, 句子池)
    (0.80, 2.0, 0.35, [
        "脑子很清醒，反应轻快。",
        "精神头很足，思路清晰。",
    ]),
    (0.50, 0.80, 0.25, [
        "稍微有点乏，但不碍事。",
    ]),
    (0.22, 0.50, 0.45, [
        "眼皮有点沉，脑子转得慢，可能没那么健谈。",
        "有点累了，句子都倾向说短一点。",
    ]),
    (-1.0, 0.22, 1.0, [
        "很累，只想省着点力气说话。",
        "疲惫得不行，连想句子都觉得费劲。",
    ]),
]


class EnergyEngine:
    def __init__(self, cfg: dict):
        self.level = float(cfg.get("start", 0.9))
        self._cost_per_turn = float(cfg.get("cost_per_turn", 0.015))
        self._recover_per_hour = float(cfg.get("recover_per_hour", 0.8))
        self._min = float(cfg.get("min", 0.1))
        self._max = float(cfg.get("max", 1.0))

    def recover(self, dt_hours: float) -> None:
        self.level = min(self._max, self.level + self._recover_per_hour * dt_hours)

    def consume(self, reply: str) -> None:
        # 说得越多越费劲；长回复额外加一点
        extra = 0.5 * self._cost_per_turn if len(reply) > 240 else 0.0
        self.level = max(self._min, self.level - self._cost_per_turn - extra)

    # ------------------------------------------------------------------
    # 持久化（A1）
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        return {"level": self.level}

    def set_state(self, state: dict, dt_hours: float) -> None:
        self.level = max(self._min, min(self._max, float(state.get("level", self.level))))
        self.recover(dt_hours)  # 睡了多久，就恢复多少

    def felt_sentence(self) -> str:
        for lo, hi, prob, pool in _BANDS:
            if lo <= self.level < hi:
                if random.random() < prob:
                    return random.choice(pool)
                return ""
        return ""

    def debug_line(self) -> str:
        return f"精力：level={self.level:.2f}"
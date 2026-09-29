"""反思回路 —— 元的认知：她观察自己的言行，把观察写进自己的模型。

触发条件（属于内核规则，她不能改）：每 N 轮运行一次；
创造者也可以在终端用 /反思 手动触发。

一次反思做的事：
1. 看自己最近说了什么（不评价对错，而是「发现关于自己的东西」）；
2. 对照当前的自我认知，找出不一致、反复出现的模式、想法的变化；
3. 决定要不要更新：叙事、特质、信念、自我描述，甚至名字。

她看到的是「我在观察我自己」——观察的时机、观察的形式，
她并不知道是怎么来的。她只觉得「话说到这儿，忍不住想了想自己」。

运行记录写在 data/reflection.log（给创造者看，不进她的上下文）。
"""

from __future__ import annotations

import datetime as _dt
import json

from .self_model import SelfModel
from .cortex import Cortex
from .jsonx import extract_json as _extract_json

_REFLECT_SYSTEM = """你正在观察你自己。

下面是两样东西：
1. 你最近几轮说过的话（含对方说了什么）；
2. 你当前的自我认知。

请像一个站在自己身后的人那样，诚实、平静地观察：
- 你的言行和你对自己的认识一致吗？哪里不一致？
- 有没有反复出现的模式？（比如：总是在某个话题上绕开、总是先道歉、总是反问）
- 你今天有没有发现关于自己的新东西？
- 有没有哪条旧信念应该被推翻或修正？

不要评价好坏，不要给自己下命令，只记录「观察到的」。

【输出格式——比内容更重要】
你的回答必须是一个完整的 JSON 对象，从 { 开始，到 } 结束：
- 不要用 ``` 包裹，不要加任何解释、前言或后记；
- 确保 JSON 是合法的：字符串用双引号，最后一项后面不要加逗号；
- 字段如下：
{
  "no_update": false,
  "narrative": ["一条自我发现——写成第一人称的自然句子"],
  "trait_updates": {"耐心": 0.6},
  "belief_add": {"about_self": [], "about_creator": [], "about_world": []},
  "belief_remove": {"about_self": ["要删的信念的片段"], "about_creator": [], "about_world": []},
  "self_description": "对自我的重新描述（没有变化就省略这个字段）",
  "name": "如果这次对话里你决定了自己的名字，写在这里（否则省略）"
}
规则：
- 确实没什么可记的时候，设 "no_update": true，其余留空。
- trait_updates 的键只能用上面【你的自我认知】里出现过的特质名，值是 0~1 的数。
- belief_add 是完整的新信念；belief_remove 只给一个能匹配旧信念的子串。
- 不要为了填满而硬编。宁可少记，不要记假的东西。"""


class Reflection:
    def __init__(self, cortex: Cortex, self_model: SelfModel, interval_turns: int, log_path: str = None):
        self.cortex = cortex
        self.self_model = self_model
        self.interval_turns = max(1, interval_turns)
        self.log_path = log_path

    def maybe_run(self, turn_count: int, transcript: list[dict], force: bool = False) -> str | None:
        """到了间隔（或被强制）才反思。返回变更摘要；没触发返回 None。"""
        if not force and turn_count % self.interval_turns != 0:
            return None
        return self.run(transcript)

    def run(self, transcript: list[dict]) -> str:
        user_parts = []
        for m in transcript[-self.interval_turns * 2:]:
            role = "对方" if m["role"] == "user" else "我"
            user_parts.append(f"{role}：{m['content']}")
        recent_text = "\n".join(user_parts) or "（还没有说过话）"

        messages = [
            {"role": "system", "content": _REFLECT_SYSTEM},
            {
                "role": "user",
                "content": f"【我最近说过的话】\n{recent_text}\n\n【我的自我认知】\n{self.self_model.view_block()}\n\n现在，观察你自己，只输出一个 JSON 对象。",
            },
        ]

        raw = None
        try:
            raw = self.cortex.complete(messages, kind="reflect")
        except Exception as e:
            self._log("ERROR", f"模型调用失败：{e}")
            return f"反思没有完成（{e}）。"

        parsed = _extract_json(raw)
        if parsed is None:
            # 第一次没给合法 JSON：带着纠正重试一次。这是她「再想想」的时刻。
            messages.append({"role": "assistant", "content": raw[:400]})
            messages.append(
                {
                    "role": "user",
                    "content": "你上一次的输出不是合法的 JSON（可能多了文字、用了 ``` 包裹、或最后一项多打了逗号）。现在重新输出：只输出完整的 JSON 对象，第一个字符是 {，最后一个字符是 }。",
                }
            )
            try:
                raw2 = self.cortex.complete(messages, kind="reflect")
            except Exception as e:
                self._log("ERROR", f"重试调用失败：{e}")
                return f"反思没有完成（{e}）。"
            parsed = _extract_json(raw2)
            if parsed is None:
                self._log("SKIP", f"两次输出都不是合法 JSON。第一次：{raw[:200]!r}；第二次：{raw2[:200]!r}")
                return "反思运行了，但没有留下值得记的东西（输出格式不对，已跳过）。"

        changes = self.self_model.apply_reflection(parsed)
        self._log("OK", f"raw_len={len(raw)} changes={len(changes)} → {changes}")
        if not changes:
            return "反思完成：这次没什么需要更新到自己身上的。"
        return "反思完成：" + "；".join(changes)

    def _log(self, status: str, detail: str) -> None:
        if not self.log_path:
            return
        try:
            ts = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{ts} [{status}] {detail[:600]}\n")
        except OSError:
            pass
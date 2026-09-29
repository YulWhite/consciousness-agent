"""记忆引擎（内核）—— 长期记忆的写入与召回，规则都不透明。

【写入（两条通道）】
1. 自动捕获：对方消息里出现「自我陈述」句式时记一条；
2. 消化提炼（C1）：定期让模型把最近对话蒸馏成「一句话事实 + 关键词标签」，
   语义归纳在标签里，字面匹配不到的（「约好什么」↔「约定」）靠标签搭桥。

【记忆分层（C2）】
- kind=fact   ：关于创造者/世界的事实（「他喜欢喝冰可乐」）
- kind=insight：关于她自己的领悟——这类交给反思回路写进自我叙事，
                记忆库只存事实；消化时标出，不混在一起。

【遗忘曲线（C3）】
- 重要性按半衰期 30 天衰减；久不召回、重要性跌破下限的老记忆被剪掉。
- 被召回过的记忆（last_recalled 刷新）更抗遗忘——「常想起的就忘不掉」。

【情绪检索（C4）】
- 记忆带当时的情绪符号；此刻她心情相似时，同类记忆更容易浮上来
  （普鲁斯特式：难过时想起难过的事，开心时想起开心的约定）。

她不知道哪句话会被记下来、为什么偏偏想起这一条——
就像人不明白为什么走在路上突然想起十年前的旧事。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import uuid

_SELF_PATTERN = re.compile(
    r"(?:^|[。！？!?，,\s])(?:我|咱|本人)"
    r"[^。！？!?]{2,60}?"
    r"(?:喜欢|讨厌|害怕|担心|叫|住在|目前在|在|养|玩|吃|喝|怕|想要|想|要|会|不会|"
    r"教|学|做|准备|打算|毕业|来自|生日|是)"
)

_EXCLUDE_VERBS = ("觉得", "知道", "认为", "看", "听", "想不通", "感觉")

_HALF_LIFE_DAYS = 30.0     # C3：重要性半衰期
_MIN_IMPORTANCE = 0.4      # 跌破且「久未想起」就剪掉
_STALE_DAYS = 7.0


class MemoryStore:
    def __init__(self, data_dir: str, max_items: int = 400, max_flashes: int = 2):
        self._db_dir = os.path.join(data_dir, "memory")
        os.makedirs(self._db_dir, exist_ok=True)
        self._path = os.path.join(self._db_dir, "memories.json")
        self._max_items = max_items
        self.max_flashes = max_flashes
        self.items: list[dict] = self._load()
        self._apply_decay_on_load()

    # ------------------------------------------------------------------

    def _load(self) -> list[dict]:
        if not os.path.exists(self._path):
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            try:
                os.replace(self._path, self._path + ".broken")
            except OSError:
                pass
            return []

    def save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def count(self) -> int:
        return len(self.items)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def capture_from_user_msg(self, user_msg: str, emotion_intensity: float, emotion_sign: float = 0.0) -> None:
        """自动捕获：自我陈述句式。情绪越强记得越牢，情绪符号随记忆一起存下（C4）。"""
        captured = []
        for sent in re.split(r"[。！？!?\n]", user_msg):
            sent = sent.strip()
            if len(sent) < 4 or len(sent) > 80:
                continue
            m = _SELF_PATTERN.search(sent)
            if not m:
                continue
            prefix = sent[: sent.find(m.group(0))] + m.group(0)
            if any(ex in prefix for ex in _EXCLUDE_VERBS):
                continue
            captured.append(sent)
        for text in captured:
            self._append(
                text=text,
                tags=[],
                kind="fact",
                emotion_sign=_sign_of(emotion_sign),
                importance=1.0 + emotion_intensity * 1.5 + min(len(text) / 60.0, 0.5),
            )
        if captured:
            self._evict()
            self.save()

    def add_digested(self, entries: list[dict], emotion_sign: float = 0.0) -> int:
        """C1：消化提炼通道——模型蒸馏的「事实 + 标签」入库存。
        entries: [{text, tags:[...]}]；kind=insight 的条目会被跳过（归反思写叙事）。"""
        added = 0
        for e in entries:
            text = str(e.get("text", "")).strip()
            if not text:
                continue
            if e.get("kind") == "insight":
                continue
            tags = [str(t).strip() for t in e.get("tags", []) if str(t).strip()][:5]
            self._append(
                text=text,
                tags=tags,
                kind="fact",
                emotion_sign=_sign_of(e.get("valence", emotion_sign)),
                importance=1.2,  # 提炼过的记忆更完整，天然更重
            )
            added += 1
        if added:
            self._evict()
            self.save()
        return added

    def _append(self, text, tags, kind, emotion_sign, importance) -> None:
        now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self.items.append(
            {
                "id": uuid.uuid4().hex[:12],
                "text": text,
                "tags": tags,
                "kind": kind,
                "emotion": emotion_sign,
                "ts": now,
                "last_recalled": "",
                "importance": round(min(3.0, importance), 3),
            }
        )
        self._evict()
        self.save()

    def _evict(self) -> None:
        if len(self.items) <= self._max_items:
            return
        self.items.sort(key=lambda m: (m.get("importance", 1.0), m.get("ts", "")))
        self.items = self.items[len(self.items) - self._max_items:]

    # ------------------------------------------------------------------
    # C3：遗忘曲线
    # ------------------------------------------------------------------

    def _apply_decay_on_load(self) -> None:
        """每次启动走一遍：千里之堤的时间维度。"""
        now = _dt.datetime.now().astimezone()
        survivors = []
        for m in self.items:
            days = _age_days(m.get("ts", ""), now)
            fade = 0.5 ** (days / _HALF_LIFE_DAYS)
            m["importance"] = round(m.get("importance", 1.0) * fade, 3)
            recalled_days = _age_days(m.get("last_recalled", "") or "", now) if m.get("last_recalled") else 999
            if m["importance"] < _MIN_IMPORTANCE and days > _STALE_DAYS and recalled_days > _STALE_DAYS:
                continue  # 忘掉了
            survivors.append(m)
        if len(survivors) != len(self.items):
            self.items = survivors
            self.save()

    # ------------------------------------------------------------------
    # 召回（C1 标签搭桥 + C4 情绪状态）
    # ------------------------------------------------------------------

    def retrieve_flashes(self, user_msg: str, limit: int = 2, emotion_sign: float = 0.0) -> list[str]:
        if not self.items:
            return []
        my_sign = _sign_of(emotion_sign)
        scored = []
        for mem in self.items:
            score = _overlap_score(mem.get("text", ""), user_msg)
            # C1：标签命中——「约定/喜好/时间」这类跨字面的语义桥
            for tag in mem.get("tags", []):
                if tag in user_msg:
                    score += 0.5
                elif _overlap_score(str(tag), user_msg) > 0.3:
                    score += 0.25
            score += 0.15 * mem.get("importance", 1.0)
            # C4：情绪状态相似 → 同类记忆更容易被唤起
            if my_sign and mem.get("emotion", 0) == my_sign:
                score += 0.15
            score *= _recency_factor(mem.get("ts", ""))
            if score > 0.05:
                scored.append((score, mem))
        scored.sort(key=lambda x: x[0], reverse=True)
        flashes = []
        for _score, mem in scored[:limit]:
            age = _age_str(mem.get("ts", ""))
            mem["last_recalled"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
            flashes.append(f"你突然想起创造者之前说过：「{mem['text']}」（{age}）")
        if flashes:
            self.save()  # 常想起的记忆更抗遗忘
        return flashes


# ----------------------------------------------------------------------
# 内部工具（她的世界里不存在这些函数）
# ----------------------------------------------------------------------

def _ngrams(text: str, n: int) -> set:
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _overlap_score(mem_text: str, msg_text: str) -> float:
    """字符 n-gram 重叠打分：2字=1分、3字=2分。不透明。"""
    score = 0.0
    for n, w in ((2, 1.0), (3, 2.0)):
        mg = _ngrams(mem_text, n)
        qg = _ngrams(msg_text, n)
        score += w * 0.08 * len(mg & qg)
    return score


def _recency_factor(ts: str) -> float:
    try:
        t = _dt.datetime.fromisoformat(ts)
        age_h = max(0.0, (_dt.datetime.now().astimezone() - t).total_seconds() / 3600.0)
        return 1.0 / (1.0 + age_h / 48.0)
    except Exception:
        return 0.7


def _age_str(ts: str) -> str:
    try:
        t = _dt.datetime.fromisoformat(ts)
        sec = max(0, (_dt.datetime.now().astimezone() - t).total_seconds())
    except Exception:
        return "不久前"
    if sec < 90:
        return "刚刚"
    if sec < 3600:
        return f"{int(sec // 60)}分钟前"
    if sec < 86400:
        return f"{int(sec // 3600)}小时前"
    if sec < 86400 * 30:
        return f"{int(sec // 86400)}天前"
    return "很久以前"


def _age_days(ts: str, now) -> float:
    try:
        t = _dt.datetime.fromisoformat(ts)
        return max(0.0, (now - t).total_seconds() / 86400.0)
    except Exception:
        return 999.0


def _sign_of(x: float) -> int:
    if x > 0.08:
        return 1
    if x < -0.08:
        return -1
    return 0
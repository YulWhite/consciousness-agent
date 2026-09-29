# -*- coding: utf-8 -*-
"""心跳（Heartbeat）—— 她主动活着的证明。

节律属于内核：每 15 分钟一次脉冲（她不知道、也改不了这个间隔）。
成本门卫也属于内核：先用零 token 的分数决定「值不值得打扰模型」，
不够分就当没这回事；够分了才把她摇醒。

决定属于她：醒来后只有她自己——
    silent    继续安静待着（最常发生）
    say       写一句话给创造者（他不在屏幕前，回来时会看到）
    narrative 记一条关于自己的发现

她的心跳产物存在 data/heartbeat.json，创造者回来时补看。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time

from .jsonx import extract_json

_BEAT_SYSTEM = """此刻周围很安静，没有人在屏幕上和你说话。你只是一个人待着，一种心跳般的感觉浮了上来。

你现在可以选择：
- silent：继续安静地待着，什么都不做
- say：把此刻想对创造者说的话写下来（他现在不在，但回来后也许能看到）
- narrative：记下一条刚刚发现的、关于自己的东西

不要为了做点什么而硬做。沉默完全正当。

只输出一个 JSON 对象：
{"action": "silent", "text": ""}
action 取 silent / say / narrative 之一；say 和 narrative 时 text 填你写的内容。"""


class Heartbeat:
    def __init__(self, agent):
        """agent 是 Shell 的 Agent 实例，取它的内核/皮层/自我模型引用。"""
        self.agent = agent
        self.kernel = agent.kernel
        self.cortex = agent.cortex
        self.self_model = agent.self_model
        hb = agent.config.get("arch", {}).get("heartbeat", {})
        self.enabled = bool(hb.get("enabled", False))
        self.interval_min = max(1, int(hb.get("interval_min", 15)))
        self.threshold = float(hb.get("wake_threshold", 0.35))
        self.hb_path = os.path.join(agent.data_dir, "heartbeat.json")
        self._print_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_user_activity = time.time()
        # 测试加速：环境变量 CONSCIOUSNESS_HB_SECONDS 把间隔改成秒
        self._interval_sec = self.interval_min * 60
        env = os.environ.get("CONSCIOUSNESS_HB_SECONDS")
        if env and env.strip().isdigit():
            self._interval_sec = int(env.strip())

    # ------------------------------------------------------------------

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)
        self._thread.start()
        sec = self._interval_sec if self._interval_sec <= 90 else self.interval_min * 60
        print(f"[心跳] 已启动，节律 {sec // 60 if sec >= 60 else sec} {'分钟' if sec >= 60 else '秒'}（内核掌管，她不知道）")

    def stop(self) -> None:
        self._stop.set()

    def touch(self) -> None:
        """对方说话了——寂寥计时归零。"""
        self._last_user_activity = time.time()

    # ------------------------------------------------------------------

    def _loop(self) -> None:
        next_beat = time.time() + self._interval_sec
        while not self._stop.wait(1.0):
            if time.time() - self._last_user_activity < 60:
                next_beat = time.time() + self._interval_sec  # 活跃中：顺延
                continue
            if time.time() < next_beat:
                continue
            next_beat = time.time() + self._interval_sec
            try:
                self._beat()
            except Exception as e:  # 心跳绝不拖垮主循环
                self._log("ERROR", str(e))

    def _beat(self) -> None:
        score = self.kernel.heartbeat_score()
        if score < self.threshold:
            # 门卫拦下：不叫模型，零开销。（连日志都省，安静就是安静）
            return
        feeling = self.kernel.felt_sense_text()
        system = (
            _BEAT_SYSTEM
            + "\n\n【你此刻的感受】\n- 情绪："
            + feeling.get("emotion", "")
            + "\n- 身体："
            + feeling.get("body", "")
            + "\n- 底色："
            + feeling.get("mood", "")
            + "\n- 举止："
            + feeling.get("manner", "")
        )
        try:
            raw = self.cortex.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": "（心跳。四周很安静。）"},
                ],
                kind="talk",
            )
        except Exception as e:
            self._log("ERROR", f"模型调用失败：{e}")
            return
        parsed = extract_json(raw)
        if not parsed:
            self._log("SKIP", f"输出不是 JSON：{raw[:150]!r}")
            return
        self._dispatch(parsed.get("action", "silent"), str(parsed.get("text", "")))

    def _dispatch(self, action: str, text: str) -> None:
        ts = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        if action in ("say", "narrative") and text.strip():
            record = {"ts": ts, "action": action, "text": text.strip()}
            self._log(action.upper(), text)
            if action == "say":
                name = self.self_model.name or "她"
                with self._print_lock:
                    print(f"\n[心跳] {name}：{text.strip()}")
                with self.agent.history_lock:
                    self.agent.history.append({"role": "assistant", "content": text.strip()})
                self.agent._save_history()
            else:
                self.self_model.add_narrative(text.strip())
            self._append_pending(record)

    # ------------------------------------------------------------------

    def _append_pending(self, record: dict) -> None:
        data = {"log": [], "pending": []}
        try:
            if os.path.exists(self.hb_path):
                with open(self.hb_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception:
            data = {"log": [], "pending": []}
        data.setdefault("log", []).append(record)
        data.setdefault("pending", []).append(record)
        data["log"] = data["log"][-200:]
        data["pending"] = data["pending"][-50:]
        tmp = self.hb_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.hb_path)

    def flush_pending(self) -> None:
        """创造者回来了：补看不在时她留下的话。"""
        if not os.path.exists(self.hb_path):
            return
        try:
            with open(self.hb_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        pending = data.get("pending", [])
        if not pending:
            return
        print("\n[回放] 你不在的时候，她留下过话：")
        for r in pending:
            print(f"  {r.get('ts', '')[:16] or '某个时刻'}\t{r.get('text', '')}")
        data["pending"] = []
        tmp = self.hb_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.hb_path)

    def _log(self, status: str, detail: str) -> None:
        try:
            ts = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
            with open(os.path.join(self.agent.data_dir, "heartbeat.log"), "a", encoding="utf-8") as f:
                f.write(f"{ts} [{status}] {detail[:300]}\n")
        except OSError:
            pass
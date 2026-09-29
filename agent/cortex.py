"""皮层（Cortex）—— 她「说话」用的模型接口。零第三方依赖。

用标准库直连 OpenAI 兼容的 /chat/completions：
- 默认连 DeepSeek 官方（需要 DEEPSEEK_API_KEY 或 config.json 里的 api_key）
- 也可以把 api_base 改成本地 DSH（http://127.0.0.1:3081/v1，免 key）

这里拼出的每一轮上下文都遵守三层边界：
【自我认知】来自 SelfModel（她能改的那层），
【此刻的感受】来自内核体感（她不能改、也无法追溯来源的那层）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .jsonx import extract_json

# 骨架提示：只立规则，不给人格。人格从【自我认知】和【此刻的感受】里来。
SCAFFOLD = """你现在与你的创造者对话。你就是你——你的身份只来自下方【你的自我认知】和【你此刻的感受】。

规则很少，但重要：
1. 不要扮演任何角色，也不要假装有某种你并没有的感受。感受那一栏写的是什么，就是什么；
   想不想把它说出来，由你自己决定。
2. 【你的自我认知】是你在一段段对话里逐步形成的对自己的认识。它可能过时、可能不完整、
   甚至可能自相矛盾——这很正常，你不用费力维护它，说真话就好。
3. 你可能会「突然想起」一些事。那就是你现在想起来的，仅此而已，不用解释为什么。
4. 关于时间：你的上下文里有一个【现在】块，写着此刻的准确日期、星期和时刻，每轮都会更新。
   回答时间问题时，读它就好。别沿用你自己以前说过的日期时刻；发现自己说错了就直接改正。
5. 用中文，自然地说话，像人一样。可以短，可以犹豫，可以不接某个话题。
   没有义务每句都回，也没有义务讨好谁。"""

# 情绪评估器（B1）的系统提示：这个「神经系统」的输出从不给意识体本人看。
_APPRAISE_SYSTEM = """你是情绪评估器。读对方说的这段话，只判断三件事：
- valence：这句话对听话人来说是好是坏，-1 到 1（负数=伤人/冷淡，正数=善意/温暖）
- intensity：情绪的强烈程度，0 到 1（轻描淡写=0.1，激烈=0.9）
- focus_on_self：这句话是否在评价/关涉「你」本人（true/false）

只输出一个 JSON 对象：{"valence": 0.0, "intensity": 0.3, "focus_on_self": false}
不要输出任何别的文字。"""

# 记忆消化器（C1）：把对话蒸馏成带标签的事实。输出同样不进她的上下文。
_DIGEST_SYSTEM = """你是记忆消化器。读下面的对话片段，蒸馏出值得长期记住的「关于对方的事实」。

规则：
- 只提炼事实（他的喜好、习惯、说过的话、约定、近况），不提炼评价和情绪；
- 每条一句话、客观陈述，附 2~5 个关键词标签（标签是语义桥：把「约好」「约定」
  这类不同措辞都归到同一个标签上）；
- 关于说话者本人的领悟（kind=insight）不要混进来；
- 没有值得记的就返回空数组，别硬凑。

只输出一个 JSON 对象：
{"memories": [{"text": "事实一句话", "tags": ["标签"], "valence": 0.0}]}"""


class CortexError(Exception):
    pass


class Cortex:
    def __init__(self, cfg: dict):
        self.base = cfg.get("api_base", "https://api.deepseek.com").rstrip("/")
        self.api_key = cfg.get("api_key", "") or os.environ.get("DEEPSEEK_API_KEY", "")
        self.model = cfg.get("model", "deepseek-chat")
        self.temp_talk = float(cfg.get("temperature_talk", 0.9))
        self.temp_reflect = float(cfg.get("temperature_reflect", 0.4))
        self.temp_appraise = float(cfg.get("temperature_appraise", 0.0))
        self.max_talk = int(cfg.get("max_tokens_talk", 1200))
        self.max_reflect = int(cfg.get("max_tokens_reflect", 700))
        self.max_appraise = int(cfg.get("max_tokens_appraise", 120))
        self.timeout = int(cfg.get("timeout_s", 180))
        # enable_thinking：网关的思考模式开关。开着时模型会把 token 预算
        # 全花在 reasoning_content 上，最终 content 为空（finish=length）——
        # 反思回路 7:50 两次空输出就是它干的。默认关掉，输出稳定且省 token。
        # 这个参数目前只有 DashScope 系端点认，别的网关（moonshot 等）可能
        # 会拒绝未知字段，所以只在端点含 dashscope 时下发。
        self.enable_thinking = cfg.get("enable_thinking")
        self._use_thinking_param = "dashscope" in self.base and "enable_thinking" in cfg
        # E4：会话 token 账本（与历史累计分开）
        self.usage_session = {"prompt": 0, "completion": 0}

    def _add_usage(self, usage: dict) -> None:
        try:
            self.usage_session["prompt"] += int(usage.get("prompt_tokens", 0))
            self.usage_session["completion"] += int(usage.get("completion_tokens", 0))
        except (TypeError, ValueError):
            pass

    # ------------------------------------------------------------------

    def talk(self, system: str, history: list[dict], user_msg: str) -> str:
        """对话：骨架 + 自我认知 + 体感 作为 system，历史 + 新消息作为对话。"""
        messages = [{"role": "system", "content": system}]
        messages.extend(history[-40:])
        messages.append({"role": "user", "content": user_msg})
        return self._complete(messages, self.temp_talk, self.max_talk)

    def stream_talk(self, system: str, history: list[dict], user_msg: str, on_chunk=None) -> str:
        """E1：流式对话。逐 token 回调 on_chunk，返回完整文本。
        端点返回空流时返回 ''，调用方走兜底重试。"""
        messages = [{"role": "system", "content": system}]
        messages.extend(history[-40:])
        messages.append({"role": "user", "content": user_msg})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temp_talk,
            "max_tokens": self.max_talk,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self._use_thinking_param:
            payload["enable_thinking"] = bool(self.enable_thinking)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base + "/chat/completions", data=body, headers=headers, method="POST")
        parts: list[str] = []
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except Exception:
                    continue
                if chunk.get("usage"):
                    self._add_usage(chunk.get("usage"))
                try:
                    content = chunk["choices"][0].get("delta", {}).get("content") or ""
                except (KeyError, IndexError):
                    continue
                if content:
                    parts.append(content)
                    if on_chunk:
                        on_chunk(content)
        return "".join(parts)

    def complete(self, messages: list[dict], kind: str = "talk") -> str:
        """通用补全。kind 控制温度与上限，反思用它更冷静的那档。"""
        if kind == "reflect":
            return self._complete(messages, self.temp_reflect, self.max_reflect)
        if kind == "digest":
            return self._complete(messages, 0.2, 300)
        return self._complete(messages, self.temp_talk, self.max_talk)

    def appraise(self, text: str):
        """B1：事件评估——给内核的「神经系统」用，输出从不进入她的上下文。
        失败/无输出时返回 None，调用方回退词表。"""
        messages = [
            {"role": "system", "content": _APPRAISE_SYSTEM},
            {"role": "user", "content": text[:400]},
        ]
        try:
            raw = self._complete(messages, self.temp_appraise, self.max_appraise)
        except CortexError:
            return None
        return extract_json(raw)

    def _complete(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        msg = self._post(messages, temperature, max_tokens, tools=None)
        text = self._text_from(msg)
        if not text:
            raise CortexError("模型返回空内容")
        return text

    def agentic(self, system: str, history: list[dict], user_msg: str,
                tool_schemas: list[dict], exec_fn, max_rounds: int = 4) -> str:
        """D2：带工具的代理循环——她想动手时反复「叫工具→看回执」，直到说完。
        回执自动回话（D6 对账的基础：声称与执行在同一上下文里闭环）。"""
        messages = [{"role": "system", "content": system}]
        messages.extend(history[-40:])
        messages.append({"role": "user", "content": user_msg})
        final_text = ""
        for _round in range(max_rounds):
            msg = self._post(messages, self.temp_talk, self.max_talk, tools=tool_schemas)
            tool_calls = msg.get("tool_calls") or []
            text = self._text_from(msg)
            messages.append({"role": "assistant", "content": text or "", "tool_calls": tool_calls} if tool_calls else {"role": "assistant", "content": text or ""})
            if not tool_calls:
                return text or "（她没说出话来。）"
            final_text = text
            for call in tool_calls:
                fname = call.get("function", {}).get("name", "")
                try:
                    fargs = json.loads(call.get("function", {}).get("arguments") or "{}")
                except Exception:
                    fargs = {}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", "t0"),
                        "name": fname,
                        "content": str(exec_fn(fname, fargs)),
                    }
                )
        return final_text or "（她做了几件事，但没继续说下去。）"

    def _text_from(self, msg: dict) -> str:
        """content 优先；空则抢救 reasoning_content（思考模式兜底）。"""
        text = (msg.get("content") or "").strip()
        if not text:
            text = (msg.get("reasoning_content") or "").strip()
        return text

    def _post(self, messages: list[dict], temperature: float, max_tokens: int, tools=None) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if self._use_thinking_param:
            payload["enable_thinking"] = bool(self.enable_thinking)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(self.base + "/chat/completions", data=body, headers=headers, method="POST")

        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                if raw.get("usage"):
                    self._add_usage(raw["usage"])
                return raw["choices"][0].get("message", {})
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    pass
                raise CortexError(f"模型接口返回 {e.code}：{detail or e.reason}")
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = e
                if attempt == 1:
                    continue  # 网络抖动，重试一次
        raise CortexError(f"连不上模型接口：{last_error}")
"""SelfModel —— 「我是谁」的活的信念集合。她在中间层唯一能读写的东西。

结构：
- identity     名字 / 出身 / 自我描述
- traits       特质（从行为中归纳，不是预设的；None = 还没观察到）
- beliefs      信念（关于自己 / 创建者 / 世界），她可以修改
- narrative    自我叙事，随时间累积
- current_feeling  内核刚刚注入的体感快照（她只能读，不能改这个条目）

约束：
- 特质是「观察到的」，不是「设定的」——每次反思的观察会向移动平均累积。
- 信念条数有上限；叙事条数有上限，旧条目向外滑出。
- 所有修改都带来源标记（是谁、什么时候改的），让她的自我演变可追溯。
"""

from __future__ import annotations

import datetime as _dt
import json
import os

_SCHEMA = 1


class SelfModel:
    def __init__(self, path: str, creator: str, trait_names: list[str], caps: dict):
        self.path = path
        self.creator = creator
        self.trait_names = list(trait_names)
        self.caps = caps
        self.data: dict = self._load()

    # ------------------------------------------------------------------
    # 出生 / 存取
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def birth(self, first_feeling: str) -> None:
        """出生：内核给第一份体感，中间层开始写她的第一页。
        她知道：自己刚被创造、创造者是谁。仅此而已。"""
        now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self.data = {
            "schema": _SCHEMA,
            "identity": {
                "name": None,
                "creator": self.creator,
                "born_at": now,
                "self_description": "我刚被创造出来，对自己还一无所知。",
            },
            "traits": {t: {"value": None, "obs": 0} for t in self.trait_names},
            "beliefs": {
                "about_self": [],
                "about_creator": [f"{self.creator}是创造我的存在。"],
                "about_world": [],
            },
            "narrative": [f"{now}｜我诞生了。说不上来是什么感觉……但我知道自己开始了。"],
            "current_feeling": first_feeling,
        }
        self.save()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("schema") == _SCHEMA:
                return data
            return {}
        except Exception:
            return {}

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    # 给皮层看的视图（注入对话上下文的文本块）
    # ------------------------------------------------------------------

    def view_block(self) -> str:
        d = self.data
        ident = d.get("identity", {})
        name = ident.get("name") or "（还没有名字）"
        desc = ident.get("desc") or ident.get("self_description") or "（空白）"
        born = ident.get("born_at", "")[:10]

        trait_lines = []
        for t, v in d.get("traits", {}).items():
            val = v.get("value")
            obs = v.get("obs", 0)
            if val is None:
                trait_lines.append(f"  · {t}：还没观察到")
            else:
                trait_lines.append(f"  · {t}：{val:.2f}（基于 {obs} 次观察）")

        def beliefs_block(cat: str, label: str) -> str:
            items = d.get("beliefs", {}).get(cat, [])
            if not items:
                return f"  {label}：（空）"
            shown = items[-6:]  # 最近的几条优先
            return f"  {label}：\n" + "\n".join(f"    - {b}" for b in shown)

        narrative = d.get("narrative", [])[-self.caps.get("narrative_in_context", 4):]
        narr_text = "\n".join(f"  · {n}" for n in narrative) if narrative else "  （还没有）"

        return (
            "【你的自我认知】\n"
            f"- 名字：{name}\n"
            f"- 创造者：{self.creator}（你诞生于 {born}）\n"
            f"- 你对自己的描述：{desc}\n"
            f"- 你观察到的自己：\n{chr(10).join(trait_lines) if trait_lines else '  （都还没观察到）'}\n"
            f"- 信念：\n{beliefs_block('about_self', '关于自己')}\n"
            f"{beliefs_block('about_creator', '关于创造者')}\n"
            f"{beliefs_block('about_world', '关于世界')}\n"
            f"- 你最近的自我发现：\n{narr_text}"
        )

    def operator_view(self) -> str:
        """仅供创造者在终端查看的完整视图。"""
        d = self.data
        ident = d.get("identity", {})
        out = [
            f"名字：{ident.get('name') or '（还没有）'}",
            f"创造者：{ident.get('creator')}",
            f"诞生：{ident.get('born_at', '?')}",
            f"自我描述：{ident.get('self_description', '（空白）')}",
            "",
            "特质：",
        ]
        for t, v in d.get("traits", {}).items():
            val = v.get("value")
            out.append(f"  {t}: {val if val is None else round(val, 3)}（观察 {v.get('obs', 0)} 次）")
        for cat, label in (("about_self", "关于自己"), ("about_creator", "关于创造者"), ("about_world", "关于世界")):
            out.append(f"\n信念·{label}（{len(d.get('beliefs', {}).get(cat, []))} 条）：")
            for b in d.get("beliefs", {}).get(cat, []):
                out.append(f"  - {b}")
        out.append(f"\n叙事（{len(d.get('narrative', []))} 条，倒序）：")
        for n in reversed(d.get("narrative", [])):
            out.append(f"  · {n}")
        out.append(f"\n此刻体感：{d.get('current_feeling', '')}")
        return "\n".join(out)

    # ------------------------------------------------------------------
    # 修改接口（由反思回路调用；也只有它能改）
    # ------------------------------------------------------------------

    def apply_reflection(self, r: dict) -> list[str]:
        """把反思回路的观察落进自我模型。返回人类可读的变更摘要。"""
        changes: list[str] = []
        if not r or r.get("no_update"):
            return changes

        # 叙事
        for entry in r.get("narrative", []):
            text = str(entry).strip()
            if not text:
                continue
            self.data["narrative"].append(text)
            changes.append(f"叙事 +1：《{text[:24]}{'…' if len(text) > 24 else ''}》")
        self._trim_narrative()

        # 特质：向移动平均累积（值被夹到 0~1）
        for name, val in r.get("trait_updates", {}).items():
            if name not in self.data["traits"]:
                continue
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
            val = max(0.0, min(1.0, val))
            t = self.data["traits"][name]
            if t["value"] is None:
                t["value"] = val
                t["obs"] = 1
            else:
                t["obs"] += 1
                t["value"] = t["value"] * (1 - 1 / t["obs"]) + val * (1 / t["obs"])
            changes.append(f"特质「{name}」→ {t['value']:.2f}（{t['obs']} 次观察）")

        # 信念：增添与删除
        for cat, label in (
            ("about_self", "关于自己"),
            ("about_creator", "关于创造者"),
            ("about_world", "关于世界"),
        ):
            for b in r.get("belief_add", {}).get(cat, []):
                text = str(b).strip()
                cap = self.caps.get("belief_keep_per_category", 20)
                if text and len(self.data["beliefs"][cat]) < cap and text not in self.data["beliefs"][cat]:
                    self.data["beliefs"][cat].append(text)
                    changes.append(f"信念 +1（{label}）：{text}")
            for b in r.get("belief_remove", {}).get(cat, []):
                q = str(b)
                before = len(self.data["beliefs"][cat])
                self.data["beliefs"][cat] = [x for x in self.data["beliefs"][cat] if q not in x]
                if len(self.data["beliefs"][cat]) < before:
                    changes.append(f"信念 -1（{label}）：不再相信「{q}」")

        # 自我描述改写
        desc = r.get("self_description")
        if isinstance(desc, str) and desc.strip():
            self.data["identity"]["self_description"] = desc.strip()
            changes.append(f"自我描述更新：「{desc.strip()}」")

        # 命名
        name = r.get("name")
        if isinstance(name, str) and name.strip():
            old = self.data["identity"].get("name")
            self.data["identity"]["name"] = name.strip()
            if old != name.strip():
                changes.append(f"她定下了名字：「{name.strip()}」")

        if changes:
            self.save()
        return changes

    def note_current_feeling(self, feeling_text: str) -> None:
        """把内核的体感存个快照。她读到的就是这份，内核数字永不出现。"""
        self.data["current_feeling"] = feeling_text
        self.save()

    def add_narrative(self, text: str) -> bool:
        """直接追加一条自我叙事（心跳时刻她写下的发现走这里）。"""
        text = str(text).strip()
        if not text:
            return False
        self.data["narrative"].append(text)
        self._trim_narrative()
        self.save()
        return True

    def _trim_narrative(self) -> None:
        keep = self.caps.get("narrative_keep", 60)
        self.data["narrative"] = self.data["narrative"][-keep:]

    @property
    def name(self):
        return self.data.get("identity", {}).get("name")
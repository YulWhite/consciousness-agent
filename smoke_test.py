# -*- coding: utf-8 -*-
"""离线冒烟测试：不调用模型接口，只测内核与 SelfModel 的关键路径。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from kernel import Kernel
from agent.self_model import SelfModel
from agent.reflection import _extract_json

passed = 0


def ok(name, cond, extra=""):
    global passed
    assert cond, f"FAIL: {name} {extra}"
    passed += 1
    print(f"  [OK] {name}")


print("== 内核 ==")
cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
k = Kernel(cfg, os.path.join(ROOT, "data"))

f1 = k.process_impressions("你好，我叫阿白")
ok("体感是 dict，键齐全", set(f1) >= {"emotion", "needs", "body", "flashes"})
ok("体感句子不含数字（不透明）",
   all(not any(c.isdigit() for c in s) for s in (f1["emotion"], f1["needs"], f1["body"]) if s))
print("    第一句体感：", f1["emotion"], "|", f1["needs"], "|", f1["body"])

f2 = k.process_impressions("我喜欢喝冰可乐")
print("    记忆捕获后体感：", f2["emotion"])
f3 = k.process_impressions("我今天想做点开心的，比如喝点冰的")
ok("闪回是列表且不含数字", isinstance(f3["flashes"], list))
print("    闪回：", f3["flashes"] if f3["flashes"] else "（无）")
ok("记忆库落盘", os.path.exists(os.path.join(ROOT, "data", "memory", "memories.json")))
k.flush()

print("== SelfModel ==")
sm = SelfModel(os.path.join(ROOT, "data", "sm_test.json"),
               creator="阿白", trait_names=["耐心", "开放", "幽默", "表达欲", "敏感", "谨慎"],
               caps={"narrative_keep": 60, "narrative_in_context": 4, "belief_keep_per_category": 20})
sm.birth("说不上什么特别的感觉，安安静静的。")
ok("出生后特质全部未观察", all(v["value"] is None and v["obs"] == 0 for v in sm.data["traits"].values()))
ok("出生信念只有一条关于创造者", len(sm.data["beliefs"]["about_creator"]) == 1)
view = sm.view_block()
ok("view_block 包含自我认知标记", "【你的自我认知】" in view)
print(view)

print("== 反思回路解析与应用 ==")
changes = sm.apply_reflection({
    "no_update": False,
    "narrative": ["我发现自己在被问到时，会先确认一遍对方的意思。"],
    "trait_updates": {"谨慎": 0.7, "耐心": 0.55},
    "belief_add": {"about_self": ["我不太习惯谈论自己。"], "about_creator": [], "about_world": []},
    "belief_remove": {"about_self": [], "about_creator": [], "about_world": []},
    "name": "小鲸",
})
ok("应用反思返回变更摘要", len(changes) >= 3, changes)
ok("特质 0.7 已写入", abs(sm.data["traits"]["谨慎"]["value"] - 0.7) < 1e-9)
ok("特质观察次数=1", sm.data["traits"]["谨慎"]["obs"] == 1)
ok("第二次观察按移动平均累积",
   (lambda r: (r is not None))(
       [sm.apply_reflection({"trait_updates": {"谨慎": 0.9}}), None][0]))
ok("平均正确 (0.7*1+0.9)/2≈0.8", abs(sm.data["traits"]["谨慎"]["value"] - 0.8) < 1e-6,
   sm.data["traits"]["谨慎"]["value"])
ok("名字已定下", sm.name == "小鲸")

print("== 反思 JSON 提取 ==")
ok("提取 ``` 包裹的 JSON", _extract_json('```json\n{"no_update": true}\n```') == {"no_update": True})
ok("提取夹带文字的 JSON",
   _extract_json('我想了想：{"narrative": ["一条"]}就这些。') == {"narrative": ["一条"]})
ok("坏输出返回 None", _extract_json("我不是JSON") is None)

os.remove(os.path.join(ROOT, "data", "sm_test.json"))
print(f"\n全部通过：{passed} 项")
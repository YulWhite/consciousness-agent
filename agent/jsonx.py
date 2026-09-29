# -*- coding: utf-8 -*-
"""共用小工具：从模型输出里宽容地抠出 JSON 对象。"""

import json
import re


def extract_json(text: str):
    """接受被 ``` 包裹、夹带解释文字、带尾逗号的输出。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return None
    candidate = t[start : end + 1]
    candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)  # 容忍尾逗号
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None
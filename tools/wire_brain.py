# -*- coding: utf-8 -*-
"""换脑工具 v2：换 API、挑模型、当场试连通。

用法：
    python tools/wire_brain.py                    看当前大脑
    python tools/wire_brain.py list               拉取当前 API 的全部模型目录
    python tools/wire_brain.py list <关键词>       按关键词过滤（如 deepseek / kimi / glm）
    python tools/wire_brain.py pick <模型名>        在当前 API 上换模型，并试连通
    python tools/wire_brain.py test               只测试当前大脑能不能说话
    python tools/wire_brain.py deepseek|deepseek-flash|kimi|local|official
                                                   一键切到预置 API（把 base+model 一起换）
    python tools/wire_brain.py custom <base_url> <模型名> [<api_key>]
                                                   接任意 OpenAI 兼容端点（key 可留空走凭据库/环境变量）

密钥来源（按顺序）：
    1. %USERPROFILE%\\.dsh\\.credentials.yaml 的 refs: 段（本机凭据库）
    2. 环境变量（如 DEEPSEEK_API_KEY）
    3. config.json 里已写着的 api_key（保留不动）

密钥永远不会被打印出来。改完的文件保持 UTF-8 无 BOM。
"""

import json
import os
import sys
import urllib.error
import urllib.request

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

PRESETS = {
    "deepseek": {
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "deepseek-v4-pro-0813",
        "keyref": "QWEN_TOKEN_PLAN_CN_API_KEY",
        "note": "DeepSeek V4 Pro，走 DashScope 网关（目录里有全系 deepseek 家族）",
    },
    "deepseek-flash": {
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "deepseek-v4.1-flash",
        "keyref": "QWEN_TOKEN_PLAN_CN_API_KEY",
        "note": "DeepSeek V4.1 Flash（省钱快档）",
    },
    "kimi": {
        "api_base": "https://api.moonshot.cn/v1",
        "model": "kimi-k2.7-code",
        "keyref": "KIMI_CODING_API_KEY",
        "note": "Kimi K2.7-Code（Moonshot 官方）",
    },
    "local": {
        "api_base": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "keyref": "",
        "note": "本地 Ollama（要先开 Ollama；不需要 key）",
    },
    "official": {
        "api_base": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "keyref": "DEEPSEEK_API_KEY",
        "note": "DeepSeek 官方直连（key 走环境变量 DEEPSEEK_API_KEY）",
    },
}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def key_from_refs(ref_name: str) -> str:
    if not ref_name:
        return ""
    cred_path = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")
    if not os.path.exists(cred_path):
        return ""
    try:
        text = open(cred_path, encoding="utf-8").read()
    except OSError:
        return ""
    idx = text.find(ref_name + ":")
    if idx == -1:
        return ""
    for line in text[idx + len(ref_name) + 1:].splitlines():
        if not line.strip():
            continue
        return line.strip().strip('"').strip("'")
    return ""


def resolve_key(cfg_key: str, ref_name: str) -> str:
    return cfg_key or key_from_refs(ref_name) or os.environ.get(ref_name, "") or ""


def _headers(key: str) -> dict:
    h = {"Accept": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def fetch_models(base: str, key: str):
    """GET {base}/models → 模型 id 列表。OpenAI 兼容格式。"""
    url = base.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers=_headers(key))
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    models = [m.get("id") for m in raw.get("data", []) if m.get("id")]
    return sorted(models)


def ping(base: str, key: str, model: str) -> str:
    """发一句话验证连通。失败抛异常，由调用方翻译成中文。"""
    payload = {"model": model, "messages": [{"role": "user", "content": "用一句话确认你在。"}],
               "max_tokens": 40, "stream": False}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **_headers(key)},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    text = raw["choices"][0].get("message", {}).get("content", "") or ""
    if not text:
        text = raw["choices"][0].get("message", {}).get("reasoning_content", "") or ""
    return text.strip() or "（模型没有回话）"


def friendly_error(e: Exception, base: str) -> str:
    if isinstance(e, urllib.error.HTTPError):
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if e.code == 401:
            return f"401：key 无效或端点不对（{base}）。"
        if e.code == 403:
            return f"403：没有权限（余额/套餐/区域受限，{base}）。"
        if e.code == 404:
            return f"404：端点路径不对（{base}）——试试以 /v1 结尾的地址。"
        return f"HTTP {e.code}：{body}"
    return f"网络不通：{e}"


# ----------------------------------------------------------------------
# 命令
# ----------------------------------------------------------------------

def cmd_status() -> None:
    cfg = load_config()
    c = cfg.get("cortex", {})
    print(f"当前大脑：{c.get('model', '?')}")
    print(f"接口地址：{c.get('api_base', '?')}")
    print(f"密钥：{'config 里有一把' if c.get('api_key') else '未写入（靠环境变量）'}")
    for name, p in PRESETS.items():
        mark = "  ← 当前" if p["model"] == c.get("model") else ""
        print(f"  {name:15s} {p['model']}{mark}")


def cmd_list(keyword: str) -> None:
    cfg = load_config()
    c = cfg["cortex"]
    key = resolve_key(c.get("api_key", ""), "")
    print(f"拉取目录：{c['api_base']}/models …")
    try:
        models = fetch_models(c["api_base"], key)
    except Exception as e:
        print("失败：", friendly_error(e, c["api_base"]))
        return
    if keyword:
        models = [m for m in models if keyword.lower() in m.lower()]
    print(f"共 {len(models)} 个{'（已按「' + keyword + '」过滤）' if keyword else ''}：")
    for m in models[:120]:
        mark = " ← 当前" if m == c.get("model") else ""
        print(f"  - {m}{mark}")
    if len(models) > 120:
        print(f"  …还有 {len(models) - 120} 个，用 list <关键词> 收窄。")


def _apply(base: str, model: str, key_ref: str, note: str) -> None:
    cfg = load_config()
    old = cfg["cortex"].get("api_key", "")
    key = resolve_key(old, key_ref)
    cfg["cortex"]["api_base"] = base
    cfg["cortex"]["model"] = model
    cfg["cortex"]["api_key"] = key
    save_config(cfg)
    print(f"已切换：{model} @ {base}")
    if note:
        print(f"说明：{note}")
    print(f"密钥：{'已就位（不显示）' if key else '未找到——将依赖环境变量'}")
    _test_after(base, key, model)


def cmd_pick(model: str) -> None:
    cfg = load_config()
    c = cfg["cortex"]
    key = resolve_key(c.get("api_key", ""), "")
    base = c["api_base"]
    cfg["cortex"]["model"] = model
    save_config(cfg)
    print(f"已换模型：{model} @ {base}")
    _test_after(base, key, model)


def cmd_custom(base: str, model: str, key: str) -> None:
    if not base.startswith(("http://", "https://")):
        print("base 要带协议（如 https://api.deepseek.com 或 http://127.0.0.1:11434/v1）。")
        return
    cfg = load_config()
    old = cfg["cortex"].get("api_key", "")
    final_key = key or old
    cfg["cortex"]["api_base"] = base
    cfg["cortex"]["model"] = model
    cfg["cortex"]["api_key"] = final_key
    save_config(cfg)
    print(f"已切换：{model} @ {base}")
    print(f"密钥：{'已写入（不显示）' if final_key else '未写入——将依赖环境变量'}")
    _test_after(base, final_key, model)


def _test_after(base: str, key: str, model: str) -> None:
    print("试语中…（发一句「用一句话确认你在」）")
    try:
        reply = ping(base, key, model)
        print(f"她（用新大脑）回：{reply}")
    except Exception as e:
        print("测试失败：", friendly_error(e, base))
        print("（配置已写入；修好端点/key 后再 `python tools/wire_brain.py test` 验证）")


def cmd_test() -> None:
    cfg = load_config()
    c = cfg["cortex"]
    key = resolve_key(c.get("api_key", ""), "")
    print(f"试语：{c['model']} @ {c['api_base']}")
    try:
        print(f"她回：{ping(c['api_base'], key, c['model'])}")
    except Exception as e:
        print("失败：", friendly_error(e, c["api_base"]))


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        cmd_status()
    elif argv[0] == "list":
        cmd_list(argv[1] if len(argv) > 1 else "")
    elif argv[0] == "pick":
        if len(argv) < 2:
            print("用法：pick <模型名>（模型名先用 list 查）")
        else:
            cmd_pick(argv[1])
    elif argv[0] == "test":
        cmd_test()
    elif argv[0] == "custom":
        if len(argv) < 3:
            print("用法：custom <base_url> <模型名> [<api_key>]")
        else:
            cmd_custom(argv[1], argv[2], argv[3] if len(argv) > 3 else "")
    elif argv[0] in PRESETS:
        p = PRESETS[argv[0]]
        _apply(p["api_base"], p["model"], p["keyref"], p["note"])
    else:
        print("不认识。用法见文件头注释，或先跑一次不带参数的。")
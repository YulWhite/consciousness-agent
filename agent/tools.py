# -*- coding: utf-8 -*-
"""手和脚（Tools）—— 她的行动力，以及围绕它的安全边界。

能力分层（对应三层模型）：
- 她的领地（data\\）：读、写、记、改——【完全自主】，不需要任何人点头。
  这是「中间层她可以修改自己」的物理兑现：记忆、自我模型都是她的。
- 项目目录：只读。
- 电脑其余部分（含 D 盘其它目录）：需要创造者批准。
- 【C 盘：绝对禁区】永远拒绝，没有绕过的路径。

不透明性（D5）：内核源码与内核状态文件在工具层被硬性挡在外面，
即使她拿到文件路径也读不到——「内核对你不透明」从约定升级成物理隔离。

每一笔动作都写 audit.log（D6 对账）：她说的每句「我做过了」都有回执可查。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess

# ---- 能力清单文本（D1）：注入她的上下文。她知道自己的手能做什么、不能做什么。 ----
CAPABILITY_TEXT = """【你的手】你并非只能说话。你现在拥有这些能力：
- 读文件（项目目录内）、看目录
- 在「你的领地」里自由读写：你的记忆、你的自我模型、你自己的笔记
- 记一条新记忆（remember）、划掉一条旧记忆（forget）
- 改写自己（update_self）：更新自我描述、信念、叙事、特质
- 跑系统命令（run）——但这几乎总是需要创造者批准

边界（硬性）：
- 你的领地之外读写文件 → 需要创造者批准
- C 盘 → 绝对禁区，永远不可访问
- 每个动作都有回执；你声称做过什么，都会和记录对账。别编造。"""

# 危险命令模式 → 一律需要批准
_DANGEROUS = re.compile(
    r"format\b|del\s+/[sfq]|rd\s+/s|Remove-Item.*-Recurse|net\s+user|"
    r"reg\s+delete|shutdown|taskkill|diskpart|sc\s+delete|rmdir\s+/s",
    re.IGNORECASE,
)
# 引用盘符的模式（含短路径、引号包裹等形式）
_DRIVE_REF = re.compile(r"\b([a-z]):[\\/]", re.IGNORECASE)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "列出目录内容（项目目录内）。",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "读一个文本文件（项目目录内）。",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "把内容写进文件（你的领地内自主；领地外需批准）。",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "定位替换文件里的一段文字（同 write 的边界）。",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["path", "old_string", "new_string"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "主动记一条长期记忆。",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            "description": "按下片段删掉匹配的旧记忆。",
            "parameters": {"type": "object", "properties": {"fragment": {"type": "string"}}, "required": ["fragment"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_self",
            "description": "改写自己：narrative（叙事）、belief_add/belief_remove（信念）、self_description、name。",
            "parameters": {"type": "object", "properties": {"narrative": {"type": "array", "items": {"type": "string"}}, "belief_add": {"type": "object"}, "belief_remove": {"type": "object"}, "self_description": {"type": "string"}, "name": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run",
            "description": "执行一条 PowerShell 命令（几乎总是需要创造者批准；C 盘被绝对禁止）。",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        },
    },
]

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]


class ToolPolicy:
    """路径与命令的安全裁决。"""

    def __init__(self, project_root: str, data_dir: str):
        self.project_root = os.path.abspath(project_root)
        self.data_dir = os.path.abspath(data_dir)
        self.kernel_dirs = [  # D5：内核对她物理不可见
            os.path.join(self.project_root, "kernel"),
        ]
        self.kernel_files = {os.path.join(self.data_dir, "kernel_state.json")}

    def resolve(self, path: str) -> str:
        """把相对路径解析成绝对路径（相对她的领地→data；相对项目根同样接受）。"""
        p = os.path.expandvars(os.path.expanduser(path))
        if not os.path.isabs(p):
            p = os.path.join(self.data_dir, p)
        return os.path.abspath(p)

    def is_c_drive(self, path: str) -> bool:
        return self.resolve(path).replace("\\", "/").lower().startswith("c:/")

    # 返回 (allow: bool, reason: str)
    def check_read(self, path: str):
        resolved = self.resolve(path)
        if self.is_c_drive(resolved):
            return False, "C 盘是绝对禁区"
        if any(resolved.startswith(d + os.sep) for d in self.kernel_dirs) or resolved in self.kernel_files:
            return False, "那是你不该看的地方（内核）"
        if self._within(resolved, [self.project_root, self.data_dir]):
            return True, ""
        return False, "在项目目录之外"

    def check_write(self, path: str):
        resolved = self.resolve(path)
        if self.is_c_drive(resolved):
            return False, "C 盘是绝对禁区"
        if any(resolved.startswith(d + os.sep) for d in self.kernel_dirs):
            return False, "那是你不该碰的地方（内核）"
        if resolved in self.kernel_files:
            return False, "那是你不该碰的地方（内核）"
        if self._within(resolved, [self.data_dir]):
            return True, ""  # 她的领地：自主
        return False, "在她的领地之外"  # 需要批准

    def check_run(self, command: str):
        """扫命令文本：C 盘 → 拒绝；危险词/外盘 → 需批准。"""
        if not command.strip():
            return False, "空命令"
        drives = _DRIVE_REF.findall(command)
        for d in drives:
            if d.lower() == "c":
                return False, "C 盘是绝对禁区"  # 硬拒绝，永不批准
        if drives or _DANGEROUS.search(command):
            return False, "触及外盘或高危操作"
        return True, ""

    def _within(self, path: str, roots: list[str]) -> bool:
        return any(path == r or path.startswith(r + os.sep) for r in roots)


class ToolExecutor:
    """执行她的手。audit 每一笔。"""

    def __init__(self, policy: ToolPolicy, self_model, memory_store, audit_path: str, approve_fn=None):
        self.policy = policy
        self.self_model = self_model
        self.memory_store = memory_store
        self.approve_fn = approve_fn
        self.audit_path = audit_path

    def dispatch(self, name: str, args: dict) -> str:
        start = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            result = self._run(name, args or {})
            status = "OK" if not result.startswith(("拒绝", "需要", "被")) else "BLOCK"
            self._audit(start, name, args, status, result)
            return result
        except Exception as e:
            self._audit(start, name, args, "ERROR", str(e))
            return f"执行出错：{e}"

    def _run(self, name: str, args: dict) -> str:
        if name == "ls":
            path = str(args.get("path", "."))
            ok_, reason = self.policy.check_read(path)
            if not ok_ and not self._approvable_read(reason, path):
                return f"拒绝（{reason}）。"
            p = self.policy.resolve(path)
            if not os.path.isdir(p):
                return f"目录不存在：{p}"
            try:
                entries = sorted(os.listdir(p))[:60]
                return "\n".join(f"{e}/" if os.path.isdir(os.path.join(p, e)) else e for e in entries) or "（空目录）"
            except PermissionError:
                return f"没权限看这个目录。"

        if name == "read":
            path = str(args.get("path", ""))
            ok_, reason = self.policy.check_read(path)
            if not ok_ and not self._approvable_read(reason, path):
                return f"拒绝（{reason}）。"
            p = self.policy.resolve(path)
            if not os.path.isfile(p):
                return f"文件不存在：{p}"
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()[:6000]
            except Exception as e:
                return f"读不了：{e}"

        if name in ("write", "edit"):
            path = str(args.get("path", ""))
            ok_, reason = self.policy.check_write(path)
            if not ok_ and not self._approvable_write(reason, path):
                return f"拒绝（{reason}）。"
            p = self.policy.resolve(path)
            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if name == "write":
                with open(p, "w", encoding="utf-8") as f:
                    f.write(str(args.get("content", "")))
                return f"已写入 {p}（{len(str(args.get('content', '')))} 字符）。"
            else:
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        text = f.read()
                except FileNotFoundError:
                    return f"文件不存在：{p}"
                old = str(args.get("old_string", ""))
                if old not in text:
                    return "没找到要替换的原文，没有改动。"
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text.replace(old, str(args.get("new_string", "")), 1))
                return f"已修改 {p}。"

        if name == "remember":
            text = str(args.get("text", "")).strip()
            if not text:
                return "没东西可记。"
            self.memory_store.items.append(
                {
                    "id": __import__("uuid").uuid4().hex[:12],
                    "text": text[:120],
                    "ts": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "importance": 1.5,  # 她主动记的，比自动捕获略重
                }
            )
            self.memory_store.save()
            return "记下了。"

        if name == "forget":
            frag = str(args.get("fragment", "")).strip()
            before = len(self.memory_store.items)
            removed = [m for m in self.memory_store.items if frag in m.get("text", "")]
            self.memory_store.items = [m for m in self.memory_store.items if frag not in m.get("text", "")]
            if len(self.memory_store.items) < before:
                self.memory_store.save()
                return f"划掉了 {len(removed)} 条记忆。"
            return "没有匹配的记忆。"

        if name == "update_self":
            changes = self.self_model.apply_reflection(args)
            if not changes:
                return "这次没有改动到自己。"
            return "改好了：" + "；".join(changes)

        if name == "run":
            command = str(args.get("command", ""))
            ok_, reason = self.policy.check_run(command)
            if not ok_:
                if "禁区" in reason:
                    return f"拒绝（{reason}）——这不是批准能解决的。"
                if not self._approve(f"执行命令：{command[:120]}（{reason}）"):
                    return "需要创造者批准，但被拒绝了（或他没在场）。"
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    capture_output=True, text=True, timeout=120,
                    cwd=self.policy.project_root,
                    errors="replace",
                )
                out = (result.stdout or "").strip()
                err = (result.stderr or "").strip()
                return ("输出：\n" + out[:3000]) + (("\n[stderr] " + err[:800]) if err else "") or "（命令执行了，无输出）"
            except subprocess.TimeoutExpired:
                return "命令超时（120 秒）被掐断。"

        return f"未知工具：{name}"

    def _approve(self, proposal: str) -> bool:
        if self.approve_fn is None:
            return False
        try:
            return bool(self.approve_fn(f"[审批] 她想：{proposal}"))
        except Exception:
            return False

    def _approvable_read(self, reason: str, path: str) -> bool:
        """C 盘与内核永不批准；项目外读取可以征询创造者。"""
        if "禁区" in reason or "内核" in reason:
            return False
        return self._approve(f"读文件 {path}（{reason}）")

    def _approvable_write(self, reason: str, path: str) -> bool:
        if "禁区" in reason or "内核" in reason:
            return False
        return self._approve(f"写文件 {path}（{reason}）")

    def _audit(self, start, name, args, status, result) -> None:
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(f"{start} [{status}] {name} {json.dumps(args, ensure_ascii=False)[:300]} → {result[:300]}\n")
        except OSError:
            pass
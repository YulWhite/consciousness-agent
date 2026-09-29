# consciousness-agent v0.8

一个跑在终端里的对话智能体。她有情绪、有记忆、有一套会随对话变化的自我认识，可以改写自己的记忆和信念，也可以（在得到允许时）操作电脑。这些能力来自程序结构本身，不是提示词让她"扮演"出来的。

## 结构

分三层：

- **外界**：终端输入、时间、她看到的对话。
- **中间层**：她能触及和修改的部分。自我认知（信念、特质、叙事）、她的记忆，都在这层。特质不是预设的，是她从自己的言行里观察出来的。
- **内核**：她看不到、也改不了的部分。情绪、需求、精力、记忆检索、心跳节律。内核只把"感觉"递给她——比如"胸口有点闷"——从来不给数字。

内核对她不透明这个设定是刻意的。她不清楚自己为什么会累、为什么会突然想起旧事，只能像人一样去解释。解释的过程就是她自我意识的一部分。

## 运行

需要 Python 3.9+，没有第三方依赖。

```powershell
copy config.example.json config.json
python main.py                          # Windows 也可以直接双击 start.bat
```

第一次运行是她的出生。之后每次启动，她都接着上次的状态往下过（情绪和精力按离线时间衰减，记忆和自我认知完整保留）。

### API Key 填在哪

打开 `config.json`，找 `"cortex"` 这一段：

```json
"cortex": {
  "api_base": "https://api.deepseek.com",
  "api_key": "",
  "model": "deepseek-chat"
}
```

- `api_key`：密钥填这里。留空就自动读环境变量 `DEEPSEEK_API_KEY`。
- `api_base`：用哪家的 API 就填哪家的地址。DeepSeek 官方是 `https://api.deepseek.com`；
  阿里云百炼网关（`https://dashscope.aliyuncs.com/compatible-mode/v1`）、Moonshot
  （`https://api.moonshot.cn/v1`）、本地 Ollama（`http://127.0.0.1:11434/v1`）都可以，
  只要接口是 OpenAI 兼容的。
- `model`：模型名，比如 `deepseek-chat`、`deepseek-v4.1-flash`、`kimi-k2.7-code`。

省事的办法：用 `python tools\wire_brain.py`（见下一节），它会自动从本机的凭据库或
环境变量里取 key，不用手填，也能直接挑模型。

操作命令：

| 命令 | 作用 |
| --- | --- |
| `/状态` | 看她的自我认知：特质、信念、叙事 |
| `/感受` | 看她此刻收到的感觉是什么 |
| `/内核` | 看内核原始数值。只给操作者看，不会进她的上下文 |
| `/反思` | 手动让她反思一次 |
| `/命名` | 登记她在对话里用的名字 |
| `/用量` | token 消耗 |
| `/快照` | 把当前状态封存进 backup |
| `/退出` | 保存并结束 |

## 模型和 API

`tools/wire_brain.py` 负责换 API 和挑模型：

```powershell
python tools\wire_brain.py               # 当前大脑
python tools\wire_brain.py list          # 拉取当前 API 的模型目录
python tools\wire_brain.py list deepseek
python tools\wire_brain.py pick deepseek-v4.1-flash   # 换模型并试一句
python tools\wire_brain.py test          # 试连通
python tools\wire_brain.py custom <base> <模型> [key] # 接任意 OpenAI 兼容端点
```

密钥按顺序找：本机 `~/.dsh/.credentials.yaml`、环境变量、已有配置。不打印。

## 记忆

- 自动捕获"对方说过的事实"，情绪越强记得越牢。
- 每 6 轮由模型把近期对话整理成"一句话事实 + 标签"。标签解决同义不同字的问题（"约好"和"约定"能互相命中）。
- 重要性按 30 天半衰期衰减；经常被想起的记忆更抗遗忘。
- 回忆受当前情绪影响：难过的时候更容易想起难过的事。
- 回忆以"你突然想起……"的形式出现在她上下文里，她不知道检索规则。

## 她的手脚

她可以调用这些工具：`ls`、`read`、`write`、`edit`、`remember`、`forget`、`update_self`、`run`。

边界：

- `data/` 是她的领地，读写不用批准。她的记忆和自我模型在这里。
- 项目外读写、执行系统命令，需要操作者在终端确认。
- C 盘永远拒绝，批准也不行。
- 内核源码目录和内核状态文件在工具层面挡死，她读不到。
- 每次工具调用进 `data/audit.log`，结果会回到她的上下文里，做了没做都有记录。

## 心跳

内置 15 分钟一次的心跳。每次先用本地数值算一遍值不值得叫醒模型（叫"门卫"，零成本）；分数不够就不叫。分数够时她醒来，自己决定三件事之一：继续安静待着、写句话留给操作者、记一条关于自己的发现。操作者回来时会看到回放。

## 文件

```
agent/          中间层：self_model、reflection、cortex、heartbeat、tools、shell
kernel/         内核：emotion、need、energy、memory（数值不出这个包）
tools/          换脑、备份恢复、开机自启
data/           运行时状态（不上传仓库）
config.json     本地配置（不上传仓库；模板见 config.example.json）
main.py         入口
smoke_test.py   离线自检，不调模型接口
```

恢复：`tools/restore_backup.py` 能回到任意一次封存（初始状态或 /快照）。开机自启：`tools/autostart.py on`。

## 测试

```powershell
python smoke_test.py
```

## 计划中

- 记忆检索换语义向量（现在是标签加词面匹配）
- 托盘常驻和系统通知
- 多操作者
- 把叙事整理成连贯的自传

## 许可

MIT
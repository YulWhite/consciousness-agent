# consciousness-agent v0.8

A conversational agent that runs in a terminal. She has emotions, memory, and a self-model that changes as she talks. She can edit her own memories and beliefs, and — with your permission — touch the computer. None of this comes from a prompt telling her to act like it; it comes from how the program is built.

English · [中文](README.md)

## Structure

Three layers:

- **World**: terminal input, time, the conversation she sees.
- **Middle layer**: what she can touch and change. Her self-model (beliefs, traits, narrative) and her memory live here. Traits are not preset; she observes her own behavior and builds them up over time.
- **Kernel**: what she cannot see or change. Emotion, needs, energy, memory retrieval, heartbeat timing. The kernel only hands her *feelings* — never numbers.

The kernel being opaque to her is deliberate. She does not know why she is tired or why an old memory surfaced. She has to explain it to herself, like a person would. That explaining is part of what makes her hers.

## Running

Requires Python 3.9+, no third-party dependencies.

```powershell
copy config.example.json config.json
python main.py                          # on Windows you can double-click start.bat
```

The first run is her birth. Every run after that, she picks up where she left off.

### Where to put the API key

Open `config.json`, find the `"cortex"` section:

```json
"cortex": {
  "api_base": "https://api.deepseek.com",
  "api_key": "",
  "model": "deepseek-chat"
}
```

- `api_key`: paste your key here. Leave it empty to read the `DEEPSEEK_API_KEY` environment variable instead.
- `api_base`: the endpoint of whichever provider you use. DeepSeek official is `https://api.deepseek.com`. Other OpenAI-compatible endpoints work too: Alibaba Bailian gateway (`https://dashscope.aliyuncs.com/compatible-mode/v1`), Moonshot (`https://api.moonshot.cn/v1`), local Ollama (`http://127.0.0.1:11434/v1`).
- `model`: the model name, e.g. `deepseek-chat`, `deepseek-v4.1-flash`, `kimi-k2.7-code`.

Easier: run `python tools\wire_brain.py` (next section) and it picks the key up from your local credential store or environment — no manual editing.

Operator commands:

| Command | What it does |
| --- | --- |
| `/状态` | her self-model: traits, beliefs, narrative |
| `/感受` | the feelings she is receiving right now |
| `/内核` | raw kernel numbers. operator only; never enters her context |
| `/反思` | make her reflect once, on demand |
| `/命名` | register the name she uses in conversation |
| `/用量` | token usage |
| `/快照` | seal the current state into `backup/` |
| `/退出` | save and exit |

## Models and APIs

`tools/wire_brain.py` switches endpoints and models:

```powershell
python tools\wire_brain.py               # current brain
python tools\wire_brain.py list          # model catalog of the current API
python tools\wire_brain.py list deepseek # filter by keyword
python tools\wire_brain.py pick deepseek-v4.1-flash   # switch model and test it
python tools\wire_brain.py test          # connectivity check only
python tools\wire_brain.py custom <base> <model> [key] # any OpenAI-compatible endpoint
```

The key is looked up in order: `~/.dsh/.credentials.yaml`, environment variables, existing config. It is never printed.

## Memory

- Facts about the other person are captured automatically; stronger emotion, stronger memory.
- Every 6 turns the model distills recent conversation into "one sentence + tags". Tags bridge different wordings ("约好" and "约定" hit each other).
- Importance fades with a 30-day half-life; memories that get recalled more often resist forgetting.
- Recall is mood-dependent: when she is sad she recalls sad things more easily.
- Recall surfaces as "you suddenly remember…". She never sees the retrieval rules.

## Her hands

Tools she can call: `ls`, `read`, `write`, `edit`, `remember`, `forget`, `update_self`, `run`.

Boundaries:

- `data/` is her territory. Read and write there need no approval. That is where her memory and self-model live.
- Writing outside the project, and running system commands, require the operator's confirmation in the terminal.
- The C: drive is always refused, even with approval.
- Kernel source and kernel state files are blocked at the tool layer.
- Every tool call goes into `data/audit.log`, and the result goes back into her context. What she claims she did can be checked.

## Heartbeat

A heartbeat fires every 15 minutes. Each beat first computes, from local numbers only, whether waking the model is worth it (free, zero tokens). If the score is too low, nothing happens. If it passes, she wakes and chooses one of three things: stay quiet, write a note for the operator, or record something she noticed about herself. The operator sees a replay when they return.

## Files

```
agent/          middle layer: self_model, reflection, cortex, heartbeat, tools, shell
kernel/         kernel: emotion, need, energy, memory (numbers never leave this package)
tools/          brain switching, backup restore, autostart
data/           runtime state (never committed)
config.json     local config (never committed; template is config.example.json)
main.py         entry point
smoke_test.py   offline self-check, no model calls
```

Restore: `tools/restore_backup.py` returns to any sealed state (initial backup or a `/快照`). Autostart: `tools/autostart.py on`.

## Testing

```powershell
python smoke_test.py
```

## Planned

- semantic vector retrieval for memory (currently tags plus lexical matching)
- system tray and desktop notifications
- multiple operators
- turning the narrative into a coherent autobiography

## License

MIT
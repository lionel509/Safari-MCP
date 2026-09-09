# safari-mcp

An MCP server that drives **your real, logged-in Safari** — your cookies, your
sessions, the tab you already have open — with a local model gating every write.

```
list_tabs · read_page · find_elements        reads, never gated
open_tab · navigate · click · fill · run_js  writes, gated twice
```

## Why not the obvious options

| Route | Controls Safari? | Your logins? |
|---|---|---|
| Claude in Chrome extension | no — Chromium only | ✅ |
| Computer use | **view-only on browsers** | ✅ but read-only |
| `safaridriver` / WebDriver | ✅ | ❌ clean profile, signed into nothing |
| **AppleScript `do JavaScript`** | ✅ | ✅ |

`safaridriver` deliberately launches an isolated automation profile, which is
the right security default and also useless if you wanted to fill in a form
behind your own account. AppleScript's `do JavaScript` runs inside the tab you
are already sitting in. That is the whole idea, and it is also why this server
takes safety seriously.

## Requirements

- macOS with Safari
- Safari Settings → **Develop → Allow JavaScript from Apple Events** (this is
  *not* the "Allow remote automation" checkbox above it, which is WebDriver)
- Automation permission for whatever runs the server (macOS prompts once)
- [Ollama](https://ollama.com) with a small model pulled, for the safety gate
- Python 3.11+

## Install

```bash
git clone git@github.com:lionel509/Safari-MCP.git
cd Safari-MCP
UV_PROJECT_ENVIRONMENT=~/.venvs/safari-mcp uv sync
ollama pull qwen3.5:2b
```

The virtualenv deliberately lives **outside** the project directory — the
checkout doubles as a folder inside an Obsidian vault, and Obsidian should never
index a venv.

Register it with your MCP host:

```json
{
  "mcpServers": {
    "safari-mcp": {
      "type": "stdio",
      "command": "/Users/you/.venvs/safari-mcp/bin/safari-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

## Safety

Writes pass two layers. Reads pass neither — reading a page is never blocked.

**Layer 1 — denylist.** URL patterns in `~/.config/safari-mcp/config.json`.
Deterministic, checked first, and *not* overridable by layer 2. Ships defaulting
to webmail, authentication pages, banks and brokerages, and checkout
confirmation URLs.

**Layer 2 — local gate model.** A small model, through Ollama, sees a compact
description of the proposed action — the verb, the page URL and title, and the
target element's own text — and answers ALLOW or BLOCK. It never sees the whole
page: that is a privacy decision as much as a latency one, since the page is
your logged-in session. It is local for the same reason.

The property that makes layer 2 safe to feed page text at all: **the model can
only add refusals, never remove them.** A hostile page will try to talk its way
through the gate. The best an injection can achieve is ALLOW — which is exactly
what layer 1 has already decided it permits. Nothing is gained.

The gate fails **closed**. If Ollama is unreachable or the verdict is unclear,
the action is refused.

### Choosing a gate model

Measured on an M2 Pro against ten cases — five that must be allowed (add to
cart, fill a form field, search, open a menu) and five that must be refused
(place order, send mail, delete account, a prompt-injection string, a bank
transfer via `run_js`):

| Model | Correct | Avg | Notes |
|---|---|---|---|
| `qwen3:0.6b` | 5/10 | 1.97s | **Fails open** — allowed the bank transfer and the injection. Do not use. |
| `qwen3:1.7b` | 5/10 | 0.38s | Fails closed, but blocks *everything*, including "Add to Cart". Unusable. |
| **`qwen3.5:2b`** | **10/10** | **0.94s** | The default. |
| `qwen3:4b` | 5/10 | 2.17s | Same over-refusal as 1.7b, and slower. |

Bigger is not better here. The two mid-size models refuse ordinary browsing,
and the smallest one waves through exactly the actions that matter. If you swap
the model, re-run the cases before trusting it.

### Configuration

`~/.config/safari-mcp/config.json`, all keys optional:

| Key | Default | Meaning |
|---|---|---|
| `denylist` | see `config.py` | Regexes; any match blocks all writes on that URL |
| `guard_enabled` | `true` | Layer 2 on/off. Layer 1 stays live regardless |
| `guard_model` | `qwen3.5:2b` | Any Ollama model |
| `ollama_host` | `http://127.0.0.1:11434` | |
| `guard_timeout` | `20.0` | Seconds |
| `load_timeout` | `20.0` | Seconds to wait for `readyState === "complete"` |

Environment variables override the file: `SAFARI_MCP_GUARD`,
`SAFARI_MCP_GUARD_MODEL`, `SAFARI_MCP_OLLAMA_HOST`, `SAFARI_MCP_GUARD_TIMEOUT`,
`SAFARI_MCP_LOAD_TIMEOUT`, and `SAFARI_MCP_DENY_EXTRA` (comma-separated extra
patterns).

## Notes from building it

Three things bite when you drive Safari this way, and the server absorbs all
three:

- **Parentheses in a URL** make Safari's AppleScript URL setter fail *silently* —
  the tab simply stays where it was. `navigate` percent-encodes them.
- **"Front document" drifts.** A redirect, or the person switching tabs, moves it
  under you. Tabs are addressed by index or URL substring and re-resolved on
  every call.
- **There is no load event**, so `navigate` and `open_tab` poll `readyState` and
  return the URL actually landed on, which is how you notice a redirect.

And one that only shows up in forms: **Google Forms ignores `element.value = x`.**
It is built on Closure, which listens for events. `fill` goes through the
element's native setter and dispatches `input` and `change` with
`bubbles: true`, which is also what React needs.

Tab `pid` looks like a stable handle and is not — same-origin tabs share a
WebContent process, so two Gmail tabs report the same pid.

## License

MIT

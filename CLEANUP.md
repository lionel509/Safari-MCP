# Cleanup — safari-mcp

What this repo leaves behind while you work on it, and how to put the working tree
back to what a fresh `git clone` gives you. Companion to the `.gitignore` coverage
added in #1.

Nothing here touches files git tracks: `pyproject.toml`, `uv.lock`, `src/`, `README.md`.

## What this project leaves behind

| path / thing | created by | size note |
|---|---|---|
| `__pycache__/`, `*.pyc` | importing `safari_mcp` while running `safari-mcp` or `uv run` | a few hundred KB |
| `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `htmlcov/` | test / typecheck / lint passes — the stock template already ignores these | a few MB if you add a suite |
| `build/`, `dist/`, `*.egg-info/` | `uv build` or `pip install -e .` | ~100 KB |
| `.venv/`, `venv/`, `env/` | **not normally created here** — the README pins the venv outside the checkout with `UV_PROJECT_ENVIRONMENT=~/.venvs/safari-mcp`, so Obsidian never indexes it | if one appears, ~30 MB |
| `.env`, `.env.*` (`SAFARI_MCP_*` overrides) | you, by hand | tiny — **kept on purpose**, see Secrets |
| `*.key`, `*.pem`, `credentials.json`, `secrets.json` | you, if a credential ever lands here | **kept on purpose** |
| `graphify-out/` | `graphify update .` | grows with the repo |
| `Safari MCP — *.md` | the vault hub note; this folder doubles as a note folder | one file, ignored at the root — never delete it from a cleanup |
| `.DS_Store`, `.idea/`, `.vscode/`, `*.swp` | Finder / editors | tiny |

No log files: `server.py` sends every log line to **stderr**, because stdio carries
the MCP protocol — so your MCP host holds the logs, not this folder.

## Preview

Lists every ignored file the clean below would remove, keeping your secrets:

```bash
git clean -ndX -e '!.env' -e '!.env.*'
```

## Clean the repo

```bash
git clean -fdX -e '!.env' -e '!.env.*'
```

That is everything `git clean` can see. It does **not** remove the virtualenv,
because this project never puts one inside the repo — see below.

After a clean, restore the environment the way the README installs it:

```bash
UV_PROJECT_ENVIRONMENT=~/.venvs/safari-mcp uv sync
```

`uv` resolves straight from the committed `uv.lock`, so the restore is exact and
needs no network beyond fetching the wheels.

## Outside the repo

These are the things this project *actually* installs elsewhere. Each is a separate
decision — the first two are shared with other work.

| thing | exact command | shared? |
|---|---|---|
| the virtualenv the README creates | `rm -rf ~/.venvs/safari-mcp` | **yours alone**, but it is also what your MCP host launches — after removing it, delete the `safari-mcp` entry from your MCP host's config (Claude Desktop / Claude Code `mcpServers`) or the host will error on every session |
| uv's package cache | `uv cache clean` | **shared — every uv project on this Mac uses it.** Only worth it for the disk; the next `uv sync` re-downloads. |
| the gate model pulled into Ollama | `ollama rm qwen3.5:4b` (and `qwen3.5:2b` if you pulled it too) | **shared — `~/.ollama` is system-wide**, and other tools (including other repos in this fleet) use it. Check nothing else needs it first. |
| the settings file | `rm -f ~/.config/safari-mcp/config.json` | yours alone; deleting it restores the built-in denylist defaults from `config.py`, so the server still runs |

Nothing else. No Docker images, no Playwright browsers, no launchd plists, no pip
installs outside uv — and no model weights beyond the Ollama pull above.

## Secrets

`.env`, `.env.*`, `*.key`, `*.pem`, `credentials.json` and `secrets.json` are ignored
**and deliberately kept** by both commands above — the `-e '!.env' -e '!.env.*'`
exceptions mean `git clean` skips them. Your local secrets survive a cleanup.

This server needs no API key — the gate model runs locally through Ollama, which is
the point of the design (`config.py` says so in as many words). If you set
`SAFARI_MCP_*` overrides, put them in `.env` or your shell profile, never in `src/`.
The denylist file `~/.config/safari-mcp/config.json` holds no credentials either.

If you truly mean to get rid of a secret file, remove it yourself and mean it:

```bash
rm -f .env .env.*
```

If a secret was ever *committed*, deleting the file is not enough — it is in history.
Open an issue and rotate the credential first; never rewrite published history.

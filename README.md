<div align="center">

# opencode-telegram

**Drive your opencode coding agent from Telegram — with the safety rails of a remote control, not a remote shell.**

[![tests](https://img.shields.io/badge/tests-45%20passing-brightgreen)](tests/test_bridge.py)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![dependencies](https://img.shields.io/badge/dependencies-zero-success)](bridge/requirements.txt)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)
[![backend](https://img.shields.io/badge/backend-cli%20%7C%20serve-informational)](bridge/bridge.py)

</div>

---

## What this is

A standalone Telegram bridge for [opencode](https://opencode.ai). You send it a task from
your phone; it runs a real opencode session on your machine and streams the answer back.

It is **not** a remote shell and **not** a prompt relay. It is a governed control surface:

| Concern | How it is handled |
|---|---|
| Who can drive the machine | Strict chat allowlist, checked before anything is parsed |
| Destructive commands | Pattern gate puts the request on hold; you must confirm with `/onay HOLD-xxx` |
| Secret leakage | Every outgoing byte passes through a redaction filter before it reaches Telegram |
| Spending runaway | Optional daily token ceiling that halts new work |
| Losing your place | Sessions are mapped per chat and survive restarts |
| Double work | One in-flight job per chat, with `/abort` and full process-tree kill |

**Zero dependencies.** Standard library only. `bridge/requirements.txt` is empty on purpose,
and the test suite proves it: the bridge never imports a third-party package.

---

## Architecture

```
        ┌──────────────────────┐
        │      Telegram        │   Bot API, long-polling
        └──────────┬───────────┘
                   │  getUpdates / sendMessage / editMessageText
                   │
        ┌──────────▼───────────┐
        │   bridge/bridge.py   │   daemon · stdlib only
        │                      │
        │  allowlist → gate →  │
        │  danger HOLD →       │
        │  run (cli | serve)   │
        │  redact → Telegram   │
        └──────────┬───────────┘
                   │  JSON mailbox (file-locked, cross-process)
        ┌──────────▼───────────┐        ┌──────────────────────┐
        │ .opencode/mailbox/   │◄──────►│   mcp/server.py      │
        │  inbox / outbox /    │        │  telegram_send       │
        │  sessions / budget   │        │  telegram_broadcast  │
        └──────────┬───────────┘        │  telegram_poll       │
                   │                    │  telegram_ack        │
                   │                    │  telegram_status     │
        ┌──────────▼───────────┐        └──────────┬───────────┘
        │       opencode       │◄──────────────────┘
        │  cli  `opencode run` │   or   serve  `opencode serve`
        └──────────────────────┘   (HTTP + SSE, warm session)
```

The bridge never opens an inbound port. It dials out to `api.telegram.org`, so no firewall
rule, no port forwarding, and no public exposure.

### Repository layout

```
opencode-telegram/
├── README.md               # this file
├── README_TR.md            # Turkish edition
├── VERSION                 # single source of truth for the version
├── PLAN.md                 # product phases and acceptance criteria
├── GAP-PLAN.md             # quality backlog (local only, git-ignored)
├── CHANGELOG.md
├── bridge/
│   ├── bridge.py           # the daemon
│   ├── serve_client.py     # stdlib HTTP/SSE client for `opencode serve`
│   └── atomic_json.py      # cross-process file-locked JSON store
├── mcp/
│   └── server.py           # the `telegram` MCP server (opencode side)
├── scripts/                # install / uninstall / start helpers
├── docs/                   # KURULUM.md, SERVIS.md
├── steering/               # telegram-ops.md — agent discipline
└── tests/                  # 45 tests, no network, no model calls
```

---

## Quick start

### Requirements

- Windows, macOS, or Linux. Windows is the primary target.
- **Python 3.10+**
- **opencode** on `PATH` (`opencode --version`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### 1. Create the bot

Message `/newbot` to **@BotFather**, pick a name, and copy the token. Then open your bot
and send it one message — a bot cannot message you before you message it first.

### 2. Install

```powershell
# Windows — interactive menu (install / start / selftest)
.\setup.bat
```

<details>
<summary>Non-Windows, or manual steps</summary>

```bash
# macOS / Linux
./scripts/install-global.sh

# or drive the merge yourself
python scripts/merge_global_config.py \
    ~/.config/opencode/opencode.json \
    ./mcp/server.py \
    ./steering/telegram-ops.md \
    python3
```

</details>

The installer registers a `telegram` MCP server in your **global** `opencode.json` and adds
`telegram-ops.md` to your instructions. It is idempotent and takes a timestamped backup
before writing.

### 3. Configure

```powershell
copy .env.example .env
notepad .env
```

```ini
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_DEFAULT_CHAT_ID=
```

### 4. Learn your chat ID

```powershell
python bridge/bridge.py --selftest
```

Start the bridge, send it any message, and read your chat ID off the `DENIED chat=...` line
in the log. Stop the bridge, put that number in `TELEGRAM_ALLOWED_CHAT_IDS`, and start it
again.

### 5. Go

```powershell
.\scripts\start-bridge.ps1
```

Send `/yardim` in Telegram. Done.

---

## Using it

| From Telegram | What happens |
|---|---|
| plain text | Runs as a question or task in your session, answer comes back |
| `/sor <text>` | Same, explicit form |
| photo / document | Downloaded and handed to opencode for inspection (albums group automatically) |
| voice note | Transcribed, then the request inside it is carried out |
| `/durum` | Project state + this chat's session, model, and token usage |
| `/model`, `/model list`, `/model set <provider/model>` | Per-chat model control |
| `/project list \| set <alias>` | Switch between multiple configured projects |
| `/onay HOLD-xxx` | Release a held destructive request (valid 30 minutes) |
| `/onay <note>` | Leave a note in the orchestrator queue |
| `/abort` | Kill the running job and its whole process tree |
| `/reset` | Delete this chat's session and start fresh |
| `/yardim` | Command list |

Long jobs keep you posted: a progress ping every three minutes, and `/abort` works the
whole time.

---

## Backends

Pick one with `TELEGRAM_BACKEND`.

### `cli` (default)

Shells out to `opencode run` per message. No extra services, no ports, nothing to supervise.
The trade-off is a cold start on the first message of each session.

### `serve`

Keeps `opencode serve` warm and talks to it over HTTP + SSE. You get incremental message
edits as the model writes, inline buttons for permission requests, and buttons for multiple
choice questions. The bridge starts `serve` itself if it is not already up.

```ini
TELEGRAM_BACKEND=serve
TELEGRAM_SERVE_URL=http://127.0.0.1:4096
TELEGRAM_SERVE_PORT=4096
```

---

## Security model

Read this before you point it at a machine you care about.

**Access.** Only chats in `TELEGRAM_ALLOWED_CHAT_IDS` are served. Everything else is dropped
with a `DENIED` line in the log and never parsed. An allowed chat is equivalent to full
opencode authority on that machine — treat the ID as a credential and never share it.

**Destructive-request gate.** A request matching a known-dangerous pattern
(`rm -rf`, `DROP TABLE`, `terraform destroy`, `kubectl delete`, `git push --force`,
`git reset --hard`, `diskpart`, fork bombs, …) is not executed. It is parked, echoed back
to you for review, and released only by an explicit `/onay HOLD-xxx` that is valid for 30
minutes and bound to the chat that created it. The pattern list lives in `DANGER_PATTERNS`
in `bridge.py`; extend it for your environment.

**Secret redaction.** Every string bound for Telegram passes through a filter that redacts
OpenAI-style keys, GitHub tokens and PATs, AWS access key IDs, Slack tokens, PEM private
key blocks, and your bot token. This applies to answers, streamed updates, and audit records
alike.

**Audit trail.** Every run appends a JSON line to `telegram_audit.log`: who asked, a
redacted summary, the session, the model, the duration, the output length, and the running
daily token totals. The log rotates at 100 KB.

**Cost ceiling.** Set `TELEGRAM_DAILY_TOKEN_LIMIT` and new work stops once the day's total
crosses it. `/durum` always shows the current count.

**Single instance.** A PID lock plus Telegram's own 409 response mean two bridges can never
fight over one bot. The second one exits with a clear message.

---

## Configuration

Everything is environment variables, read from the environment or from `.env` in the project
directory, falling back to the package root. See `.env.example` for the annotated list.

The ones you are most likely to touch:

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | **Required.** Bot token from BotFather |
| `TELEGRAM_ALLOWED_CHAT_IDS` | — | **Required.** Comma-separated chat IDs |
| `TELEGRAM_DEFAULT_CHAT_ID` | — | Default target for `telegram_send` |
| `TELEGRAM_PROJECT_DIR` | auto-detected | The project the agent works in |
| `TELEGRAM_BACKEND` | `cli` | `cli` or `serve` |
| `TELEGRAM_OPENCODE_AGENT` | `orchestrator` | Which agent answers |
| `TELEGRAM_OPENCODE_MODEL` | opencode default | Model override |
| `TELEGRAM_BRIDGE_EXEC` | `1` | `0` = queue only, let the TUI orchestrator work |
| `TELEGRAM_DAILY_TOKEN_LIMIT` | `0` (off) | Daily token ceiling |
| `TELEGRAM_FOLLOW_EDITS` | `0` | Re-run a message when you edit it |
| `TELEGRAM_PROJECTS` | — | `alias=path,alias2=path2` for multi-project |
| `TELEGRAM_SCHEDULE` | — | `09:00:/durum;18:00:/gelen` |
| `TELEGRAM_DIGEST` | `0` | Batch notifications hourly instead of one by one |

---

## Running it as a service

`docs/SERVIS.md` covers both routes. The quick one on Windows:

```powershell
schtasks /create /tn TelegramBridge /tr "<full-path>\scripts\start-bridge.bat" /sc onlogon
```

For a supervised, restart-on-crash setup, use WinSW or NSSM — the bridge is a plain
foreground process and takes cleanly to a service wrapper.

---

## Development

```bash
git clone https://github.com/gkhantyln/opencode-telegram.git
cd opencode-telegram
python -m pip install pytest          # the only dev dependency

python -m pytest tests/ -v            # 45 tests
python bridge/bridge.py --selftest    # environment check, no network
```

The suite runs offline, needs no model, and needs no Telegram token. It covers the redaction
filter, the destructive-command gate, hold lifecycle, budget enforcement, session migration,
outbox retry and single-delivery guarantees, cross-process file locking, subprocess pipe
draining, the media and digest paths, and the MCP tool surface.

CI runs on Windows and Linux for every push and pull request, and fails the build if a secret
file is ever about to be committed.

### Design notes

- **Why a JSON mailbox?** The bridge and the MCP server are separate processes. A file queue
  is inspectable, debuggable, and survives restarts. Every read-modify-write goes through a
  transaction lock (`bridge/atomic_json.py`) that spans both threads and processes, so
  concurrent writers cannot lose a record or corrupt the file.
- **Why not a websocket?** `getUpdates` long-polling needs no inbound port, no TLS
  certificate, and no reverse proxy. For a personal bridge that is the right trade.
- **Why no dependencies?** A tool whose whole job is to survive being unattended should not
  inherit someone else's release schedule.

---

## Troubleshooting

| Symptom | Look at |
|---|---|
| Bot does not respond | `DENIED` in the log means the chat ID is not allowed. A `401` means the token is wrong. |
| `opencode bulunamadi` | `opencode --version` in the same terminal you start the bridge from |
| No `telegram_*` tools in the TUI | Restart opencode — MCP servers are read at startup |
| `telegram_status` reports `STALE` or `DOWN` | The bridge is not running; check the heartbeat age |
| A job seems stuck | `/abort`, then `/durum`. A stale lock is cleared automatically when the owning PID is gone. |
| Answer is truncated | Normal. Output is trimmed at 11500 characters; the full text is on the PC. |
| Everything is slow | First `opencode run` loads the model. Later messages in the same session are fast. |

More in `docs/KURULUM.md`.

---

## Contributing

Issues and pull requests are welcome. Before opening a PR:

1. `python -m pytest tests/ -v` must be green.
2. Keep the standard library only. If you truly need a package, say why in the issue first.
3. New behaviour needs a test that fails without your change.
4. Add a `CHANGELOG.md` entry under `Unreleased`.

## License

MIT — see [LICENSE](LICENSE).

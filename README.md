<div align="center">

# opencode-telegram

**Drive your opencode coding agent from Telegram — with the safety rails of a remote control, not a remote shell.**

[![tests](https://img.shields.io/badge/tests-250%20passing-brightgreen)](tests/)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![dependencies](https://img.shields.io/badge/dependencies-zero-success)](bridge/requirements.txt)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)
[![backend](https://img.shields.io/badge/backend-cli%20%7C%20serve-informational)](bridge/bridge.py)

[English](README.md) · [Türkçe](README_TR.md)

</div>

---

## What this is

A standalone Telegram bridge for [opencode](https://opencode.ai). You send it a task from
your phone; it runs a real opencode session on your machine and streams the answer back.

It is **not** a remote shell and **not** a prompt relay. It is a governed control surface:

| Concern | How it is handled |
|---|---|
| Who can drive the machine | Separate chat and user allowlists, checked before anything is parsed |
| Destructive commands | Pattern gate puts the request on hold; you must confirm with `/onay HOLD-xxx` |
| Secret leakage | Every outgoing byte passes through a redaction filter before it reaches Telegram |
| Spending runaway | Optional daily token ceiling, plus automatic blocking of providers that ran out of credit |
| Silent failure | Consecutive loop errors trigger a Telegram notification, then a clean shutdown |
| Losing your place | Sessions are listed, switchable, and survive restarts |
| Double work | One in-flight job per chat, with a message queue and `/abort` |
| Broken formatting | Code blocks are never split; a rejected message is resent as plain text, never dropped |

**Zero dependencies.** Standard library only. `bridge/requirements.txt` is empty on purpose,
and the test suite proves it: nothing in `bridge/` or `mcp/` imports a third-party package.

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
        │  allowlist → queue → │
        │  danger HOLD →       │
        │  run (cli | serve)   │
        │  clean → redact →    │
        │  MarkdownV2 → send   │
        └──────────┬───────────┘
                   │  JSON mailbox (file-locked, cross-process)
        ┌──────────▼───────────┐        ┌──────────────────────┐
        │ .opencode/mailbox/   │◄──────►│   mcp/server.py      │
        │  inbox / outbox /    │        │  telegram_send       │
        │  sessions / queue /  │        │  telegram_broadcast  │
        │  budget / holds /    │        │  telegram_poll       │
        │  bad_providers       │        │  telegram_ack        │
        │                      │        │  telegram_status     │
        └──────────┬───────────┘        └──────────┬───────────┘
                   │                              │
        ┌──────────▼──────────────────────────────▼──┐
        │                  opencode                    │
        │  cli: `opencode run`  |  serve: HTTP + SSE   │
        └──────────────────────────────────────────────┘

  bridge/md2.py           Markdown -> Telegram MarkdownV2 (kod bloklari kirpilmaz)
  bridge/atomic_json.py   islem-semantik JSON: thread + dosya kilidi
```

The bridge never opens an inbound port. It dials out to `api.telegram.org`, so no firewall
rule, no port forwarding, and no public exposure.

### Repository layout

```
opencode-telegram/
├── README.md / README_TR.md   # this file / Turkish edition
├── VERSION                     # single source of truth for the version
├── PLAN.md                     # product phases
├── GAP-PLAN.md                 # quality backlog (local only, git-ignored)
├── CHANGELOG.md
├── bridge/
│   ├── bridge.py               # the daemon
│   ├── md2.py                  # Markdown -> MarkdownV2
│   ├── serve_client.py         # stdlib HTTP/SSE client for `opencode serve`
│   └── atomic_json.py          # cross-process file-locked JSON store
├── mcp/
│   └── server.py               # the `telegram` MCP server (opencode side)
├── scripts/                    # install / uninstall / start helpers
├── docs/                       # KURULUM.md, SERVIS.md
├── steering/telegram-ops.md    # agent discipline
└── tests/                      # 250 tests, no network, no model calls
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
./scripts/install-global.sh          # register the MCP server
./scripts/uninstall-global.sh        # remove it again
```

</details>

The installer registers a `telegram` MCP server in your **global** `opencode.json` and adds
`telegram-ops.md` to your instructions. It is idempotent, takes a timestamped backup before
writing, and leaves your other MCP servers alone.

### 3. Configure

```powershell
copy .env.example .env
notepad .env
```

```ini
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_OPENCODE_MODEL=opencode/space-bunny-free
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
| `/model`, `/model list [filter] [page]`, `/model set <p/m>`, `/model auto` | Per-chat model control |
| `/sessions [page]` | List sessions in this project, newest first |
| `/sessions ac <no>` | Switch to a listed session (the old one is **not** deleted) |
| `/sessions yeni` | New session mode — the old one is **not** deleted |
| `/sessions bilgi` | Active session details, message count, token usage |
| `/project list \| set <alias>` | Switch between multiple configured projects |
| `/kuyruk` | Messages that arrived while busy, in order |
| `/onay HOLD-xxx` | Release a held destructive request (valid 30 minutes) |
| `/onay <note>` | Leave a note in the orchestrator queue |
| `/abort` | Kill the running job and its whole process tree |
| `/klavye` | Turn the bottom shortcut keyboard on |
| `/klavye kapat` | Turn it off |
| `/yardim` | Command list |

Long jobs keep you posted: a progress ping every three minutes, and `/abort` works the
whole time.

### The bottom keyboard

Off by default, on purpose: a persistent keyboard sits directly above the text field and
makes it awkward to just type normally. Send `/klavye` when you want the shortcuts —
two rows, six buttons, and you can still type anything you want.

```
[ Durum ] [ Model ] [ Oturum ]
[ Kuyruk ] [ Yeni  ] [ Durdur ]
```

Tapping a button runs the matching command. Button labels are never sent to the agent as
text.

### Sessions

`/sessions` lists the sessions belonging to the current project, newest first, and marks the
active one. Sessions are project-scoped: a session from a different directory is neither
listed nor selectable, because the agent would end up working in the wrong tree.

Switching or starting a new session **never deletes** anything. `/sessions sil` (same as
`/reset`) is the explicit destructive command.

When you switch, the model override follows the chat. If you would rather use the session's
own model, send `/model auto` once and the override is dropped.

### Message queue

If you send a message while the agent is busy, it is **queued, not rejected**. When the
current job finishes the queue drains in order. `/kuyruk` shows what is waiting,
`/kuyruk temizle` empties it. Queue depth defaults to 5 and is configurable. If you clear
the queue while it is draining, the drain stops instead of running work you withdrew.

---

## Backends

Pick one with `TELEGRAM_BACKEND`.

### `cli` (default)

Shells out to `opencode run` per message. No extra services, no ports, nothing to supervise.
The trade-off is a cold start on the first message of each session.

### `serve`

Keeps `opencode serve` warm and talks to it over HTTP + SSE. You get incremental message
edits as the model writes, inline buttons for permission requests, and buttons for multiple
choice questions. The bridge starts `serve` itself if it is not already up, health-checks
the connection on every use, and shuts the process down when it exits.

```ini
TELEGRAM_BACKEND=serve
TELEGRAM_SERVE_URL=http://127.0.0.1:4096
TELEGRAM_SERVE_PORT=4096
```

---

## Security model

Read this before you point it at a machine you care about.

**Access.** Two separate allowlists. `TELEGRAM_ALLOWED_CHAT_IDS` decides which chats are
served; `TELEGRAM_ALLOWED_USER_IDS` decides which *people* may drive them. In a private
chat the two are the same number and the second can stay empty. In a group it cannot: if
`TELEGRAM_ALLOWED_USER_IDS` is empty, nobody gets in, so you have to list your user ID
explicitly. An allowed chat is equivalent to full opencode authority on that machine.

**Destructive-request gate.** A request matching a known-dangerous pattern
(`rm -rf`, `DROP TABLE`, `terraform destroy`, `kubectl delete`, `git push --force`,
`git reset --hard`, `diskpart`, fork bombs, …) is not executed. It is parked, echoed back
to you for review, and released only by an explicit `/onay HOLD-xxx` that is valid for 30
minutes and bound to the chat that created it. The pattern list lives in `DANGER_PATTERNS`
in `bridge.py`; extend it for your environment.

**Secret redaction.** Every string bound for Telegram passes through a filter that redacts
OpenAI-style keys, GitHub tokens and PATs, AWS access key IDs, Slack tokens, PEM private
key blocks, and your bot token. Answers, streamed updates, audit records and the console log
all go through it.

**Audit trail.** Every run appends a JSON line to `telegram_audit.log`: who asked, a
redacted summary, the session, the model, the duration, the output length, and the running
daily token totals. The log rotates at 100 KB.

**Cost ceiling.** `TELEGRAM_DAILY_TOKEN_LIMIT` halts new work once the day's total crosses
it. Separately, if a provider answers with a credit or quota error, that provider is
remembered and **no further requests are sent to it** until you change model or the block
expires — you get the reason and the exact command to run instead of a silent retry.

**Single instance.** A PID lock plus Telegram's own 409 response mean two bridges can never
fight over one bot. The second one exits with a clear message.

**No silent death.** If the poll loop fails repeatedly the bridge notifies your chat and
exits cleanly, instead of sitting there looking alive. A successful iteration resets the
counter. The exit code is `4`, so a supervisor can tell this apart from a normal stop.

---

## Configuration

Everything is environment variables, read from the environment or from `.env` in the project
directory, falling back to the package root. See `.env.example` for the annotated list.

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | **Required.** Bot token from BotFather |
| `TELEGRAM_ALLOWED_CHAT_IDS` | — | **Required.** Comma-separated chat IDs |
| `TELEGRAM_ALLOWED_USER_IDS` | — | User IDs; required for group chats |
| `TELEGRAM_DEFAULT_CHAT_ID` | — | Default target for `telegram_send` |
| `TELEGRAM_PROJECT_DIR` | auto-detected | The project the agent works in |
| `TELEGRAM_BACKEND` | `cli` | `cli` or `serve` |
| `TELEGRAM_OPENCODE_AGENT` | `orchestrator` | Which agent answers |
| `TELEGRAM_OPENCODE_MODEL` | opencode default | Default model |
| `TELEGRAM_MARKDOWN` | `1` | Render replies as MarkdownV2. `0` = plain text |
| `TELEGRAM_SET_COMMANDS` | `1` | Register the `/` command menu on startup |
| `TELEGRAM_API_MAX_RETRIES` | `3` | Retries for 429 and transient 5xx |
| `TELEGRAM_LOOP_MAX_ERRORS` | `5` | Consecutive loop errors before shutdown |
| `TELEGRAM_BRIDGE_EXEC` | `1` | `0` = queue only, let the TUI orchestrator work |
| `TELEGRAM_MAX_QUEUED` | `5` | Messages that can wait while busy |
| `TELEGRAM_DAILY_TOKEN_LIMIT` | `0` (off) | Daily token ceiling |
| `TELEGRAM_BAD_PROVIDER_TTL_H` | `6` | How long a credit-exhausted provider stays blocked |
| `TELEGRAM_FOLLOW_EDITS` | `0` | Re-run a message when you edit it |
| `TELEGRAM_MODEL_PAGE_SIZE` | `25` | Models per `/model list` page |
| `TELEGRAM_SESSION_PAGE_SIZE` | `15` | Sessions per `/sessions` page |
| `TELEGRAM_OUTPUT_ENCODING` | `cp1254,cp1252,latin-1` | Fallback code pages for model output |
| `TELEGRAM_PROJECTS` | — | `alias=path,alias2=path2` for multi-project |
| `TELEGRAM_SCHEDULE` | — | `09:00:/durum;18:00:/gelen` |
| `TELEGRAM_DIGEST` | `0` | Batch notifications hourly instead of one by one |

### Message formatting

Replies are rendered as Telegram MarkdownV2, so `**bold**`, `_italic_`, `~~strikethrough~~`,
inline code and fenced code blocks keep their formatting on the phone.

Three details worth knowing:

- **Code blocks are never cut in half.** Long answers are split on block and line
  boundaries, so every message contains a complete, valid code block.
- **Formatting can never lose a message.** If Telegram rejects a message for a formatting
  reason, the same content is immediately resent as plain text. Set
  `TELEGRAM_MARKDOWN=0` to turn formatting off entirely.
- **Model output is sanitised first.** Colour codes are stripped and Windows code pages
  are decoded correctly, so Turkish characters survive the trip.

---

## Running it as a service

`docs/SERVIS.md` covers both routes. The quick one on Windows:

```powershell
schtasks /create /tn TelegramBridge /tr "<full-path>\scripts\start-bridge.bat" /sc onlogon
```

For a supervised, restart-on-crash setup, use WinSW or NSSM — the bridge is a plain
foreground process and takes cleanly to a service wrapper. Use the scheduler, not a bare
retry loop: the bridge now exits deliberately when the poll loop is broken, and a
supervisor should bring it back.

---

## Development

```bash
git clone https://github.com/gkhantyln/opencode-telegram.git
cd opencode-telegram
python -m pip install pytest          # the only dev dependency

python -m pytest tests/ -v            # 250 tests
python bridge/bridge.py --selftest    # environment check, no network
```

The suite runs offline, needs no model, and needs no Telegram token. It covers the redaction
filter, output sanitising, the destructive-command gate, hold lifecycle, budget enforcement,
credit-exhaustion blocking, session listing and switching, the message queue, the keyboard
mapping, outbox retry and single-delivery guarantees, cross-process file locking, subprocess
pipe draining, the Markdown converter, the serve HTTP/SSE client, and the MCP tool surface.

CI runs on Windows and Linux for every push and pull request, and fails the build if a
secret or log file is ever about to be committed.

### Design notes

- **Why a JSON mailbox?** The bridge and the MCP server are separate processes. A file queue
  is inspectable, debuggable, and survives restarts. Every read-modify-write goes through a
  transaction lock (`bridge/atomic_json.py`) that spans both threads and processes, so
  concurrent writers cannot lose a record or corrupt the file.
- **Why not a websocket?** `getUpdates` long-polling needs no inbound port, no TLS
  certificate, and no reverse proxy. For a personal bridge that is the right trade.
- **Why no dependencies?** A tool whose whole job is to survive being unattended should not
  inherit someone else's release schedule.
- **Why ASCII in the source?** Every string the bridge *sends* is plain ASCII Turkish, so a
  code page mismatch can never mangle a command reply. Turkish characters appear in model
  output, which is decoded explicitly.

---

## Troubleshooting

| Symptom | Look at |
|---|---|
| Bot does not respond | `DENIED` in the log means the chat or user is not allowed. A `401` means the token is wrong. |
| Group chat is ignored | `TELEGRAM_ALLOWED_USER_IDS` is empty. In a group it must list your user ID. |
| `opencode bulunamadi` | `opencode --version` in the same terminal you start the bridge from |
| No `telegram_*` tools in the TUI | Restart opencode — MCP servers are read at startup |
| Every message answers "kredisi bitmis" | The provider is blocked on purpose. `/model list <provider>` then `/model set ...` |
| `telegram_status` reports `STALE` or `DOWN` | The bridge is not running; check the heartbeat age |
| A job seems stuck | `/abort`, then `/durum`. A stale lock is cleared automatically when the owning PID is gone. |
| Answer is truncated | Normal. Output is trimmed at 11500 characters; the full text is on the PC. |
| Turkish characters look wrong | Set `TELEGRAM_OUTPUT_ENCODING` to your code page, e.g. `cp1254`. |
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

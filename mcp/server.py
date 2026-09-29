"""Telegram MCP server — opencode <-> Telegram koprusunun opencode tarafi.

Mimari (cift yonlu kontrol):
  Telegram --(long-polling)--> bridge/bridge.py --(JSON kuyruk)--> bu MCP --> orchestrator
  orchestrator --(telegram_send)--> outbox kuyrugu --> bridge --(sendMessage)--> Telegram

Bu server network'e cikmaz, token bilmez. Sadece kuyruk dosyalari okur/yazar:
  .opencode/mailbox/telegram_inbox.json   (bridge yazar, opencode okur)
  .opencode/mailbox/telegram_outbox.json  (opencode yazar, bridge gonderir)
  .opencode/mailbox/telegram_bridge.json  (bridge heartbeat)

Guvenlik: gonderim hedefi bridge tarafinda allowlist'e tabidir.
Buraya secret/token yazilmasin (communication-protocol.md kurali).
"""

import contextlib
import json
import os
import sys
import time
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_BRIDGE = os.path.normpath(os.path.join(_HERE, "..", "bridge"))
if _BRIDGE not in sys.path:
    sys.path.insert(0, _BRIDGE)

import atomic_json as _aj  # noqa: E402  (bridge ile ayni kilit protokolu)

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

server = Server("telegram")


def _pkg_version(default="0.0.0"):
    """Tek surum kaynagi: paket kokundeki VERSION (bridge ile ayni dosya)."""
    p = os.path.normpath(os.path.join(_HERE, "..", "VERSION"))
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip() or default
    except OSError:
        return default


VERSION = _pkg_version()


def _root():
    override = os.environ.get("TEAM_MAILBOX_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    here = os.getcwd()
    for _ in range(6):
        if os.path.isdir(os.path.join(here, ".opencode")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        if os.path.isdir(os.path.join(here, ".opencode")):
            return here
        here = os.path.dirname(here)
    return os.getcwd()


def _mailbox_dir():
    if os.environ.get("TEAM_MAILBOX_DIR"):
        d = os.environ["TEAM_MAILBOX_DIR"]
    else:
        d = os.path.join(_root(), ".opencode", "mailbox")
    os.makedirs(d, exist_ok=True)
    return d


def _path(name):
    return os.path.join(_mailbox_dir(), name)


def _load(name, default):
    return _aj.load(_path(name), default)


def _save(name, state):
    _aj.save(_path(name), state)


@contextlib.contextmanager
def _tx(name):
    """bridge ile ayni kilit: iki surec ayni dosyaya yazarken kayit kaybettirmesin."""
    with _aj.tx(_path(name)):
        yield


def _text(s):
    return [types.TextContent(type="text", text=s)]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@server.list_tools()
async def handle_list_tools():
    return [
        types.Tool(
            name="telegram_send",
            description="Telegram'a mesaj gonder (kuyruga yazar, bridge gonderir). "
                        "to bos birakilirsa varsayilan chat'e gider. "
                        "Uzun metinler bridge tarafinda 4000 karakterlik parcalara bolunur.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Gonderilecek mesaj (Markdown desteksiz, duz metin)"},
                    "to": {"type": "string", "description": "Hedef chat_id (bos = varsayilan)"},
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="telegram_broadcast",
            description="Tum izinli chat'lere duyuru gonder (faz baslangic/bitis, kritik durum). "
                        "Hedef listesini bridge allowlist belirler.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Duyuru metni"},
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="telegram_poll",
            description="Telegram'dan gelen kuyrugu oku (bridge getUpdates ile doldurur). "
                        "Okumak isaretlemez; islenenler telegram_ack ile kapatilir. "
                        "Bridge otomatik calistirmissa kayit status=READ + handled_by=bridge olur.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Azami kayit (varsayilan 20, max 50)"},
                    "unread_only": {"type": "boolean", "description": "Sadece UNREAD (varsayilan true)"},
                },
            },
        ),
        types.Tool(
            name="telegram_ack",
            description="Isledigin Telegram mesajlarini READ isaretle.",
            inputSchema={
                "type": "object",
                "properties": {
                    "msg_ids": {"type": "string", "description": 'Virgullu ID listesi, orn. "TG-001, TG-002"'},
                },
                "required": ["msg_ids"],
            },
        ),
        types.Tool(
            name="telegram_status",
            description="Kopru durumunu gor: bridge heartbeat yasi, bekleyen in/out kuyruk sayisi, "
                        "config eksigi (token/allowlist bridge tarafinda kontrol edilir).",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    try:
        if name == "telegram_send":
            text = (arguments.get("text") or "").strip()
            if not text:
                return _text("Error: text bos olamaz.")
            if len(text) > 20000:
                return _text("Error: text 20000 karakteri asamaz (bridge 4000'lik parcalar gonderir).")
            to = str(arguments.get("to") or "").strip() or "default"
            with _tx("telegram_outbox.json"):
                state = _load("telegram_outbox.json", {"seq": 0, "messages": []})
                state["seq"] += 1
                msg = {
                    "id": "OUT-%03d" % state["seq"],
                    "ts": _now(),
                    "to": to,
                    "text": text,
                    "status": "QUEUED",
                }
                state["messages"].append(msg)
                # kuyrugu sisirmemek icin son 200 SENT kaydi budanir
                sent = [m for m in state["messages"] if m.get("status") != "QUEUED"]
                if len(sent) > 200:
                    keep = [m for m in state["messages"] if m.get("status") == "QUEUED"] + sent[-200:]
                    # sira bozulmasin diye ts'ye gore degil, eklenme sirasi korunur
                    state["messages"] = keep
                _save("telegram_outbox.json", state)
            return _text("Queued %s -> %s (bridge gonderecek)" % (msg["id"], msg["to"]))

        elif name == "telegram_broadcast":
            text = (arguments.get("text") or "").strip()
            if not text:
                return _text("Error: text bos olamaz.")
            if len(text) > 20000:
                return _text("Error: text 20000 karakteri asamaz (bridge 4000'lik parcalar gonderir).")
            with _tx("telegram_outbox.json"):
                state = _load("telegram_outbox.json", {"seq": 0, "messages": []})
                state["seq"] += 1
                msg = {
                    "id": "OUT-%03d" % state["seq"],
                    "ts": _now(),
                    "to": "broadcast",
                    "text": text,
                    "status": "QUEUED",
                }
                state["messages"].append(msg)
                # broadcast da budanir, yoksa outbox sinirsiz buyur
                sent = [m for m in state["messages"] if m.get("status") != "QUEUED"]
                if len(sent) > 200:
                    state["messages"] = ([m for m in state["messages"]
                                          if m.get("status") == "QUEUED"] + sent[-200:])
                _save("telegram_outbox.json", state)
            return _text("Queued %s -> broadcast" % msg["id"])

        elif name == "telegram_poll":
            limit = max(1, min(int(arguments.get("limit", 20)), 50))
            unread_only = arguments.get("unread_only", True)
            state = _load("telegram_inbox.json", {"seq": 0, "messages": []})
            msgs = state.get("messages", [])
            if unread_only:
                msgs = [m for m in msgs if m.get("status") == "UNREAD"]
            return _text(json.dumps(msgs[-limit:], ensure_ascii=False, indent=1))

        elif name == "telegram_ack":
            raw = arguments.get("msg_ids", "")
            ids = [i.strip() for i in str(raw).replace(";", ",").split(",") if i.strip()]
            done = []
            with _tx("telegram_inbox.json"):
                state = _load("telegram_inbox.json", {"seq": 0, "messages": []})
                for m in state.get("messages", []):
                    if m.get("id") in ids and m.get("status") == "UNREAD":
                        m["status"] = "READ"
                        done.append(m["id"])
                _save("telegram_inbox.json", state)
            return _text("Acked: %s" % (", ".join(done) if done else "(yok)"))

        elif name == "telegram_status":
            inbox = _load("telegram_inbox.json", {"seq": 0, "messages": []})
            outbox = _load("telegram_outbox.json", {"seq": 0, "messages": []})
            heart = _load("telegram_bridge.json", {})
            now = time.time()
            in_unread = sum(1 for m in inbox.get("messages", []) if m.get("status") == "UNREAD")
            out_queued = sum(1 for m in outbox.get("messages", []) if m.get("status") == "QUEUED")
            epoch = heart.get("epoch", 0) or 0
            age = int(now - epoch) if epoch else -1
            verdict = "DOWN"
            if age >= 0:
                verdict = "ALIVE" if age <= 120 else "STALE"
            return _text(json.dumps({
                "bridge": verdict,
                "bridge_last_seen_sec_ago": age,
                "bridge_info": heart,
                "inbox_total": len(inbox.get("messages", [])),
                "inbox_unread": in_unread,
                "outbox_total": len(outbox.get("messages", [])),
                "outbox_queued": out_queued,
                "mailbox_dir": _mailbox_dir(),
                "note": "Token/allowlist bridge surecinde kontrol edilir; burada gorunmez.",
            }, ensure_ascii=False, indent=1))

        else:
            raise ValueError("Unknown tool: %s" % name)

    except Exception as e:
        return _text("Error: %s" % str(e))


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="telegram",
                server_version=VERSION,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

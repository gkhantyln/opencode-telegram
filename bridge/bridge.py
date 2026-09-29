#!/usr/bin/env python3
"""Telegram <-> opencode koprusu (daemon).

Ne yapar:
  1. Telegram Bot API'yi long-polling ile dinler (getUpdates) — stdlib only.
  2. Izinli chat'lerden gelen her soruyu `opencode run` ile calistirir,
     cevabi Telegram'a geri gonderir. Ilk soruda yeni oturum acilir, gercek
     `ses_...` ID `telegram_sessions.json`'a eslenir; sonraki sorular ayni
     oturumda devam eder. Boylece PC basinda olmadan tam kontrol saglanir.
  3. opencode TUI icinden `telegram_send` ile kuyruga yazilanlari
     (telegram_outbox.json) Telegram'a gonderir.
  4. Gelen her mesaji telegram_inbox.json'a da yazar (MCP telegram_poll
     ile opencode icinden gorulebilir, audit trail).

Calistirma:
  python bridge/bridge.py
  python bridge/bridge.py --selftest   (ag baglantisiz kontrol)
  # veya paket kokunden: .\scripts\start-bridge.ps1

Config (.env veya ortam degiskeni):
  TELEGRAM_BOT_TOKEN       (zorunlu, calisma icin)
  TELEGRAM_ALLOWED_CHAT_IDS (zorunlu, virgul/; listesi. Bos = kimse giremez)
  TELEGRAM_DEFAULT_CHAT_ID  (opsiyonel, telegram_send hedefi)
  TELEGRAM_PROJECT_DIR      (varsayilan: repo koku — bu dosyanin 3 ustu)
  TELEGRAM_SESSION_PREFIX   (varsayilan: tg)
  TELEGRAM_OPENCODE_AGENT   (varsayilan: orchestrator)
  TELEGRAM_BRIDGE_EXEC      (1/0, varsayilan 1 — 0 ise sadece kuyruklar, calistirmaz)
  TELEGRAM_OPENCODE_TIMEOUT (sn, varsayilan 600)
  TELEGRAM_POLL_TIMEOUT     (sn, varsayilan 30)

Guvenlik: allowlist disi chat'ler yok sayilir (log'a dusulur, cevap verilmez).
Izinli chat == PC'de opencode yetkisi demektir; chat_id'leri kimseyle paylasmayin.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---------- yollar ----------

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_project_dir():
    # Ic ice (team-template) kullanimda 2 ust proje kokudur; bagimsiz pakette
    # proje isareti yoksa paket dizininin kendisi kullanilir.
    # Her durumda TELEGRAM_PROJECT_DIR ile ezilebilir.
    nested = os.path.normpath(os.path.join(BRIDGE_DIR, "..", ".."))
    for marker in (".opencode", "opencode.json", ".git"):
        if os.path.exists(os.path.join(nested, marker)):
            return nested
    return os.path.normpath(os.path.join(BRIDGE_DIR, ".."))


DEFAULT_PROJECT_DIR = _default_project_dir()


def load_dotenv(path):
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                v = v.strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


load_dotenv(os.path.join(DEFAULT_PROJECT_DIR, ".env"))
load_dotenv(os.path.join(BRIDGE_DIR, ".env"))


def env(name, default=""):
    return os.environ.get(name, default)


PROJECT_DIR = env("TELEGRAM_PROJECT_DIR", DEFAULT_PROJECT_DIR)
SESSION_PREFIX = env("TELEGRAM_SESSION_PREFIX", "tg")
OPENCODE_AGENT = env("TELEGRAM_OPENCODE_AGENT", "orchestrator")
OPENCODE_MODEL = env("TELEGRAM_OPENCODE_MODEL", "")
BRIDGE_EXEC = env("TELEGRAM_BRIDGE_EXEC", "1") not in ("0", "false", "no")
try:
    OPENCODE_TIMEOUT = max(30, int(env("TELEGRAM_OPENCODE_TIMEOUT", "600")))
except ValueError:
    OPENCODE_TIMEOUT = 600
try:
    POLL_TIMEOUT = max(5, min(int(env("TELEGRAM_POLL_TIMEOUT", "30")), 50))
except ValueError:
    POLL_TIMEOUT = 30

BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
_raw_allowed = env("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(";", ",")
ALLOWED = {c.strip() for c in _raw_allowed.split(",") if c.strip()}
DEFAULT_CHAT = env("TELEGRAM_DEFAULT_CHAT_ID", "").strip()

# ---------- mailbox kuyruklari ----------

MAILBOX_DIR = os.environ.get("TEAM_MAILBOX_DIR") or os.path.join(PROJECT_DIR, ".opencode", "mailbox")
INBOX_FILE = os.path.join(MAILBOX_DIR, "telegram_inbox.json")
OUTBOX_FILE = os.path.join(MAILBOX_DIR, "telegram_outbox.json")
HEART_FILE = os.path.join(MAILBOX_DIR, "telegram_bridge.json")


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(*a):
    print("[%s]" % _now(), *a, flush=True)


# ---------- Telegram API (stdlib) ----------

API = "https://api.telegram.org/bot%s/%s"


def api(method, payload=None, timeout=45):
    url = API % (BOT_TOKEN, method)
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def send_message(chat_id, text, reply_to=None):
    """4000'luk parcalarla gonderir. Donus: gonderilen parca sayisi."""
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    n = 0
    for ch in chunks:
        payload = {"chat_id": chat_id, "text": ch}
        if reply_to is not None and n == 0:
            payload["reply_to_message_id"] = reply_to
        api("sendMessage", payload)
        n += 1
        if len(chunks) > 1:
            time.sleep(0.4)
    return n


def send_action(chat_id):
    try:
        api("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
    except Exception:
        pass


def is_allowed(chat_id):
    return str(chat_id) in ALLOWED


# ---------- inbox/outbox ----------

def inbox_add(chat_id, from_label, text, handled_by="", response_preview=""):
    st = _load(INBOX_FILE, {"seq": 0, "messages": []})
    st["seq"] += 1
    entry = {
        "id": "TG-%03d" % st["seq"],
        "ts": _now(),
        "chat_id": str(chat_id),
        "from": from_label,
        "text": text[:4000],
        "status": "READ" if handled_by else "UNREAD",
        "handled_by": handled_by,
        "response_preview": (response_preview or "")[:500],
    }
    st["messages"].append(entry)
    st["messages"] = st["messages"][-300:]
    _save(INBOX_FILE, st)
    return entry["id"]


def outbox_flush():
    """QUEUED kayitlari gonderir (basarisizlar 4 denemeye kadar ertelenir). Donus: (gonderilen, hata)."""
    import time as _t
    now = _t.time()
    st = _load(OUTBOX_FILE, {"seq": 0, "messages": []})
    sent, failed = 0, 0
    changed = False
    for m in st.get("messages", []):
        if m.get("status") != "QUEUED":
            continue
        if m.get("next_try", 0) > now:
            continue
        to = str(m.get("to") or "default")
        if to in ("default", ""):
            dests = [DEFAULT_CHAT] if DEFAULT_CHAT else []
        elif to == "broadcast":
            dests = sorted(ALLOWED) if ALLOWED else []
        else:
            dests = [to]
        if not dests:
            m["status"] = "FAILED"
            m["error"] = "hedef yok (TELEGRAM_DEFAULT_CHAT_ID bos ya da allowlist bos)"
            failed += 1
            changed = True
            continue
        ok_all = True
        for d in dests:
            if d not in ALLOWED:
                ok_all = False
                m["error"] = "hedef allowlist disi: %s" % d
                continue
            try:
                send_message(d, m.get("text", ""))
            except Exception as e:  # noqa: BLE001
                ok_all = False
                m["error"] = str(e)[-300:]
        if ok_all:
            m["status"] = "SENT"
            m.pop("attempts", None)
            m.pop("next_try", None)
            sent += 1
        else:
            att = int(m.get("attempts", 0)) + 1
            m["attempts"] = att
            if att >= 4:
                m["status"] = "FAILED"
                failed += 1
            else:
                m["next_try"] = now + 60 * att
        changed = True
    if changed:
        _save(OUTBOX_FILE, st)
    return sent, failed


def heartbeat(offset, extra=None):
    hb = {"ts": _now(), "epoch": time.time(), "offset": offset,
          "project_dir": PROJECT_DIR, "exec": BRIDGE_EXEC,
          "agent": OPENCODE_AGENT, "session_prefix": SESSION_PREFIX}
    if extra:
        hb.update(extra)
    _save(HEART_FILE, hb)


# ---------- dosya okuyucular (yerel komutlar) ----------

def _head(path, n=30):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return "".join(f.readlines()[:n])
    except OSError:
        return "(dosya yok: %s)" % os.path.basename(path)


def cmd_status():
    s = _head(os.path.join(PROJECT_DIR, "SESSION_STATE.md"), 25).strip()
    t = _head(os.path.join(PROJECT_DIR, "TODO.md"), 20).strip()
    return "SESSION_STATE\n%s\n\nTODO (ilk 20 satir)\n%s" % (s, t)


def cmd_roster():
    st = _load(os.path.join(MAILBOX_DIR, "inbox.json"), {"messages": [], "heart": {}})
    heart = st.get("heart", {})
    if not heart:
        return "roster bos (henuz heartbeat yok)."
    now = time.time()
    lines = []
    for agent in sorted(heart):
        h = heart[agent]
        age = int(now - h.get("epoch", 0))
        verdict = "ALIVE" if age <= 180 else "STALE"
        lines.append("%s: %s %s (%ds once) task=%s" % (
            agent, verdict, h.get("status", "?"), age, h.get("task", "")))
    return "\n".join(lines)


def cmd_inbox():
    st = _load(os.path.join(MAILBOX_DIR, "inbox.json"), {"messages": [], "heart": {}})
    unread = [m for m in st.get("messages", []) if m.get("status") == "UNREAD"]
    if not unread:
        return "Okunmamis team-mailbox mesaji yok."
    out = []
    for m in unread[-5:]:
        out.append("%s | %s -> %s | %s" % (m.get("id"), m.get("from"), m.get("to"), m.get("subject", "")))
    return "%d okunmamis (son 5):\n%s" % (len(unread), "\n".join(out))


HELP = (
    "Komutlar:\n"
    "/durum — SESSION_STATE + TODO + Telegram oturumu (model, token)\n"
    "/roster — agent canlilik durumu\n"
    "/gelen — okunmamis team-mailbox\n"
    "/sor <metin> — orchestrator'a sor (duz yazi da olur)\n"
    "/onay <metin> — orchestrator kuyruguna onay/not birak (HOLD-xxx ile bekleyen isi onayla)\n"
    "/abort — calisan isi durdur\n"
    "/reset — Telegram oturumunu sifirla (yeni oturum acar)\n"
    "/model — aktif model | /model list | /model set <provider/model>\n"
    "/yardim — bu liste\n\n"
    "Duz mesaj = /sor. Fotograf/belge gonderebilirsin (incelenir). "
    "Her chat'te ayni anda tek is calisir."
)


# ---------- opencode calistirma ----------

def _opencode_base():
    """Native .exe tercih edilir: `cmd /c` ara katmani satir sonlarini yutar
    (multiline argumanlar kirpilir). .CMD shim sadece yedek."""
    if os.name == "nt":
        cand = os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules",
                            "@opencode", "cli", "bin", "opencode.exe")
        if os.path.exists(cand):
            return cand
    for name in ("opencode", "opencode.cmd", "opencode.CMD"):
        p = shutil.which(name)
        if p:
            return p
    # npm global varsayilan konumu (yukaridaki which PATHEXT yuzunden kacirabilir)
    for cand in (
        os.path.join(os.environ.get("APPDATA", ""), "npm", "opencode.CMD"),
        os.path.join(os.environ.get("ProgramFiles", ""), "nodejs", "opencode.CMD"),
    ):
        if cand and os.path.exists(cand):
            return cand
    return None


def _opencode_cmd(args):
    base = _opencode_base()
    if base is None:
        return None, None
    if os.name == "nt" and base.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", base] + args, base
    return [base] + args, base


SESSIONS_FILE = os.path.join(MAILBOX_DIR, "telegram_sessions.json")


def _load_sessions():
    return _load(SESSIONS_FILE, {})


def _sess_entry(chat_id):
    """Eski format (duz string) ile yeni format (dict) uyumu."""
    v = _load_sessions().get(str(chat_id))
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v:
        return {"ses": v}
    return {}


def _save_session(chat_id, ses_id):
    _save_sess(chat_id, ses=ses_id)


def _save_sess(chat_id, ses=None, model=None, clear_ses=False):
    try:
        m = _load_sessions()
        ent = _sess_entry(chat_id)
        if clear_ses:
            ent.pop("ses", None)
        elif ses is not None:
            if ses:
                ent["ses"] = ses
            else:
                ent.pop("ses", None)
        if model is not None:
            if model:
                ent["model"] = model
            else:
                ent.pop("model", None)
        if ent:
            m[str(chat_id)] = ent
        else:
            m.pop(str(chat_id), None)
        _save(SESSIONS_FILE, m)
    except Exception:
        pass


def _chat_model(chat_id):
    return _sess_entry(chat_id).get("model") or OPENCODE_MODEL


_models_cache = {"ts": 0, "list": []}


def _opencode_models():
    """`opencode models` ciktisini satir listesi doner (10 dk cache)."""
    now = time.time()
    if now - _models_cache["ts"] < 600 and _models_cache["list"]:
        return _models_cache["list"]
    cmd, _ = _opencode_cmd(["models"])
    if cmd is None:
        return []
    try:
        p = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        lines = [l.strip() for l in (p.stdout or "").splitlines()]
        got = [l for l in lines if "/" in l and " " not in l and len(l) < 120]
        if got:
            _models_cache.update({"ts": now, "list": got})
            return got
    except Exception:
        pass
    return _models_cache["list"]


def _newest_session(since_ms=0):
    """Local `session list` (model harcamaz). since_ms sonrasi en yeni oturumu doner."""
    cmd, _ = _opencode_cmd(["session", "list", "--format", "json", "-n", "5"])
    if cmd is None:
        return None
    try:
        p = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        if p.returncode != 0 or not p.stdout.strip():
            return None
        sessions = json.loads(p.stdout)
        if since_ms:
            fresh = [s for s in sessions if int(s.get("created", 0)) >= since_ms]
            if fresh:
                return fresh[0].get("id")
            return None
        return sessions[0].get("id") if sessions else None
    except Exception:
        return None


def _debug_log(text):
    try:
        p = os.path.join(MAILBOX_DIR, "telegram_debug.log")
        os.makedirs(MAILBOX_DIR, exist_ok=True)
        entry = "[%s] %s\n" % (_now(), text)
        old = ""
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()[-40000:]
        with open(p, "w", encoding="utf-8") as f:
            f.write(old + entry)
    except Exception:
        pass


# ---------- paralel calisma: chat basina tek is + /abort ----------

_inflight = {}
_inflight_lock = threading.Lock()


def _is_busy(chat_id):
    with _inflight_lock:
        w = _inflight.get(str(chat_id))
        return bool(w and w["thread"].is_alive())


def _kill_tree(proc):
    try:
        if proc is None or proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            proc.kill()
    except Exception:
        pass


def _abort_chat(chat_id):
    with _inflight_lock:
        w = _inflight.pop(str(chat_id), None)
    if not w or not w["thread"].is_alive():
        return "Calisan is yok."
    w["abort"].set()
    _kill_tree(w.get("box", {}).get("proc"))
    return "Durdurma sinyali gonderildi, is sonlandiriliyor."


def _wait_proc(proc, abort, chat_id, timeout, reply_to=None):
    """Popen'i bekler; abort/timeout'ta oldurur. Donus: 'ok' | 'ABORTED' | 'TIMEOUT'."""
    start = time.time()
    ping = 0
    last_progress = start
    while True:
        try:
            proc.wait(timeout=5)
            return "ok"
        except subprocess.TimeoutExpired:
            if abort is not None and abort.is_set():
                _kill_tree(proc)
                return "ABORTED"
            if time.time() - start > timeout:
                _kill_tree(proc)
                return "TIMEOUT"
            ping += 1
            now = time.time()
            if ping % 3 == 0:
                try:
                    send_action(chat_id)
                except Exception:
                    pass
            if now - last_progress >= 180:
                last_progress = now
                try:
                    mins = int((now - start) // 60)
                    send_message(chat_id,
                                 "Hala calisiyorum (%d dk gecti)... Bitince yazacagim. "
                                 "/abort ile durdurabilirsin." % mins,
                                 reply_to=reply_to)
                except Exception:
                    pass


def run_opencode(prompt, chat_id, from_label, files=None, abort=None, proc_box=None):
    sid = "%s-%s" % (SESSION_PREFIX, str(chat_id).lstrip("-"))
    wrapped = (
        "[Telegram] Gonderen: %s (chat %s). "
        "Cevap Telegram'a gonderilecek: kisa yaz, 3000 karakteri gecme, "
        "secret/token sizdirma. Soru/gorev:\n\n%s" % (from_label, chat_id, prompt)
    )
    stored = _sess_entry(chat_id).get("ses")
    model = _chat_model(chat_id)
    model_args = ["--model", model] if model else []
    file_args = []
    for f in (files or [])[:3]:
        file_args += ["--file", f]
    base_args = ["run", "--agent", OPENCODE_AGENT] + model_args + file_args + [wrapped]

    def _exec(with_session):
        args = (["run", "--session", with_session, "--agent", OPENCODE_AGENT]
                + model_args + file_args + [wrapped]) if with_session else \
               (base_args + ["--title", "Telegram %s" % sid])
        cmd, _ = _opencode_cmd(args)
        if cmd is None:
            return None
        try:
            proc = subprocess.Popen(cmd, cwd=PROJECT_DIR, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            return e
        if proc_box is not None:
            proc_box["proc"] = proc
        if abort is not None and abort.is_set():
            _kill_tree(proc)
            return "ABORTED"
        res = _wait_proc(proc, abort, chat_id, OPENCODE_TIMEOUT, reply_to=proc_box.get("reply_to") if proc_box else None)
        if res != "ok":
            return res
        try:
            out, err = proc.communicate(timeout=30)
        except Exception:
            _kill_tree(proc)
            out, err = "", "cikti okunamadi"
        p = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        return p

    started_ms = int(time.time() * 1000)
    p = _exec(stored) if stored else _exec(None)
    if p is None:
        return "HATA: 'opencode' komutu bulunamadi (PATH'te yok)."
    if isinstance(p, Exception):
        return "HATA: opencode baslatilamadi: %s" % str(p)[-300:]
    if p == "ABORTED":
        return "Durduruldu (/abort)."
    if p == "TIMEOUT":
        return "HATA: opencode %d sn'de bitmedi. /durum ile kontrol edin." % OPENCODE_TIMEOUT
    # Kayitli oturum gecersizse/silindiyse sessizce yeniden ac ve eslestir
    err = (p.stderr or "")
    if p.returncode != 0 and stored and ("ses" in err or "ession" in err):
        log("session %s gecersiz, yeniden aciliyor" % stored)
        stored = None
        _save_sess(chat_id, clear_ses=True)
        started_ms = int(time.time() * 1000)
        p = _exec(None)
        if p is None:
            return "HATA: 'opencode' komutu bulunamadi (PATH'te yok)."
        if isinstance(p, Exception):
            return "HATA: opencode baslatilamadi: %s" % str(p)[-300:]
        if p == "ABORTED":
            return "Durduruldu (/abort)."
        if p == "TIMEOUT":
            return "HATA: opencode %d sn'de bitmedi. /durum ile kontrol edin." % OPENCODE_TIMEOUT
    if not stored and p.returncode == 0:
        fresh = _newest_session(started_ms)
        if fresh:
            _save_sess(chat_id, ses=fresh)
            log("yeni session eslestirildi chat=%s -> %s" % (chat_id, fresh))
    out = (p.stdout or "").strip() or (p.stderr or "").strip() or "(bos cikti)"
    if p.returncode != 0 and not (p.stdout or "").strip():
        out = "HATA (exit %d): %s" % (p.returncode, (p.stderr or "")[-1500:])
        _debug_log("RUN-FAIL chat=%s stored=%s rc=%d\nSTDOUT-tail: %s\nSTDERR-full:\n%s" % (
            chat_id, stored, p.returncode, (p.stdout or "")[-500:], p.stderr or ""))
    return out


def _spawn_worker(chat_id, from_label, prompt, files=None, reply_to=None):
    abort = threading.Event()
    box = {"reply_to": reply_to}

    def _run():
        try:
            out = trim_output(run_opencode(prompt, chat_id, from_label,
                                           files=files, abort=abort, proc_box=box))
            shown = prompt if len(prompt) < 4000 else prompt[:4000]
            if files:
                shown = "[dosya: %s] %s" % (", ".join(os.path.basename(f) for f in files), shown)
            mid = inbox_add(chat_id, from_label, shown,
                            handled_by="bridge", response_preview=out)
            send_message(str(chat_id), "%s\n\n[%s]" % (out, mid), reply_to=reply_to)
            s, f = outbox_flush()
            if s or f:
                log("outbox flush: sent=%d failed=%d" % (s, f))
        except Exception as e:  # noqa: BLE001
            log("ERROR worker: %s" % str(e)[-200:])
            try:
                send_message(str(chat_id), "HATA: %s" % str(e)[-500:])
            except Exception:
                pass
        finally:
            with _inflight_lock:
                cur = _inflight.get(str(chat_id))
                if cur is not None and cur.get("abort") is abort:
                    _inflight.pop(str(chat_id), None)

    th = threading.Thread(target=_run, daemon=True)
    with _inflight_lock:
        cur = _inflight.get(str(chat_id))
        if cur is not None and cur["thread"].is_alive():
            return False
        _inflight[str(chat_id)] = {"thread": th, "abort": abort, "box": box}
    th.start()
    return True


def trim_output(out, limit=11500):
    out = out.strip()
    if len(out) > limit:
        return out[:limit] + "\n\n...(kisaltildi)"
    return out


# ---------- mesaj isleme ----------

def _session_stats(ses_id):
    """session export'tan: mesaj sayisi + son asistan tokenleri (ucretsiz, local)."""
    cmd, _ = _opencode_cmd(["session", "export", ses_id])
    if cmd is None:
        return ""
    try:
        p = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        if p.returncode != 0 or not (p.stdout or "").strip():
            return ""
        d = json.loads(p.stdout)
        ms = d.get("messages", [])
        last = None
        for m in reversed(ms):
            if isinstance(m, dict) and m.get("type") == "assistant" and m.get("tokens"):
                last = m["tokens"]
                break
        if not last:
            return "Mesaj: %d (token bilgisi yok)" % len(ms)
        return ("Mesaj: %d | Son asistan: in=%s out=%s reasoning=%s" % (
            len(ms), last.get("input"), last.get("output"), last.get("reasoning")))
    except Exception:
        return ""


def cmd_status_for_chat(chat_id=None):
    base = cmd_status()
    if not chat_id:
        return base
    ent = _sess_entry(chat_id)
    ses = ent.get("ses")
    if not ses:
        return base + "\n\nTelegram oturumu: yok (ilk soruda acilir)"
    model = ent.get("model") or OPENCODE_MODEL or "(opencode varsayilani)"
    stats = _session_stats(ses)
    extra = "\n\nTelegram oturumu: %s...\nModel: %s" % (ses[:16], model)
    if stats:
        extra += "\n%s" % stats
    if _is_busy(chat_id):
        extra += "\nDurum: IS CALISIYOR (/abort ile durdurabilirsin)"
    return base + extra


def cmd_model(text, chat_id):
    parts = text.strip().split()
    cur = _chat_model(chat_id) or "(opencode varsayilani)"
    if len(parts) == 1 or parts[1].lower() in ("current", "goster"):
        return "Aktif model: %s\nDegistir: /model set <provider/model> | Liste: /model list" % cur
    if parts[1].lower() == "list":
        models = _opencode_models()
        if not models:
            return "Model listesi alinamadi."
        cur_full = _chat_model(chat_id)
        lines = []
        for m in models[:80]:
            mark = "*" if m == cur_full else " "
            lines.append("%s %s" % (mark, m))
        out = "Modeller (aktif *):\n" + "\n".join(lines)
        if len(models) > 80:
            out += "\n...(+%d)" % (len(models) - 80)
        return out
    if parts[1].lower() == "set" and len(parts) >= 3:
        model = parts[2].strip()
        if "/" not in model:
            return "Format: provider/model (orn. opencode/space-bunny-free)"
        known = _opencode_models()
        warn = "" if (not known or model in known) else " (UYARI: listede yok, yine de kaydedildi)"
        _save_sess(chat_id, model=model)
        return "Model ayarlandi: %s%s. Sonraki sorular bununla calisir." % (model, warn)
    return "Kullanim: /model | /model list | /model set <provider/model>"


def cmd_reset(chat_id):
    if _is_busy(chat_id):
        return "Once /abort ile calisan isi durdur."
    ent = _sess_entry(chat_id)
    ses = ent.get("ses")
    if ses:
        cmd, _ = _opencode_cmd(["session", "delete", ses])
        if cmd is not None:
            try:
                subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
            except Exception:
                pass
    _save_sess(chat_id, clear_ses=True)
    if ses:
        return "Oturum sifirlandi (eski oturum silindi). Sonraki soru yeni oturum acar."
    return "Aktif oturum yoktu; sonraki soru yeni oturum acar."


# ---------- yikici islem kapisi (/onay HOLD-xxx) ----------

HOLDS_FILE = os.path.join(MAILBOX_DIR, "telegram_holds.json")
HOLD_TTL_SEC = 1800

DANGER_PATTERNS = [
    r"\brm\s+(-[a-z]*r[a-z]*|--recursive)\b",
    r"\bdel\s+/[sq]\b",
    r"\bformat\s+[a-z]:",
    r"\bmkfs\b",
    r"\bdiskpart\b",
    r"\bdrop\s+(database|table|schema)\b",
    r"\btruncate\s+\w+",
    r"\bdelete\s+from\s+\w+",
    r"\bterraform\s+(destroy|apply)\b",
    r"\bkubectl\s+delete\b",
    r"\bhelm\s+uninstall\b",
    r"\bgit\s+push\b.{0,40}--force\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-fd",
    r"\bshutdown\b",
    r":\(\)\s*\{\s*:\|\:&\s*\}\s*;",
]


def _check_dangerous(text):
    low = text.lower()
    for pat in DANGER_PATTERNS:
        m = re.search(pat, low)
        if m:
            return m.group(0).strip()[:80]
    return None


def _load_holds():
    st = _load(HOLDS_FILE, {"seq": 0, "holds": []})
    now = time.time()
    fresh = [h for h in st.get("holds", []) if now - h.get("ts_epoch", 0) < HOLD_TTL_SEC]
    if len(fresh) != len(st.get("holds", [])):
        st["holds"] = fresh
        _save(HOLDS_FILE, st)
    return st


def _hold_create(chat_id, label, prompt, files):
    st = _load_holds()
    st["seq"] += 1
    hid = "HOLD-%03d" % st["seq"]
    st["holds"].append({
        "id": hid, "chat": str(chat_id), "from": label,
        "prompt": prompt[:4000], "files": files or [],
        "ts": _now(), "ts_epoch": time.time()})
    st["holds"] = st["holds"][-50:]
    _save(HOLDS_FILE, st)
    return hid


def _hold_take(chat_id, hid):
    st = _load_holds()
    for h in st.get("holds", []):
        if h.get("id") == hid.upper() and h.get("chat") == str(chat_id):
            st["holds"] = [x for x in st["holds"] if x.get("id") != h["id"]]
            _save(HOLDS_FILE, st)
            return h
    return None


def handle_text(chat_id, from_label, text, reply_to=None):
    t = text.strip()
    low = t.lower()
    if low in ("/start", "/yardim", "/help", "yardim"):
        return HELP, False
    if low in ("/durum", "/status"):
        return cmd_status_for_chat(chat_id), False
    if low.startswith("/roster"):
        return cmd_roster(), False
    if low.startswith("/gelen") or low.startswith("/inbox"):
        return cmd_inbox(), False
    if low == "/abort" or low.startswith("/abort "):
        return _abort_chat(chat_id), False
    if low == "/reset" or low.startswith("/reset"):
        return cmd_reset(chat_id), False
    if low == "/model" or low.startswith("/model ") or low.startswith("/model@"):
        return cmd_model(t, chat_id), False
    if low.startswith("/onay"):
        note = t[5:].strip() or "(bos onay)"
        first = note.split()[0].upper() if note.split() else ""
        if first.startswith("HOLD-"):
            h = _hold_take(chat_id, first)
            if h is None:
                return "%s bulunamadi/suresi dolmus (30 dk) ya da baska chate ait." % first, False
            if _is_busy(chat_id):
                # hold'u geri koy (tuketilmesin)
                st = _load_holds()
                st["holds"].append(h)
                _save(HOLDS_FILE, st)
                return "Halen bir is calisiyor. Bitince /onay %s ile tekrar dene." % h["id"], False
            if _spawn_worker(chat_id, from_label, h["prompt"], files=h.get("files"),
                              reply_to=reply_to):
                return ("%s onaylandi, calisiyorum... (/abort ile durdurabilirsin)" % h["id"], True)
            return "Halen bir is calisiyor. Bitince /onay %s ile tekrar dene." % h["id"], False
        mid = inbox_add(chat_id, from_label, "[ONAY] " + note)
        try:
            st = _load(os.path.join(MAILBOX_DIR, "inbox.json"), {"seq": 0, "messages": [], "heart": {}})
            st["seq"] += 1
            st["messages"].append({
                "id": "MSG-%03d" % st["seq"], "ts": _now(), "from": "telegram:%s" % chat_id,
                "to": "orchestrator", "type": "FYI", "subject": "Telegram onayi %s" % mid,
                "re": "", "body": note, "status": "UNREAD"})
            _save(os.path.join(MAILBOX_DIR, "inbox.json"), st)
        except Exception as e:  # noqa: BLE001
            return "Kuyruga yazilamadi: %s" % e, False
        return "Orchestrator kuyruguna birakildi (%s)." % mid, False
    # /sor prefix'i varsa temizle, yoksa duz metin = soru
    if low.startswith("/sor") or low.startswith("/ask"):
        t = t.split(" ", 1)[1] if " " in t else ""
        if not t.strip():
            return "Kullanim: /sor <metin>", False
    if not BRIDGE_EXEC:
        mid = inbox_add(chat_id, from_label, t)
        return "Kuyruga alindi (%s). Bridge calistirma modunda degil; TUI'daki orchestrator bakacak." % mid, False
    danger = _check_dangerous(t)
    if danger:
        hid = _hold_create(chat_id, from_label, t, None)
        return ("Duraklatildi: '%s' yakalandi.\n\nOngosterim: %s\n\n"
                "Devam icin: /onay %s (30 dk gecerli)\nVazgecmek icin: gormezden gel." % (
                    danger, t[:200], hid), False)
    return _gate_or_spawn(chat_id, from_label, t, reply_to=reply_to)


# ---------- fotograf / belge destegi (opencode --file) ----------

TG_FILES_DIR = os.path.join(MAILBOX_DIR, "tg_files")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
pending_media = {}


def _download_tg_file(file_id, hint="dosya"):
    info = api("getFile", {"file_id": file_id}, timeout=30)
    if not info.get("ok"):
        raise RuntimeError("getFile basarisiz: %s" % json.dumps(info)[:200])
    fpath = (info.get("result") or {}).get("file_path", "")
    if not fpath:
        raise RuntimeError("file_path yok.")
    url = "https://api.telegram.org/file/bot%s/%s" % (BOT_TOKEN, fpath)
    os.makedirs(TG_FILES_DIR, exist_ok=True)
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in
                   os.path.basename(fpath))[-80:] or hint
    local = os.path.join(TG_FILES_DIR, "%d_%s" % (int(time.time()), safe))
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as r, open(local, "wb") as f:
        total = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                try:
                    os.remove(local)
                except OSError:
                    pass
                raise RuntimeError("Dosya 20MB sinirini asiyor.")
            f.write(chunk)
    return local


def _queue_media(chat_id, label, group, local, caption):
    g = pending_media.setdefault(str(group), {
        "chat": str(chat_id), "label": label, "files": [],
        "caption": "", "first": time.time()})
    if local not in g["files"]:
        g["files"].append(local)
    if caption and not g["caption"]:
        g["caption"] = caption


def _gate_or_spawn(chat_id, label, prompt, files=None, reply_to=None):
    """Yikici icerikse HOLD'a al, yoksa worker baslat. Donus: (reply, executed)."""
    danger = _check_dangerous(prompt)
    if danger:
        hid = _hold_create(chat_id, label, prompt, files)
        return ("Duraklatildi: '%s' yakalandi.\n\nOngosterim: %s\n\n"
                "Devam icin: /onay %s (30 dk gecerli)\nVazgecmek icin: gormezden gel." % (
                    danger, prompt[:200], hid), False)
    if _is_busy(chat_id):
        return "Halen bir is calisiyor. Bitmesini bekle ya da /abort ile durdur.", False
    if _spawn_worker(chat_id, label, prompt, files=files, reply_to=reply_to):
        return "Alindi, calisiyorum... (bitince yazacagim; /abort ile durdurabilirsin)", True
    return "Halen bir is calisiyor. Bitmesini bekle ya da /abort ile durdur.", False


def _flush_media(force=False):
    now = time.time()
    for group in list(pending_media):
        g = pending_media[group]
        age = now - g["first"]
        if not force and age < 5:
            continue
        if age > 120:
            pending_media.pop(group, None)
            try:
                send_message(g["chat"], "Bekleyen dosya grubu zaman asimina ugradi, atildi.")
            except Exception:
                pass
            continue
        if _is_busy(g["chat"]):
            continue
        pending_media.pop(group, None)
        prompt = g["caption"] or "Ekteki dosyayi incele ve kisaca ozetle."
        log("MEDIA chat=%s files=%d" % (g["chat"], len(g["files"])))
        if not BRIDGE_EXEC:
            mid = inbox_add(g["chat"], g["label"], "[DOSYA] " + prompt)
            try:
                send_message(g["chat"], "Dosya kuyruga alindi (%s)." % mid)
            except Exception:
                pass
            continue
        reply, _ = _gate_or_spawn(g["chat"], g["label"], prompt, files=g["files"][:3])
        try:
            send_message(g["chat"], reply)
        except Exception:
            pass


def handle_update(u):
    msg = u.get("message") or u.get("edited_message") or {}
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return
    text = msg.get("text", "") or ""
    msg_id = msg.get("message_id")
    user = msg.get("from", {})
    label = ("@" + user.get("username")) if user.get("username") else str(user.get("id", "?"))
    if not is_allowed(chat_id):
        log("DENIED chat=%s user=%s text=%.60s" % (chat_id, label, text))
        return
    photo = msg.get("photo")
    doc = msg.get("document")
    if photo or doc:
        caption = (msg.get("caption", "") or "").strip()
        try:
            if photo:
                fid = photo[-1].get("file_id")
                hint = "foto.jpg"
            else:
                if int(doc.get("file_size", 0)) > MAX_UPLOAD_BYTES:
                    send_message(str(chat_id), "Dosya 20MB sinirini asiyor.")
                    return
                fid = doc.get("file_id")
                hint = doc.get("file_name", "belge")
            local = _download_tg_file(fid, hint)
            group = msg.get("media_group_id")
            log("FILE chat=%s user=%s file=%s" % (chat_id, label, os.path.basename(local)))
            if group:
                _queue_media(chat_id, label, group, local, caption)
            elif _is_busy(str(chat_id)):
                send_message(str(chat_id), "Halen bir is calisiyor. Bitmesini bekle ya da /abort ile durdur.")
            elif not BRIDGE_EXEC:
                mid = inbox_add(str(chat_id), label, "[DOSYA] " + (caption or hint))
                send_message(str(chat_id), "Dosya kuyruga alindi (%s)." % mid)
            else:
                reply, _ = _gate_or_spawn(str(chat_id), label,
                                          caption or "Ekteki dosyayi incele ve kisaca ozetle.",
                                          files=[local], reply_to=msg_id)
                send_message(str(chat_id), reply, reply_to=msg_id)
        except Exception as e:  # noqa: BLE001
            log("ERROR file: %s" % str(e)[-200:])
            try:
                send_message(str(chat_id), "Dosya alinamadi: %s" % str(e)[-300:])
            except Exception:
                pass
        return
    if not text.strip():
        send_message(str(chat_id), "Yazi, fotograf veya belge gonder.", reply_to=msg_id)
        return
    log("IN chat=%s user=%s len=%d" % (chat_id, label, len(text)))
    try:
        reply, executed = handle_text(str(chat_id), label, text, reply_to=msg_id)
        send_message(str(chat_id), reply, reply_to=msg_id)
        # opencode TUI tarafi bir sey kuyruklamissa hemen gonder
        s, f = outbox_flush()
        if s or f:
            log("outbox flush: sent=%d failed=%d" % (s, f))
    except Exception as e:  # noqa: BLE001
        log("ERROR handle: %s" % e)
        try:
            send_message(str(chat_id), "HATA: %s" % str(e)[-500:])
        except Exception:
            pass


LOCK_FILE = os.path.join(MAILBOX_DIR, "telegram_bridge.lock")
_lock_ours = False


def _acquire_lock():
    """Ayni makinede ikinci bridge'i engelle (409'un yerel sebebi)."""
    global _lock_ours
    me = os.getpid()
    if os.path.exists(LOCK_FILE):
        try:
            opid = int((_load(LOCK_FILE, {}) or {}).get("pid", 0))
        except Exception:
            opid = 0
        if opid and opid != me:
            alive = False
            try:
                if os.name == "nt":
                    r = subprocess.run(["tasklist", "/FI", "PID eq %d" % opid],
                                       capture_output=True, text=True, timeout=15)
                    alive = str(opid) in (r.stdout or "")
                else:
                    os.kill(opid, 0)
                    alive = True
            except Exception:
                alive = False
            if alive:
                return False, opid
    try:
        _save(LOCK_FILE, {"pid": me, "ts": _now()})
        _lock_ours = True
        return True, me
    except Exception:
        return True, me


def _release_lock():
    global _lock_ours
    if not _lock_ours:
        return
    try:
        cur = (_load(LOCK_FILE, {}) or {}).get("pid")
        if cur == os.getpid() and os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass
    _lock_ours = False


def main_loop():
    if not BOT_TOKEN:
        log("HATA: TELEGRAM_BOT_TOKEN yok. .env'ye ekleyin (bkz. TELEGRAM-KURULUM.md).")
        sys.exit(2)
    if not ALLOWED:
        log("HATA: TELEGRAM_ALLOWED_CHAT_IDS bos — kimse giremez. Once chat_id'nizi ekleyin.")
        sys.exit(2)
    hb = _load(HEART_FILE, {})
    offset = hb.get("offset", 0)
    ok_lock, other = _acquire_lock()
    if not ok_lock:
        log("HATA: baska bir bridge calisiyor (PID %s). Once onu kapatin; tek ornek calisir." % other)
        sys.exit(3)
    import atexit as _atexit
    _atexit.register(_release_lock)
    log("telegram-bridge v1.5 (guvenlik+saglamlik+UX) basladi project=%s exec=%s agent=%s model=%s allowed=%d kisi" % (
        PROJECT_DIR, BRIDGE_EXEC, OPENCODE_AGENT, OPENCODE_MODEL or "default", len(ALLOWED)))
    if DEFAULT_CHAT and DEFAULT_CHAT in ALLOWED:
        try:
            send_message(DEFAULT_CHAT,
                         "Kopru acildi (v1.5). Proje: %s | Model: %s | /yardim" % (
                             os.path.basename(PROJECT_DIR),
                             OPENCODE_MODEL or "opencode varsayilani"))
        except Exception as e:  # noqa: BLE001
            log("acilis ping gonderilemedi: %s" % str(e)[-150:])
    while True:
        try:
            _flush_media()
            res = api("getUpdates", {"timeout": POLL_TIMEOUT, "offset": offset,
                                     "allowed_updates": ["message", "edited_message"]},
                      timeout=POLL_TIMEOUT + 15)
            if not res.get("ok"):
                log("getUpdates ok=false: %.200s" % json.dumps(res))
                time.sleep(5)
                continue
            for u in res.get("result", []):
                offset = max(offset, int(u.get("update_id", 0)) + 1)
                handle_update(u)
            s, f = outbox_flush()
            if s or f:
                log("outbox flush: sent=%d failed=%d" % (s, f))
            heartbeat(offset)
        except KeyboardInterrupt:
            log("durduruldu.")
            heartbeat(offset, {"stopped": _now()})
            break
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "409" in msg:
                log("UYARI 409 Conflict: baska bir bridge ayni botla calisiyor! "
                    "Diger bridge penceresini/komutunu kapatip tek ornek birakin.")
            else:
                log("loop hatasi: %s" % msg[-300:])
            heartbeat(offset, {"last_error": msg[-200:]})
            time.sleep(5)


def selftest():
    ok = True
    print("== telegram-bridge selftest ==")
    print("project_dir :", PROJECT_DIR, "->", "VAR" if os.path.isdir(PROJECT_DIR) else "YOK")
    print("mailbox_dir :", MAILBOX_DIR)
    try:
        os.makedirs(MAILBOX_DIR, exist_ok=True)
        probe = os.path.join(MAILBOX_DIR, "selftest_probe.tmp")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        print("mailbox yazma: OK")
    except Exception as e:  # noqa: BLE001
        print("mailbox yazma: HATA", e)
        ok = False
    print("BOT_TOKEN   :", "VAR" if BOT_TOKEN else "YOK (.env'ye ekleyin)")
    print("ALLOWED     :", sorted(ALLOWED) if ALLOWED else "BOS (kimse giremez)")
    print("DEFAULT_CHAT:", DEFAULT_CHAT or "(bos)")
    print("EXEC        :", BRIDGE_EXEC, "| agent:", OPENCODE_AGENT,
          "| model:", OPENCODE_MODEL or "(opencode varsayilani)", "| timeout:", OPENCODE_TIMEOUT)
    try:
        cmd, _ = _opencode_cmd(["--version"])
        if cmd is None:
            print("opencode    : BULUNAMADI (PATH'te yok)")
            ok = False
        else:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=20)
            print("opencode    :", (p.stdout or p.stderr or "").strip() or "cikti yok")
            if p.returncode != 0:
                ok = False
    except Exception as e:  # noqa: BLE001
        print("opencode    : BULUNAMADI (%s)" % e)
        ok = False
    for name in ("server.py",):
        p = os.path.join(BRIDGE_DIR, "..", "mcp", name)
        print("mcp/%s :" % name, "VAR" if os.path.exists(p) else "YOK")
    print("SONUC:", "OK" if ok and BOT_TOKEN and ALLOWED else "EKSIK AYAR (ustteki YOK/BOS satirlari tamamlayin)")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)
    main_loop()

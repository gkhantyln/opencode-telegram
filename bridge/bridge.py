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
import urllib.error
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

if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

import atomic_json as _aj  # noqa: E402  (sys.path ayarindan sonra gelmeli)
import md2 as _md2  # noqa: E402


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
SERVE_BACKEND = env("TELEGRAM_BACKEND", "cli").lower() == "serve"
SERVE_URL = env("TELEGRAM_SERVE_URL", "http://127.0.0.1:4096")
try:
    SERVE_PORT = int(env("TELEGRAM_SERVE_PORT", "4096"))
except ValueError:
    SERVE_PORT = 4096
SERVE_PASSWORD = env("TELEGRAM_SERVE_PASSWORD", "")
_serve_proc = None
BRIDGE_EXEC = env("TELEGRAM_BRIDGE_EXEC", "1") not in ("0", "false", "no")
# Mesaj duzenlemesi ayni isi ikinci kez tetiklemesin (GAP-04). Varsayilan kapali.
FOLLOW_EDITS = env("TELEGRAM_FOLLOW_EDITS", "0") not in ("0", "false", "no")
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


def _pkg_version(default="0.0.0"):
    """Tek surum kaynagi: paket kokundeki VERSION dosyasi (GAP-13)."""
    p = os.path.normpath(os.path.join(BRIDGE_DIR, "..", "VERSION"))
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip() or default
    except OSError:
        return default


VERSION = _pkg_version()

# ---------- mailbox kuyruklari ----------

MAILBOX_DIR = os.environ.get("TEAM_MAILBOX_DIR") or os.path.join(PROJECT_DIR, ".opencode", "mailbox")
INBOX_FILE = os.path.join(MAILBOX_DIR, "telegram_inbox.json")
OUTBOX_FILE = os.path.join(MAILBOX_DIR, "telegram_outbox.json")
HEART_FILE = os.path.join(MAILBOX_DIR, "telegram_bridge.json")


def _load(path, default):
    return _aj.load(path, default)


def _save(path, obj):
    """Atomik yazim (paylasilan `.tmp` yok). Oku-degistir-yaz icin `_aj.tx` kullan."""
    _aj.save(path, obj)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(*a):
    """Konsola yaz. Token maskesi buradan gecer.

    Canli testte log dosyalari paylasiliyor; bir hata mesaji yanlislikla
    bot token'ini icerse (orn. HTTP hata metni URL'den turesin) gizli kalmiyor.
    """
    print("[%s]" % _now(), *[_outgoing(str(x)) for x in a], flush=True)


# ---------- Telegram API (stdlib) ----------

API = "https://api.telegram.org/bot%s/%s"

# Retry politikasi (GAP-06). Gecici sunucu hatalari (5xx) ve Telegram'in rate
# limit'i (429) gecicidir; 400/401/403/404/409 kalicidir ve TEKRARLANMAZ.
# 409 ozel: baska bir bridge ayni botta demektir, tekrar denemek durumu
# bulaniklastirir (main_loop bunu ozel mesajla yazar).
API_RETRY_STATUSES = (500, 502, 503, 504)
try:
    API_MAX_RETRIES = max(0, int(env("TELEGRAM_API_MAX_RETRIES", "3")))
except ValueError:
    API_MAX_RETRIES = 3
API_RETRY_BASE = 0.6
API_RETRY_CAP = 8.0
API_RETRY_AFTER_CAP = 30.0


def _read_error_body(e):
    """HTTPError govdesini JSON olarak oku (retry_after burada gelir).

    Govde yalnizca BIR kez okunabilir; ayni istisna uzerinde birden fazla
    karar verilirse (parse hatasi mi, not-modified mi) ilk okuma ikincisini
    boslardi. Bu yuzden sonuc istisnaya cache'lenir.
    """
    cached = getattr(e, "_tg_body", None)
    if cached is not None:
        return cached
    body = {}
    try:
        raw = e.read()
    except Exception:
        raw = b""
    if raw:
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
            body = d if isinstance(d, dict) else {}
        except Exception:
            body = {}
    try:
        e._tg_body = body
    except Exception:
        pass
    return body


def _err_desc(e):
    return str((_read_error_body(e) or {}).get("description") or "").lower()


def _retry_wait(code, body, attempt):
    """Tekrar bekleme suresi (sn). None = bu hata tekrar edilmemeli."""
    if code == 429:
        secs = 0.0
        params = body.get("parameters")
        if isinstance(params, dict):
            try:
                secs = float(params.get("retry_after") or 0)
            except (TypeError, ValueError):
                secs = 0.0
        if secs <= 0:
            m = re.search(r"retry after\s+(\d+)",
                          str(body.get("description") or ""), re.IGNORECASE)
            secs = float(m.group(1)) if m else 0.0
        return min(secs if secs > 0 else API_RETRY_BASE, API_RETRY_AFTER_CAP)
    if code in API_RETRY_STATUSES:
        return min(API_RETRY_BASE * (2 ** max(0, attempt)), API_RETRY_CAP)
    return None


def api(method, payload=None, timeout=45, max_retries=None):
    """Telegram Bot API cagrisi. 429 ve gecici 5xx icin ustel backoff'lu
    tekrar dener; 400/401/403/404/409 gibi kalici hatalarda tekrar etmez."""
    url = API % (BOT_TOKEN, method)
    data = json.dumps(payload or {}).encode("utf-8")
    n = API_MAX_RETRIES if max_retries is None else max(0, int(max_retries))
    attempt = 0
    while True:
        try:
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # 4xx/5xx (URLError'un alt sinifi)
            if attempt >= n:
                raise
            wait = _retry_wait(e.code, _read_error_body(e), attempt)
            if wait is None:
                raise
            attempt += 1
            log("api %s -> HTTP %s, %.1f sn sonra tekrar (%d/%d)"
                % (method, e.code, wait, attempt, n))
            time.sleep(wait)
        except OSError as e:  # ag/soket hatasi: URLError, timeout, reset
            if attempt >= n:
                raise
            attempt += 1
            wait = min(API_RETRY_BASE * (2 ** (attempt - 1)), API_RETRY_CAP)
            log("api %s ag hatasi: %s | %.1f sn sonra tekrar (%d/%d)"
                % (method, str(e)[-120:], wait, attempt, n))
            time.sleep(wait)


# ---------- opencode ciktisini coz ve temizle ----------
#
# Canli test bulgulari (2026-09-29, gercek Telegram):
#   1. Windows'ta opencode Turkce karakterleri Windows kodlamasiyla (cp1254)
#      yaziyor. Salt UTF-8 ile okununca 'g' U+FFFD'ye duser ve cevapta
#      "deste?Yini" gibi bozuk metin gorunuyordu (5 cevabin 5'i).
#   2. opencode renkli cikti uretiyor: ESC[0m, ESC[91m, ESC[1m... Bunlar ham
#      gonderilince Telegram'da bozuk karakter olarak gorunuyordu.

# CSI (ESC[...son) + OSC (ESC]...son) + tek karakterli ESC dizileri
# SIRA ONEMLI: once OSC gelmeli. `[@-Z\\-_]` araligi `]` karakterini de
# icerdigi icin (backslash -> underscore araligi) once denirse ESC]'i
# yutar ve OSC govdesi artik eslesmez.
_ANSI_RE = re.compile(
    r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|[@-Z\\-_]"
    r"|\[[0-?]*[ -/]*[@-~]"
    r")")

try:
    OUTPUT_ENCODINGS = [e.strip() for e in
                        env("TELEGRAM_OUTPUT_ENCODING",
                            "cp1254,cp1252,latin-1").split(",") if e.strip()]
except Exception:  # noqa: BLE001
    OUTPUT_ENCODINGS = ["cp1254", "cp1252", "latin-1"]


def decode_output(raw):
    """opencode bayt ciktisini metne cevir.

    Once UTF-8 dener (dogru olan yol). Tutmazsa Turkce Windows kodlamasini
    ve latin-1'i sirayla dener. Hicbiri tutmazsa cokertmeli coz - mesaj
    kaybolmamali, en kotu birkac bozuk karakter gorunur.
    """
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for enc in OUTPUT_ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def clean_text(text):
    """Telegram'a gidecek metni hazirla: ANSI kontrol kodlarini at, NUL temizle."""
    if not text:
        return text
    return _ANSI_RE.sub("", text).replace("\x00", "")


def _outgoing(text):
    """Telegram'a giden her metin bu darbogazdan gecer: temizle -> maskele."""
    return _redact(clean_text(text))


# ---------- secret maskeleme (tum giden Telegram metni buradan gecer) ----------

_REDACT_PATTERNS = [
    r"sk-[A-Za-z0-9\-_]{16,}",
    r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}",
    r"github_pat_[A-Za-z0-9_]{16,}",
    r"AKIA[0-9A-Z]{16}",
    r"xox[bpas]-[A-Za-z0-9\-]+",
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
]


def _redact(text):
    if not text:
        return text
    out = text
    for pat in _REDACT_PATTERNS:
        out = re.sub(pat, "[REDACTED]", out)
    tok = BOT_TOKEN.strip()
    if tok and len(tok) > 8 and tok in out:
        out = out.replace(tok, "[REDACTED-BOT-TOKEN]")
    return out


# ---------- audit log (kim, ne istedi, ne oldu) ----------

AUDIT_FILE = os.path.join(MAILBOX_DIR, "telegram_audit.log")
AUDIT_MAX_BYTES = 100 * 1024  # PLAN.md TG1-2 ile ayni (GAP-14)


def _audit(chat_id, kind, summary, extra=None):
    try:
        os.makedirs(MAILBOX_DIR, exist_ok=True)
        rec = {"ts": _now(), "chat": str(chat_id), "kind": kind,
               "summary": _outgoing((summary or "")[:500])}
        if extra:
            rec.update(extra)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        if os.path.exists(AUDIT_FILE) and os.path.getsize(AUDIT_FILE) > AUDIT_MAX_BYTES:
            with open(AUDIT_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            with open(AUDIT_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[len(lines) // 2:])
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def send_message_id(chat_id, text):
    """Ilk parcayi gonderip Telegram message_id doner (duzenleme/callback icin)."""
    text = _outgoing(text)
    first = (text[:4000] or "")
    res = api("sendMessage", {"chat_id": chat_id, "text": first})
    mid = ((res or {}).get("result") or {}).get("message_id")
    rest = text[4000:]
    for i in range(0, len(rest), 4000):
        api("sendMessage", {"chat_id": chat_id, "text": rest[i:i + 4000]})
        time.sleep(0.4)
    return mid


def edit_message(chat_id, message_id, text):
    try:
        api("editMessageText", {"chat_id": chat_id, "message_id": message_id,
                                "text": _outgoing(text)[:4000]})
        return True
    except Exception:
        return False


def answer_callback(callback_id, text=""):
    try:
        api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})
    except Exception:
        pass


CALLBACK_DATA_MAX = 64  # Telegram'in kesin limiti; asarsa mesaj gonderilmez


def _fit_callback(data):
    """callback_data 64 bayta sigmiyorsa kisalt (Telegram mesaji reddeder)."""
    raw = (data or "").encode("utf-8")
    if len(raw) <= CALLBACK_DATA_MAX:
        return data or ""
    return raw[:CALLBACK_DATA_MAX - 3].decode("utf-8", "ignore") + "..."


def button_grid(buttons, cols=2):
    """Butonlari `cols` sutunlu izarga olusturur; bosluk birakilmaz.

    Onceki surumde butonlar tek satira diziliyordu: 8 secenekli bir soruda
    Telegram 8 butonu bir satira sigdiromaya calisiyordu.
    """
    rows, cur = [], []
    for b in buttons:
        cur.append({"text": b[0], "callback_data": _fit_callback(b[1])})
        if len(cur) >= max(1, cols):
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    return rows


def send_buttons(chat_id, text, buttons, reply_to=None, cols=2, cancel=None):
    """Inline butonlu mesaj.

    buttons: [(label, callback_data)]
    cancel:  (label, callback_data) -> en altta tek satira eklenir
    Donus: message_id
    """
    rows = button_grid(buttons, cols=cols)
    if cancel:
        rows.append([{"text": cancel[0], "callback_data": _fit_callback(cancel[1])}])
    payload = {"chat_id": chat_id, "text": _outgoing(text)[:4000],
               "reply_markup": {"inline_keyboard": rows}}
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to
    res = api("sendMessage", payload)
    return ((res or {}).get("result") or {}).get("message_id")


pending_perms = {}
pending_forms = {}
_perm_seq = [0]


def _prune_pending():
    now = time.time()
    for store in (pending_perms, pending_forms):
        for k in [k for k, v in store.items() if now - v.get("ts", 0) > 1800]:
            store.pop(k, None)


def send_message(chat_id, text, reply_to=None):
    """4000'luk parcalarla gonderir. Donus: gonderilen parca sayisi."""
    text = _outgoing(text)
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


# ---------- Markdown gonderimi (bicimleme + geri dusus zinciri) ----------

# opencode Markdown dondurur; duz metin gonderilince isaretler ham gorunur.
# 0 ile tamamen kapatilabilir (bicimleme hatasi sorunu olursa ilk bakacak yer).
MARKDOWN_ON = env("TELEGRAM_MARKDOWN", "1") not in ("0", "false", "no")

_PARSE_ERR_MARKERS = (
    "can't parse", "can't find end of the entity", "entity beginning",
    "entity url", "wrong http url", "url host is empty", "unsupported start tag",
)


def _is_parse_error(exc):
    """Telegram bicimlemeyi mi reddetti? (400 + parse aciklamasi)"""
    if getattr(exc, "code", None) != 400:
        return False
    desc = _err_desc(exc)
    return any(m in desc for m in _PARSE_ERR_MARKERS)


def _md_pieces(text, limit=3900):
    """(metin, parse_mode) listesi.

    Markdown kapaliysa duz parcalama. Aciksa blok bazli paketleme: kod
    blogu asla karakter sayisiyla kesilmez (bkz. md2.pack).
    """
    if not text:
        return []
    if not MARKDOWN_ON:
        return [(text[i:i + limit], None) for i in range(0, len(text), limit)]
    return [(c["text"], c["parse_mode"]) for c in _md2.pack(text, limit=limit)] or [(text, None)]


def _with_footer(text, parse_mode, footer):
    """`[TG-001]` gibi etiketi ekler.

    MarkdownV2'de `[` `]` ayrac oldugu icin koseli parantez de kacislanir.
    """
    if not footer:
        return text
    return (text + "\n\n" + _md2.render_text("[%s]" % footer)) if parse_mode \
        else "%s\n\n[%s]" % (text, footer)


def _send_chunk(chat_id, text, parse_mode=None, reply_to=None):
    """Tek mesaj gonderir. Bicim reddedilirse duz metne duser.

    Donu: message_id. Bu, bicimleme hatasinin mesaji KAYBETTIRMESINI saglar:
    Telegram 400 + parse aciklamasi dondugunde ayni icerik parse_mode'suz
    tekrar gonderilir.
    """
    payload = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        res = api("sendMessage", payload)
    except urllib.error.HTTPError as e:
        if not parse_mode or not _is_parse_error(e):
            raise
        log("markdown parse hatasi -> duz metne dusuluyor")
        payload.pop("parse_mode")
        payload["text"] = _md2.unescape(text)
        res = api("sendMessage", payload)
    return ((res or {}).get("result") or {}).get("message_id")


def _is_not_modified(exc):
    """Telegram ayni icerikle duzenlemeyi 400 ile reddeder.

    Bu bir hata degil, hedef duruma ulasildi demektir. Yanlisca basarisiz
    sayilirsa ayni mesaj ikinci kez gonderilir (mukerrer cevap).
    """
    if getattr(exc, "code", None) != 400:
        return False
    return "message is not modified" in _err_desc(exc)


def _edit_chunk(chat_id, message_id, text, parse_mode=None):
    """`editMessageText` + ayni geri dusus zinciri."""
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        api("editMessageText", payload)
        return True
    except urllib.error.HTTPError as e:
        if _is_not_modified(e):
            return True
        if not parse_mode or not _is_parse_error(e):
            return False
        payload.pop("parse_mode")
        payload["text"] = _md2.unescape(text)
        try:
            api("editMessageText", payload)
            return True
        except urllib.error.HTTPError as e2:
            return _is_not_modified(e2)
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001
        return False


def is_allowed(chat_id):
    return str(chat_id) in ALLOWED


# ---------- inbox/outbox ----------

def inbox_add(chat_id, from_label, text, handled_by="", response_preview=""):
    with _aj.tx(INBOX_FILE):
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


def _outbox_dests(m):
    """Kuyruk kaydinin gonderilecegi hedefler. Bos liste = teslim edilemez."""
    to = str(m.get("to") or "default")
    if to in ("default", ""):
        return [DEFAULT_CHAT] if DEFAULT_CHAT else []
    if to == "broadcast":
        return sorted(ALLOWED) if ALLOWED else []
    return [to]


def outbox_recover(max_age=300):
    """Crash/kill sonrasi SENDING'de kalan kayitlari QUEUED'a al.

    Bridge tek ornek calistigi icin baslangicta SENDING gormesi = o surec
    olurken takilip kalmis demektir. `max_age` ile yeni baslayan baska bir
    surecin isini ezmeden korunur.
    """
    now = time.time()
    n = 0
    with _aj.tx(OUTBOX_FILE):
        st = _load(OUTBOX_FILE, {"seq": 0, "messages": []})
        for m in st.get("messages", []):
            if m.get("status") != "SENDING":
                continue
            if now - float(m.get("sending_since") or 0) < max_age:
                continue
            m["status"] = "QUEUED"
            m.pop("sending_since", None)
            m["attempts"] = int(m.get("attempts", 0)) + 1
            m["next_try"] = now
            n += 1
        if n:
            _save(OUTBOX_FILE, st)
    return n


def outbox_flush():
    """QUEUED kayitlari gonderir; basarisizlar 4 denemeye kadar ertelenir.

    Uc asamali: (1) `tx` icinde SENDING'e sahiplen, (2) ag islemini kilit
    disinda yap, (3) sonucu yine `tx` icinde yaz. Boylece uc farkli yerden
    (poll loop, mesaj sonrasi, worker) cagrildiginda ayni kayit iki kez
    Telegram'a gitmez (GAP-03). Donus: (gonderilen, hata).
    """
    now = time.time()
    sent, failed = 0, 0

    # --- asama 1: sahiplen (kilitli, ag yok) ---
    claimed = []
    with _aj.tx(OUTBOX_FILE):
        st = _load(OUTBOX_FILE, {"seq": 0, "messages": []})
        for m in st.get("messages", []):
            if m.get("status") != "QUEUED" or m.get("next_try", 0) > now:
                continue
            if not _outbox_dests(m):
                m["status"] = "FAILED"
                m["error"] = "hedef yok (TELEGRAM_DEFAULT_CHAT_ID bos ya da allowlist bos)"
                failed += 1
                continue
            if DIGEST_ON and str(m.get("to") or "default") in ("default", ""):
                _digest_push(m)
                m["status"] = "SENT"
                m["error"] = "digest'e alindi"
                sent += 1
                continue
            m["status"] = "SENDING"
            m["sending_since"] = now
            claimed.append({"id": m.get("id"), "to": m.get("to"),
                            "text": m.get("text", "")})
        _save(OUTBOX_FILE, st)

    # --- asama 2: gonder (kilit disinda) ---
    results = {}
    for c in claimed:
        ok_all, err = True, ""
        for d in _outbox_dests(c):
            if d not in ALLOWED:
                ok_all = False
                err = "hedef allowlist disi: %s" % d
                continue
            try:
                send_message(d, c["text"])
            except Exception as e:  # noqa: BLE001
                ok_all = False
                err = str(e)[-300:]
        results[c["id"]] = (ok_all, err)

    # --- asama 3: sonucu yaz (kilitli) ---
    if results:
        with _aj.tx(OUTBOX_FILE):
            st = _load(OUTBOX_FILE, {"seq": 0, "messages": []})
            for m in st.get("messages", []):
                res = results.get(m.get("id"))
                if res is None or m.get("status") != "SENDING":
                    continue
                ok_all, err = res
                m.pop("sending_since", None)
                if ok_all:
                    m["status"] = "SENT"
                    m.pop("attempts", None)
                    m.pop("next_try", None)
                    m.pop("error", None)
                    sent += 1
                    continue
                m["error"] = err
                att = int(m.get("attempts", 0)) + 1
                m["attempts"] = att
                if att >= 4:
                    m["status"] = "FAILED"
                    failed += 1
                else:
                    m["status"] = "QUEUED"
                    m["next_try"] = time.time() + 60 * att
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
    "/project — proje listele/sec (cok projeli kullanimda)\n"
    "/model — aktif model | /model list [filtre] [sayfa] | /model set <p/m>\n"
    "/sessions [sayfa] — oturum listesi | /sessions ac <no> gec | /sessions yeni\n"
    "/yardim — bu liste\n\n"
    "Duz mesaj = /sor. Fotograf/belge gonderebilirsin (incelenir). "
    "Her chat'te ayni anda tek is calisir."
)

# ---------- Telegram komut menusu (GAP-B) ----------
# Onceki surumde kullanici BotFather'a elle komut girmesi gerekiyordu
# (docs/KURULUM.md) ve cogu kullanici yapmiyordu -> Telegram'in "/" menusu
# bos kaliyordu. Bridge acilista `setMyCommands` ile doldurur.
# Kurallar: isim kucuk harf/rakam/alt cizgi, 1-32 karakter; aciklama 1-256.
SET_COMMANDS = env("TELEGRAM_SET_COMMANDS", "1") not in ("0", "false", "no")

TG_COMMANDS = [
    {"command": "yardim", "description": "Komut listesi"},
    {"command": "durum", "description": "Proje, oturum ve model durumu"},
    {"command": "sor", "description": "Orchestrator'a soru veya gorev gonder"},
    {"command": "onay", "description": "Onay / not birak (HOLD-xxx ile isiyi calistir)"},
    {"command": "abort", "description": "Calisan isi durdur"},
    {"command": "reset", "description": "Telegram oturumunu sifirla"},
    {"command": "model", "description": "Model gor / listele / degistir"},
    {"command": "sessions", "description": "Oturumlari listele / gec / yeni olustur"},
    {"command": "project", "description": "Proje listele / sec"},
    {"command": "roster", "description": "Agent canlilik durumu"},
    {"command": "gelen", "description": "Okunmamis team-mailbox mesajlari"},
    {"command": "start", "description": "Baslangic / tanitim mesaji"},
]


def set_my_commands():
    """Telegram'in "/" komut menusunu kur. Basarisiz olursa bridge yine acilir.

    Donus: True gonderildi, False atlandi ya da gonderilemedi.
    """
    if not SET_COMMANDS:
        return False
    try:
        api("setMyCommands", {"commands": TG_COMMANDS}, timeout=20)
        return True
    except Exception as e:  # noqa: BLE001
        log("setMyCommands atlandi: %s" % str(e)[-150:])
        return False


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


_UNSET = object()


def _save_sess(chat_id, ses=_UNSET, model=_UNSET, clear_ses=False, project=_UNSET):
    """Oturum kaydini guncelle.

    Varsayilan `_UNSET`: parametre verilmedigi durumda o alan DEGISTIRILMEZ.
    `None` verilirse o alan TEMIZLENIR. Onceki imzada `None` "dokunma" demekti,
    bu yuzden `/model otomatik` gecersiz kilmayi kaldiramiyordu.
    """
    try:
        with _aj.tx(SESSIONS_FILE):
            m = _load_sessions()
            ent = _sess_entry(chat_id)
            if clear_ses:
                ent.pop("ses", None)
            elif ses is not _UNSET:
                if ses:
                    ent["ses"] = ses
                else:
                    ent.pop("ses", None)
            if model is not _UNSET:
                if model:
                    ent["model"] = model
                else:
                    ent.pop("model", None)
            if project is not _UNSET:
                if project:
                    ent["project"] = project
                else:
                    ent.pop("project", None)
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
# 356 model donen bir opencode kurulumunda tek sayfa okunamazdi. Canli test
# bulgusu: onceki surum ilk 80'i gosterip geri kalana erisim birakiyordu.
MODEL_PAGE_SIZE = max(10, int(env("TELEGRAM_MODEL_PAGE_SIZE", "25") or 25))


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


def _session_list(limit=50, cwd=None):
    """opencode oturumlarini JSON olarak listele (yeni once).

    Dizi dondurur: [{'id','title','created','updated','directory'}]
    Hata durumunda bos liste; cagiran taraf bunu kullaniciya soyler.
    """
    cmd, _ = _opencode_cmd(["session", "list", "--format", "json", "-n", str(limit)])
    if cmd is None:
        return []
    try:
        p = subprocess.run(cmd, cwd=cwd or PROJECT_DIR, capture_output=True, timeout=60)
        if p.returncode != 0 or not p.stdout.strip():
            return []
        data = json.loads(decode_output(p.stdout))
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict) and d.get("id")]
    except Exception:
        return []


def _newest_session(since_ms=0, cwd=None):
    """Local `session list` (model harcamaz). since_ms sonrasi en yeni oturum."""
    for s in _session_list(5, cwd or PROJECT_DIR):
        if since_ms and int(s.get("created", 0)) < since_ms:
            return None
        return s.get("id")
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


def _drain_pipe(stream, sink):
    """Popen stdout/stderr pipe'unu bosaltir.

    Bunu yapmazsak PIPE ~64 KB'da dolar, cocuk surec yazarken kilitlenir ve
    hic cikmaz; parent da cikis beklerken asilir (bkz. GAP-02).
    Bayt toplanir, cozme islemi tek yerde yapilir (bkz. decode_output).
    """
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            sink.append(chunk)
    except (ValueError, OSError):
        pass


def _wait_proc(proc, abort, chat_id, timeout, reply_to=None):
    """Popen'i bekler; abort/timeout'ta oldurur.

    stdout/stderr ayri thread'lerde bosaltilir, yoksa pipe dolup surec
    kilitlenir (GAP-02). Donus: (durum, stdout, stderr) durum:
    'ok' | 'ABORTED' | 'TIMEOUT'.
    """
    out_buf, err_buf = [], []
    readers = [threading.Thread(target=_drain_pipe, args=(proc.stdout, out_buf),
                                daemon=True),
               threading.Thread(target=_drain_pipe, args=(proc.stderr, err_buf),
                                daemon=True)]
    for r in readers:
        r.start()
    start = time.time()
    ping = 0
    last_progress = start
    while True:
        if proc.poll() is not None:
            for r in readers:
                r.join(timeout=5)
            return "ok", decode_output(b"".join(out_buf)), decode_output(b"".join(err_buf))
        if abort is not None and abort.is_set():
            _kill_tree(proc)
            return "ABORTED", "", ""
        if time.time() - start > timeout:
            _kill_tree(proc)
            return "TIMEOUT", "", ""
        time.sleep(1.0)
        ping += 1
        now = time.time()
        if ping % 15 == 0:  # ~15 sn'de bir "yaziyor"
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


# ---------- serve backend (TG2-1; TELEGRAM_BACKEND=serve) ----------

_serve_clients = {}


def progressive_throttle(elapsed_sec):
    """Sureye gore streaming guncelleme araligi (sn).

    Sabit bir aralik yanlistir: 15 dakikalik bir iste 2 sn'de bir
    `editMessageText` 450 gereksiz API cagrisi demektir. Kademeli:
      <1 dk   -> 1 sn   (kisa isler akici gorunur)
      <5 dk   -> 2 sn
      <15 dk  -> 5 sn
      >=15 dk -> 10 sn  (uzun islerde rate limit'e takilmaz)

    """
    t = max(0.0, float(elapsed_sec or 0))
    if t < 60:
        return 1.0
    if t < 300:
        return 2.0
    if t < 900:
        return 5.0
    return 10.0


def _serve(directory=None):
    """ServeClient (dizin bazinda cache); ulasilamiyorsa serve'i ayaga kaldirir."""
    global _serve_proc
    import serve_client as _sc
    directory = directory or PROJECT_DIR
    if directory in _serve_clients:
        return _serve_clients[directory]
    pw = SERVE_PASSWORD or ("tg-local-%d" % (int(time.time()) % 1000000))
    c = _sc.ServeClient(SERVE_URL, pw, directory)
    try:
        c._req("GET", "/api/info", timeout=8)
        _serve_clients[directory] = c
        return c
    except Exception:
        pass
    # ayaga kaldir
    cmd, _ = _opencode_cmd(["serve", "--port", str(SERVE_PORT)])
    if cmd is None:
        raise _sc.ServeError("'opencode' bulunamadi, serve acilamiyor.")
    eenv = dict(os.environ)
    eenv["OPENCODE_PASSWORD"] = pw
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    _serve_proc = subprocess.Popen(cmd, cwd=PROJECT_DIR, env=eenv,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=flags)
    c = _sc.ServeClient("http://127.0.0.1:%d" % SERVE_PORT, pw, directory)
    deadline = time.time() + 30
    last = None
    while time.time() < deadline:
        try:
            c._req("GET", "/api/info", timeout=5)
            _serve_clients[directory] = c
            log("serve ayaga kalkti (port %d)" % SERVE_PORT)
            return c
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1)
    raise _sc.ServeError("serve acilamadi: %s" % (str(last)[-200:] if last else "?"))


def _serve_model():
    m = OPENCODE_MODEL.strip()
    if m and "/" in m:
        provider, mid = m.split("/", 1)
        return (provider, mid)
    return None


def _form_snapshot(form):
    """Form sozlugunden (key, field, options, title) cikarir; seceneksizse None."""
    if not isinstance(form, dict):
        return None
    title = ""
    for k in ("title", "question", "message", "label"):
        if isinstance(form.get(k), str) and form[k].strip():
            title = form[k].strip()[:200]
            break
    found = []

    def _walk(node, key=""):
        if isinstance(node, dict):
            opts = node.get("options")
            if isinstance(opts, list) and opts and all(isinstance(o, (str, dict)) for o in opts):
                labels = [o if isinstance(o, str) else str(o.get("label", o.get("value", o)))[:60]
                          for o in opts]
                found.append((key, labels))
            for k, v in node.items():
                if k != "options":
                    _walk(v, k)
        elif isinstance(node, list):
            for v in node:
                _walk(v, key)

    _walk(form.get("fields", form))
    if not found:
        return None
    key, labels = found[0]
    return (key or "secim", None, labels, title)


def run_opencode_serve(prompt, chat_id, from_label, files=None, abort=None,
                       reply_to=None, _session_retry=True):
    import serve_client as _sc
    sid_title = "%s-%s" % (SESSION_PREFIX, str(chat_id).lstrip("-"))
    wrapped = (
        "[Telegram] Gonderen: %s (chat %s). "
        "Cevap Telegram'a gonderilecek: kisa yaz, 3000 karakteri gecme, "
        "secret/token sizdirma. Soru/gorev:\n\n%s" % (from_label, chat_id, prompt)
    )
    try:
        c = _serve(_chat_dir(chat_id))
    except Exception as e:  # noqa: BLE001
        return "HATA: serve baglantisi kurulamadi: %s" % str(e)[-300:], None
    stored = _sess_entry(chat_id).get("ses")
    if not stored:
        try:
            stored = c.create_session(title="Telegram %s" % sid_title,
                                      agent=OPENCODE_AGENT, model=_serve_model())
            _save_sess(chat_id, ses=stored)
            log("serve session eslestirildi chat=%s -> %s" % (chat_id, stored))
        except Exception as e:  # noqa: BLE001
            _debug_log("SERVE-CREATE-FAIL chat=%s err=%s" % (chat_id, str(e)[-500:]))
            return "HATA: oturum acilamadi: %s" % str(e)[-300:], None
    fargs = None
    if files:
        import mimetypes
        fargs = []
        for f in files[:3]:
            mt, _ = mimetypes.guess_type(f)
            fargs.append((mt or "application/octet-stream", f))
    started_ms = int(time.time() * 1000)
    try:
        c.prompt(stored, wrapped, files=fargs,
                 model=_serve_model() if _sess_entry(chat_id).get("model") else None)
    except Exception as e:  # noqa: BLE001
        err = str(e)
        if _session_retry and ("ses" in err or "ession" in err or "404" in err):
            _save_sess(chat_id, clear_ses=True)
            return run_opencode_serve(prompt, chat_id, from_label, files=files,
                                      abort=abort, reply_to=reply_to,
                                      _session_retry=False)
        _debug_log("SERVE-PROMPT-FAIL chat=%s err=%s" % (chat_id, err[-800:]))
        return "HATA: %s" % err[-400:], None
    last_ping = [time.time()]
    stream = {"buf": [], "assistant": None, "failed": False, "stop": False,
              "msg_id": None, "last_edit": 0.0, "edited": ""}
    seen_perms = set()
    seen_forms = set()

    def _sse_loop():
        import serve_client as _sc2
        while not stream["stop"]:
            if abort is not None and abort.is_set():
                return
            try:
                for ev in c.events(stop_flag=lambda: stream["stop"] or (
                        abort is not None and abort.is_set()), timeout=25):
                    d = ev.get("data") or {}
                    if not isinstance(d, dict):
                        continue
                    ed = d.get("data") or {}
                    if not isinstance(ed, dict):
                        continue
                    if ed.get("sessionID") != stored:
                        continue
                    t = d.get("type", "")
                    if t == "session.text.delta":
                        stream["buf"].append(str(ed.get("delta", "")))
                        if ed.get("assistantMessageID"):
                            stream["assistant"] = ed["assistantMessageID"]
                    elif t in ("session.execution.succeeded", "session.execution.failed",
                               "session.text.ended"):
                        if t != "session.text.ended" or not stream["buf"]:
                            stream["failed"] = (t == "session.execution.failed")
            except Exception:
                time.sleep(2)

    def _poll_approvals():
        try:
            for p in c.list_permissions(stored):
                rid = p.get("id") if isinstance(p, dict) else None
                if rid and rid not in seen_perms and rid not in pending_perms:
                    seen_perms.add(rid)
                    action = p.get("action", "?") if isinstance(p, dict) else "?"
                    res = (p.get("resources") or []) if isinstance(p, dict) else []
                    msg = (p.get("message") or "") if isinstance(p, dict) else ""
                    txt = "Izin istegi: %s\n%s%s" % (
                        action, ", ".join(res[:5]),
                        ("\n%s" % msg[:300]) if msg else "")
                    mid = send_buttons(str(chat_id), txt, [
                        ("Onayla (1 kez)", "prm:%s:once" % rid),
                        ("Her zaman", "prm:%s:always" % rid),
                        ("Reddet", "prm:%s:reject" % rid)], cols=3, reply_to=reply_to)
                    pending_perms[rid] = {"session": stored, "chat": str(chat_id),
                                          "msg": mid, "ts": time.time()}
            for f in c.list_forms(stored):
                fid = f.get("id") if isinstance(f, dict) else None
                if not fid or fid in seen_forms:
                    continue
                seen_forms.add(fid)
                snap = _form_snapshot(f)
                if snap is None:
                    continue
                _perm_seq[0] += 1
                short = "F-%03d" % _perm_seq[0]
                pending_forms[short] = {"session": stored, "chat": str(chat_id),
                                        "formID": fid, "key": snap[0],
                                        "options": snap[2], "ts": time.time(), "msg": None}
                mid = send_buttons(str(chat_id),
                                   "Secim gerekli%s:\n%s" % (
                                       (" (%s)" % snap[3]) if snap[3] else "",
                                       "\n".join("%d. %s" % (i + 1, o)
                                                 for i, o in enumerate(snap[2][:8]))),
                                   [("Sec: %s" % o[:20], "frm:%s:%d" % (short, i))
                                    for i, o in enumerate(snap[2][:8])],
                                   cols=2, cancel=("Vazgec (is durdurulsun)",
                                                   "frm:%s:cancel" % short),
                                   reply_to=reply_to)
                pending_forms[short]["msg"] = mid
            _prune_pending()
        except Exception as e:  # noqa: BLE001
            log("approval poll: %s" % str(e)[-150:])

    def _tick():
        if abort is not None and abort.is_set():
            return
        _poll_approvals()
        now = time.time()
        if now - last_ping[0] >= 180:
            last_ping[0] = now
            try:
                mins = int((now - started_ms / 1000) // 60)
                send_message(str(chat_id),
                             "Hala calisiyorum (%d dk gecti)... /abort ile durdurabilirsin." % mins)
            except Exception:
                pass
        else:
            try:
                send_action(str(chat_id))
            except Exception:
                pass

    # streaming: placeholder + SSE toplayici
    try:
        stream["msg_id"] = send_message_id(str(chat_id), "...")
    except Exception:
        stream["msg_id"] = None
    sse_th = threading.Thread(target=_sse_loop, daemon=True)
    sse_th.start()
    last_flush = [0.0]
    stream_start = time.time()

    def stream_throttle():
        return progressive_throttle(time.time() - stream_start)

    def _tick_stream():
        _tick()
        # C: sabit 2 sn yerine kademeli throttle. Kisa isler akici kalir,
        # uzun isler Telegram rate limit'ine takilmaz.
        if stream["msg_id"] and time.time() - last_flush[0] >= stream_throttle() and stream["buf"]:
            last_flush[0] = time.time()
            cur = "".join(stream["buf"])[:3900]
            if cur != stream["edited"]:
                stream["edited"] = cur
                edit_message(str(chat_id), stream["msg_id"], cur + " ...")

    try:
        out = c.wait_reply(stored, started_ms, timeout=OPENCODE_TIMEOUT,
                           stop_flag=(lambda: abort is not None and abort.is_set()),
                           on_tick=_tick_stream)
    except Exception as e:  # noqa: BLE001
        stream["stop"] = True
        _debug_log("SERVE-WAIT-FAIL chat=%s err=%s" % (chat_id, str(e)[-500:]))
        return "HATA: %s" % str(e)[-400:], None
    stream["stop"] = True
    if out is None:
        try:
            c.interrupt(stored)
        except Exception:
            pass
        if stream["msg_id"]:
            edit_message(str(chat_id), stream["msg_id"], "Durduruldu (/abort).")
        return "Durduruldu (/abort).", stream["msg_id"]
    final = out.strip() or "(bos cikti)"
    if stream["msg_id"]:
        edit_message(str(chat_id), stream["msg_id"], final)
    return final, stream["msg_id"]


def run_opencode(prompt, chat_id, from_label, files=None, abort=None, proc_box=None):
    sid = "%s-%s" % (SESSION_PREFIX, str(chat_id).lstrip("-"))
    wrapped = (
        "[Telegram] Gonderen: %s (chat %s). "
        "Cevap Telegram'a gonderilecek: kisa yaz, 3000 karakteri gecme, "
        "secret/token sizdirma. Soru/gorev:\n\n%s" % (from_label, chat_id, prompt)
    )
    stored = _sess_entry(chat_id).get("ses")
    cdir = _chat_dir(chat_id)
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
            proc = subprocess.Popen(cmd, cwd=cdir, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
        except Exception as e:  # noqa: BLE001
            return e
        if proc_box is not None:
            proc_box["proc"] = proc
        if abort is not None and abort.is_set():
            _kill_tree(proc)
            return "ABORTED"
        res, out, err = _wait_proc(proc, abort, chat_id, OPENCODE_TIMEOUT,
                                   reply_to=proc_box.get("reply_to") if proc_box else None)
        if res != "ok":
            return res
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)

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
        fresh = _newest_session(started_ms, cdir)
        if fresh:
            _save_sess(chat_id, ses=fresh)
            log("yeni session eslestirildi chat=%s -> %s" % (chat_id, fresh))
    out = (p.stdout or "").strip() or (p.stderr or "").strip() or "(bos cikti)"
    if p.returncode != 0 and not (p.stdout or "").strip():
        out = "HATA (exit %d): %s" % (p.returncode, (p.stderr or "")[-1500:])
        _debug_log("RUN-FAIL chat=%s stored=%s rc=%d\nSTDOUT-tail: %s\nSTDERR-full:\n%s" % (
            chat_id, stored, p.returncode, (p.stdout or "")[-500:], p.stderr or ""))
    return out


def _finish_streamed(chat_id, msg_id, out, footer):
    """Streaming mesajini tamamla: ilk parcayi duzenle, tasmayi yeni mesajlarda gonder.

    Markdown acikken parcalama blok bazlidir (`md2.pack`): 4000 karakteri asan
    bir kod blogu karakter sayisiyla kesilmez, satirlara gore bolunur ve her
    parca gecerli bir ```blog``` olarak kalir. Onceki surumde tasma 3800
    karakterden sonra duz metin gonderiyordu (GAP-10).
    """
    pieces = _md_pieces(out)
    if not pieces:
        return 0
    head, pm = pieces[0]
    head = _with_footer(head, pm, footer)
    if not msg_id:
        if _send_chunk(chat_id, head, pm) is None:
            return 0
        n = 1
        rest = pieces[1:]
    elif _edit_chunk(chat_id, msg_id, head, pm):
        n = 1
        rest = pieces[1:]
    else:
        # duzenleme tutmadi (eski icerik degismis ya da bicim reddi) -> yeni mesaj
        if _send_chunk(chat_id, head, pm) is None:
            return 0
        n = 1
        rest = pieces[1:]
    for text, mode in rest:
        try:
            if _send_chunk(chat_id, text, mode) is not None:
                n += 1
        except Exception:  # noqa: BLE001
            break
        time.sleep(0.4)
    return n


def _spawn_worker(chat_id, from_label, prompt, files=None, reply_to=None):
    abort = threading.Event()
    box = {"reply_to": reply_to}

    def _run():
        t0 = time.time()
        try:
            streamed = None
            if SERVE_BACKEND:
                out, streamed = run_opencode_serve(prompt, chat_id, from_label,
                                                   files=files, abort=abort,
                                                   reply_to=reply_to)
                out = trim_output(out)
            else:
                out = trim_output(run_opencode(prompt, chat_id, from_label,
                                               files=files, abort=abort, proc_box=box))
            shown = prompt if len(prompt) < 4000 else prompt[:4000]
            if files:
                shown = "[dosya: %s] %s" % (", ".join(os.path.basename(f) for f in files), shown)
            mid = inbox_add(chat_id, from_label, shown,
                            handled_by="bridge", response_preview=out)
            day_in, day_out = _budget_add((_sess_entry(chat_id).get("ses") or ""),
                                            _chat_dir(chat_id))
            _audit(chat_id, "run", shown, {
                "from": from_label, "model": _chat_model(chat_id) or "default",
                "session": (_sess_entry(chat_id).get("ses") or "")[:20],
                "secs": round(time.time() - t0, 1),
                "out_len": len(out), "files": len(files or []),
                "aborted": out.startswith("Durduruldu"),
                "day_in": day_in, "day_out": day_out})
            if streamed:
                _finish_streamed(str(chat_id), streamed, out, mid)
            elif MARKDOWN_ON:
                pieces = _md_pieces(out)
                first = _with_footer(pieces[0][0], pieces[0][1], mid)
                n = 1 if _send_chunk(str(chat_id), first, pieces[0][1], reply_to=reply_to) else 0
                for text, mode in pieces[1:]:
                    if _send_chunk(str(chat_id), text, mode) is not None:
                        n += 1
                    time.sleep(0.4)
                if not n:
                    send_message(str(chat_id), "%s\n\n[%s]" % (out, mid), reply_to=reply_to)
            else:
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

def _session_stats(ses_id, cwd=None):
    """session export'tan: mesaj sayisi + son asistan tokenleri (ucretsiz, local)."""
    cmd, _ = _opencode_cmd(["session", "export", ses_id])
    if cmd is None:
        return ""
    try:
        p = subprocess.run(cmd, cwd=cwd or PROJECT_DIR, capture_output=True, text=True,
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


def _session_stats_serve(ses_id, directory=None):
    try:
        c = _serve(directory)
        ms = c.messages(ses_id) or []
        last = None
        for m in reversed(ms):
            if isinstance(m, dict) and m.get("type") == "assistant" \
                    and isinstance(m.get("tokens"), dict):
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
    if SERVE_BACKEND:
        stats = _session_stats_serve(ses, _chat_dir(chat_id))
    else:
        stats = _session_stats(ses, _chat_dir(chat_id))
    extra = "\n\nTelegram oturumu: %s...\nModel: %s" % (ses[:16], model)
    if stats:
        extra += "\n%s" % stats
    if _is_busy(chat_id):
        extra += "\nDurum: IS CALISIYOR (/abort ile durdurabilirsin)"
    return base + extra


def _model_providers(models):
    """Provider adlarini model listesinden cikar, sirayla, tekrarsiz."""
    seen = []
    for m in models:
        p = m.split("/", 1)[0]
        if p and p not in seen:
            seen.append(p)
    return seen


def _model_list(args, chat_id):
    """Sayfali + filtreli model listesi.

    Canli test bulgusu: opencode 356 model donuyor, onceki surum ilk 80'i
    gosterip "...(+276)" yaziyor ve geri kalana erisim yolu birakiyordu.
    Artik her model bir sayfada gorunur; provider veya alt dize ile filtre
   lenebilir: `/model list zenmux`, `/model list bunny`, `/model list zenmux 2`.
    """
    models = _opencode_models()
    if not models:
        return "Model listesi alinamadi. `opencode models` calismadi ya da bos dondu."
    rest = [a for a in args if a]
    page = 1
    if rest and rest[-1].isdigit():
        page = int(rest[-1])
        rest.pop()
    query = " ".join(rest).strip()
    sel = models
    if query:
        q = query.lower()
        sel = [m for m in models if q in m.lower()]
        if not sel:
            return ("Eslesme yok: '%s'\n\nProvider'lar: %s"
                    % (query, ", ".join(_model_providers(models))))
    total = max(1, (len(sel) + MODEL_PAGE_SIZE - 1) // MODEL_PAGE_SIZE)
    page = max(1, min(page, total))
    start = (page - 1) * MODEL_PAGE_SIZE
    cur = _chat_model(chat_id)
    lines = ["Modeller%s | sayfa %d/%d | toplam %d"
             % (("  '%s'" % query) if query else "", page, total, len(sel))]
    for m in sel[start:start + MODEL_PAGE_SIZE]:
        lines.append("%s %s" % ("*" if m == cur else " ", m))
    suffix = (" " + query) if query else ""
    nav = []
    if page > 1:
        nav.append("onceki: /model list%s %d" % (suffix, page - 1))
    if page < total:
        nav.append("sonraki: /model list%s %d" % (suffix, page + 1))
    if nav:
        lines.append("")
        lines.append("   ".join(nav))
    lines.append("Aktif: %s" % (cur or "(opencode varsayilani)"))
    if not query:
        lines.append("Filtre: /model list <provider veya arama>"
                     "  -  orn. /model list opencode")
    return "\n".join(lines)


def cmd_model(text, chat_id):
    parts = text.strip().split()
    cur = _chat_model(chat_id) or "(opencode varsayilani)"
    if len(parts) == 1 or parts[1].lower() in ("current", "goster"):
        return ("Aktif model: %s\nDegistir: /model set <provider/model>\n"
                "Liste: /model list   -   Filtre: /model list <provider|arama>" % cur)
    if parts[1].lower() == "list":
        return _model_list(parts[2:], chat_id)
    if parts[1].lower() == "set" and len(parts) >= 3:
        model = parts[2].strip()
        if "/" not in model:
            return "Format: provider/model (orn. opencode/space-bunny-free)"
        known = _opencode_models()
        warn = "" if (not known or model in known) else " (UYARI: listede yok, yine de kaydedildi)"
        _save_sess(chat_id, model=model)
        return "Model ayarlandi: %s%s. Sonraki sorular bununla calisir." % (model, warn)
    if parts[1].lower() in ("otomatik", "auto", "session"):
        # Sohbet gecersiz kilmasi kalksin: gecilen oturumun kendi modeli
        # kullanilsin (/sessions ile oturum degistirirken lazim).
        _save_sess(chat_id, model=None)
        return ("Model gecersiz kilmasi kaldirildi. Bundan sonra aktif oturumun "
                "kendi modeli kullanilir. Geri almak: /model set <provider/model>")
    return "Kullanim: /model | /model list [filtre] [sayfa] | /model set <p/m> | /model otomatik"


# ---------- oturum listeleme ve gecis (/sessions) ----------

SESSION_PAGE_SIZE = max(5, int(env("TELEGRAM_SESSION_PAGE_SIZE", "15") or 15))


def _tshort(ms):
    """Epoch ms -> '29.09 22:14'."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%d.%m %H:%M")
    except Exception:
        return "?"


def _sessions_for_chat(chat_id, limit=60):
    """Bu sohbetin projesine ait oturumlar (yeni once).

    Oturumlar proje bazlidir: `directory` eslesmeyenler gosterilmez, aksi
    halde baska bir projedeki oturuma gecilir ve ajan yanlis yerde calisir.
    """
    cdir = os.path.normcase(os.path.abspath(_chat_dir(chat_id)))
    out = []
    for s in _session_list(limit, _chat_dir(chat_id)):
        d = s.get("directory")
        if d and os.path.normcase(os.path.abspath(d)) != cdir:
            continue
        out.append(s)
    return out


def _sessions_page(items, page):
    total = max(1, (len(items) + SESSION_PAGE_SIZE - 1) // SESSION_PAGE_SIZE)
    page = max(1, min(int(page), total))
    start = (page - 1) * SESSION_PAGE_SIZE
    return page, total, items[start:start + SESSION_PAGE_SIZE]


def cmd_sessions(text, chat_id):
    """`/sessions` — listele, gec, yeni olustur, bilgi gor.

    Canli test bulgusu: bridge her sohbet icin tek oturum tutuyordu, listeleme
    ve gecis hic yokti. Gecis etkin oturumu silmez.
    """
    parts = text.strip().split()
    cur = _sess_entry(chat_id).get("ses")
    if len(parts) == 1 or (len(parts) == 2 and parts[1].isdigit()):
        return _sessions_show(chat_id, int(parts[1]) if len(parts) == 2 else 1, cur)
    act = parts[1].lower()
    if act == "ac" and len(parts) >= 3 and parts[2].isdigit():
        n = int(parts[2])
        items = _sessions_for_chat(chat_id)
        if not items:
            return "Bu proje icin oturum bulunamadi."
        if n < 1 or n > len(items):
            return "Numara 1-%d araliginda olmali (/sessions ile listele)." % len(items)
        if _is_busy(chat_id):
            return "Once /abort ile calisan isi durdur."
        target = items[n - 1]
        sid = target.get("id")
        if sid == cur:
            return "Zaten bu oturum aktif (#%d)." % n
        _save_sess(chat_id, clear_ses=True)
        _save_sess(chat_id, ses=sid)
        return ("Oturum degistirildi: #%d  %s\n  id: %s\n\n"
                "Sonraki mesajlar bu oturumda devam eder. Eski oturum SILINMADI."
                % (n, _tshort(target.get("updated")), sid))
    if act == "yeni":
        if _is_busy(chat_id):
            return "Once /abort ile calisan isi durdur."
        _save_sess(chat_id, clear_ses=True)
        return ("Yeni oturum modu. Eski oturum SILINMADI; /sessions ile geri "
                "donebilirsin.\nSonraki mesaj yeni bir oturum acar.")
    if act == "bilgi":
        return _sessions_info(chat_id, cur)
    if act == "sil":
        return cmd_reset(chat_id)
    return ("Kullanim:\n"
            "  /sessions [sayfa]      oturumlari listele\n"
            "  /sessions ac <no>     listelenen oturuma gec\n"
            "  /sessions yeni        yeni oturum modu (eski silinmez)\n"
            "  /sessions bilgi       aktif oturum detayi\n"
            "  /sessions sil         aktif oturumu sil (= /reset)")


def _sessions_show(chat_id, page, cur):
    items = _sessions_for_chat(chat_id)
    if not items:
        return ("Oturum bulunamadi. Proje: %s\n"
                "(`opencode session list` bu dizin icin bos dondu)"
                % _chat_dir(chat_id))
    page, total, chunk = _sessions_page(items, page)
    first = (page - 1) * SESSION_PAGE_SIZE + 1
    lines = ["Oturumlar | sayfa %d/%d | toplam %d" % (page, total, len(items))]
    for i, s in enumerate(chunk, start=first):
        sid = s.get("id", "")
        mark = "*" if sid == cur else " "
        lines.append("%s %d. %s  %s" % (mark, i, _tshort(s.get("updated")), sid[-16:]))
    lines.append("")
    nav = []
    if page > 1:
        nav.append("onceki: /sessions %d" % (page - 1))
    if page < total:
        nav.append("sonraki: /sessions %d" % (page + 1))
    if nav:
        lines.append("   ".join(nav))
    lines.append("Gecis: /sessions ac <no>   -   yeni: /sessions yeni   -   sil: /sessions sil")
    lines.append("Aktif: %s" % (cur or "(yok - ilk mesajda acilir)"))
    return "\n".join(lines)


def _sessions_info(chat_id, cur):
    if not cur:
        return "Aktif oturum yok. Ilk mesajda acilir."
    info = next((s for s in _sessions_for_chat(chat_id) if s.get("id") == cur), None)
    ent = _sess_entry(chat_id)
    lines = ["Aktif oturum", "  id       : %s" % cur,
             "  baslik   : %s" % ((info or {}).get("title") or "-")]
    if info:
        lines.append("  olusturma: %s" % _tshort(info.get("created")))
        lines.append("  guncelleme: %s" % _tshort(info.get("updated")))
    lines.append("  model    : %s"
                 % (ent.get("model") or OPENCODE_MODEL or "(oturumun kendi modeli)"))
    cdir = _chat_dir(chat_id)
    stats = (_session_stats_serve(cur, cdir) if SERVE_BACKEND
             else _session_stats(cur, cdir))
    if stats:
        lines.append("  " + stats)
    return "\n".join(lines)


def cmd_reset(chat_id):
    if _is_busy(chat_id):
        return "Once /abort ile calisan isi durdur."
    ent = _sess_entry(chat_id)
    ses = ent.get("ses")
    if ses:
        if SERVE_BACKEND:
            try:
                _serve(_chat_dir(chat_id)).delete_session(ses)
            except Exception:
                pass
        else:
            cmd, _ = _opencode_cmd(["session", "delete", ses])
            if cmd is not None:
                try:
                    subprocess.run(cmd, cwd=_chat_dir(chat_id), capture_output=True, text=True,
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
        with _aj.tx(HOLDS_FILE):
            st["holds"] = fresh
            _save(HOLDS_FILE, st)
    return st


def _hold_create(chat_id, label, prompt, files):
    with _aj.tx(HOLDS_FILE):
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
    with _aj.tx(HOLDS_FILE):
        st = _load_holds()
        for h in st.get("holds", []):
            if h.get("id") == hid.upper() and h.get("chat") == str(chat_id):
                st["holds"] = [x for x in st["holds"] if x.get("id") != h["id"]]
                _save(HOLDS_FILE, st)
                return h
    return None


def _hold_restore(h):
    """Is busy yuzunden tuketilen hold'u geri koy (TTL korunur)."""
    if not h:
        return
    with _aj.tx(HOLDS_FILE):
        st = _load_holds()
        if not any(x.get("id") == h.get("id") for x in st.get("holds", [])):
            st["holds"].append(h)
            st["holds"] = st["holds"][-50:]
            _save(HOLDS_FILE, st)


# ---------- multi-project (TG3-3): TELEGRAM_PROJECTS="alias=yol,..." ----------

def _projects():
    out = {}
    for chunk in env("TELEGRAM_PROJECTS", "").split(","):
        chunk = chunk.strip()
        if "=" in chunk:
            alias, _, path = chunk.partition("=")
            alias, path = alias.strip(), path.strip().strip('"')
            if alias and path and os.path.isdir(path):
                out[alias] = os.path.normpath(path)
    return out


def _chat_dir(chat_id):
    projs = _projects()
    alias = _sess_entry(chat_id).get("project")
    if alias and alias in projs:
        return projs[alias]
    return PROJECT_DIR


def cmd_project(text, chat_id):
    parts = text.strip().split()
    projs = _projects()
    if not projs:
        return "Proje listesi bos (TELEGRAM_PROJECTS tanimli degil). Tek proje modundasiniz."
    cur = _sess_entry(chat_id).get("project")
    if len(parts) == 1 or parts[1].lower() in ("list", "liste"):
        lines = ["Projeler (aktif *):"]
        for alias, path in sorted(projs.items()):
            lines.append("%s %s: %s" % ("*" if alias == cur else " ", alias, path))
        if not cur:
            lines.append("(varsayilan proje: %s)" % PROJECT_DIR)
        return "\n".join(lines)
    if parts[1].lower() == "current":
        return "Aktif proje: %s" % (cur or "(varsayilan) %s" % PROJECT_DIR)
    if parts[1].lower() == "set" and len(parts) >= 3:
        alias = parts[2]
        if alias not in projs:
            return "Bilinmeyen proje. /project list ile bak."
        if _is_busy(chat_id):
            return "Once /abort ile calisan isi durdur."
        _save_sess(chat_id, clear_ses=True)
        _save_sess(chat_id, project=alias)
        return "Proje ayarlandi: %s (%s). Sonraki soru burada yeni oturum acar." % (alias, projs[alias])
    return "Kullanim: /project list | /project current | /project set <alias>"


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
    if low == "/sessions" or low.startswith("/sessions ") or low.startswith("/sessions@"):
        return cmd_sessions(t, chat_id), False
    if low == "/project" or low.startswith("/project ") or low.startswith("/project@"):
        return cmd_project(t, chat_id), False
    if low.startswith("/onay"):
        note = t[5:].strip() or "(bos onay)"
        first = note.split()[0].upper() if note.split() else ""
        if first.startswith("HOLD-"):
            h = _hold_take(chat_id, first)
            if h is None:
                return "%s bulunamadi/suresi dolmus (30 dk) ya da baska chate ait." % first, False
            if _is_busy(chat_id):
                # hold'u geri koy (tuketilmesin)
                _hold_restore(h)
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
    # Yikici icerik kapisi `_gate_or_spawn` icinde tek yerde (GAP-19).
    return _gate_or_spawn(chat_id, from_label, t, reply_to=reply_to)


# ---------- zamanlayici (TG3-2): "HH:MM:/komut;..." -> DEFAULT_CHAT ----------

def _parse_schedule():
    out = []
    for chunk in env("TELEGRAM_SCHEDULE", "").split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        hm, _, cmd = chunk.partition(":/")
        hm = hm.strip()
        if len(hm) == 5 and hm[2] == ":" and cmd.strip():
            out.append((hm, "/" + cmd.strip()))
    return out


_schedule_entries = None
_schedule_fired = {}


def _run_schedule():
    global _schedule_entries
    if _schedule_entries is None:
        _schedule_entries = _parse_schedule()
    if not _schedule_entries or not DEFAULT_CHAT or DEFAULT_CHAT not in ALLOWED:
        return
    now = datetime.now(timezone.utc).astimezone()
    day, hm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    for when, cmd in _schedule_entries:
        key = (day, when, cmd)
        if hm >= when and key not in _schedule_fired:
            _schedule_fired[key] = True
            try:
                reply, _ = handle_text(DEFAULT_CHAT, "scheduler", cmd)
                send_message(DEFAULT_CHAT, "[Zamanlayici %s]\n%s" % (cmd, reply))
                log("schedule fired %s %s" % (when, cmd))
            except Exception as e:  # noqa: BLE001
                log("schedule err: %s" % str(e)[-150:])


# ---------- digest modu (TG3-4): bildirimleri saatlik topla ----------

DIGEST_ON = env("TELEGRAM_DIGEST", "0") not in ("0", "", "false", "no")
DIGEST_FILE = os.path.join(MAILBOX_DIR, "telegram_digest.json")
DIGEST_SECS = 3600


def _digest_push(m):
    with _aj.tx(DIGEST_FILE):
        st = _load(DIGEST_FILE, {"items": [], "window_start": time.time()})
        st["items"].append({"ts": _now(), "to": m.get("to"), "text": m.get("text", "")})
        st["items"] = st["items"][-50:]
        _save(DIGEST_FILE, st)


def _digest_due():
    st = _load(DIGEST_FILE, {"items": [], "window_start": time.time()})
    items = st.get("items", [])
    if not items:
        return False
    return (time.time() - st.get("window_start", 0)) >= DIGEST_SECS


def _digest_flush():
    with _aj.tx(DIGEST_FILE):
        st = _load(DIGEST_FILE, {"items": [], "window_start": time.time()})
        items = st.get("items", [])
        if items:
            _save(DIGEST_FILE, {"items": [], "window_start": time.time()})
    if not items:
        return 0
    body = "Saatlik ozet (%d bildirim):\n\n%s" % (
        len(items), "\n---\n".join(i.get("text", "")[:800] for i in items))
    dests = sorted({(i.get("to") or "default") for i in items})
    sent = 0
    for d in dests:
        to = DEFAULT_CHAT if d in ("default", "") else d
        if to and to in ALLOWED:
            try:
                send_message(to, body)
                sent += 1
            except Exception:
                pass
    return sent


# ---------- fotograf / belge destegi (opencode --file) ----------

TG_FILES_DIR = os.path.join(MAILBOX_DIR, "tg_files")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
try:
    TG_FILES_MAX_AGE = float(env("TELEGRAM_TGFILE_RETENTION_H", "6") or 0) * 3600
except ValueError:
    TG_FILES_MAX_AGE = 6 * 3600
try:
    TG_FILES_MAX_COUNT = int(env("TELEGRAM_TGFILE_MAX_COUNT", "50") or 0)
except ValueError:
    TG_FILES_MAX_COUNT = 50
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


def _cleanup_tg_files():
    """Indirilen Telegram eklerini temizle (dolu diski onle, GAP-16).

    Onceki surumde hicbir temizlik yoktu; her gonderilen fotograf/belge kalici
    olarak birikiyordu. Donus: silinen dosya sayisi.
    """
    if not os.path.isdir(TG_FILES_DIR):
        return 0
    try:
        files = [os.path.join(TG_FILES_DIR, n) for n in os.listdir(TG_FILES_DIR)]
        files = [f for f in files if os.path.isfile(f)]
    except OSError:
        return 0
    files.sort(key=lambda f: os.path.getmtime(f))
    now = time.time()
    doomed = set()
    if TG_FILES_MAX_AGE > 0:
        for f in files:
            try:
                if now - os.path.getmtime(f) > TG_FILES_MAX_AGE:
                    doomed.add(f)
            except OSError:
                doomed.add(f)
    alive = [f for f in files if f not in doomed]
    if TG_FILES_MAX_COUNT > 0 and len(alive) > TG_FILES_MAX_COUNT:
        doomed.update(alive[:len(alive) - TG_FILES_MAX_COUNT])
    n = 0
    for f in doomed:
        try:
            os.remove(f)
            n += 1
        except OSError:
            pass
    if n:
        log("tg_files temizlendi: %d dosya" % n)
    return n


# ---------- butce freni (gunluk token sayaci) ----------

BUDGET_FILE = os.path.join(MAILBOX_DIR, "telegram_budget.json")
try:
    DAILY_TOKEN_LIMIT = int(env("TELEGRAM_DAILY_TOKEN_LIMIT", "0"))
except ValueError:
    DAILY_TOKEN_LIMIT = 0


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _budget_add(ses_id, cwd=None):
    """Oturumdaki yeni asistan mesajlarinin tokenlerini bugune ekler. Donus: (gunluk_in, gunluk_out)."""
    if not ses_id:
        st = _load(BUDGET_FILE, {})
        day = st.get(_today(), {"in": 0, "out": 0})
        return day.get("in", 0), day.get("out", 0)
    cmd, _ = _opencode_cmd(["session", "export", ses_id])
    new_in, new_out, last_id = 0, 0, None
    try:
        if cmd is not None:
            p = subprocess.run(cmd, cwd=cwd or PROJECT_DIR, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
            if p.returncode == 0 and (p.stdout or "").strip():
                ms = json.loads(p.stdout).get("messages", [])
                # subprocess yukarida bitti; kilidi sadece RMW icinde tut
                with _aj.tx(BUDGET_FILE):
                    st = _load(BUDGET_FILE, {})
                    seen = (st.get("sessions", {}).get(ses_id) or "")
                    started = not seen
                    for m in ms:
                        if not isinstance(m, dict):
                            continue
                        if m.get("id") == seen:
                            started = True
                            continue
                        if not started:
                            continue
                        last_id = m.get("id") or last_id
                        if m.get("type") == "assistant" and isinstance(m.get("tokens"), dict):
                            new_in += int(m["tokens"].get("input", 0) or 0)
                            new_out += int(m["tokens"].get("output", 0) or 0)
                    if last_id:
                        st.setdefault("sessions", {})[ses_id] = last_id
                    day = st.setdefault(_today(), {"in": 0, "out": 0})
                    day["in"] = day.get("in", 0) + new_in
                    day["out"] = day.get("out", 0) + new_out
                    # eski gunleri buda
                    for k in [k for k in st if k not in ("sessions", _today())][:30]:
                        if len(k) == 10:
                            st.pop(k, None)
                    _save(BUDGET_FILE, st)
                    return day["in"], day["out"]
    except Exception:
        pass
    st = _load(BUDGET_FILE, {})
    day = st.get(_today(), {"in": 0, "out": 0})
    return day.get("in", 0), day.get("out", 0)


def _budget_exceeded():
    if not DAILY_TOKEN_LIMIT or DAILY_TOKEN_LIMIT <= 0:
        return None
    st = _load(BUDGET_FILE, {})
    day = st.get(_today(), {"in": 0, "out": 0})
    total = day.get("in", 0) + day.get("out", 0)
    if total >= DAILY_TOKEN_LIMIT:
        return total
    return None


def _gate_or_spawn(chat_id, label, prompt, files=None, reply_to=None):
    """Yikici icerikse HOLD'a al, yoksa worker baslat. Donus: (reply, executed)."""
    used = _budget_exceeded()
    if used is not None:
        return ("Gunluk token limiti asildi (%d/%d). Yarin devam ederiz "
                "(TELEGRAM_DAILY_TOKEN_LIMIT)." % (used, DAILY_TOKEN_LIMIT), False)
    danger = _check_dangerous(prompt)
    if danger:
        hid = _hold_create(chat_id, label, prompt, files)
        _audit(chat_id, "hold", prompt, {"from": label, "pattern": danger, "hold": hid,
                                         "files": len(files or [])})
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


def _callback_msg_id(cb):
    msg = cb.get("message") or {}
    mid = msg.get("message_id")
    return mid if isinstance(mid, int) else None


def _stale_callback(cb_id, info, seen_msg):
    """Butonun ait oldugu mesaj artik guncel mi? (bayat buton korumasi)

    Eskiden eski bir izin/soru mesajindaki buton 30 dk boyunca tiklanabiliyor
    ve yanlis oturuma onay verebiliyordu. Simdi mesaj kimligi eslesmezse
    islem yapilmaz.
    """
    if seen_msg is None or info.get("msg") is None:
        return False
    if seen_msg == info.get("msg"):
        return False
    answer_callback(cb_id, "Bu mesaj artik guncel degil. Yenisini bekleyin.")
    return True


def handle_callback(cb):
    data = cb.get("data", "") or ""
    cb_id = cb.get("id", "")
    frm = cb.get("from", {}) or {}
    uid = frm.get("id")
    label = ("@" + frm.get("username")) if frm.get("username") else str(uid or "?")
    if uid is None or not is_allowed(uid):
        answer_callback(cb_id)
        log("DENIED-cb user=%s data=%.40s" % (label, data))
        return
    _prune_pending()
    seen_msg = _callback_msg_id(cb)
    if data.startswith("prm:"):
        parts = data.split(":")
        if len(parts) != 3:
            answer_callback(cb_id, "Hatali buton.")
            return
        _, rid, decision = parts
        info = pending_perms.get(rid)
        if info is None or decision not in ("once", "always", "reject"):
            answer_callback(cb_id, "Istek suresi dolmus.")
            return
        if _stale_callback(cb_id, info, seen_msg):
            return
        pending_perms.pop(rid, None)
        try:
            _serve().reply_permission(info["session"], rid, decision)
            answer_callback(cb_id, "Kaydedildi.")
            edit_message(info["chat"], info.get("msg"),
                         "Izin istegi sonuclandi: %s (%s)" % (
                             {"once": "1 kez onay", "always": "her zaman",
                              "reject": "reddedildi"}[decision], rid))
        except Exception as e:  # noqa: BLE001
            answer_callback(cb_id, "Hata: %s" % str(e)[-100:])
        return
    if data.startswith("frm:"):
        parts = data.split(":")
        if len(parts) != 3:
            answer_callback(cb_id, "Hatali buton.")
            return
        _, short, idx = parts
        info = pending_forms.get(short)
        if info is None:
            answer_callback(cb_id, "Secim suresi dolmus.")
            return
        if _stale_callback(cb_id, info, seen_msg):
            return
        if idx == "cancel":
            # Soruyu reddetmek = isi durdurmak. Yarim birakilmis soru birakmamak
            # icin (opencode cevap bekliyor olurdu) oturum kesiliyor.
            pending_forms.pop(short, None)
            try:
                _serve().interrupt(info["session"])
            except Exception:  # noqa: BLE001
                pass
            answer_callback(cb_id, "Vazgecildi.")
            edit_message(info["chat"], info.get("msg"),
                         "Soruldu, vazgecildi. Is durduruldu. /yardim")
            return
        pending_forms.pop(short, None)
        try:
            i = int(idx)
            value = info["options"][i]
        except (ValueError, IndexError):
            answer_callback(cb_id, "Hatali secim.")
            return
        try:
            _serve().reply_form(info["session"], info["formID"], {info["key"]: value})
            answer_callback(cb_id, "Gonderildi.")
            edit_message(info["chat"], info.get("msg"),
                         "Secimin iletildi: %s" % value[:200])
        except Exception as e:  # noqa: BLE001
            answer_callback(cb_id, "Hata: %s" % str(e)[-100:])
        return
    answer_callback(cb_id)


_seen_edits = set()


def _edit_seen(chat_id, msg_id, text):
    """Telegram ayni duzenlemeyi her getUpdates cagrisinda tekrar yollar.

    `FOLLOW_EDITS` acikken bile ayni (mesaj, metin) cifti bir kez islenir.
    """
    k = (str(chat_id), msg_id, hash(text) & 0xFFFFFFFF)
    if k in _seen_edits:
        return True
    if len(_seen_edits) > 2000:
        _seen_edits.clear()
    _seen_edits.add(k)
    return False


def handle_update(u):
    if "callback_query" in u:
        try:
            handle_callback(u["callback_query"])
        except Exception as e:  # noqa: BLE001
            log("ERROR callback: %s" % str(e)[-200:])
        return
    edited = u.get("edited_message") or {}
    msg = u.get("message") or edited
    if not msg:
        return
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return
    text = msg.get("text", "") or ""
    msg_id = msg.get("message_id")
    if edited:
        # GAP-04: duzenleme, ayni isi ikinci kez tetiklemesin.
        if not FOLLOW_EDITS:
            log("EDIT-IGNORED chat=%s msg=%s (TELEGRAM_FOLLOW_EDITS=0)" % (chat_id, msg_id))
            return
        if _edit_seen(chat_id, msg_id, text):
            log("EDIT-DEDUP chat=%s msg=%s" % (chat_id, msg_id))
            return
    user = msg.get("from", {})
    label = ("@" + user.get("username")) if user.get("username") else str(user.get("id", "?"))
    if not is_allowed(chat_id):
        log("DENIED chat=%s user=%s text=%.60s" % (chat_id, label, text))
        return
    photo = msg.get("photo")
    doc = msg.get("document")
    voice = msg.get("voice") or msg.get("audio")
    if voice:
        try:
            if int(voice.get("file_size", 0)) > MAX_UPLOAD_BYTES:
                send_message(str(chat_id), "Ses dosyasi 20MB sinirini asiyor.", reply_to=msg_id)
                return
            if int(voice.get("duration", 0)) > 600:
                send_message(str(chat_id), "Ses 10 dakikadan uzun olmamali.", reply_to=msg_id)
                return
            local = _download_tg_file(voice.get("file_id"), "ses.ogg")
            log("VOICE chat=%s user=%s file=%s" % (chat_id, label, os.path.basename(local)))
            if _is_busy(str(chat_id)):
                send_message(str(chat_id), "Halen bir is calisiyor. Bitmesini bekle ya da /abort ile durdur.",
                             reply_to=msg_id)
            elif not BRIDGE_EXEC:
                mid = inbox_add(str(chat_id), label, "[SES] " + os.path.basename(local))
                send_message(str(chat_id), "Ses kuyruga alindi (%s)." % mid, reply_to=msg_id)
            else:
                reply, _ = _gate_or_spawn(
                    str(chat_id), label,
                    "Ekteki ses kaydini once kelimesi kelimesine yaziya dok, sonra icindeki "
                    "istegi yerine getir. (Modelin ses destegi yoksa bunu belirt.)",
                    files=[local], reply_to=msg_id)
                send_message(str(chat_id), reply, reply_to=msg_id)
        except Exception as e:  # noqa: BLE001
            log("ERROR voice: %s" % str(e)[-200:])
            try:
                send_message(str(chat_id), "Ses alinamadi: %s" % str(e)[-300:])
            except Exception:
                pass
        return
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
        log("HATA: TELEGRAM_BOT_TOKEN yok. .env'ye ekleyin (bkz. docs/KURULUM.md).")
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
    set_my_commands()
    log("telegram-bridge v%s basladi project=%s exec=%s backend=%s agent=%s model=%s allowed=%d kisi" % (
        VERSION, PROJECT_DIR, BRIDGE_EXEC, "serve" if SERVE_BACKEND else "cli",
        OPENCODE_AGENT, OPENCODE_MODEL or "default", len(ALLOWED)))
    # Onceki calisma surec olurken SENDING'de takilip kalmis olabilir (GAP-03).
    try:
        n = outbox_recover()
        if n:
            log("outbox kurtarildi: %d kayit yeniden kuyruga alindi" % n)
    except Exception as e:  # noqa: BLE001
        log("outbox kurtarma hatasi: %s" % str(e)[-150:])
    if DEFAULT_CHAT and DEFAULT_CHAT in ALLOWED:
        try:
            send_message(DEFAULT_CHAT,
                         "Kopru acildi (v%s). Proje: %s | Model: %s | /yardim" % (
                             VERSION,
                             os.path.basename(PROJECT_DIR),
                             OPENCODE_MODEL or "opencode varsayilani"))
        except Exception as e:  # noqa: BLE001
            log("acilis ping gonderilemedi: %s" % str(e)[-150:])
    last_sweep = [time.time()]
    while True:
        try:
            _flush_media()
            _run_schedule()
            if DIGEST_ON and _digest_due():
                n = _digest_flush()
                if n:
                    log("digest flush: %d chat" % n)
            if time.time() - last_sweep[0] > 1800:
                last_sweep[0] = time.time()
                _cleanup_tg_files()
            res = api("getUpdates", {"timeout": POLL_TIMEOUT, "offset": offset,
                                     "allowed_updates": ["message", "edited_message",
                                                         "callback_query"]},
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
    print("surum       :", VERSION)
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
    print("BACKEND     :", "serve (%s)" % SERVE_URL if SERVE_BACKEND else "cli (opencode run)")
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


def _notify_death(err):
    try:
        if DEFAULT_CHAT and DEFAULT_CHAT in ALLOWED:
            api("sendMessage", {"chat_id": DEFAULT_CHAT,
                                "text": "Kopru beklenmedik sekilde durdu: %s. Yeniden baslatin." % str(err)[-200:]},
                timeout=15)
    except Exception:
        pass


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)
    try:
        main_loop()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        _notify_death(e)
        raise

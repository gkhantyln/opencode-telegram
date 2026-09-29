"""opencode-telegram bridge unit testleri (ag/model gerektirmez).

Calistir: pytest opencode-telegram/tests/test_bridge.py
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

import pytest


TMP = tempfile.mkdtemp()
os.environ["TEAM_MAILBOX_DIR"] = TMP
os.environ["TELEGRAM_PROJECT_DIR"] = TMP
os.environ["TELEGRAM_BOT_TOKEN"] = "test-token-xyz"
os.environ["TELEGRAM_ALLOWED_CHAT_IDS"] = "111"
os.environ["TELEGRAM_DEFAULT_CHAT_ID"] = "111"
os.environ["TELEGRAM_BRIDGE_EXEC"] = "1"
os.environ["TELEGRAM_DAILY_TOKEN_LIMIT"] = "0"
os.environ["TELEGRAM_OPENCODE_MODEL"] = ""

_SPEC = importlib.util.spec_from_file_location(
    "tgbridge", os.path.join(os.path.dirname(__file__), "..", "bridge", "bridge.py"))
B = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(B)


def test_danger_rm():
    assert B._check_dangerous("lutfen rm -rf /tmp/eskiyi sil") is not None


def test_danger_force_push():
    assert B._check_dangerous("git push --force origin main") is not None


def test_danger_drop_table():
    assert B._check_dangerous("DROP TABLE users") is not None


def test_danger_terraform():
    assert B._check_dangerous("terraform destroy -auto-approve") is not None


def test_clean_text_ok():
    assert B._check_dangerous("merhaba, proje durumunu ozetle") is None


def test_redact_keys():
    assert B._redact("k sk-abcdefghijklmnopqrstUVWX s") == "k [REDACTED] s"
    assert "[REDACTED]" in B._redact("t ghp_1234567890abcdef1234567890 x")
    assert "[REDACTED]" in B._redact("id AKIAIOSFODNN7EXAMPLE !")


def test_redact_privkey_and_token():
    blob = "k -----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY----- s"
    assert "MIIB" not in B._redact(blob)
    assert "[REDACTED-BOT-TOKEN]" in B._redact("bot test-token-xyz kullan")


def test_redact_clean_unchanged():
    assert B._redact("merhaba dunya") == "merhaba dunya"


def test_hold_create_take():
    hid = B._hold_create("111", "@t", "rm -rf /tmp/x", None)
    assert hid.startswith("HOLD-")
    h = B._hold_take("111", hid.lower())
    assert h is not None and h["prompt"] == "rm -rf /tmp/x"
    assert B._hold_take("111", hid) is None  # tuketildi


def test_hold_expiry_pruned():
    B._save(B.HOLDS_FILE, {"seq": 1, "holds": [{
        "id": "HOLD-001", "chat": "111", "from": "@t", "prompt": "x",
        "files": [], "ts": "x", "ts_epoch": 1.0}]})
    assert B._load_holds()["holds"] == []


def test_lock_lifecycle():
    ok, _ = B._acquire_lock()
    assert ok
    B._save(B.LOCK_FILE, {"pid": 999999999, "ts": "x"})
    ok2, _ = B._acquire_lock()
    assert ok2  # stale devralma
    B._release_lock()
    assert not os.path.exists(B.LOCK_FILE)


def test_outbox_retry_counts(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net")
    monkeypatch.setattr(B, "send_message", boom)
    B._save(B.OUTBOX_FILE, {"seq": 1, "messages": [
        {"id": "OUT-001", "ts": "x", "to": "111", "text": "hi", "status": "QUEUED"}]})
    assert B.outbox_flush() == (0, 0)
    m = B._load(B.OUTBOX_FILE, {})["messages"][0]
    assert m["status"] == "QUEUED" and m["attempts"] == 1 and m["next_try"] > 0


def test_budget_gate():
    assert B._budget_exceeded() is None  # limit kapali (env 0)
    B._save(B.BUDGET_FILE, {B._today(): {"in": 8, "out": 5}, "sessions": {}})
    old = B.DAILY_TOKEN_LIMIT
    B.DAILY_TOKEN_LIMIT = 10
    try:
        assert B._budget_exceeded() == 13
        reply, _ = B._gate_or_spawn("111", "@t", "merhaba")
        assert "limiti asildi" in reply
    finally:
        B.DAILY_TOKEN_LIMIT = old


def test_sess_migration():
    B._save(B.SESSIONS_FILE, {"111": "ses_abc"})
    assert B._sess_entry("111") == {"ses": "ses_abc"}
    B._save_sess("111", model="opencode/x-free")
    assert B._chat_model("111") == "opencode/x-free"


def test_gate_danger_holds():
    reply, executed = B._gate_or_spawn("111", "@t", "rm -rf /tmp/a")
    assert not executed and "/onay HOLD-" in reply


def test_local_commands():
    assert B.handle_text("111", "@t", "/abort")[0] == "Calisan is yok."
    assert "oturum" in B.handle_text("111", "@t", "/reset")[0].lower()
    assert "Format" in B.handle_text("111", "@t", "/model set hatali")[0]


def test_busy_blocks_question():
    ev = threading.Event()
    fake = threading.Thread(target=lambda: ev.wait(5))
    fake.start()
    B._inflight["111"] = {"thread": fake, "abort": threading.Event(), "box": {}}
    try:
        reply, _ = B.handle_text("111", "@t", "merhaba")
        assert "abort" in reply.lower()
    finally:
        ev.set()
        fake.join()
        B._inflight.pop("111", None)


def test_audit_writes_jsonl():
    p = os.path.join(TMP, "telegram_audit.log")
    if os.path.exists(p):
        os.remove(p)
    B._audit("111", "run", "deneme", {"secs": 1.0})
    rec = json.loads(open(p, encoding="utf-8").read().strip())
    assert rec["chat"] == "111" and rec["kind"] == "run"


def test_mcp_tools():
    spec = importlib.util.spec_from_file_location(
        "tgmcp", os.path.join(os.path.dirname(__file__), "..", "mcp", "server.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    import asyncio
    names = [t.name for t in asyncio.run(m.handle_list_tools())]
    assert names == ["telegram_send", "telegram_broadcast", "telegram_poll",
                     "telegram_ack", "telegram_status"]


def test_form_snapshot_options():
    form = {"title": "Sec", "fields": {"renk": {"options": ["kirmizi", "mavi"]}}}
    snap = B._form_snapshot(form)
    assert snap is not None and snap[0] == "renk" and snap[2] == ["kirmizi", "mavi"]


def test_form_snapshot_none():
    assert B._form_snapshot({"title": "Serbest", "fields": {"ad": {"type": "string"}}}) is None
    assert B._form_snapshot({}) is None


def test_callback_denied(monkeypatch):
    seen = []
    monkeypatch.setattr(B, "answer_callback", lambda *a, **k: seen.append(a))
    B.handle_callback({"id": "cb1", "data": "prm:x:once",
                       "from": {"id": 999, "username": "yabanci"}})
    assert seen and seen[0][0] == "cb1"


def test_callback_bad_data(monkeypatch):
    seen = []
    monkeypatch.setattr(B, "answer_callback", lambda *a, **k: seen.append(a))
    B.handle_callback({"id": "cb2", "data": "prm:eksik",
                       "from": {"id": 111, "username": "t"}})
    assert seen and "Hatali" in seen[0][1]


def test_callback_unknown_hold(monkeypatch):
    seen = []
    monkeypatch.setattr(B, "answer_callback", lambda *a, **k: seen.append(a))
    monkeypatch.setattr(B, "_serve", lambda: (_ for _ in ()).throw(AssertionError("no-serve")))
    B.handle_callback({"id": "cb3", "data": "prm:per_yok:once",
                       "from": {"id": 111, "username": "t"}})
    assert seen and "suresi" in seen[0][1]


def test_serve_model_parse(monkeypatch):
    monkeypatch.setattr(B, "OPENCODE_MODEL", "opencode/space-bunny-free")
    assert B._serve_model() == ("opencode", "space-bunny-free")
    monkeypatch.setattr(B, "OPENCODE_MODEL", "")
    assert B._serve_model() is None


def test_projects_empty():
    assert B._projects() == {}
    assert "TELEGRAM_PROJECTS" in B.cmd_project("/project list", "111")


def test_project_set_unknown():
    import tempfile as _t
    d = _t.mkdtemp()
    import os as _o
    _o.environ["TELEGRAM_PROJECTS"] = "ali=%s" % d
    try:
        assert "Bilinmeyen" in B.cmd_project("/project set yok", "111")
        out = B.cmd_project("/project list", "111")
        assert "ali" in out
        assert "ayarlandi" in B.cmd_project("/project set ali", "111")
        assert B._chat_dir("111") == d
        assert "ali" in B.cmd_project("/project current", "111")
    finally:
        del _o.environ["TELEGRAM_PROJECTS"]
        B._save_sess("111", project=None)


def test_schedule_parse(monkeypatch):
    monkeypatch.setenv("TELEGRAM_SCHEDULE", "09:00:/durum;18:00:/gelen")
    assert B._parse_schedule() == [("09:00", "/durum"), ("18:00", "/gelen")]
    monkeypatch.setenv("TELEGRAM_SCHEDULE", "bos-girdi")
    assert B._parse_schedule() == []


def test_digest_push_flush(monkeypatch):
    monkeypatch.setattr(B, "DIGEST_ON", True)
    B._save(B.DIGEST_FILE, {"items": [], "window_start": 0})
    B._digest_push({"to": "default", "text": "selam"})
    assert B._digest_due()
    sent = []
    monkeypatch.setattr(B, "send_message", lambda *a, **k: sent.append(a) or 1)
    assert B._digest_flush() == 1
    assert sent and "selam" in sent[0][1]


# ---------------------------------------------------------------- GAP-20
# PIPE doldugunda cocuk surec yazmada kilitlenir. Onceki surumde _wait_proc
# yalnizca proc.wait() cagriyordu, hic kimse pipe'i okumuyordu; 64 KB asilince
# surec hic cikmiyor, bridge OPENCODE_TIMEOUT'a kadar bekleyip cevabi
# kaybediyordu. Bu test regresyondur.

_BIG = 300000


def _big_output_proc():
    script = ("import sys;"
              "sys.stdout.write('x'*%d);sys.stderr.write('y'*%d);"
              "sys.stdout.flush();sys.stderr.flush()" % (_BIG, _BIG // 2))
    return subprocess.Popen([sys.executable, "-c", script],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")


def test_wait_proc_drains_pipes(monkeypatch):
    """300 KB stdout + 150 KB stderr: 'ok' donmeli, sure timeout'a takilmamali."""
    monkeypatch.setattr(B, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(B, "send_message", lambda *a, **k: 1)
    proc = _big_output_proc()
    t0 = time.time()
    try:
        res, out, err = B._wait_proc(proc, None, "111", timeout=20)
        took = time.time() - t0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert res == "ok", "sonuc: %s" % res
    assert took < 15, "pipe kilitlendi, surec %.1f sn sonra hala cikmadi" % took
    assert len(out) == _BIG, "stdout kaybi: %d/%d" % (len(out), _BIG)
    assert len(err) == _BIG // 2, "stderr kaybi: %d/%d" % (len(err), _BIG // 2)


def test_wait_proc_abort_kills_child(monkeypatch):
    """Cok uzun surec + abort isareti: ABORTED donmeli, surec gercekten olmeli."""
    monkeypatch.setattr(B, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(B, "send_message", lambda *a, **k: 1)
    proc = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(120)"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ev = threading.Event()
    threading.Timer(1.0, ev.set).start()
    t0 = time.time()
    res, _, _ = B._wait_proc(proc, ev, "111", timeout=60)
    assert res == "ABORTED", res
    assert time.time() - t0 < 15
    assert proc.poll() is not None, "surec oldurulmemis"


def test_wait_proc_timeout(monkeypatch):
    monkeypatch.setattr(B, "send_action", lambda *a, **k: None)
    monkeypatch.setattr(B, "send_message", lambda *a, **k: 1)
    proc = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(120)"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    t0 = time.time()
    try:
        res, _, _ = B._wait_proc(proc, None, "111", timeout=2)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert res == "TIMEOUT", res
    assert 2 <= time.time() - t0 < 15


# ---------------------------------------------------------------- GAP-03
# Mailbox JSON'lari oku-degistir-yaz ile guncellenir. Yazicilar: ana poll loop,
# her worker thread, zamanlayici ve ayri surec olan MCP server. Onceki
# surumde lock yoktu ve _save sabit isimli ".tmp" kullaniyordu -> kayit kaybi
# ve bozuk JSON. Asagidaki testler regresyondur.

def test_inbox_add_concurrent_no_loss():
    """20 thread ayni dosyaya yazmali: kayit kaybolmamali, ID'ler tekil olmali."""
    B._save(B.INBOX_FILE, {"seq": 0, "messages": []})
    n, errs = 20, []

    def _w(i):
        try:
            B.inbox_add("111", "@t%d" % i, "mesaj %d" % i)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=_w, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs, errs
    st = B._load(B.INBOX_FILE, {})
    msgs = st.get("messages", [])
    assert len(msgs) == n, "kayit kaybi: %d/%d" % (len(msgs), n)
    ids = [m["id"] for m in msgs]
    assert len(set(ids)) == n, "ID cakismasi (%d tekil): %s" % (len(set(ids)), sorted(ids))
    assert len(set(m["text"] for m in msgs)) == n, "metin kaybi"
    assert st["seq"] == n, "seq kaydi: %s" % st.get("seq")


def test_json_never_corrupt_under_concurrent_saves():
    """Yazim sirasinda okuyan biri asla bozuk/eksik veri gormemeli.

    Okuyucu gercek t tuketicilerin yolunu kullanir (`_load`), cunku ham
    `open()` Windows'ta `os.replace` aninda gecici PermissionError alir ve
    bu bir bozulma degildir.
    """
    B._save(B.BUDGET_FILE, {"seq": 0, "n": 0, "pad": ""})
    stop, bad = threading.Event(), []

    def _writer():
        i = 0
        while not stop.is_set():
            i += 1
            B._save(B.BUDGET_FILE, {"seq": i, "n": i, "pad": "x" * 20000})

    def _reader():
        seen = 0
        while not stop.is_set():
            d = B._load(B.BUDGET_FILE, None)
            if d is None:
                bad.append("okunamadi")
                return
            if not isinstance(d, dict) or d.get("n") is None or d.get("pad") is None:
                bad.append("bozuk/eksik: %r" % (d,))
                return
            seen += 1
        if seen == 0:
            bad.append("hic okunamadi")

    ts = ([threading.Thread(target=_writer) for _ in range(2)]
          + [threading.Thread(target=_reader) for _ in range(2)])
    for t in ts:
        t.start()
    time.sleep(1.0)
    stop.set()
    for t in ts:
        t.join(timeout=5)
    assert not bad, bad[:2]


def test_outbox_flush_sends_once_under_concurrency(monkeypatch):
    """outbox_flush 3 yerden cagriliyor: ayni QUEUED kayit iki kez gitmemeli."""
    sent, lk = [], threading.Lock()

    def _fake_send(chat_id, text, reply_to=None):
        with lk:
            sent.append(text)
        time.sleep(0.05)  # gonderim penceresini ac
        return 1

    monkeypatch.setattr(B, "send_message", _fake_send)
    B._save(B.OUTBOX_FILE, {"seq": 1, "messages": [
        {"id": "OUT-001", "ts": "x", "to": "111", "text": "tek sefer",
         "status": "QUEUED"}]})
    ts = [threading.Thread(target=B.outbox_flush) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(sent) == 1, "mesaj %d kez gonderildi" % len(sent)
    m = B._load(B.OUTBOX_FILE, {})["messages"][0]
    assert m["status"] == "SENT", m
    assert "attempts" not in m and "next_try" not in m, m


def test_outbox_recover_reclaims_stuck_sending():
    """Surec olurken SENDING'de kalan kayit yeniden kuyruga alinmali."""
    B._save(B.OUTBOX_FILE, {"seq": 1, "messages": [
        {"id": "OUT-001", "ts": "x", "to": "111", "text": "takildi",
         "status": "SENDING", "sending_since": time.time() - 9999}]})
    assert B.outbox_recover() == 1
    m = B._load(B.OUTBOX_FILE, {})["messages"][0]
    assert m["status"] == "QUEUED" and "sending_since" not in m, m


def test_outbox_recover_leaves_fresh_sending():
    """Yeni baslayan baska bir surecin isi ezilmemeli."""
    B._save(B.OUTBOX_FILE, {"seq": 1, "messages": [
        {"id": "OUT-001", "ts": "x", "to": "111", "text": "canli",
         "status": "SENDING", "sending_since": time.time()}]})
    assert B.outbox_recover() == 0
    assert B._load(B.OUTBOX_FILE, {})["messages"][0]["status"] == "SENDING"


def test_cross_process_write_no_loss():
    """MCP server ayri bir surec: iki surec ayni dosyayi yaziyor, kayit kaybolmamali."""
    bridge_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bridge"))
    target = os.path.join(TMP, "telegram_cross.json")
    child = os.path.join(TMP, "_child_writer.py")
    with open(child, "w", encoding="utf-8") as f:
        f.write(
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "import atomic_json as aj\n"
            "P = %r\n"
            "for _ in range(15):\n"
            "    with aj.tx(P):\n"
            "        st = aj.load(P, {})\n"
            "        st['n'] = st.get('n', 0) + 1\n"
            "        aj.save(P, st)\n" % (bridge_dir, target))
    B._save(target, {"n": 0})
    proc = subprocess.Popen([sys.executable, child])
    try:
        for _ in range(15):
            with B._aj.tx(target):
                st = B._load(target, {})
                st["n"] = st.get("n", 0) + 1
                B._save(target, st)
        assert proc.wait(timeout=60) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert B._load(target, {}).get("n") == 30, "kayit kaybi: %s" % B._load(target, {})


# ---------------------------------------------------------------- GAP-04 / 10 / 13 / 16

def test_edited_message_does_not_rerun(monkeypatch):
    """GAP-04: duzenleme ayni isi ikinci kez tetiklememeli."""
    monkeypatch.setattr(B, "FOLLOW_EDITS", False)
    ran = []
    monkeypatch.setattr(B, "_gate_or_spawn",
                        lambda *a, **k: (ran.append(a[2]) or ("ok", True)))
    upd = {"edited_message": {"chat": {"id": 111}, "message_id": 7,
                              "from": {"id": 111}, "text": "duzeltilmis soru"}}
    B.handle_update(upd)
    assert not ran, "duzenleme isi tetikledi: %s" % ran


def test_edited_message_dedup_when_enabled(monkeypatch):
    """GAP-04: FOLLOW_EDITS=1 iken ayni duzenleme yine bir kez islenir."""
    monkeypatch.setattr(B, "FOLLOW_EDITS", True)
    B._seen_edits.clear()
    ran = []
    monkeypatch.setattr(B, "_gate_or_spawn",
                        lambda *a, **k: (ran.append(a[2]) or ("ok", True)))
    upd = {"edited_message": {"chat": {"id": 111}, "message_id": 8,
                              "from": {"id": 111}, "text": "soru v2"}}
    B.handle_update(upd)
    B.handle_update(upd)
    assert ran == ["soru v2"], ran


def test_finish_streamed_chunks_overflow(monkeypatch):
    """GAP-10: 4000 karakteri asan cevap kesilmemeli, parca parca gitmeli."""
    edits, sends = [], []
    monkeypatch.setattr(B, "edit_message",
                        lambda c, m, t: edits.append(t) or True)
    monkeypatch.setattr(B, "send_message", lambda c, t, reply_to=None: sends.append(t) or 1)
    long_out = "".join(chr(97 + (i % 26)) for i in range(11000))
    B._finish_streamed("111", 42, long_out, "TG-001")
    assert len(edits) == 1 and "TG-001" in edits[0]
    assert len(edits[0]) <= 4000, "ilk mesaj limiti asti: %d" % len(edits[0])
    assert "".join(sends) == long_out[3800:], "tasma kaybi: %d" % len("".join(sends))
    assert all(len(s) <= 4000 for s in sends)


def test_version_is_read_from_file():
    """GAP-13: surum tek kaynaktan (VERSION) okunur."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    p = os.path.join(root, "VERSION")
    assert os.path.exists(p), "VERSION dosyasi yok"
    with open(p, encoding="utf-8") as f:
        want = f.read().strip()
    assert B.VERSION == want, "%s != %s" % (B.VERSION, want)
    assert B.VERSION[0].isdigit(), "surum bicimlendirmesi: %r" % B.VERSION


def test_mcp_version_matches_bridge():
    """GAP-13: MCP sunucusu ayni surumu bildirmeli."""
    spec = importlib.util.spec_from_file_location(
        "tgmcp2", os.path.join(os.path.dirname(__file__), "..", "mcp", "server.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.VERSION == B.VERSION, "%s != %s" % (m.VERSION, B.VERSION)


def test_cleanup_tg_files_by_age_and_count(monkeypatch):
    """GAP-16: indirilen ekler yas + adet limitiyle temizlenmeli."""
    d = os.path.join(TMP, "tg_files")
    os.makedirs(d, exist_ok=True)
    for n in os.listdir(d):
        os.remove(os.path.join(d, n))
    old = os.path.join(d, "eski.jpg")
    for i in range(5):
        p = os.path.join(d, "yeni_%d.jpg" % i)
        with open(p, "wb") as f:
            f.write(b"x")  # yeni: mtime simdi
    with open(old, "wb") as f:
        f.write(b"x")
    os.utime(old, (time.time() - 86400, time.time() - 86400))  # 24 saat once
    monkeypatch.setattr(B, "TG_FILES_MAX_AGE", 3600)
    monkeypatch.setattr(B, "TG_FILES_MAX_COUNT", 2)
    n = B._cleanup_tg_files()
    left = sorted(os.listdir(d))
    assert not os.path.exists(old), "yasli dosya silinmedi"
    assert n >= 1
    assert len(left) == 2, "adet limiti uygulanmadi: %s" % left


def test_send_message_chunks(monkeypatch):
    """4000 karakter ustu metin parcalara bolunmeli (Telegram siniri)."""
    sent = []
    monkeypatch.setattr(B, "api", lambda m, p=None, timeout=45, max_retries=None: sent.append((m, p)) or {"ok": True})
    B.send_message("111", "y" * 9500)
    texts = [p["text"] for (m, p) in sent if m == "sendMessage"]
    assert len(texts) == 3, len(texts)
    assert all(len(t) <= 4000 for t in texts)
    assert "".join(texts) == "y" * 9500


# ================================================================ B: setMyCommands
# Telegram'in "/" komut menusunu bridge doldurur. Onceki surumde kullanici
# BotFather'a elle komut girmesi gerekiyordu (KURULUM.md) ve cogu kullanic
# yapmiyordu -> komutlar Telegram'da gorunmuyordu.

def test_command_catalog_is_valid():
    names = [c["command"] for c in B.TG_COMMANDS]
    assert names, "komut listesi bos"
    assert len(names) == len(set(names)), "komut adlari tekil degil: %s" % names
    assert len(names) <= 100, "Telegram en fazla 100 komut kabul eder"
    for c in B.TG_COMMANDS:
        assert re.match(r"^[a-z0-9_]{1,32}$", c["command"]), c
        assert 1 <= len(c["description"]) <= 256, c


def test_command_catalog_covers_help():
    """Katalog yardim metnindeki tum komutlari icermeli (iki kaynak ayrilmamali)."""
    listed = {c["command"] for c in B.TG_COMMANDS}
    for name in ("yardim", "durum", "sor", "onay", "abort", "reset", "model", "project"):
        assert name in listed, "/%s yardimda var ama katalogda yok" % name


def test_set_my_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(B, "api",
                        lambda m, p=None, timeout=45, max_retries=None: calls.append((m, p)) or {"ok": True})
    assert B.set_my_commands() is True
    assert calls and calls[0][0] == "setMyCommands"
    cmds = calls[0][1]["commands"]
    assert any(c["command"] == "yardim" for c in cmds)
    assert all(set(c) == {"command", "description"} for c in cmds)


def test_set_my_commands_disabled(monkeypatch):
    monkeypatch.setattr(B, "SET_COMMANDS", False)
    calls = []
    monkeypatch.setattr(B, "api", lambda *a, **k: calls.append(a) or {"ok": True})
    assert B.set_my_commands() is False
    assert not calls


def test_set_my_commands_never_breaks_startup(monkeypatch):
    """setMyCommands basarisiz olursa bridge yine acilmali."""
    def boom(*a, **k):
        raise RuntimeError("ag yok")
    monkeypatch.setattr(B, "api", boom)
    assert B.set_my_commands() is False


# ================================================================ C: kademeli throttle
# Sabit 2 sn yerine sureye gore kademeli: kisa isler akici, uzun isler rate

def test_progressive_throttle_tiers():
    assert B.progressive_throttle(0) == 1.0
    assert B.progressive_throttle(59) == 1.0
    assert B.progressive_throttle(61) == 2.0
    assert B.progressive_throttle(4 * 60) == 2.0
    assert B.progressive_throttle(6 * 60) == 5.0
    assert B.progressive_throttle(14 * 60) == 5.0
    assert B.progressive_throttle(16 * 60) == 10.0
    assert B.progressive_throttle(600 * 60) == 10.0


def test_progressive_throttle_never_negative():
    assert B.progressive_throttle(-5) == 1.0


# ================================================================ D: 429 / 5xx retry

def test_retry_wait_429_honors_retry_after():
    assert B._retry_wait(429, {"parameters": {"retry_after": 7}}, 0) == 7.0
    assert B._retry_wait(429, {"parameters": {"retry_after": 999}}, 0) == B.API_RETRY_AFTER_CAP


def test_retry_wait_429_parses_description():
    body = {"description": "Too Many Requests: retry after 12"}
    assert B._retry_wait(429, body, 0) == 12.0


def test_retry_wait_429_fallback_when_unknown():
    assert B._retry_wait(429, {}, 0) == B.API_RETRY_BASE
    assert B._retry_wait(429, {"parameters": {}}, 3) == B.API_RETRY_BASE


def test_retry_wait_transient_5xx_backoff():
    assert B._retry_wait(502, {}, 0) == B.API_RETRY_BASE
    assert B._retry_wait(502, {}, 1) == pytest.approx(B.API_RETRY_BASE * 2)
    assert B._retry_wait(503, {}, 20) == B.API_RETRY_CAP
    for code in (500, 502, 503, 504):
        assert B._retry_wait(code, {}, 0) is not None, code


def test_retry_wait_no_retry_for_permanent_errors():
    for code in (400, 401, 403, 404, 409):
        assert B._retry_wait(code, {}, 0) is None, code


def _http_error(code, body=None):
    import io
    import urllib.error
    raw = json.dumps(body or {"ok": False}).encode("utf-8")
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(raw))


class _FakeResp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_api_retries_429_then_succeeds(monkeypatch):
    n = []
    monkeypatch.setattr(B.time, "sleep", lambda s: None)

    def fake(req, timeout=None):
        n.append(1)
        if len(n) == 1:
            raise _http_error(429, {"ok": False, "error_code": 429,
                                    "parameters": {"retry_after": 3}})
        return _FakeResp({"ok": True, "result": {"message_id": 7}})

    monkeypatch.setattr(B.urllib.request, "urlopen", fake)
    r = B.api("sendMessage", {"chat_id": "1", "text": "x"})
    assert r["result"]["message_id"] == 7
    assert len(n) == 2


def test_api_retries_transient_5xx(monkeypatch):
    n = []
    monkeypatch.setattr(B.time, "sleep", lambda s: None)

    def fake(req, timeout=None):
        n.append(1)
        if len(n) < 3:
            raise _http_error(502, {"ok": False})
        return _FakeResp({"ok": True})

    monkeypatch.setattr(B.urllib.request, "urlopen", fake)
    assert B.api("getMe", {})["ok"] is True
    assert len(n) == 3


def test_api_gives_up_after_max_retries(monkeypatch):
    n = []
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    monkeypatch.setattr(B.urllib.request, "urlopen",
                        lambda req, timeout=None: (n.append(1), _raise(_http_error(502)))[1])
    with pytest.raises(Exception):
        B.api("sendMessage", {}, max_retries=2)
    assert len(n) == 3, "1 deneme + 2 tekrar bekleniyordu, %d oldu" % len(n)


def test_api_does_not_retry_409(monkeypatch):
    """409 = baska bir bridge ayni botta. Tekrarlanirsa durum bulaniklasir."""
    n = []
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    monkeypatch.setattr(B.urllib.request, "urlopen",
                        lambda req, timeout=None: (n.append(1), _raise(_http_error(409)))[1])
    with pytest.raises(Exception):
        B.api("getUpdates", {}, max_retries=3)
    assert len(n) == 1, "409 tekrar denenmemeli, %d deneme yapildi" % len(n)


def test_api_does_not_retry_401(monkeypatch):
    n = []
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    monkeypatch.setattr(B.urllib.request, "urlopen",
                        lambda req, timeout=None: (n.append(1), _raise(_http_error(401)))[1])
    with pytest.raises(Exception):
        B.api("getMe", {}, max_retries=3)
    assert len(n) == 1


def test_api_retries_network_error(monkeypatch):
    import urllib.error
    n = []
    monkeypatch.setattr(B.time, "sleep", lambda s: None)

    def fake(req, timeout=None):
        n.append(1)
        if len(n) < 3:
            raise urllib.error.URLError("baglanti yok")
        return _FakeResp({"ok": True})

    monkeypatch.setattr(B.urllib.request, "urlopen", fake)
    assert B.api("getMe", {})["ok"] is True
    assert len(n) == 3


def _raise(exc):
    raise exc


# ================================================================ F: buton yapisi
# Onceki surumde butonlar tek satirda diziliyordu: 8 secenekli bir soruda
# Telegram 8 butonu bir satira sigdiromaya calisiyordu. Artizgara + iptal.

def test_send_buttons_grid_layout(monkeypatch):
    calls = []
    monkeypatch.setattr(B, "api",
                        lambda m, p=None, timeout=45, max_retries=None: calls.append((m, p)) or {"result": {"message_id": 3}})
    B.send_buttons("111", "sec", [("A", "frm:x:0"), ("B", "frm:x:1"), ("C", "frm:x:2")],
                   cols=2, cancel=("Vazgec", "frm:x:cancel"))
    kb = calls[0][1]["reply_markup"]["inline_keyboard"]
    assert [len(r) for r in kb] == [2, 1, 1], kb
    assert kb[0][0]["callback_data"] == "frm:x:0"
    assert kb[-1][0]["callback_data"] == "frm:x:cancel"
    assert kb[-1][0]["text"] == "Vazgec"


def test_send_buttons_even_grid_no_trailing_gap(monkeypatch):
    calls = []
    monkeypatch.setattr(B, "api",
                        lambda m, p=None, timeout=45, max_retries=None: calls.append((m, p)) or {"result": {"message_id": 3}})
    B.send_buttons("111", "s", [("A", "a"), ("B", "b"), ("C", "c"), ("D", "d")], cols=2)
    kb = calls[0][1]["reply_markup"]["inline_keyboard"]
    assert [len(r) for r in kb] == [2, 2], kb


def test_permission_menu_is_one_row(monkeypatch):
    """Izin menusu 3 buton + cols=3 -> tek satir (gercek cagri noktasi)."""
    calls = []
    monkeypatch.setattr(B, "api",
                        lambda m, p=None, timeout=45, max_retries=None: calls.append((m, p)) or {"result": {"message_id": 3}})
    B.send_buttons("111", "s", [("Onayla (1 kez)", "prm:1:once"),
                                ("Her zaman", "prm:1:always"),
                                ("Reddet", "prm:1:reject")], cols=3)
    kb = calls[0][1]["reply_markup"]["inline_keyboard"]
    assert [len(r) for r in kb] == [3], kb
    assert [b["text"] for b in kb[0]] == ["Onayla (1 kez)", "Her zaman", "Reddet"]


def test_send_buttons_callback_data_limit(monkeypatch):
    """Telegram callback_data limiti 64 bayt; asarsa mesaj gonderilmez."""
    calls = []
    monkeypatch.setattr(B, "api",
                        lambda m, p=None, timeout=45, max_retries=None: calls.append((m, p)) or {"result": {"message_id": 3}})
    B.send_buttons("111", "s", [("A" * 40, "frm:%s:0" % ("z" * 120))])
    for row in calls[0][1]["reply_markup"]["inline_keyboard"]:
        for b in row:
            assert len(b["callback_data"].encode("utf-8")) <= 64, len(b["callback_data"])


def test_form_cancel_button_stops_run(monkeypatch):
    """Soruyu reddetmek = isi durdurmak. Yari birakilmis soru birakmamali."""
    B.pending_forms.clear()
    B.pending_forms["F-001"] = {"session": "s1", "chat": "111", "formID": "f9",
                                "key": "renk", "options": ["a", "b"],
                                "ts": time.time(), "msg": 5}
    acted = []

    class _C:
        def interrupt(self, s):
            acted.append(("interrupt", s))

    monkeypatch.setattr(B, "_serve", lambda *a, **k: _C())
    seen, edits = [], []
    monkeypatch.setattr(B, "answer_callback", lambda *a, **k: seen.append(a))
    monkeypatch.setattr(B, "edit_message", lambda c, m, t: edits.append((c, m, t)) or True)
    B.handle_callback({"id": "cb9", "data": "frm:F-001:cancel", "from": {"id": 111},
                       "message": {"message_id": 5}})
    assert "F-001" not in B.pending_forms
    assert acted == [("interrupt", "s1")], acted
    assert seen and seen[0][0] == "cb9"
    assert edits and "vazgec" in edits[0][2].lower()


def test_stale_callback_is_rejected(monkeypatch):
    """Eski mesajdaki buton artik gecerli degil (E: bayat buton korumasi)."""
    B.pending_perms.clear()
    B.pending_perms["perm-1"] = {"session": "s1", "chat": "111", "msg": 99,
                                 "ts": time.time()}
    seen = []
    monkeypatch.setattr(B, "answer_callback", lambda *a, **k: seen.append(a) or None)
    monkeypatch.setattr(B, "_serve", lambda *a, **k: (_ for _ in ()).throw(AssertionError("serve cagrilmamali")))
    B.handle_callback({"id": "cbx", "data": "prm:perm-1:once", "from": {"id": 111},
                       "message": {"message_id": 5}})
    assert seen, "callback cevapsiz kalmamali"
    assert "guncel" in seen[0][1].lower(), seen[0][1]
    assert "perm-1" in B.pending_perms, "gecersiz buton kaydi yutmemeli"


def test_current_callback_still_works(monkeypatch):
    """Mesaj eslesiyorsa buton normal calismali (regresyon)."""
    B.pending_perms.clear()
    B.pending_perms["perm-2"] = {"session": "s1", "chat": "111", "msg": 99,
                                 "ts": time.time()}
    replied = []

    class _C:
        def reply_permission(self, s, r, d):
            replied.append((s, r, d))

    monkeypatch.setattr(B, "_serve", lambda *a, **k: _C())
    monkeypatch.setattr(B, "answer_callback", lambda *a, **k: None)
    monkeypatch.setattr(B, "edit_message", lambda c, m, t: True)
    B.handle_callback({"id": "cby", "data": "prm:perm-2:always", "from": {"id": 111},
                       "message": {"message_id": 99}})
    assert replied == [("s1", "perm-2", "always")], replied
    assert "perm-2" not in B.pending_perms

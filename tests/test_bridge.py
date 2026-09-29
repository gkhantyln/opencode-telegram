"""opencode-telegram bridge unit testleri (ag/model gerektirmez).

Calistir: pytest opencode-telegram/tests/test_bridge.py
"""
import importlib.util
import json
import os
import tempfile
import threading

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

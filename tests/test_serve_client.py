"""bridge/serve_client.py testleri (ag gerektirmez; HTTP katmani sahte).

opencode `serve` istemcisi bridge'in en zor parcasidi ve hic testi yoktu
(GAP-22). Burada istek/yanit bicimi, hata siniflari, `wait_reply` zaman asimi
ve SSE ayristirma dogrulanir.
"""
import importlib.util
import json
import os
import sys
import urllib.error

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "bridge")))
import serve_client as SC  # noqa: E402


class _Resp:
    def __init__(self, payload=None, raw=None, chunks=None):
        self._raw = raw if raw is not None else json.dumps(payload or {}).encode()
        self._chunks = chunks

    def read(self, n=None):
        if self._chunks is not None:
            return self._chunks.pop(0) if self._chunks else b""
        if n is None:
            d, self._raw = self._raw, b""
            return d
        d, self._raw = self._raw[:n], self._raw[n:]
        return d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, body=None):
    import io
    raw = json.dumps(body or {"ok": False}).encode()
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(raw))


class _FakeURL:
    """urlopen yerine gecer. Yanit sirasi: dict | exception."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append({
            "url": req.full_url,
            "method": req.get_method(),
            "body": json.loads(req.data.decode()) if req.data else None,
            "auth": req.headers.get("Authorization") or
                    (req.headers.get("Authorization") if hasattr(req, "headers") else None),
            "timeout": timeout,
        })
        if not self.script:
            raise AssertionError("beklenmeyen istek: %s" % req.full_url)
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if isinstance(nxt, _Resp):
            return nxt
        if callable(nxt):
            return nxt(req)
        return _Resp(nxt)


def _client(base="http://127.0.0.1:4096"):
    return SC.ServeClient(base, "pw", "/proj")


# ---------------------------------------------------------------- temel

def test_auth_header_is_basic_opencode():
    import base64
    c = _client()
    expect = "Basic " + base64.b64encode(b"opencode:pw").decode()
    assert c.headers["Authorization"] == expect


def test_req_unwraps_data_key():
    c = _client()
    url = _FakeURL([{"data": {"id": "ses_1"}}])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        assert c._req("GET", "/api/info") == {"id": "ses_1"}
    finally:
        SC.urllib.request.urlopen = orig


def test_req_returns_whole_body_without_data_key():
    c = _client()
    url = _FakeURL([{"ok": True, "value": 7}])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        assert c._req("GET", "/api/info") == {"ok": True, "value": 7}
    finally:
        SC.urllib.request.urlopen = orig


def test_req_empty_body_returns_none():
    c = _client()
    url = _FakeURL([_Resp(raw=b"")])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        assert c._req("GET", "/api/info") is None
    finally:
        SC.urllib.request.urlopen = orig


def test_http_error_becomes_serveerror():
    c = _client()
    url = _FakeURL([_http_error(400, {"error": "bad"})])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        with pytest.raises(SC.ServeError) as e:
            c._req("POST", "/api/session", {"a": 1})
        assert "400" in str(e.value) and "bad" in str(e.value)
    finally:
        SC.urllib.request.urlopen = orig


# ---------------------------------------------------------------- sessions

def test_create_session_payload():
    c = _client()
    url = _FakeURL([{"data": {"id": "ses_9"}}])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        sid = c.create_session(title="T", agent="orchestrator",
                               model=("opencode", "m1"))
        assert sid == "ses_9"
        body = url.calls[0]["body"]
        assert body["directory"] == "/proj"
        assert body["title"] == "T" and body["agent"] == "orchestrator"
        assert body["model"] == {"providerID": "opencode", "id": "m1"}
        assert url.calls[0]["method"] == "POST"
        assert url.calls[0]["url"].endswith("/api/session")
    finally:
        SC.urllib.request.urlopen = orig


def test_create_session_omits_optional_fields():
    c = _client()
    url = _FakeURL([{"data": {"id": "x"}}])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        c.create_session()
        assert set(url.calls[0]["body"]) == {"directory"}
    finally:
        SC.urllib.request.urlopen = orig


def test_delete_session_returns_false_on_missing():
    c = _client()
    url = _FakeURL([_http_error(404, {})])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        assert c.delete_session("yok") is False
    finally:
        SC.urllib.request.urlopen = orig


def test_prompt_sends_text_and_files():
    c = _client()
    url = _FakeURL([{"data": {"ok": 1}}])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        c.prompt("ses_1", "merhaba", files=[("text/plain", "/tmp/a.txt")])
        body = url.calls[0]["body"]
        assert body["text"] == "merhaba"
        assert body["files"] == [{"mime": "text/plain", "url": "/tmp/a.txt"}]
        assert url.calls[0]["url"].endswith("/api/session/ses_1/prompt")
    finally:
        SC.urllib.request.urlopen = orig


def test_messages_returns_list():
    c = _client()
    url = _FakeURL([{"data": [{"type": "assistant"}]}])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        assert c.messages("s") == [{"type": "assistant"}]
    finally:
        SC.urllib.request.urlopen = orig


def test_interrupt_never_raises():
    c = _client()
    url = _FakeURL([_http_error(409, {})])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        assert c.interrupt("s") is False
    finally:
        SC.urllib.request.urlopen = orig


# ---------------------------------------------------------------- wait_reply

def _msg(text=None, created=0, completed=False, mid="m1"):
    m = {"id": mid, "type": "assistant", "time": {"created": created, "completed": completed}}
    if text is not None:
        m["content"] = [{"type": "text", "text": text}]
    return m


def test_wait_reply_returns_completed_text(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "messages", lambda s: [_msg("cevap", created=100, completed=True)])
    assert c.wait_reply("s", 50, timeout=5, poll=0) == "cevap"


def test_wait_reply_ignores_messages_before_since(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "messages", lambda s: [_msg("eski", created=10, completed=True)])
    with pytest.raises(SC.ServeError):
        c.wait_reply("s", 100, timeout=0.2, poll=0)


def test_wait_reply_ignores_uncompleted(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "messages",
                        lambda s: [_msg("yari", created=100, completed=False)])
    with pytest.raises(SC.ServeError):
        c.wait_reply("s", 50, timeout=0.2, poll=0)


def test_wait_reply_skips_empty_text(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "messages", lambda s: [_msg("   ", created=100, completed=True)])
    with pytest.raises(SC.ServeError):
        c.wait_reply("s", 50, timeout=0.2, poll=0)


def test_wait_reply_returns_none_on_stop_flag(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "messages", lambda s: [])
    assert c.wait_reply("s", 0, timeout=5, poll=0, stop_flag=lambda: True) is None


def test_wait_reply_calls_on_tick(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "messages", lambda s: [])
    ticks = []
    with pytest.raises(SC.ServeError):
        c.wait_reply("s", 0, timeout=0.2, poll=0, on_tick=lambda: ticks.append(1))
    assert ticks, "on_tick cagrilmadi"


def test_wait_reply_survives_serve_error(monkeypatch):
    """Tek seferlik 5xx'de cevap kaybolmamali, dongu devam etmeli."""
    c = _client()
    state = {"n": 0}

    def _msgs(s):
        state["n"] += 1
        if state["n"] == 1:
            raise SC.ServeError("502")
        return [_msg("ok", created=100, completed=True)]
    monkeypatch.setattr(c, "messages", _msgs)
    assert c.wait_reply("s", 50, timeout=5, poll=0) == "ok"


def test_wait_reply_timeout_raises(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "messages", lambda s: [])
    with pytest.raises(SC.ServeError) as e:
        c.wait_reply("s", 0, timeout=0.2, poll=0)
    assert "yanit" in str(e.value)


def test_assistant_text_reads_parts_fallback():
    m = {"parts": [{"type": "text", "text": "a"}, {"type": "image"}, {"type": "text", "text": "b"}]}
    assert SC.ServeClient._assistant_text(m) == "a\nb"


def test_assistant_text_ignores_non_text():
    assert SC.ServeClient._assistant_text({"content": [{"type": "image"}]}) == ""


# ---------------------------------------------------------------- izin / form

def test_reply_permission_validates_decision():
    c = _client()
    with pytest.raises(ValueError):
        c.reply_permission("s", "r", "maybe")


def test_reply_permission_posts():
    c = _client()
    url = _FakeURL([{"data": {}}])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        c.reply_permission("s", "r", "always")
        assert url.calls[0]["body"] == {"decision": "always"}
        assert url.calls[0]["url"].endswith("/api/session/s/permission/r/reply")
    finally:
        SC.urllib.request.urlopen = orig


def test_list_permissions_swallows_error(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_req", lambda *a, **k: (_ for _ in ()).throw(SC.ServeError("x")))
    assert c.list_permissions("s") == []


def test_list_forms_swallows_error(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_req", lambda *a, **k: (_ for _ in ()).throw(SC.ServeError("x")))
    assert c.list_forms("s") == []


def test_reply_form_posts_answer():
    c = _client()
    url = _FakeURL([{"data": {}}])
    orig = SC.urllib.request.urlopen
    SC.urllib.request.urlopen = url
    try:
        c.reply_form("s", "f1", {"renk": "mavi"})
        assert url.calls[0]["body"] == {"answer": {"renk": "mavi"}}
    finally:
        SC.urllib.request.urlopen = orig


# ---------------------------------------------------------------- SSE

def test_events_parses_sse_frames(monkeypatch):
    """SSE akisi: 'event:' ve 'data:' satirlari ayristirilir."""
    c = _client()
    body = (b"event: message\n"
            b'data: {"type":"session.text.delta","data":{"sessionID":"s","delta":"hi"}}\n\n'
            b"event: message\n"
            b'data: {"type":"session.execution.succeeded","data":{"sessionID":"s"}}\n\n')
    monkeypatch.setattr(SC.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw=body))
    evs = list(c.events(timeout=1))
    assert len(evs) == 2, evs
    assert evs[0]["data"]["type"] == "session.text.delta"
    assert evs[0]["data"]["data"]["delta"] == "hi"
    assert evs[1]["data"]["type"] == "session.execution.succeeded"


def test_events_stops_on_flag(monkeypatch):
    c = _client()
    monkeypatch.setattr(SC.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(chunks=[b"", b"", b""]))
    assert list(c.events(stop_flag=lambda: True, timeout=1)) == []


def test_events_swallows_connection_error(monkeypatch):
    c = _client()

    def boom(req, timeout=None):
        raise urllib.error.URLError("kapali")
    monkeypatch.setattr(SC.urllib.request, "urlopen", boom)
    assert list(c.events(timeout=1)) == []


def test_events_survives_invalid_json(monkeypatch):
    c = _client()
    body = b"event: message\ndata: {bozuk json\n\n"
    monkeypatch.setattr(SC.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(raw=body))
    evs = list(c.events(timeout=1))
    assert len(evs) == 1
    assert evs[0]["data"] == "{bozuk json"


def test_events_uses_sse_content_type(monkeypatch):
    c = _client()
    seen = {}

    def _cap(req, timeout=None):
        seen["accept"] = req.headers.get("Accept")
        return _Resp(raw=b"")
    monkeypatch.setattr(SC.urllib.request, "urlopen", _cap)
    list(c.events(timeout=1))
    assert seen["accept"] == "text/event-stream"

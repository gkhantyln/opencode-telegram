"""opencode `serve` HTTP istemcisi (stdlib-only).

Kullanim:
    c = ServeClient("http://127.0.0.1:4096", "sifre", "/proje/dizini")
    sid = c.create_session(title="Telegram ...", agent="orchestrator", model=("opencode","x-free"))
    c.prompt(sid, "merhaba")
    reply = c.wait_reply(sid, since_ms=..., timeout=600)   # poll ile tamamlanmayi bekler
    c.interrupt(sid)                                       # abort
    c.delete_session(sid)                                  # reset
    c.reply_permission(sid, request_id, "once"|"always"|"reject")
    for ev in c.events(stop_flag): ...                     # SSE generator (TG2-2/2-3)

Serve'in kendisi bridge tarafindan ayaga kaldirilir:
`opencode serve --port P` + OPENCODE_PASSWORD env (bkz. bridge TELEGRAM_SERVE_*).
"""

import base64
import json
import time
import urllib.request
import urllib.error


class ServeError(RuntimeError):
    pass


class ServeClient:
    def __init__(self, base_url, password, directory, timeout=30):
        self.base = base_url.rstrip("/")
        self.dir = directory
        self.timeout = timeout
        self.headers = {
            "Authorization": "Basic " + base64.b64encode(
                ("opencode:" + password).encode()).decode(),
            "Content-Type": "application/json",
        }

    def _req(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                raw = r.read()
                if not raw:
                    return None
                d = json.loads(raw.decode("utf-8"))
                return d.get("data", d)
        except urllib.error.HTTPError as e:
            raise ServeError("%s %s -> HTTP %d: %s" % (
                method, path, e.code, e.read()[:300].decode("utf-8", "replace")))

    # ---- sessions ----

    def create_session(self, title=None, agent=None, model=None):
        body = {"directory": self.dir}
        if title:
            body["title"] = title
        if agent:
            body["agent"] = agent
        if model:
            provider, mid = model
            body["model"] = {"providerID": provider, "id": mid}
        return self._req("POST", "/api/session", body)["id"]

    def delete_session(self, session_id):
        try:
            self._req("DELETE", "/api/session/%s" % session_id)
            return True
        except ServeError:
            return False

    def prompt(self, session_id, text, files=None, model=None):
        body = {"text": text}
        if files:
            body["files"] = [{"mime": m, "url": u} for (m, u) in files]
        if model:
            provider, mid = model
            body["model"] = {"providerID": provider, "id": mid}
        return self._req("POST", "/api/session/%s/prompt" % session_id, body,
                         timeout=self.timeout)

    def interrupt(self, session_id):
        try:
            self._req("POST", "/api/session/%s/interrupt" % session_id, {})
            return True
        except ServeError:
            return False

    def messages(self, session_id):
        return self._req("GET", "/api/session/%s/message" % session_id) or []

    # ---- cevap bekleme (poll) ----

    @staticmethod
    def _assistant_text(msg):
        parts = []
        for p in (msg.get("content") or msg.get("parts") or []):
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text", "").strip():
                parts.append(p["text"])
        return "\n".join(parts).strip()

    def wait_reply(self, session_id, since_ms, timeout=600, poll=3,
                   on_tick=None, stop_flag=None):
        """since_ms sonrasi tamamlanan ilk asistan mesajinin metnini doner."""
        start = time.time()
        while time.time() - start < timeout:
            if stop_flag is not None and stop_flag():
                return None
            try:
                for m in self.messages(session_id):
                    if not isinstance(m, dict) or m.get("type") != "assistant":
                        continue
                    t = m.get("time") or {}
                    created = int(t.get("created", 0) or 0)
                    if created < since_ms:
                        continue
                    if t.get("completed"):
                        txt = self._assistant_text(m)
                        if txt:
                            return txt
            except ServeError:
                pass
            if on_tick:
                try:
                    on_tick()
                except Exception:
                    pass
            time.sleep(poll)
        raise ServeError("yanit %d sn'de gelmedi" % timeout)

    # ---- izinler ----

    def reply_permission(self, session_id, request_id, decision):
        if decision not in ("once", "always", "reject"):
            raise ValueError("decision once|always|reject olmali")
        self._req("POST", "/api/session/%s/permission/%s/reply" % (session_id, request_id),
                  {"decision": decision})
        return True

    def list_permissions(self, session_id):
        try:
            return self._req("GET", "/api/session/%s/permission" % session_id) or []
        except ServeError:
            return []

    def list_forms(self, session_id):
        try:
            return self._req("GET", "/api/session/%s/form" % session_id) or []
        except ServeError:
            return []

    def reply_form(self, session_id, form_id, answer):
        self._req("POST", "/api/session/%s/form/%s/reply" % (session_id, form_id),
                  {"answer": answer})
        return True

    # ---- SSE olay akisi ----

    def events(self, stop_flag=None, timeout=300):
        """SSE generator: {'type':..., 'data':...} sozlukleri verir."""
        req = urllib.request.Request(self.base + "/api/event",
                                     headers=dict(self.headers, Accept="text/event-stream"))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                buf = ""
                while True:
                    if stop_flag is not None and stop_flag():
                        return
                    chunk = r.read(1)
                    if not chunk:
                        return
                    buf += chunk.decode("utf-8", "replace")
                    while "\n\n" in buf:
                        raw, buf = buf.split("\n\n", 1)
                        typ, data = "message", None
                        for line in raw.splitlines():
                            if line.startswith("event:"):
                                typ = line[6:].strip()
                            elif line.startswith("data:"):
                                payload = line[5:].strip()
                                try:
                                    data = json.loads(payload)
                                except Exception:
                                    data = payload
                        yield {"event": typ, "data": data}
        except Exception:
            return

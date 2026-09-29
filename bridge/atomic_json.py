"""Mailbox JSON dosyalari icin islem-semantik erisim (stdlib only).

Neden ayri modul: `bridge.py` (bir surec) ve `mcp/server.py` (baska bir
surec) ayni JSON dosyalarini okuyup yaziyor. Ikisi de AYNI kilit
protokolunu kullanmazsa biri digerinin yazmasini ezebilir (GAP-03):

  * sabit isimli `.tmp` -> iki yazici ayni dosyaya yazar, `os.replace`
    biri sirasinda WinError 5 verir, ara durumda bozuk JSON okunur.
  * kilitsiz oku-degistir-yaz -> seq sayaci iki kez artar, kayit kaybolur.

Kullanim:

    from atomic_json import load, save, tx

    save(PATH, {"a": 1})                    # tek basina atomik yazim

    with tx(PATH):                          # oku-degistir-yaz
        st = load(PATH, {"seq": 0})
        st["seq"] += 1
        save(PATH, st)

Kurallar:
  * `tx` icinde ag islemi (sendMessage, subprocess) YAPMA. Kilit tutulurken
    beklemek tum bridge'i bloklar ve baska sureclere (MCP) gecidi kapatir.
  * `tx` yeniden girilebilir: ayni thread ic ice `tx` acabilir, dosya
    kilidi ikinci kez alinmaz.
  * `load` okunamayan dosyada sessizce `default` doner (korrupt JSON'da
    veri kaybi yerine bos durum tercih edilir; `tx` ile yazma yine korunur).
"""

import json
import os
import sys
import threading
import time
from contextlib import contextmanager

# Kilit en fazla bu kadar bekler; sonra kilitsiz devam eder. Bridge'in
# sonsuza kadar kilitlenmesindense en kotu senaryoda kayit kaybi yegdir
# (tek yazici varsa zararsiz, `save` yine atomik).
LOCK_TIMEOUT = 20.0
_POLL = 0.02
# Windows'ta `os.replace` acik hedefe yazamaz (bkz. `save`).
REPLACE_TIMEOUT = 5.0

_locks = {}
_locks_meta = threading.Lock()
_held = {}          # anahtar -> [thread ident, derinlik]
_held_meta = threading.Lock()


# ---------- dosya kilidi ----------

def _key(path):
    return os.path.normcase(os.path.abspath(path))


def _thread_lock(key):
    with _locks_meta:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.RLock()
        return lk


def _open_lockfile(key):
    d = os.path.dirname(key)
    if d:
        os.makedirs(d, exist_ok=True)
    f = open(key + ".lock", "a+b")
    f.seek(0, os.SEEK_END)
    if f.tell() == 0:
        f.write(b"\0")
        f.flush()
    return f


def _lock_file(f):
    deadline = time.time() + LOCK_TIMEOUT
    if os.name == "nt":
        import msvcrt
        while True:
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.time() > deadline:
                    return
                time.sleep(_POLL)
    else:
        import fcntl
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.time() > deadline:
                    return
                time.sleep(_POLL)


def _unlock_file(f):
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def tx(path):
    """Oku-degistir-yaz icin kilitli bolge (thread + surec). Icerinde bekleme yok."""
    key = _key(path)
    with _thread_lock(key):
        with _held_meta:
            cur = _held.get(key)
            reentrant = bool(cur and cur[0] == threading.get_ident())
            if reentrant:
                cur[1] += 1
        if reentrant:
            try:
                yield
            finally:
                with _held_meta:
                    _held[key][1] -= 1
            return
        f = _open_lockfile(key)
        _lock_file(f)
        with _held_meta:
            _held[key] = [threading.get_ident(), 1]
        try:
            yield
        finally:
            with _held_meta:
                _held.pop(key, None)
            _unlock_file(f)
            f.close()


# ---------- JSON oku / yaz ----------

def _warn(msg):
    # stdout MCP stdio protokolunun parcasidir; sadece stderr'a yaziyoruz.
    try:
        sys.stderr.write("atomic_json: %s\n" % msg)
    except Exception:
        pass


def load(path, default, timeout=2.0):
    """JSON oku. Okunamayan/bozuk dosyada `default` doner.

    `PermissionError` gecicidir: Windows'ta `os.replace` hedefi aninda
    kilitledigi icin yazma aninda okuma denemesi cok kisa sureligine
    basarisiz olabilir. Bunu `default` saymak sessiz veri kaybi olurdu,
    o yuzden kisa sure tekrar deniyoruz.
    """
    deadline = time.time() + timeout
    while True:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except PermissionError as e:
            if time.time() >= deadline:
                _warn("okunamadi, varsayilana donuluyor: %s (%s)" % (path, e))
                return default
            time.sleep(0.01)
        except (json.JSONDecodeError, ValueError) as e:
            _warn("bozuk JSON, varsayilana donuluyor: %s (%s)" % (path, e))
            return default
        except OSError as e:
            _warn("okunamadi, varsayilana donuluyor: %s (%s)" % (path, e))
            return default


def save(path, obj):
    """Atomik yazim: surece-ozgu `.tmp` + `os.replace`.

    Tek basina guvenlidir. Oku-degistir-yaz icin `tx()` ile sar.

    Windows notu: `os.replace` hedef dosya baska bir handle ile ACIKSA
    (okuyucu `json.load` sirasinda tutuyor olabilir) WinError 5 verir.
    Bu yuzden kisa sureli denemek yerine `REPLACE_TIMEOUT` boyunca
    tekrar deniyoruz; tamamen takili bir okuyucu yazimi en fazla bu kadar
    geciktirir, sonra hata yukarilir.
    """
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    deadline = time.time() + REPLACE_TIMEOUT
    delay = 0.02
    last = None
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last = e
            if time.time() >= deadline:
                break
            time.sleep(delay)
            delay = min(delay * 1.5, 0.25)
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise last

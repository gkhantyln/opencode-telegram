"""Markdown -> Telegram MarkdownV2 cevirisi (stdlib only).

Neden var: opencode Markdown dondurur (`**kalin**`, `` `kod` ``, ```blok```)
ve bridge her seyi duz metin gonderiyordu; Telegram'da isaretler ham gorunuyordu.

Yalnizca sik kullanilan alt kume ele alinir: kod blogu, satir ici kod, kalin,
italik, ustunu cizili, baslik, liste, alinti, baglanti. Tablo/grafik/HTML yok.

Uc kritik garanti:
  1. **Kod blogu parcalamada bolunmez.** `pack()` bloklari tam olarak paketler;
     icerik limiti asarsa blog *dilbilgisi korunarak* birden fazla bloga
     bolunur, her parca gecerli bir ```blog``` olur. Karakter sayisiyla
     kesmek Telegram'da bozuk kod blogu gosterir.
  2. **Cikti Telegram'in MarkdownV2 kacis kurallarina uyar.** ``_ * [ ] ( ) ~ `
     > # + - = | { } . ! \\`` disari kacislanir.
  3. **Zincir kacinma yolu vardir.** `render()` -> `escape_only()` ->
     `to_plain()`. Biri calismazsa siradaki denenir; hicbiri denemeden duz
     metne dusulur. Bicimleme hatasi mesaji kaybettirmemelidir.

Iki ayrica onlem:
  * Bicimleme kurallari satir basina uygulanir (`\\n` icermez), bu yuzden
    metin blogu guvenle satir sinirindan bolunebilir.
  * Yer tutucu karakteri girdide varsa (orn. model NUL yazdiysa) bozulma
    olmamasi icin metinde bulunmayan bir kod noktasi secilir.

"""

import re

PARSE_MODE = "MarkdownV2"

# Telegram MarkdownV2'de disari kacislanmasi gereken karakterler
_RESERVED = r"_*\[\]()~`>#+\-=|{}.!\\"
_ESCAPE_RE = re.compile("([%s])" % _RESERVED)

_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*([^\s`~]*)[ \t]*$")
_INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1", re.DOTALL)


# ------------------------------------------------------------------ yardimcilar

def _escape(text):
    """Kod disi metin: tum ayirici karakterleri kacisla."""
    return _ESCAPE_RE.sub(r"\\\1", text)


def _escape_code(text):
    """Kod icerigi: yalnizca ters bolu ve backtick kacislanir."""
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _fence_for(body):
    """Govde icindeki en uzun backtick dizisinden guvenli bir fence secer."""
    longest = max((len(m) for m in re.findall(r"`+", body or "")), default=0)
    return "`" * max(3, longest + 1)


def _find_close(lines, start, marker):
    """Kapanis isaretcinin satir index'i, yoksa None.

    Ayni karakterden en az marker uzunlugunda olmali ve bilgi metni
    tasimamali. Ic satirdaki ```` ```js ```` kapanis sayilmaz.
    """
    want_char, want_len = marker[0], len(marker)
    for i in range(start, len(lines)):
        m = _FENCE_OPEN_RE.match(lines[i])
        if (m and m.group(1)[0] == want_char
                and len(m.group(1)) >= want_len and not m.group(2)):
            return i
    return None


# ------------------------------------------------------------------ bloklama

def blocks(md):
    """Markdown metnini bloklara boler: [{'type','lang','text'}].

    Kapanmayan blok da 'code' sayilir: streaming sirasinda metin yarim
    olabilir ve icerik kaybolmamalidir.
    """
    if not md:
        return []
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, buf, i = [], [], 0
    while i < len(lines):
        m = _FENCE_OPEN_RE.match(lines[i])
        if m:
            if any(x.strip() for x in buf):
                out.append({"type": "text", "text": "\n".join(buf).strip("\n")})
            buf = []
            marker, lang = m.group(1), (m.group(2) or "")
            close = _find_close(lines, i + 1, marker)
            body = lines[i + 1:close if close is not None else len(lines)]
            out.append({"type": "code", "lang": lang, "text": "\n".join(body)})
            i = (close + 1) if close is not None else len(lines)
            continue
        buf.append(lines[i])
        i += 1
    if any(x.strip() for x in buf):
        out.append({"type": "text", "text": "\n".join(buf).strip("\n")})
    return out


# ------------------------------------------------------------------ render

def render_code(lang, body):
    """Kod blogu -> ```dil\\n...\\n```. Govde backtick iceriyorsa daha uzun
    isaretci kullanilir, yoksa blog erken kapanir."""
    body = (body or "").replace("\r", "")
    fence = _fence_for(body)
    return "%s%s\n%s\n%s" % (fence, lang or "", _escape_code(body), fence)


def _line_rules(escaped):
    """Kacislanmis metin uzerinde satir kurallari.

    Kacislama Once yapildigi icin kurallar kacisli halleriyle eslesir ve
    uretilen isaretler (orn. `*kalin*`) ikinci kez kacislanmaz.
    """
    out = []
    for line in escaped.split("\n"):
        line = re.sub(r"^[ \t]*(?:\\#){1,6}[ \t]+(.*)$", r"*\1*", line)   # baslik
        line = re.sub(r"^[ \t]*\\>[ \t]?(.*)$", r"> \1", line)           # alinti
        line = re.sub(r"^([ \t]*)\\-[ \t]+", "\\1• ", line)              # madde
        line = re.sub(r"^([ \t]*)\\+[ \t]+", "\\1• ", line)
        out.append(line)
    return "\n".join(out)


def _inline_rules(escaped):
    """Kacislanmis metin uzerinde baglama/bicim kurallari.

    Sirasi onemli: uclu yildiz once, kalin sonra, italik en sonda. Aksi
    halde `**b**` once italik kuralina takilip bozulur.
    Tum desenler `\\n` icermez; boylece satir sinirindan bolmek guvenli.
    """
    def _link(m):
        # URL kacislanmaz, yoksa Telegram otomatik link yapmaz.
        return "%s (%s)" % (m.group(1), m.group(2).replace("\\", ""))

    s = escaped
    s = re.sub(r"\\\[([^\n]*?)\\\]\\\(([^\n]*?)\\\)", _link, s)
    s = re.sub(r"\\\*\\\*\\\*([^\n]+?)\\\*\\\*\\\*", r"*_\1_*", s)   # ***kalin italik***
    s = re.sub(r"\\_\\_([^\n]+?)\\_\\_", r"*\1*", s)                # __kalin__
    s = re.sub(r"\\\*\\\*([^\n]+?)\\\*\\\*", r"*\1*", s)            # **kalin**
    s = re.sub(r"\\~\\~([^\n]+?)\\~\\~", r"~\1~", s)               # ~~ustu cizili~~
    s = re.sub(r"\\\*([^*\n]+?)\\\*", r"_\1_", s)                 # *italik*
    # _italik_ yalnizca kelime ici degilse: foo_bar -> foo\_bar
    # Desende `\_` yazmali: metin once kacislandigi icin alt cizgi her
    # zaman bir ters bolunun ardindan gelir. (Bakista `\` OLMAMALI.)
    s = re.sub(r"(?<!\w)\\_([^_\\\n]+?)\\_(?!\w)", r"_\1_", s)
    return s


def _pick_sentinel(text):
    """Girdide bulunmayan bir tek nokta karakteri sec (yer tutucu kaymasi)."""
    for cp in range(0xE000, 0xE010):
        ch = chr(cp)
        if ch not in text:
            return ch
    return "\x00"


def render_text(text):
    """Duz metin bolumu -> MarkdownV2.

    Once satir ici kod parcalarini gecici yer tutuculara alir, sonra kacis
    ve bicim kurallari uygulanir, en son yer tutucular geri konur. Boylece
    ``a*b`` icindeki yildiz kalin yapilmaz.
    """
    if not text:
        return ""
    s = _pick_sentinel(text)
    spans = []

    def _take(m):
        spans.append((m.group(1), m.group(2)))
        return "%s%d%s" % (s, len(spans) - 1, s)

    tmp = _INLINE_CODE_RE.sub(_take, text)
    out = _inline_rules(_line_rules(_escape(tmp)))
    for n, (fence, raw) in enumerate(spans):
        out = out.replace("%s%d%s" % (s, n, s), "%s%s%s" % (fence, _escape_code(raw), fence))
    return out


def render(md):
    """Tum belge -> MarkdownV2."""
    if not md:
        return ""
    parts = []
    for b in blocks(md):
        parts.append(render_code(b["lang"], b["text"]) if b["type"] == "code"
                     else render_text(b["text"]))
    return "\n\n".join(p for p in parts if p)


# ------------------------------------------------------------------ geri donus

def escape_only(md):
    """Bicimlemesiz MarkdownV2: isaretler duz metin olarak gorunur.

    `render()` bir parse hatasina yol acarsa bu kullanilir. Kod bloklari
    korunur; duz metinde yalnizca kacis uygulanir.
    """
    if not md:
        return ""
    parts = []
    for b in blocks(md):
        parts.append(render_code(b["lang"], b["text"]) if b["type"] == "code"
                     else _escape(b["text"]))
    return "\n\n".join(p for p in parts if p)


_STRIP_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_STRIP_H_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_STRIP_BULLET_RE = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
_STRIP_QUOTE_RE = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)
_STRIP_MD_RE = re.compile(r"(\*\*\*|\*\*|__|~~|\*|_|`)")


def to_plain(md):
    """Son care: isaretleri soyulmus duz metin."""
    if not md:
        return ""
    parts = []
    for b in blocks(md):
        if b["type"] == "code":
            parts.append(b["text"])
            continue
        t = _STRIP_LINK_RE.sub(r"\1 (\2)", b["text"])
        t = _STRIP_QUOTE_RE.sub("", t)
        t = _STRIP_BULLET_RE.sub("• ", t)
        t = _STRIP_H_RE.sub("", t)
        parts.append(_STRIP_MD_RE.sub("", t))
    return "\n\n".join(p for p in parts if p)


def unescape(text):
    """Cevirilmis metni kabaca duz metne indirger (son care gonderim icin).

    Gorunur icerik korunur; bicimleme isaretleri geri gelir.
    """
    if not text:
        return ""
    return re.sub(r"\\([_*\[\]()~`>#+\-=|{}.!\\])", r"\1", text)


# ------------------------------------------------------------------ parcalama

def _group_lines(lines, limit):
    """Satirlari limiti asmadan sirayla birlestirir.

    Tek satir limiti asiyorsa karakterden keser; metin blogu icin bu
    guvenlidir (bicimleme kurallari satir basina).
    """
    out, cur, size = [], [], 0
    for line in lines:
        add = len(line) + (1 if cur else 0)
        if cur and size + add > limit:
            out.append("\n".join(cur))
            cur, size = [line], len(line)
        else:
            cur.append(line)
            size += add
        while len(cur[-1]) > limit:
            out.append(cur[-1][:limit])
            cur[-1] = cur[-1][limit:]
        size = sum(len(x) for x in cur) + max(0, len(cur) - 1)
    if cur:
        out.append("\n".join(cur))
    return [g for g in out if g] or [""]


def _code_pieces(block, limit):
    """Kod blogunu parcalara bol; her parca TAM ve gecerli bir blog."""
    whole = render_code(block["lang"], block["text"])
    if len(whole) <= limit:
        return [whole]
    fence = _fence_for(block["text"])
    room = max(32, limit - (2 * len(fence) + len(block["lang"] or "") + 2))
    parts, cur, size = [], [], 0
    for line in (block["text"] or "").replace("\r", "").split("\n"):
        add = len(line) + (1 if cur else 0)
        if cur and size + add > room:
            parts.append("\n".join(cur))
            cur, size = [line], len(line)
        else:
            cur.append(line)
            size += add
        while len(cur[-1]) > room:
            parts.append(cur[-1][:room])
            cur[-1] = cur[-1][room:]
        size = sum(len(x) for x in cur) + max(0, len(cur) - 1)
    if cur:
        parts.append("\n".join(cur))
    return [render_code(block["lang"], p) for p in parts if p.strip()] or [whole]


def pack(md, limit=4000):
    """MarkdownV2 mesaj listesine boler.

    Donus: [{'text':..., 'parse_mode':'MarkdownV2'}]

    Bloklar ATOMIKTIR: bir kod blogu limiti astiginda karakter sayisiyla
    kesilmez; satirlara gore bolunur ve her parca gecerli bir ```blog```
    olarak kalir. Karakter sayisiyla kesmek Telegram'da bozuk kod gosterir.
    """
    if not md:
        return []
    limit = max(120, int(limit))
    out, cur = [], []

    def _flush():
        if cur:
            out.append({"text": "\n\n".join(cur), "parse_mode": PARSE_MODE})
            del cur[:]

    for b in blocks(md):
        if b["type"] == "code":
            piece = _code_pieces(b, limit)
        else:
            piece = _group_lines(render_text(b["text"]).split("\n"), limit)
        for p in piece:
            if cur and len(p) + 2 + sum(len(x) + 2 for x in cur) > limit:
                _flush()
            cur.append(p)
    _flush()
    return out

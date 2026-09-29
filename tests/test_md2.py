"""bridge/md2.py testleri (ag/model gerektirmez).

Markdown -> Telegram MarkdownV2 cevirisi. Iki kritik garanti test edilir:
  1. Kod blogu parcalama sirasinda ASLA bolunmez (dilbelirteci ve ``` korunur).
  2. Cevirilen metin Telegram'in MarkdownV2 kacis kurallarina uyar.
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bridge")))
import md2  # noqa: E402


# ---------------------------------------------------------------- blok sinirlari

def test_block_types_split_fence():
    md = "aciklama\n\n```py\nprint(1)\n```\n\nkapanis"
    assert [b["type"] for b in md2.blocks(md)] == ["text", "code", "text"]


def test_block_types_no_fence():
    assert [b["type"] for b in md2.blocks("sadece metin")] == ["text"]


def test_code_block_keeps_language_and_body():
    b = md2.blocks("```python\nx = 1\ny = 2\n```")[0]
    assert b["lang"] == "python"
    assert b["text"] == "x = 1\ny = 2"


def test_tilde_fence():
    b = md2.blocks("~~~\nkod\n~~~")[0]
    assert b["type"] == "code"
    assert b["text"] == "kod"


def test_fence_with_more_backticks_inside():
    """Ic satirindaki ```` ```js ```` kapanis sayilmaz (bilgi metni tasir)."""
    md = "```md\n```js\nic satir\n```"
    blocks = md2.blocks(md)
    assert len(blocks) == 1, blocks
    assert blocks[0]["type"] == "code"
    assert blocks[0]["lang"] == "md"
    assert blocks[0]["text"] == "```js\nic satir"


def test_unterminated_fence_is_still_code():
    """Yarim blog (streaming sirasinda olur) icerigi kaybettirmemeli."""
    b = md2.blocks("```py\nprint(1)")[0]
    assert b["type"] == "code"
    assert b["text"] == "print(1)"


def test_empty_input():
    assert md2.blocks("") == []
    assert md2.render("") == ""
    assert md2.render(None) == ""


# ---------------------------------------------------------------- kacis kurallari

def test_reserved_chars_escaped():
    # Telegram MarkdownV2'de nokta, tire, unlem, = , | , { } kacislanir
    out = md2.render_text("a.b-c!d=e|f{g}h")
    assert out == "a\\.b\\-c\\!d\\=e\\|f\\{g\\}h"


def test_backslash_escaped():
    assert md2.render_text("a\\b") == "a\\\\b"


def test_reserved_chars_inside_code_are_NOT_escaped():
    """Kod icinde sadece \\ ve ` kacislanir; digerleri oldugu gibi kalir."""
    out = md2.render_code("py", "if a.b != c: print('x-y!z')")
    assert out == "```py\nif a.b != c: print('x-y!z')\n```"


def test_backtick_inside_code_is_escaped():
    out = md2.render_code("", "a`b")
    assert "a\\`b" in out


def test_backslash_inside_code_is_escaped():
    out = md2.render_code("", "a\\b")
    assert "a\\\\b" in out


def test_fence_uses_longer_marker_when_body_has_backticks():
    """Govde uc tane backtick iceriyorsa daha uzun isaretci kullanilir."""
    out = md2.render_code("", "a```b")
    assert out.startswith("````")
    assert out.endswith("````")


# ---------------------------------------------------------------- bicimleme

def test_bold_double_star():
    assert md2.render_text("**kalin**") == "*kalin*"


def test_bold_double_underscore():
    assert md2.render_text("__kalin__") == "*kalin*"


def test_italic_single_star():
    assert md2.render_text("*italik*") == "_italik_"


def test_italic_single_underscore():
    assert md2.render_text("_italik_") == "_italik_"


def test_bold_and_italic_together():
    assert md2.render_text("**b** ve *i*") == "*b* ve _i_"


def test_bold_italic_triple():
    assert md2.render_text("***ikisi***") == "*_ikisi_*"


def test_strikethrough():
    assert md2.render_text("~~sil~~") == "~sil~"


def test_heading_becomes_bold_line():
    assert md2.render_text("# Baslik") == "*Baslik*"
    assert md2.render_text("### Derin") == "*Derin*"


def test_unordered_list_bullet():
    out = md2.render_text("- bir\n- iki")
    assert out.count("•") == 2
    assert "-" not in out


def test_inline_code_protects_formatting():
    """`a*b` icindeki yildiz kalin yapilmamali."""
    assert md2.render_text("kullan `a*b` burada") == "kullan `a*b` burada"


def test_inline_code_escapes_its_own_backtick_content():
    assert md2.render_text("`x\\y`") == "`x\\\\y`"


def test_double_backtick_span():
    """Ic ice backtick iceren span: fence korunur, ic backtick kacislanir.

    Telegram MarkdownV2'de kod iceriginde hem `\\` hem '`' kacislanmak
    ZORUNDADIR - acikca belgelenmis kural.
    """
    assert md2.render_text("``a`b``") == "``a\\`b``"


def test_underscore_inside_word_not_italic():
    """foo_bar degistirilmemeli (Markdown'da alt cizgi kelime ici tirnak degil)."""
    out = md2.render_text("foo_bar")
    assert out == "foo\\_bar"


def test_link_becomes_text_and_bare_url():
    out = md2.render_text("bkz [opencode](https://opencode.ai/docs)")
    assert out == "bkz opencode (https://opencode.ai/docs)"


def test_url_is_not_escaped():
    """URL'de nokta kacislanirsa Telegram otomatik link yapmaz."""
    out = md2.render_text("[x](https://a.com/b?c=1)")
    assert "https://a.com/b?c=1" in out


def test_blockquote():
    assert md2.render_text("> alinti") == "> alinti"


def test_newlines_preserved():
    assert md2.render_text("a\nb") == "a\nb"


# ---------------------------------------------------------------- birlestik render

def test_render_full_document():
    md = "# Baslik\n\nMetin **kalin**.\n\n```py\nprint('x')\n```\n\nSon."
    out = md2.render(md)
    assert out.startswith("*Baslik*")
    assert "```py\nprint('x')\n```" in out
    assert "*kalin*" in out
    assert "Metin *kalin*\\." in out, "nokta MarkdownV2'de kacislanir"
    assert "Son\\." in out


def test_render_never_leaves_raw_reserved_chars_outside_code():
    """Metin bolumunde hicbir ayirici karakter kacislanmamis kalmamali."""
    assert md2.render("Bir cumle. Ikinci! Ucuncu?") == "Bir cumle\\. Ikinci\\! Ucuncu?"


def test_question_mark_is_not_escaped():
    """Telegram'in MarkdownV2 kacis listesinde '?' YOK. Kacislamak
    gerekmiyor; cift kacis birakmamak icin listeye eklenmedi."""
    assert md2.render_text("neden?") == "neden?"


# ---------------------------------------------------------------- parcalama (en kritik)

def test_pack_short_text_single_chunk():
    chunks = md2.pack("kisa metin", limit=4000)
    assert len(chunks) == 1
    assert chunks[0]["parse_mode"] == "MarkdownV2"


def test_pack_respects_limit():
    md = "\n\n".join("Paragraf %d %s" % (i, "x" * 100) for i in range(200))
    for c in md2.pack(md, limit=4000):
        assert len(c["text"]) <= 4000, len(c["text"])


def test_pack_never_splits_code_block():
    """En kritik garanti: 9000 karakterlik kod blogu kirpilir ama
    her parca kendi basina TAM ve GECERLI bir blok olur."""
    body = "\n".join("satir %04d %s" % (i, "y" * 40) for i in range(300))
    md = "```py\n%s\n```" % body
    chunks = md2.pack(md, limit=4000)
    assert len(chunks) > 1, "bu kadar icerik tek mesaja sigmamali"
    for c in chunks:
        assert len(c["text"]) <= 4000
        assert c["text"].count("```") == 2, "acik/kapanis isareti eksik: %r" % c["text"][:80]
        assert c["text"].startswith("```py")
        assert c["text"].endswith("```")


def test_pack_no_content_loss():
    body = "\n".join("satir %04d" % i for i in range(500))
    md = "```py\n%s\n```" % body
    joined = "\n".join(c["text"] for c in md2.pack(md, limit=4000))
    for i in (0, 100, 250, 499):
        assert "satir %04d" % i in joined, "satir %d kayboldu" % i


def test_pack_keeps_fence_next_to_its_text():
    md = "aciklama metni\n\n```sh\nls -la\n```\n\nkapanis"
    chunks = md2.pack(md, limit=4000)
    assert len(chunks) == 1
    assert "aciklama metni" in chunks[0]["text"]
    assert "ls -la" in chunks[0]["text"]
    assert "kapanis" in chunks[0]["text"]


def test_pack_empty():
    assert md2.pack("") == []


# ---------------------------------------------------------------- geri donus zinciri

def test_escape_only_keeps_no_formatting():
    """Ceviri basarisiz olursa kullanilan ikinci kademe: duz metin olarak
    MarkdownV2'ye kacisli, bicimlemesiz. Satir ici kod isaretleri de
    kacislanir, yani gorunen metin degismez."""
    assert md2.escape_only("**kalin** ve `kod`") == "\\*\\*kalin\\*\\* ve \\`kod\\`"


def test_escape_only_escapes_code_fences():
    out = md2.escape_only("```py\nx=1\n```")
    assert "```" in out, "fence korunmali"
    assert "\\." not in out


def test_to_plain_strips_markers():
    out = md2.to_plain("# Baslik\n\n**kalin** metin\n\n```py\nprint(1)\n```")
    assert "Baslik" in out
    assert "kalin" in out or "kalin" in out
    assert "```" not in out
    assert "**" not in out
    assert "print(1)" in out


def test_to_plain_keeps_code_body():
    out = md2.to_plain("```py\nprint('a*b')\n```")
    assert "print('a*b')" in out


def test_to_plain_strips_links_to_text_and_url():
    out = md2.to_plain("bkz [ac](https://a.com)")
    assert "ac" in out and "https://a.com" in out
    assert "[" not in out and "]" not in out


# ---------------------------------------------------------------- guvenlik

def test_placeholder_collision_does_not_corrupt():
    """Girdi metni icinde yer tutucu benzeri karakter varsa sonuc bozulmamali.

    Cevirici NUL iceren metin secilirse yer tutucu olarak baska bir kod
    noktasi kullanir; hicbiri yoksa NUL'a duser. Nihayetinde girdideki
    karakter korunur ve bicimleme yine calisir.
    """
    out = md2.render_text("deger \x001\x00 burada **kalin**")
    assert "\x001\x00" in out, "girdideki karakter korunmali"
    assert "*kalin*" in out, "bicimleme bozulmamali"
    assert not any(0xE000 <= ord(c) <= 0xE00F for c in out), "yer tutucu sizmis"


def test_render_never_raises_on_junk():
    for junk in ("", "\x00", "```", "~~~~", "**", "|||", "\\", "`" * 50,
                 "#" * 100, "-" * 200, "> " * 50, "[]()", "\n" * 50):
        md2.render(junk)
        md2.to_plain(junk)
        md2.pack(junk, limit=50)


def test_fence_in_code_body_not_confused_by_indent():
    """CommonMark: kapanis fence en fazla 3 boslukla girintilenebilir.
    4 bosluk girintili ``` kapanis DEGILDIR, govdeye aittir."""
    b = md2.blocks("```\n    ```\n    ic satir\n```")[0]
    assert b["type"] == "code"
    assert "    ```" in b["text"]
    assert "    ic satir" in b["text"]


def test_two_space_indent_does_close_fence():
    """CommonMark'a gore 2 bosluk gecerli kapanistir."""
    b = md2.blocks("```\n  ic satir\n  ```\nkalan")[0]
    assert b["text"] == "  ic satir"


def test_pack_limit_smaller_than_code_line():
    """Cok uzun tek satirlik kod satiri da kirpilabilmeli."""
    md = "```\n%s\n```" % ("z" * 9000)
    chunks = md2.pack(md, limit=500)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["text"]) <= 500
        assert c["text"].count("```") == 2

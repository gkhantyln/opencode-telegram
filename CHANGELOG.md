# Changelog — opencode-telegram

## Unreleased (2.2.0)

v0.26.1 incelemesi; alinan davranislar stdlib ile yeniden yazildi.
Testler 45 -> 70.

### Eklendi

- **Telegram komut menusu otomatik kuruluyor.** Bridge acilista
  `setMyCommands` cagirir. Once kullanici BotFather'a elle komut girmesi
  gerekiyordu (`docs/KURULUM.md`) ve cogu kullanici yapmiyordu -> `/`
  menusu bos kaliyordu. Kapatmak icin `TELEGRAM_SET_COMMANDS=0`.
- **Kademeli streaming throttle.** Sabit 2 sn yerine sureye gore kademe:
  <1 dk 1 sn, <5 dk 2 sn, <15 dk 5 sn, sonrasi 10 sn. Onceden 15
  dakikalik bir is 450 gereksiz `editMessageText` cagiriyordu.
- **Izin/soru butonlarinda iptal.** Soru menulerine "Vazgec (is
  durdurulsun)" butonu eklendi; once yalnizca secenekler vardi ve
  kullanici 30 dk bekleyip `/abort` atmak zorundaydi.
- `TELEGRAM_API_MAX_RETRIES` ayari (varsayilan 3, 0 = hic tekrar yok).

### Duzeltilen (hata)

- **429 ve gecici 5xx tekrar denemiyordu.** `api()` tek `urlopen`
  cagirip hatayi yutuyordu; Telegram rate limit'i (ve ara sira 502)
  dogrudan cevabin sessizce kaybolmasina yol aciyordu. Artik
  `parameters.retry_after` okunuyor, ustel backoff uygulaniyor.
  400/401/403/404 ve **409** (baska bridge) kasitli olarak tekrar
  edilmiyor.
- **Bayat buton korumasi.** 30 dk boyunca eski bir izin mesajindaki
  buton tiklanabiliyor ve yanlis oturuma onay verebiliyordu. Artik
  butonun ait oldugu mesajin kimligi beklenenle eslesmezse islem
  yapilmaz.
- **Butonlar tek satira diziliyordu.** 8 secenekli bir soruda Telegram
  8 butonu bir satira sigdiromaya calisiyordu. Artik 2 sutunlu izgara
  + bosluk birakmadan dizilim.
- `callback_data` 64 bayt limiti asilirsa Telegram mesaji sessizce
  reddediyordu; artik kirpilir.

## Unreleased (2.1.0)

Dogrulanmis kod incelemesi sonrasi P0/P1 duzeltmeleri. Detaylar: `GAP-PLAN.md`.

### Duzeltilen (hata)

- **Pipe kilitlenmesi (`_wait_proc`).** stdout/stderr PIPE'lari hic okunmuyordu;
  opencode ~64 KB yazdiginda surec yazmada kilitleniyor, hic cikmiyor ve bridge
  `OPENCODE_TIMEOUT` (600 sn) dolana kadar asiliyordu; sonra cevap tamamen
  kayboluyordu. Artik pipe'lar ayri thread'lerde bosaltiliyor. Regression testi:
  `test_wait_proc_drains_pipes` (onceki surumde 20 sn sonra hala kilitli).
- **Mailbox JSON yaris durumu.** `_save` sabit isimli `.tmp` kullaniyordu; es
  zamanli yazicilar (worker + poll loop + ayri surecteki MCP server) birbirinin
  yazmasini eziyor, `os.replace` WinError 5 veriyor, sessiz veri kaybi ve
  bozuk JSON olusuyordu. Ortak `bridge/atomic_json.py` moduli: surece-ozgu
  `.tmp`, thread + dosya kilidi (`tx()`), yeniden girilebilir bolge.
- **Outbox cift gonderim.** `outbox_flush()` uc yerden cagrildigi icin ayni
  `QUEUED` kayit iki kez Telegram'a gidebiliyordu. Artik `SENDING` ile
  sahipleniliyor, ag islemi kilit disinda, sonuc yine kilit icinde yaziliyor.
  `outbox_recover()` crash sonrasi takili kalan kayitlari kurtariyor.
- **`edited_message` mukerrer tetikleme.** Mesaj duzenlemesi ayni isi ikinci
  kez baslatiyordu (mukerrer is, mukerrer token, thread'de cift cevap).
  Artik varsayilan olarak yok sayiliyor; `TELEGRAM_FOLLOW_EDITS=1` ile
  acilabiliyor ve ayni duzenleme yine bir kez isleniyor.
- **serve backend'de 4000 karakter kesme.** Uzun cevabin tamami kayboluyordu;
  tasma artik parca parca gonderiliyor (cli backend ile tutarli).
- **`tg_files` hic temizlenmiyordu.** Her gonderilen ek kalici olarak birikiyordu.
  Artik yas (varsayilan 6 saat) ve adet (varsayilan 50) limiti var.
- **Windows'ta okuma yarisi.** `os.replace` acik hedefe yazamadigi icin kisa
  sureli `PermissionError` "bozuk JSON" sanilip `default` donuyordu — yani
  sessiz veri kaybi. Artik gecici hata tekrar deniyor, gercek bozulma ise
  stderr'a uyari olarak yaziliyor.
- **Surum tutarsizligi.** Bridge "v1.5", MCP "1.0.0", CHANGELOG "2.0" diyordu.
  Artik tek kaynak: `VERSION` dosyasi.
- **Kod tekrani.** `_serve_clients` cift tanim, `_check_dangerous` cift cagri,
  serve oturum kurtarma recursion'inda kayan `reply_to`.
- `telegram_broadcast` 20000 karakter sinirini ve kuyruk budamasini
  `telegram_send` ile ayni yapti; `TELEGRAM_KURULUM.md` atfi `docs/KURULUM.md`
  olarak duzeltildi.

### Eklendi

- `.gitignore`: `.env` (bot token), mailbox, audit/debug loglari, indirilen
  ekler ve `GAP-PLAN.md` artik commit edilemiyor. CI bunu dogrular.
- `bridge/atomic_json.py`: islem-semantik JSON deposu (bridge + MCP ortak).
- `VERSION`: tek surum kaynagi; `bridge.py --selftest` surumu yaziyor.
- CI (`.github/workflows/test.yml`): Windows + Linux, pytest + `py_compile` +
  sizinti kontrolu + surum kontrolu.
- Yeni testler: pipe bosaltma, abort/timeout, es zamanli inbox/outbox yazimi,
  bozuk JSON korumasi, surecler arasi kilit, duzenleme yoksayma, parcala
  gonderim, `tg_files` temizligi, surum tutarliligi. **45 test yesil** (29'dan).

## 2.0 (serve backend + FAZ1/3)
- Serve backend (`TELEGRAM_BACKEND=serve`): sicak oturum, SSE streaming
  (duzenlenen mesaj), izin butonlari (once/always/reject), soru butonlari,
  API abort/reset/status. `bridge/serve_client.py` (stdlib).
- Secret maskeleme (giden tum metin), audit log (JSONL + rotasyon).
- Olum bildirimi + winsw servis docs (`docs/SERVIS.md`).
- Maliyet freni (`TELEGRAM_DAILY_TOKEN_LIMIT`).
- pytest: 29 test yesil. Zamanlayici, multi-project (`/project`),
  digest modu, sesli mesaj destegi.

## 1.5 (team-template ici)
- Guvenlik kapisi (DANGER_PATTERNS + HOLD-xxx /onay akisi).
- Tek-ornek kilidi, outbox retry (4 deneme), acilis pingi.
- Reply threading, uzun-is progress pingi (3 dk).
- /abort, /reset, /model (chat bazinda), fotograf/belge + album destegi.
- Model override (TELEGRAM_OPENCODE_MODEL), dogrudan opencode.exe cagrisi.

## 1.3 ve oncesi
- MCP + bridge + oturum esleme + kurulum docs.

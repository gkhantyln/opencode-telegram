# Changelog — opencode-telegram

## Unreleased (2.4.0)

Guven, yetki, arayuz ve temizlik paketi. Testler 174 -> 250.

### Guven

- **Kopru artik sessizce olmuyor.** `main_loop` her seyi yutup `continue` ediyordu,
  dolayisiyla `__main__`'e hicbir sey kacmiyordu ve `_notify_death` olu koddu. Donmus
  bir kopru Telegram'dan hicbir sey bildirmiyordu. Artik ardisik hatalar sayilir
  (`TELEGRAM_LOOP_MAX_ERRORS`, varsayilan 5), basarili bir tur sayaci sifirlar,
  esik de Telegram'a bildirim gonderilir ve surec `exit 4` ile temizce kapanir.
  Bekleme suresi de ustel (5sn -> 30sn).
- **Kredisi biten saglayiciya istek atilmaz.** Canli testte `orcarouter` kredisinin
  bittigini fark etmeden iki mesaj gonderilmisti. Artik kredi/kota hatalari
  (`out of credits`, `insufficient_quota`, `402`, `billing limit`...) taninir,
  cevaba kullanim ipucu eklenir, saglayici isaretlenir ve o saglayiciyla YENI istek
  calistirilmaz (6 saat, `TELEGRAM_BAD_PROVIDER_TTL_H`).
- **Olmus serve istemcisi cache'de kalmiyordu.** `serve` olunce (ya da portu baska
  bir surec kaparsa) kopru kalici olarak kilitleniyor, kurtarmak icin elle yeniden
  baslatmak gerekiyordu. Artik cache'lenen istemci her kullanimda saglik kontrolunden
  gecer, oluyse dusurulur ve yeniden kurulur. `main_loop` cikista
  `_serve_stop()` ile bizim baslattigimiz serve surecini de kapatir.

### Yetki

- **Grup yetki modeli duzeltildi.** Metin mesajlari chat_id ile, inline butonlar
  user_id ile kontrol ediliyordu ve ikisi de ayni listeye bakiyordu: grupta metin
  gonderen HERKES makineyi kullanabiliyor, izin/soru butonlari ise hic calismiyordu.
  Artik `TELEGRAM_ALLOWED_CHAT_IDS` + `TELEGRAM_ALLOWED_USER_IDS` ayrimi var.
  Ozel sohbette (chat id = user id) eski davranis aynen korunur; grupta kullanici
  listesi zorunludur, bossa kimse girmez.

### Arayuz

- **Mesaj kuyrugu.** Mesgulken gelen mesaj reddediliyordu ("Halen bir is
  calisiyor"), kullanici telifte gonderip unutuyordu. Artik kuyruga alinir, is
  bitince sirayla calisir. `/kuyruk` bekleyeni gosterir, `/kuyruk temizle` bosaltir.
  Derinlik `TELEGRAM_MAX_QUEUED` (varsayilan 5). Kuyruk bosalirken temizlenirse
  dongu kirilir; geri cekilen isler arka arkaya calismaz.
- **Alt klavye (`/klavye`).** Bilerek KAPALI baslar: kalici klavye yazan alanin
  hemen ustune yerlesir ve normal sohbet gibi yazmak isteyenleri engeller.
  `/klavye` acar, `/klavye kapat` kapatir. Iki satir, alti buton (Durum, Model,
  Oturum, Kuyruk, Yeni, Durdur). Butona basilinca metin ajana GONDERILMEZ,
  once komuta donusur.

### Eklendi

- `scripts/uninstall-global.ps1` + `.sh`: global MCP kaydini ve steering
  talimatini kaldirir. Kullanicinin diger MCP'leri korunur, yedek alinir.
- `tests/test_serve_client.py`: 31 test. `serve_client.py` hic testi yoktu
  (GAP-22) - istek/yanit bicimi, hata siniflari, `wait_reply` zaman asimi ve
  SSE ayristirma kaplandi.
- `TELEGRAM_MODEL_PAGE_SIZE`, `TELEGRAM_SESSION_PAGE_SIZE`,
  `TELEGRAM_MAX_QUEUED`, `TELEGRAM_LOOP_MAX_ERRORS`,
  `TELEGRAM_BAD_PROVIDER_TTL_H`, `TELEGRAM_ALLOWED_USER_IDS`.

### Duzeltilen (temizlik)

- Dokuman bayatligi: `docs/KURULUM.md` "opencode.json acik gelir" yalanini
  duzeltti, `steering/telegram-ops.md` var olmayan `TELEGRAM-KURULUM.md` ve
  `mail_send` referanslarini temizledi.
- `_save_sess` parametre imzasi: `None` hem "dokunma" hem "temizle" anlamina
  geliyordu. Artik `_UNSET` sentinel'i var.

## Unreleased (2.3.0)

Markdown gonderimi. Testler 70 -> 133 (`bridge/md2.py` + 49 yeni test).

### Eklendi

- **Cevaplar MarkdownV2 olarak bicimlenir.** opencode `**kalin**`,
  `_italik_`, `~~ustu cizili~~`, `` `kod` `` ve ```blok``` uretir; once
  hepsi duz metin gonderildigi icin Telegram'da isaretler ham gorunuyordu.
  Yeni `bridge/md2.py`: kod blogu, satir ici kod, baslik, liste, alinti,
  baglanti. 49 test.
- **Kod blogu parcalamada bolunmez.** Bloklar atomik paketlenir; limiti
  asan blog satirlara gore bolunur ve her parca gecerli bir ```blog```
  olarak kalir. Karakter sayisiyla kesmek Telegram'da bozuk kod gosterir.
- Bicimleme hatasi **mesaji kaybettirmez**: Telegram 400 + parse hatasi
  dondugunde ayni icerik `parse_mode`suz tekrar gonderilir.
- `TELEGRAM_MARKDOWN=0` ile tamamen kapatilabilir.

### Duzeltilen (hata)

- **Ayni icerik iki kez gonderilebiliyordu.** Telegram duzenlemeyi
  "message is not modified" ile 400 reddediyor; bu bir basarisizlik
  sanilip ayni cevap yeni mesaj olarak tekrar gonderiliyordu.
  Artik hedef duruma ulasildi sayiliyor.
- **HTTPError govdesi iki kez okunamiyordu.** Govde yalnizca bir kez
  okunabiliyor; "once parse hatasi mi, sonra not-modified mi" diye iki
  karar verildiginde ikincisi bos donuyordu. Govde istisnaya cache'leniyor.
- Streaming sirasindaki ara guncellemeler duz metin kalmaya devam ediyor
  (metin yarim olabilir, MarkdownV2'ye cevirmek bozuk blog uretirdi);
  yalnizca final mesaj bicimleniyor.

## Unreleased (2.2.0)

Arayuz ve API dayanikligi turu. Alinan davranislar stdlib ile yeniden yazildi.
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

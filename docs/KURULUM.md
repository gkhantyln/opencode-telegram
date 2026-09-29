# Telegram ile Tam Kontrol — Kurulum

PC basinda degilken Telegram'dan opencode'u yonetmek icin: soru sor, `/durum` al,
task ver, `/onay` ile devam ettir. Mimari: Telegram bot + `bridge.py` (daemon) +
`telegram` MCP + `telegram-ops.md` disiplini.

## On sart

- PC basinda degilken calisir; PC kapaliysa calismaz. Baska bir makinede
  calistirmak icin bu bolumun sonundaki VPS notuna bakin.
- Python 3.10+ ve `opencode` CLI PATH'te (`opencode --version`).
- Bu repo TEK BASINA durur: `opencode.json` icinde hazir bir `telegram`
  kaydi **gelmez** (eski surumden kalan bir aciklamaydi). Kaydi kurulum
  adiminda biz yaziyoruz; bkz. Asagida "Global kurulum".

## Adim 1 — Bot ac

1. Telegram'da **BotFather**'a `/newbot` yaz, isim ver, token'i kopyala.
2. Botunu bulup `/start` yaz (bir mesaj sart — yoksa bot sana yazamaz).

## Adim 2 — .env doldur

```powershell
copy .env.example .env
notepad .env
```

```
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_DEFAULT_CHAT_ID=
```

`ALLOWED` ve `DEFAULT` henuz bos kalabilir.

## Adim 3 — Chat ID'ni ogren

```powershell
python bridge/bridge.py --selftest
```

`ALLOWED: BOS` uyarisi normal. Simdi bridge'i baslat:

```powershell
.\scripts\start-bridge.ps1
```

Botuna Telegram'dan bir sey yaz. Bridge log'da sunu goreceksin:

```
DENIED chat=987654321 user=@sen ...
```

`987654321` senin chat id'indir. Bridge'i durdur (Ctrl+C), `.env`'ye yaz:

```
TELEGRAM_ALLOWED_CHAT_IDS=987654321
TELEGRAM_DEFAULT_CHAT_ID=987654321
```

## Adim 4 — Baslat ve test et

```powershell
.\scripts\start-bridge.ps1
```

Telegram'dan:

1. `/yardim` — komut listesi gelmeli.
2. `/durum` — SESSION_STATE + TODO ozeti gelmeli.
3. `merhaba, proje dizinindeki README'yi 3 cumleyle ozetle` — orchestrator calisip cevap vermeli.
   Ilk calisma yavas olabilir (model + MCP acilisi).

opencode TUI'yi de yeniden baslat (MCP listesi acilista okunur), sonra iceriden test et:

```
telegram_status
telegram_send(text="Bridge testi — TUI'dan selam")
```

Telegram'a mesaj gelmeli.

## Gunluk kullanim

| Telegram | Karsiligi |
|---|---|
| duz yazi | orchestrator'a soru/gorev (esli oturumda devam eder) |
| fotograf / belge | indirilir, `opencode run --file` ile inceletilir (albumler toplu) |
| `/sor <metin>` | ayni |
| `/durum` | SESSION_STATE + TODO + Telegram oturumu (model, son token kullanim) |
| `/roster` | agent canlilik |
| `/gelen` | okunmamis team-mailbox |
| `/onay <metin>` | orchestrator kuyruguna not (yikici islerde onay icin) |
| `/onay HOLD-xxx` | bekleyen yikici isi onayla ve calistir (30 dk gecerli) |
| `/abort` | calisan isi durdur |
| `/reset` | Telegram oturumunu sifirla (oturumu siler, sonraki soru yeniler) |
| `/model` / `/model list` / `/model set <p/m>` | chat bazinda model gor/listele/degistir |
| `/yardim` | liste |

Ornek: `PROJ-004 login API'yi bitir, testleri calistir, sonucu yaz` — bridge yeni bir
opencode oturumu acar (basligi `Telegram tg-<chat>` olur, ID `telegram_sessions.json`'a
eslenir), bitince cevabi atar. Sonraki sorularin ayni oturumda devam eder. Uzun surerse
bridge log'da bekler; ara durum icin `/durum` yaz.

Otomatik bildirim de var: TUI'da calisan orchestrator task bitince/hataya dununce
`telegram_send` ile sana yazar (bridge outbox'i dagitir).

## PC acilisinda otomatik baslat

```powershell
schtasks /create /tn TelegramBridge /tr "<tam-yol>\scripts\start-bridge.bat" /sc onlogon
```

veya `start-bridge.bat`'a sag tiklayip Startup klasorune kisayol birak.

## PC kapaliyken de calissin istiyorsan (VPS)

Bridge baska makinede de calisabilir — tek sart: `TELEGRAM_PROJECT_DIR` o makinedeki
repo klonu, `opencode` kurulu ve girisli olmali. Kodu kopyala, `.env`'yi doldur,
calistir. Ekstra port acmaya gerek yok (bridge Telegram'a outbound baglanir).

## Guvenlik (oku)

- Izinli chat == PC'de opencode yetkisi. `ALLOWED` listesine baskasini ekleme.
- Token'i kimseyle paylasma, `.env` git'e girmez (`.gitignore` kontrol et).
- Yikici islem kapisi: `rm -rf`, `terraform destroy/apply`, `kubectl delete`,
  `DROP TABLE`, `git push --force` gibi kalıplar otomatik durdurulur; devam
  etmek icin `/onay HOLD-xxx` gerekir (30 dk gecerli). Kalip listesi
  `bridge.py` icinde `DANGER_PATTERNS` — ihtiyaca gore genislet.
- Daha kismi yetki istersen `TELEGRAM_OPENCODE_AGENT` ile baska bir ajan sec
  (orn. salt-okunur yetkili ozel ajan); varsayilan `orchestrator` tam yetkilidir.
- Ayni makinede tek bridge calisir (kilid dosyasi engeller); ikinci baslatma
  `PID ...` hatasiyla cikar. Farkli makinede (VPS+PC) cift calistirma 409 verir —
  log artik bunu acikca yazar.
- Bridge acilinca varsayilan chat'e "Kopru acildi" pingu gelir; gelmediyse
  bridge ayaga kalkmamis demektir.
- `telegram-ops.md` kurali: secret/log/token Telegram'a gonderilmez.
- **Izin ve soru butonlari.** Bir izin/soru mesaji belirli bir sohbete ait;
  buton tiklandiginda mesajin kimligi beklenenle eslesmezse islem yapilmaz
  ("bu mesaj artik guncel degil"). Soru menulerinde "Vazgec" butonu isi
  durdurur; yanlis bir onay vermek istemiyorsaniz onu kullanin.

## BotFather komut listesi (opsiyonel)

Bridge acilista `setMyCommands` ile Telegram'in `/` menusunu kendisi doldurur
(`TELEGRAM_SET_COMMANDS=0` ile kapatilir). Elle girmek istersen
**BotFather -> `/setcommands`**:

```
yardim - Komut listesi
durum - Proje, oturum ve model durumu
sor - Orchestrator'a soru veya gorev gonder
onay - Onay / not birak
abort - Calisan isi durdur
reset - Telegram oturumunu sifirla
model - Model gor / listele / degistir
project - Proje listele / sec
roster - Agent canlilik durumu
gelen - Okunmamis team-mailbox
```

## Sorun giderme

| Belirti | Bakilacak |
|---|---|
| Bot cevap vermiyor | bridge log'da DENIED mi? `ALLOWED` yanlis olabilir. Token yanlissa `loop hatasi: 401` gorursun. |
| `opencode bulunamadi` | `opencode --version` PATH'te mi? Bridge'i opencode'un calistigi terminalden baslat. |
| TUI'da `telegram_*` tool yok | opencode'yu kapat-ac (MCP acilista yuklenir). |
| `telegram_status` -> STALE/DOWN | bridge calismiyor; `.ps1` ile baslat. |
| Cevap cok uzun/kisaltildi | normal — 11500 karakter ustu kirpilir, detay TUI/PC'de. |
| Her sey yavas | ilk `opencode run` model yukler; sonrakiler ayni `tg-<chat>` oturumunda hizlidir. |

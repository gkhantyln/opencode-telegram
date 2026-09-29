<div align="center">

# opencode-telegram

**opencode ajanınızı Telegram'dan yönetin — uzak kabuğun değil, uzaktan kumandanın güvenlik raylarıyla.**

[![tests](https://img.shields.io/badge/tests-250%20passing-brightgreen)](tests/)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![dependencies](https://img.shields.io/badge/dependencies-zero-success)](bridge/requirements.txt)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)
[![backend](https://img.shields.io/badge/backend-cli%20%7C%20serve-informational)](bridge/bridge.py)

[English](README.md) · Türkçe

</div>

---

## Bu ne?

[opencode](https://opencode.ai) için bağımsız bir Telegram köprüsü. Telefonundan bir iş
yazıyorsunuz; köprü makinenizde gerçek bir opencode oturumu çalıştırıp cevabı akıtararak
geri gönderiyor.

Bu **uzak bir kabuk değil**, ve sadece bir istem/iletme aracı değil. Denetimli bir kontrol
yüzeyi:

| Konu | Nasıl ele alınıyor |
|---|---|
| Bu makineyi kim çalıştırabilir | Sohbet ve kullanıcı için ayrı izin listeleri, hiçbir şey ayrıştırılmadan önce |
| Yıkıcı komutlar | Kalıp kapısı isteği bekletir; `/onay HOLD-xxx` ile onaylamanız gerekir |
| Sır sızıntısı | Telegram'a çıkan her bayt, gönderilmeden önce maskeleme filtresinden geçer |
| Kontrolsüz harcama | İsteğe bağlı günlük token tavanı + kredisi biten sağlayıcıya otomatik engel |
| Sessizce ölme | Ardışık hatalarda Telegram'a bildirim, sonra temiz kapanış |
| Yeri kaybetmek | Oturumlar listelenir, aralarında geçilir, yeniden başlatmalarda korunur |
| Mükerrer iş | Sohbet başına tek iş, mesaj kuyruğu ve `/abort` |
| Bozuk biçimleme | Kod blokları asla kesilmez; reddedilen mesaj düz metne çevrilir, **kaybolmaz** |

**Sıfır bağımlılık.** Yalnızca standart kütüphane. `bridge/requirements.txt` bilerek boş
ve testler bunu kanıtlıyor: `bridge/` ve `mcp/` içinde hiçbir üçüncü taraf paketi yok.

---

## Mimari

```
        ┌──────────────────────┐
        │      Telegram        │   Bot API, long-polling
        └──────────┬───────────┘
                   │  getUpdates / sendMessage / editMessageText
                   │
        ┌──────────▼───────────┐
        │   bridge/bridge.py   │   daemon · yalnızca stdlib
        │                      │
        │  izin listesi →kuyruk│
        │  tehlike HOLD →      │
        │  çalıştır (cli|serve)│
        │  temizle → maskele → │
        │  MarkdownV2 → gönder │
        └──────────┬───────────┘
                   │  JSON mailbox (dosya kilitli, süreçler arası)
        ┌──────────▼───────────┐        ┌──────────────────────┐
        │ .opencode/mailbox/   │◄──────►│   mcp/server.py      │
        │  inbox / outbox /    │        │  telegram_send       │
        │  sessions / queue /  │        │  telegram_broadcast  │
        │  budget / holds /    │        │  telegram_poll       │
        │  bad_providers       │        │  telegram_ack        │
        │                      │        │  telegram_status     │
        └──────────┬───────────┘        └──────────┬───────────┘
                   │                              │
        ┌──────────▼──────────────────────────────▼──┐
        │                  opencode                    │
        │  cli: `opencode run`  |  serve: HTTP + SSE   │
        └──────────────────────────────────────────────┘

  bridge/md2.py           Markdown -> Telegram MarkdownV2 (kod blokları kırpılmaz)
  bridge/atomic_json.py   işlem-semantik JSON: thread + dosya kilidi
```

Köprü hiçbir zaman gelen port açmaz. `api.telegram.org` adresine dışarıdan bağlanır; yani
güvenlik duvarı kuralı, port yönlendirme veya herkese açık bir uç nokta gerekmez.

### Depo yapısı

```
opencode-telegram/
├── README.md / README_TR.md   # bu dosya / İngilizce sürüm
├── VERSION                     # sürüm için tek doğru kaynak
├── PLAN.md                     # ürün fazları
├── GAP-PLAN.md                 # kalite borcu (yerel, git-ignored)
├── CHANGELOG.md
├── bridge/
│   ├── bridge.py               # daemon
│   ├── md2.py                  # Markdown -> MarkdownV2
│   ├── serve_client.py         # `opencode serve` için stdlib HTTP/SSE istemcisi
│   └── atomic_json.py          # süreçler arası kilitli JSON deposu
├── mcp/
│   └── server.py               # `telegram` MCP sunucusu (opencode tarafı)
├── scripts/                    # kurulum / kaldırma / başlatma
├── docs/                       # KURULUM.md, SERVIS.md
├── steering/telegram-ops.md    # ajan disiplini
└── tests/                      # 250 test, ağ yok, model çağrısı yok
```

---

## Hızlı kurulum

### Gereksinimler

- Windows, macOS veya Linux. Birincil hedef Windows.
- **Python 3.10+**
- **opencode** `PATH` üzerinde (`opencode --version`)
- [@BotFather](https://t.me/BotFather)'dan Telegram bot token'ı

### 1. Bot'u oluştur

**@BotFather**'a `/newbot` yaz, isim ver, token'ı kopyala. Sonra botunu aç ve ona bir mesaj
gönder — bir bot, senden önce mesaj almadan sana yazamaz.

### 2. Kur

```powershell
# Windows — etkileşimli menü (kur / başlat / selftest)
.\setup.bat
```

<details>
<summary>Windows dışı veya manuel adımlar</summary>

```bash
./scripts/install-global.sh          # MCP sunucusunu kaydet
./scripts/uninstall-global.sh        # kaydı kaldır
```

</details>

Kurulum **global** `opencode.json` dosyana bir `telegram` MCP sunucusu kaydeder ve
`instructions` listesine `telegram-ops.md` ekler. İdempotenttir, yazmadan önce zaman damgalı
yedek alır ve diğer MCP sunucularına dokunmaz.

### 3. Yapılandır

```powershell
copy .env.example .env
notepad .env
```

```ini
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_OPENCODE_MODEL=opencode/space-bunny-free
```

### 4. Chat ID'ni öğren

```powershell
python bridge/bridge.py --selftest
```

Köprüyü başlat, botuna herhangi bir mesaj gönder ve log'daki `DENIED chat=...` satırından
chat ID'ni oku. Köprüyü durdur, o numarayı `TELEGRAM_ALLOWED_CHAT_IDS` içine yaz, tekrar
başlat.

### 5. Başla

```powershell
.\scripts\start-bridge.ps1
```

Telegram'dan `/yardim` yaz. Bitti.

---

## Kullanım

| Telegram'dan | Ne olur |
|---|---|
| Düz metin | Oturumunuzda soru/görev olarak çalışır, cevap geri gelir |
| `/sor <metin>` | Aynı iş, açık biçim |
| Fotoğraf / belge | İndirilir ve incelenmek üzere opencode'a verilir (albümler otomatik gruplanır) |
| Sesli mesaj | Yazıya dökülür, içindeki istek yerine getirilir |
| `/durum` | Proje durumu + bu sohbetin oturumu, modeli ve token kullanımı |
| `/model`, `/model list [filtre] [sayfa]`, `/model set <p/m>`, `/model otomatik` | Sohbet bazında model kontrolü |
| `/sessions [sayfa]` | Bu projedeki oturumları listeler (yeni önce) |
| `/sessions ac <no>` | Listelenen oturuma geçer (eski oturum **silinmez**) |
| `/sessions yeni` | Yeni oturum modu (eski oturum **silinmez**) |
| `/sessions bilgi` | Aktif oturum detayı, mesaj sayısı, token kullanımı |
| `/project list \| set <alias>` | Yapılandırılmış projeler arasında geçiş |
| `/kuyruk` | Meşgulken gelen mesajlar, sırayla |
| `/onay HOLD-xxx` | Bekleyen yıkıcı isteği serbest bırak (30 dk geçerli) |
| `/onay <not>` | Orkestratör kuyruğuna not bırak |
| `/abort` | Çalışan işi ve tüm süreç ağacını öldür |
| `/klavye` | Alt kısayol klavyesini açar |
| `/klavye kapat` | Kapatır |
| `/yardim` | Komut listesi |

Uzun işler sizi bilgilendirmeyi sürdürür: üç dakikada bir ilerleme pingu, ve `/abort` her
an çalışır.

### Alt klavye

**Bilerek varsayılan olarak kapalı:** kalıcı klavye yazma alanının hemen üstüne yerleşir ve
sadece yazmak istediğinde rahatsız eder. Kısayolları istediğinde `/klavye` yaz — iki satır,
altı buton, ve yine istediğin yere yazabilirsin.

```
[ Durum ] [ Model ] [ Oturum ]
[ Kuyruk ] [ Yeni  ] [ Durdur ]
```

Butona basınca ilgili komut çalışır. Buton etiketleri ajana metin olarak **asla** gitmez.

### Oturumlar

`/sessions` aktif projeye ait oturumları yeni önce listeler ve aktif olanı işaretler.
Oturumlar proje bazlıdır: başka bir dizine ait oturum ne listelenir ne de seçilir — ajan
yanlış ağaçta çalışmasın diye.

Oturum değiştirmek veya yenisini başlatmak **hiçbir şeyi silmez**. Silmek isteyen tek komut
`/sessions sil` (eski `/reset` ile aynı).

Oturum değiştirdiğinde model geçersiz kılması sohbette kalır. Oturumun kendi modelini
kullanmasını istersen bir kez `/model otomatik` yaz; geçersiz kılma düşer.

### Mesaj kuyruğu

Ajan meşgulken gönderdiğin mesaj **reddedilmez, kuyruğa alınır.** İş bitince kuyruk sırayla
boşalır. `/kuyruk` bekleyeni gösterir, `/kuyruk temizle` boşaltır. Derinlik varsayılan 5'tir
ve ayarlanabilir. Kuyruk boşalırken temizlersen, geri çektiğin işler peş peşe çalışmaz —
döngü kırılır.

---

## Backend'ler

`TELEGRAM_BACKEND` ile seçilir.

### `cli` (varsayılan)

Her mesaj için `opencode run` sürecini çalıştırır. Ek servis yok, port yok, denetlenecek
süreç yok. Bedeli, her oturumun ilk mesajında soğuk başlangıçtır.

### `serve`

`opencode serve`'u sıcak tutar ve HTTP + SSE ile konuşur. Model yazarken mesajın kademeli
olarak düzenlenmesini, izin istekleri için satır içi butonları ve çoktan seçmeli sorular
için butonları kazanırsın. `serve` zaten ayakta değilse köprü onu kendisi başlatır,
her kullanımda bağlantıyı sağlık kontrolünden geçirir ve çıkarken süreci kapatır.

```ini
TELEGRAM_BACKEND=serve
TELEGRAM_SERVE_URL=http://127.0.0.1:4096
TELEGRAM_SERVE_PORT=4096
```

---

## Güvenlik modeli

Bunu, değer verdiğin bir makineye kurmadan önce oku.

**Erişim.** İki ayrı izin listesi. `TELEGRAM_ALLOWED_CHAT_IDS` hangi sohbetlerin hizmet
göreceğini belirler; `TELEGRAM_ALLOWED_USER_IDS` kimlerin onları *sürebileceğini*. Özel
sohbette ikisi aynı sayıdır ve ikincisi boş kalabilir. Grupta olamaz: `TELEGRAM_ALLOWED_USER_IDS`
boşsa kimse giremez, yani kendi user ID'ni açıkça yazmak zorundasın. İzinli bir sohbet, o
makinede tam opencode yetkisine eşdeğerdir.

**Yıkıcı istek kapısı.** Bilinen tehlikeli bir kalıpla eşleşen istek (`rm -rf`,
`DROP TABLE`, `terraform destroy`, `kubectl delete`, `git push --force`, `git reset --hard`,
`diskpart`, fork bombası, …) çalıştırılmaz. Beklemeye alınır, sana incelemen için geri
eklenir ve yalnızca 30 dakika geçerli, onu oluşturan sohbete bağlı açık bir
`/onay HOLD-xxx` ile serbest bırakılır. Kalıp listesi `bridge.py` içindeki `DANGER_PATTERNS`'tadır;
kendi ortamına göre genişlet.

**Sır maskeleme.** Telegram'a giden her metin bir filtreden geçer: OpenAI tipi anahtarlar,
GitHub token ve PAT'ları, AWS erişim anahtarı ID'leri, Slack token'ları, PEM özel anahtar
blokları ve bot token'ın maskelenir. Cevaplar, akan güncellemeler, audit kayıtları ve konsol
logu da bu filtreden geçer.

**Audit izi.** Her çalıştırma `telegram_audit.log` dosyasına bir JSON satırı ekler: kim
istedi, maskelenmiş özet, oturum, model, süre, çıktı uzunluğu ve günlük token
toplamları. Log 100 KB'ta döner.

**Maliyet tavanı.** `TELEGRAM_DAILY_TOKEN_LIMIT` günün toplamı bu değeri aşınca yeni işleri
durdurur. Ayrıca bir sağlayıcı kredi veya kota hatası döndürürse o sağlayıcı hatırlanır ve
**o sağlayıcıya hiçbir istek gönderilmez** — model değiştirene veya engel süresi dolana
kadar. Retransmission yerine sebebini ve çalıştıracağın komutu alırsın.

**Tek örnek.** PID kilidi ve Telegram'ın kendi 409 yanıtı birlikte iki köprünün aynı bot
üzerinde kavga etmesini engeller. İkinci örnek açık bir mesajla çıkar.

**Sessiz ölüm yok.** Poll loop'u arka arkaya başarısız olursa köprü Telegram'a bildirim
gönderir ve temizce kapanır — orada "ayakta" görünüp donmuş kalmaz. Başarılı bir tur sayacı
sıfırlar. Çıkış kodu `4`'tür, böylece bir servis yöneticisi bunu normal bir kapanıştan
ayırt edebilir.

---

## Yapılandırma

Her şey ortam değişkenidir; ortamdan, proje dizinindeki `.env`'den, yoksa paket kökündeki
`.env`'den okunur. Açıklamalı liste için `.env.example` dosyasına bak.

| Değişken | Varsayılan | Amaç |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | **Zorunlu.** BotFather token'ı |
| `TELEGRAM_ALLOWED_CHAT_IDS` | — | **Zorunlu.** Virgüllü chat ID listesi |
| `TELEGRAM_ALLOWED_USER_IDS` | — | Kullanıcı ID'leri; grupta zorunlu |
| `TELEGRAM_DEFAULT_CHAT_ID` | — | `telegram_send` varsayılan hedefi |
| `TELEGRAM_PROJECT_DIR` | otomatik | Ajanın çalışacağı proje |
| `TELEGRAM_BACKEND` | `cli` | `cli` veya `serve` |
| `TELEGRAM_OPENCODE_AGENT` | `orchestrator` | Hangi ajan cevap verir |
| `TELEGRAM_OPENCODE_MODEL` | opencode varsayılanı | Varsayılan model |
| `TELEGRAM_MARKDOWN` | `1` | Cevapları MarkdownV2 olarak biçimle. `0` = düz metin |
| `TELEGRAM_SET_COMMANDS` | `1` | `/` komut menüsünü açılışta kur |
| `TELEGRAM_API_MAX_RETRIES` | `3` | 429 ve geçici 5xx tekrar sayısı |
| `TELEGRAM_LOOP_MAX_ERRORS` | `5` | Kaç ardışık hatadan sonra kapan |
| `TELEGRAM_BRIDGE_EXEC` | `1` | `0` = yalnızca kuyruk, TUI orkestratörü çalışsın |
| `TELEGRAM_MAX_QUEUED` | `5` | Meşgulken bekleyebilen mesaj sayısı |
| `TELEGRAM_DAILY_TOKEN_LIMIT` | `0` (kapalı) | Günlük token tavanı |
| `TELEGRAM_BAD_PROVIDER_TTL_H` | `6` | Kredisi biten sağlayıcının engel süresi |
| `TELEGRAM_FOLLOW_EDITS` | `0` | Mesajı düzenleyince tekrar çalıştır |
| `TELEGRAM_MODEL_PAGE_SIZE` | `25` | `/model list` sayfa başına model |
| `TELEGRAM_SESSION_PAGE_SIZE` | `15` | `/sessions` sayfa başına oturum |
| `TELEGRAM_OUTPUT_ENCODING` | `cp1254,cp1252,latin-1` | Model çıktısı için yedek kodlamalar |
| `TELEGRAM_PROJECTS` | — | Çok proje için `alias=yol,alias2=yol2` |
| `TELEGRAM_SCHEDULE` | — | `09:00:/durum;18:00:/gelen` |
| `TELEGRAM_DIGEST` | `0` | Bildirimleri tek tek yerine saatlik özetle |

### Mesaj biçimleme

Cevaplar Telegram MarkdownV2 olarak gönderilir; `**kalın**`, `_italik_`, `~~üstü çizili~~`,
satır içi kod ve ```kod blokları``` telefonda biçimli görünür.

Üç ayrıntı bilmeye değer:

- **Kod blokları asla yarıda kesilmez.** Uzun cevaplar blok ve satır sınırlarında
  bölünür, böylece her mesaj tam ve geçerli bir kod bloğu içerir.
- **Biçimleme mesaj kaybettiremez.** Telegram bir mesajı biçim nedeniyle reddederse aynı
  içerik hemen düz metin olarak tekrar gönderilir. Biçimlemeyi tamamen kapatmak için
  `TELEGRAM_MARKDOWN=0`.
- **Model çıktısı önce temizlenir.** Renk kodları atılır ve Windows kodlamaları doğru
  çözülür, böylece Türkçe karakterler yolda bozulmaz.

---

## Servis olarak çalıştırma

`docs/SERVIS.md` iki yolu da anlatıyor. Windows'ta hızlı olanı:

```powershell
schtasks /create /tn TelegramBridge /tr "<tam-yol>\scripts\start-bridge.bat" /sc onlogon
```

Çökünce yeniden başlatılan, denetimli bir kurulum için WinSW veya NSSM kullan — köprü sade
bir ön plan sürecidir ve servis sarmalayıcısına temiz oturur. Zamanlayıcıyı kullan, çıplak
bir tekrar döngüsü değil: köprü poll loop'u bozulduğunda **kasıtlı olarak** çıkıyor ve bir
süreç yöneticisinin onu geri getirmesi gerekiyor.

---

## Geliştirme

```bash
git clone https://github.com/gkhantyln/opencode-telegram.git
cd opencode-telegram
python -m pip install pytest          # tek geliştirme bağımlılığı

python -m pytest tests/ -v            # 250 test
python bridge/bridge.py --selftest    # ortam kontrolü, ağ yok
```

Test paketi çevrimdışı çalışır, model ve Telegram token'ı gerektirmez. Maskeleme filtresi,
çıktı temizleme, yıkıcı komut kapısı, hold yaşam döngüsü, bütçe uygulaması, kredi bitişi
engellemesi, oturum listeleme ve geçiş, mesaj kuyruğu, klavye eşlemesi, outbox tekrar deneme
ve tek seferlik gönderim garantisi, süreçler arası dosya kilitleme, alt süreç pipe boşaltma,
Markdown dönüştürücü, serve HTTP/SSE istemcisi ve MCP tool yüzeyini kapsar.

CI, her push ve pull request'te Windows ve Linux'ta çalışır ve bir sır ya da log dosyası
commit edilmek üzereyse derlemeyi başarısız kılar.

### Tasarım notları

- **Neden JSON mailbox?** Köprü ve MCP sunucusu ayrı süreçlerdir. Dosya kuyruğu
  incelenebilir, hata ayıklanabilir ve yeniden başlatmalarda hayatta kalır. Her
  oku-değiştir-yaz işlemi, hem thread'leri hem süreçleri kapsayan bir işlem kilidinden
  (`bridge/atomic_json.py`) geçer; böylece eşzamanlı yazıcılar kayıt kaybedemez veya
  dosyayı bozamaz.
- **Neden websocket değil?** `getUpdates` long-polling gelen port, TLS sertifikası veya
  ters vekil sunucu gerektirmez. Kişisel bir köprü için doğru denge budur.
- **Neden bağımlılık yok?** İşinin tamamı sahipsiz kalmaya devam etmek olan bir araç,
  başkasının sürüm takvimini miras almamalıdır.
- **Neden kaynakta ASCII?** Köprünün *gönderdiği* her metin ASCII Türkçedir; böylece bir
  kod sayfası uyuşmazlığı komut cevabını bozamaz. Türkçe karakterler model çıktısında
  görünür ve orası açıkça çözülür.

---

## Sorun giderme

| Belirti | Bakılacak |
|---|---|
| Bot cevap vermiyor | Log'daki `DENIED` chat veya kullanıcının izinli olmadığını gösterir. `401` token'ın yanlış olduğunu gösterir. |
| Grup sohbeti yok sayılıyor | `TELEGRAM_ALLOWED_USER_IDS` boş. Grupta kendi user ID'ni yazmalısın. |
| `opencode bulunamadi` | Köprüyü başlattığın terminalde `opencode --version` çalışıyor mu? |
| TUI'da `telegram_*` tool yokları | opencode'yi kapat-aç — MCP sunucuları açılışta okunur |
| Her mesaj "kredisi bitmis" diyor | Sağlayıcı kasıtlı olarak engellendi. `/model list <sağlayıcı>` sonra `/model set ...` |
| `telegram_status` `STALE` veya `DOWN` diyor | Köprü çalışmıyor; heartbeat yaşını kontrol et |
| Bir iş takılmış gibi | `/abort`, sonra `/durum`. Sahibi ölmüşse kilit otomatik temizlenir. |
| Cevap kırpılmış | Normal. Çıktı 11500 karakterde kırpılır; tam metin PC'dedir. |
| Türkçe karakterler bozuk | `TELEGRAM_OUTPUT_ENCODING` ile kod sayfanı ver, örn. `cp1254`. |
| Her şey yavaş | İlk `opencode run` modeli yükler. Aynı oturumdaki sonraki mesajlar hızlıdır. |

Daha fazlası `docs/KURULUM.md` dosyasında.

---

## Katkı

Issue ve pull request'ler açıktır. PR açmadan önce:

1. `python -m pytest tests/ -v` yeşil olmalı.
2. Yalnızca standart kütüphaneyi koruyun. Gerçekten bir pakete ihtiyaç duyuyorsan önce
   issue'da gerekçelendirin.
3. Yeni davranış, sizin değişikliğiniz olmadan kırılan bir test içermelidir.
4. `CHANGELOG.md` dosyasının `Unreleased` bölümüne bir madde ekleyin.

## Lisans

MIT — bkz. [LICENSE](LICENSE).

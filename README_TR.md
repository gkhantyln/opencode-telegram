<div align="center">

# opencode-telegram

**opencode ajanınızı Telegram'dan yönetin — uzak kabuğun değil, uzaktan kumandanın güvenlik raylarıyla.**

[![tests](https://img.shields.io/badge/tests-45%20passing-brightgreen)](tests/test_bridge.py)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![dependencies](https://img.shields.io/badge/dependencies-zero-success)](bridge/requirements.txt)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)
[![backend](https://img.shields.io/badge/backend-cli%20%7C%20serve-informational)](bridge/bridge.py)

[English](README.md) · Türkçe

</div>

---

## Bu ne?

[opencode](https://opencode.ai) için bağımsız bir Telegram köprüsü. Telefonundan bir iş
yazıyorsunuz; köprü makinenizde gerçek bir opencode oturumu çalıştırıp cevabı akıtarak
geri gönderiyor.

Bu **uzak bir kabuk değil**, ve sadece bir istem/iletme aracı değil. Denetimli bir kontrol
yüzeyi:

| Konu | Nasıl ele alınıyor |
|---|---|
| Bu makineyi kim çalıştırabilir | Katı sohbet izin listesi, hiçbir şey ayrıştırılmadan önce kontrol edilir |
| Yıkıcı komutlar | Kalıp kapısı isteği bekletir; `/onay HOLD-xxx` ile onaylamanız gerekir |
| Sır sızıntısı | Telegram'a çıkan her bayt, gönderilmeden önce maskeleme filtresinden geçer |
| Kontrolsüz harcama | İsteğe bağlı günlük token tavanı yeni işleri durdurur |
| Yeri kaybetmek | Oturumlar sohbet bazında eşlenir, yeniden başlatmalarda korunur |
| Mükerrer iş | Sohbet başına tek iş, `/abort` ve tüm süreç ağacı öldürme |

**Sıfır bağımlılık.** Yalnızca standart kütüphane. `bridge/requirements.txt` bilerek boş
ve testler bunu kanıtlıyor: köprü hiçbir üçüncü taraf paketi içe aktarmıyor.

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
        │  izin listesi → kapı │
        │  tehlike HOLD →      │
        │  çalıştır (cli|serve)│
        │  maskele → Telegram  │
        └──────────┬───────────┘
                   │  JSON mailbox (dosya kilitli, süreçler arası)
        ┌──────────▼───────────┐        ┌──────────────────────┐
        │ .opencode/mailbox/   │◄──────►│   mcp/server.py      │
        │  inbox / outbox /    │        │  telegram_send       │
        │  sessions / budget   │        │  telegram_broadcast  │
        └──────────┬───────────┘        │  telegram_poll       │
                   │                    │  telegram_ack        │
                   │                    │  telegram_status     │
        ┌──────────▼───────────┐        └──────────┬───────────┘
        │       opencode       │◄──────────────────┘
        │  cli  `opencode run` │   veya   serve  `opencode serve`
        └──────────────────────┘   (HTTP + SSE, sıcak oturum)
```

Köprü hiçbir zaman gelen port açmaz. `api.telegram.org` adresine dışarıdan bağlanır; yani
güvenlik duvarı kuralı, port yönlendirme veya herkese açık bir uç nokta gerekmez.

### Depo yapısı

```
opencode-telegram/
├── README.md               # bu dosya değil, İngilizce sürüm
├── README_TR.md            # bu dosya
├── VERSION                 # sürüm için tek doğru kaynak
├── PLAN.md                 # ürün fazları ve kabul kriterleri
├── GAP-PLAN.md             # kalite borcu (yerel, git-ignored)
├── CHANGELOG.md
├── bridge/
│   ├── bridge.py           # daemon
│   ├── serve_client.py     # `opencode serve` için stdlib HTTP/SSE istemcisi
│   └── atomic_json.py      # süreçler arası kilitli JSON deposu
├── mcp/
│   └── server.py           # `telegram` MCP sunucusu (opencode tarafı)
├── scripts/                # kurulum / kaldırma / başlatma yardımcıları
├── docs/                   # KURULUM.md, SERVIS.md
├── steering/               # telegram-ops.md — ajan disiplini
└── tests/                  # 45 test, ağ yok, model çağrısı yok
```

---

## Hızlı kurulum

### Gereksinimler

- Windows, macOS veya Linux. Birincil hedef Windows.
- **Python 3.10+**
- **opencode** `PATH` üzerinde (`opencode --version`)
- [@BotFather](https://t.me/BotFather)'dan Telegram bot token'ı

### 1. Bot'u oluştur

**@BotFather**'a `/newbot` yaz, bir isim ver, token'ı kopyala. Sonra botunu aç ve ona bir
mesaj gönder — bir bot, senden önce mesaj almadan sana yazamaz.

### 2. Kur

```powershell
# Windows — etkileşimli menü (kur / başlat / selftest)
.\setup.bat
```

<details>
<summary>Windows dışı veya manuel adımlar</summary>

```bash
# macOS / Linux
./scripts/install-global.sh

# ya da birleştirmeyi kendiniz yapin
python scripts/merge_global_config.py \
    ~/.config/opencode/opencode.json \
    ./mcp/server.py \
    ./steering/telegram-ops.md \
    python3
```

</details>

Kurulum, **global** `opencode.json` dosyanıza bir `telegram` MCP sunucusu kaydeder ve
`instructions` listesine `telegram-ops.md` ekler. İdempotenttir ve yazmadan önce zaman
damgalı yedek alır.

### 3. Yapılandır

```powershell
copy .env.example .env
notepad .env
```

```ini
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_DEFAULT_CHAT_ID=
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
| `/model`, `/model list`, `/model set <provider/model>` | Sohbet bazında model kontrolü |
| `/project list \| set <alias>` | Yapılandırılmış projeler arasında geçiş |
| `/onay HOLD-xxx` | Bekleyen yıkıcı isteği serbest bırak (30 dk geçerli) |
| `/onay <not>` | Orkestratör kuyruğuna not bırak |
| `/abort` | Çalışan işi ve tüm süreç ağacını öldür |
| `/reset` | Bu sohbetin oturumunu sil, sıfırdan başla |
| `/yardim` | Komut listesi |

Uzun işler sizi bilgilendirmeyi sürdürür: üç dakikada bir ilerleme pingu, ve `/abort` her
an çalışır.

---

## Backend'ler

`TELEGRAM_BACKEND` ile seçilir.

### `cli` (varsayılan)

Her mesaj için `opencode run` sürecini çalıştırır. Ek servis yok, port yok, denetlenecek
süreç yok. Bedeli, her oturumun ilk mesajında soğuk başlangıçtır.

### `serve`

`opencode serve`'u sıcak tutar ve HTTP + SSE ile konuşur. Model yazarken mesajın
kademeli olarak düzenlenmesini, izin istekleri için satır içi butonları ve çoktan seçmeli
sorular için butonları kazanırsınız. `serve` zaten ayakta değilse köprü onu kendisi
başlatır.

```ini
TELEGRAM_BACKEND=serve
TELEGRAM_SERVE_URL=http://127.0.0.1:4096
TELEGRAM_SERVE_PORT=4096
```

---

## Güvenlik modeli

Bunu, değer verdiğiniz bir makineye kurmadan önce okuyun.

**Erişim.** Yalnızca `TELEGRAM_ALLOWED_CHAT_IDS` içindeki sohbetler hizmet alır. Diğer
her şey log'a `DENIED` satırı düşer ve hiç ayrıştırılmaz. İzinli bir sohbet, o makinede
tam opencode yetkisine eşdeğerdir — chat ID'yi bir kimlik bilgisi gibi görün ve
paylaşmayın.

**Yıkıcı istek kapısı.** Bilinen tehlikeli bir kalıpla eşleşen istek (`rm -rf`,
`DROP TABLE`, `terraform destroy`, `kubectl delete`, `git push --force`, `git reset --hard`,
`diskpart`, fork bombası, …) çalıştırılmaz. Beklemeye alınır, size incelemeniz için geri
eklenir ve yalnızca 30 dakika geçerli, onu oluşturan sohbete bağlı açık bir
`/onay HOLD-xxx` ile serbest bırakılır. Kalıp listesi `bridge.py` içindeki
`DANGER_PATTERNS`'tadır; kendi ortamınıza göre genişletin.

**Sır maskeleme.** Telegram'a giden her metin bir filtreden geçer: OpenAI tipi anahtarlar,
GitHub token ve PAT'ları, AWS erişim anahtarı ID'leri, Slack token'ları, PEM özel anahtar
blokları ve bot token'ınız maskeler. Bu, cevaplar, akan güncellemeler ve audit kayıtları
için de geçerlidir.

**Audit izi.** Her çalıştırma `telegram_audit.log` dosyasına bir JSON satırı ekler: kim
istedi, maskelenmiş özet, oturum, model, süre, çıktı uzunluğu ve günlük token
toplamları. Log 100 KB'ta döner.

**Maliyet tavanı.** `TELEGRAM_DAILY_TOKEN_LIMIT` ayarlayın; günün toplamı bu değeri
aşınca yeni işler durur. `/durum` her an sayacı gösterir.

**Tek örnek.** PID kilidi ve Telegram'ın kendi 409 yanıtı birlikte iki köprünün aynı bot
üzerinde kavga etmesini engeller. İkinci örnek açık bir mesajla çıkar.

---

## Yapılandırma

Her şey ortam değişkenidir; ortamdan, proje dizinindeki `.env`'den, yoksa paket kökündeki
`.env`'den okunur. Açıklamalı liste için `.env.example` dosyasına bakın.

En çok dokunacağınız değişkenler:

| Değişken | Varsayılan | Amaç |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | **Zorunlu.** BotFather token'ı |
| `TELEGRAM_ALLOWED_CHAT_IDS` | — | **Zorunlu.** Virgüllü chat ID listesi |
| `TELEGRAM_DEFAULT_CHAT_ID` | — | `telegram_send` varsayılan hedefi |
| `TELEGRAM_PROJECT_DIR` | otomatik | Ajanın çalışacağı proje |
| `TELEGRAM_BACKEND` | `cli` | `cli` veya `serve` |
| `TELEGRAM_OPENCODE_AGENT` | `orchestrator` | Hangi ajan cevap verir |
| `TELEGRAM_OPENCODE_MODEL` | opencode varsayılanı | Model geçersiz kılma |
| `TELEGRAM_BRIDGE_EXEC` | `1` | `0` = yalnızca kuyruk, TUI orkestratörü çalışsın |
| `TELEGRAM_MARKDOWN` | `1` | Cevabı MarkdownV2 olarak biçimle. `0` = her zaman düz metin |
| `TELEGRAM_SET_COMMANDS` | `1` | `/` komut menüsünü açılışta kendiliğinden kur |
| `TELEGRAM_API_MAX_RETRIES` | `3` | 429 ve geçici 5xx için tekrar sayısı. `0` = tekrar yok |
| `TELEGRAM_DAILY_TOKEN_LIMIT` | `0` (kapalı) | Günlük token tavanı |
| `TELEGRAM_FOLLOW_EDITS` | `0` | Mesajı düzenleyince tekrar çalıştır |
| `TELEGRAM_PROJECTS` | — | Çok proje için `alias=yol,alias2=yol2` |
| `TELEGRAM_SCHEDULE` | — | `09:00:/durum;18:00:/gelen` |
| `TELEGRAM_DIGEST` | `0` | Bildirimleri tek tek yerine saatlik özetle |

### Mesaj biçimleme

Cevaplar Telegram MarkdownV2 olarak gönderilir; `**kalın**`, `_italik_`, `~~üstü çizili~~`,
satır içi kod ve ```kod blokları``` telefonda biçimli görünür.

İki ayrıntı bilmeye değer:

- **Kod blokları asla yarıda kesilmez.** Cevap uzun olduğunda bölme blok ve satır
  sınırlarında yapılır, böylece her mesaj tam ve geçerli bir kod bloğu içerir.
  Ortadan kesmek Telegram'da bozuk kod gösterir.
- **Biçimleme mesaj kaybettiremez.** Telegram bir mesajı biçim nedeniyle reddederse
  (HTTP 400 + parse hatası) aynı içerik hemen düz metin olarak tekrar gönderilir.
  Biçimlemeyi tamamen kapatmak için `TELEGRAM_MARKDOWN=0`.

---

## Servis olarak çalıştırma

`docs/SERVIS.md` iki yolu da anlatıyor. Windows'ta hızlı olanı:

```powershell
schtasks /create /tn TelegramBridge /tr "<tam-yol>\scripts\start-bridge.bat" /sc onlogon
```

Çökünce yeniden başlatılan, denetimli bir kurulum için WinSW veya NSSM kullanın — köprü
sade bir ön plan sürecidir ve servis sarmalayıcısına temiz oturur.

---

## Geliştirme

```bash
git clone https://github.com/gkhantyln/opencode-telegram.git
cd opencode-telegram
python -m pip install pytest          # tek gelistirme bagimliligi

python -m pytest tests/ -v            # 45 test
python bridge/bridge.py --selftest    # ortam kontrolu, ag yok
```

Test paketi çevrimdışı çalışır, model ve Telegram token'ı gerektirmez. Maskeleme filtresini,
yıkıcı komut kapısını, hold yaşam döngüsünü, bütçe uygulamasını, oturum geçişini,
outbox tekrar deneme ve tek seferlik gönderim garantisini, süreçler arası dosya
kilitlemeyi, alt süreç pipe boşaltmayı, medya ve digest yollarını ve MCP tool
yüzeyini kapsar.

CI, her push ve pull request'te Windows ve Linux'ta çalışır ve bir sır dosyasının
commit edilmek üzere olması hâlinde derlemeyi başarısız kılar.

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

---

## Sorun giderme

| Belirti | Bakılacak |
|---|---|
| Bot cevap vermiyor | Log'daki `DENIED` chat ID'nin izinli olmadığını gösterir. `401` token'ın yanlış olduğunu gösterir. |
| `opencode bulunamadi` | Köprüyü başlattığınız terminalde `opencode --version` çalışıyor mu? |
| TUI'da `telegram_*` tool yokları | opencode'yu kapat-aç — MCP sunucuları açılışta okunur |
| `telegram_status` `STALE` veya `DOWN` diyor | Köprü çalışmıyor; heartbeat yaşını kontrol edin |
| Bir iş takılmış gibi | `/abort`, sonra `/durum`. Sahibi ölmüşse kilit otomatik temizlenir. |
| Cevap kırpılmış | Normal. Çıktı 11500 karakterde kırpılır; tam metin PC'dedir. |
| Her şey yavaş | İlk `opencode run` modeli yükler. Aynı oturumdaki sonraki mesajlar hızlıdır. |

Daha fazlası `docs/KURULUM.md` dosyasında.

---

## Katkı

Issue ve pull request'ler açıktır. PR açmadan önce:

1. `python -m pytest tests/ -v` yeşil olmalı.
2. Yalnızca standart kütüphaneyi koruyun. Gerçekten bir pakete ihtiyaç duyuyorsanız önce
   issue'da gerekçelendirin.
3. Yeni davranış, sizin değişikliğiniz olmadan kırılan bir test içermelidir.
4. `CHANGELOG.md` dosyasının `Unreleased` bölümüne bir madde ekleyin.

## Lisans

MIT — bkz. [LICENSE](LICENSE).

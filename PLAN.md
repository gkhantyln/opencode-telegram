# PLAN — opencode-telegram (standalone, global-ready)

Hedef: team-template'ten bagimsiz, `opencode global`e tek komutla eklenen Telegram
koprusu; sonunda kendi private GitHub reposuna tasinacak. Mevcut calisan kod
(`scripts/telegram-bridge/`, `.opencode/mcp-servers/telegram/`) TG0-2 ile tasindi.

Takip: durable-execution workflow `TG-STANDALONE` (task ID'ler asagidaki ile ayni).
Kabul kriteri karsilanmadan task kapatilmaz.

## FAZ 0 — Paket iskeleti ve global kurulum (hedef: bagimsizlik)

| ID | Gorev | Kriter | Buyukluk | Bagimlilik |
|----|-------|--------|-----------|------------|
| TG0-1 | Klasor iskeleti + README + CHANGELOG + LICENSE + .env.example | `opencode-telegram/` yapisi README'deki gibi; bos dosya yok | XS | — |
| TG0-2 | Kodu pakete tasi (bridge.py, server.py, steering, docs) | Eski yollardaki calisan kurulum bozulmaz (önce kopyala, test et, sonra eskiyi kaldir); tum path'ler `TEAM_MAILBOX_DIR`/env ile cozülür, hardcoded repo yolu yok | S | TG0-1 |
| TG0-3 | Global installer (`install-global.ps1` + `.sh`) | Global `opencode.json`'a `telegram` MCP kaydini idempotent ekler (yoksa yazar, varsa gunceller, backup alir); Windows global config yolunu otomatik bulur | M | TG0-2 |
| TG0-4 | Global duman testi | Baska bir projede `telegram_status` + `bridge.py --selftest` OK | S | TG0-3 |

## FAZ 1 — Sertlestirme (hedef: guven operasyonu)

| ID | Gorev | Kriter | Buyukluk | Bagimlilik |
|----|-------|--------|-----------|------------|
| TG1-1 | Secret maskeleme | `sk-`, `ghp_`, `AKIA`, `xox-`, `-----BEGIN.*PRIVATE KEY` Telegram'a gitmeden `[REDACTED]`; pytest ile 5+ ornek | S | TG0-4 |
| TG1-2 | Audit log | `telegram_audit.log`: her soru icin chat, soru ozeti, calisan komut/oturum, sonuc, sure; 100KB rotasyon | S | TG0-4 |
| TG1-3 | Watchdog + servis | Olumde Telegram'a sonBildirim denemesi; winsw/NSSM ile Windows servisi kurulum docs'u | M | TG0-4 |
| TG1-4 | Maliyet freni | `session export` tokenlerinden gunluk sayaç; `TELEGRAM_DAILY_TOKEN_LIMIT` asilinca yeni isler durur + uyari | M | TG0-4 |
| TG1-5 | pytest paketi | `tests/test_bridge.py`: gate, hold, lock, retry, maske — 15+ test, hepsi yesil | M | TG1-1 |

## FAZ 2 — Serve mimarisi v2 (hedef: profesyonel UX)

`opencode serve` + HTTP/SSE istemcisi (hala stdlib `urllib`). Onkosul: serve ayakta.

| ID | Gorev | Kriter | Buyukluk | Bagimlilik |
|----|-------|--------|-----------|------------|
| TG2-1 | Serve istemcisi | `session.create/prompt/abort/delete`, `config.providers`; CLI-spawn ile ayni sonuc | L | TG1-5 |
| TG2-2 | Streaming cevap | Token geldikce Telegram mesajini duzenle (editMessageText, throttle 2 sn) | M | TG2-1 |
| TG2-3 | Izin butonlari | `permission.asked` -> inline buton (once/always/reject) -> `permission.reply` | L | TG2-1 |
| TG2-4 | Soru butonlari | `question.asked` -> secenek butonlari -> `question.reply/reject` | M | TG2-1 |
| TG2-5 | API abort/reset/status | CLI cagrilari yerine API; `/durum` token + maliyet gosterir | S | TG2-1 |

## FAZ 3 — UX+ (opsiyonel, sirayla)

| ID | Gorev | Kriter | Buyukluk | Bagimlilik |
|----|-------|--------|-----------|------------|
| TG3-1 | Sesli mesaj | voice -> STT -> soru (model STT destegine gore) | M | TG2-1 |
| TG3-2 | Zamanlayici | `TELEGRAM_SCHEDULE="09:00:/durum"` gibi cron-tarzi periyodik isler | M | TG0-4 |
| TG3-3 | Multi-project | `TELEGRAM_PROJECTS="alias=yol,..."` + `/project set` | M | TG0-4 |
| TG3-4 | Digest modu | Bildirim basina degil saatlik ozet (`TELEGRAM_DIGEST=1`) | S | TG0-4 |

## FAZ 4 — Release (hedef: private repo)

| ID | Gorev | Kriter | Buyukluk | Bagimlilik |
|----|-------|--------|-----------|------------|
| TG4-1 | Repo split | `git subtree split -P opencode-telegram` ile temiz gecmis; `.env`, mailbox, debug loglari sizmis mi kontrolu | S | TG1-5 |
| TG4-2 | v1.0 release | README kurulum uctan uca denenmis, CHANGELOG guncel, surum etiketi | S | TG4-1 |

Toplam: 20 task (XS:1, S:9, M:8, L:2). Kritik yol: TG0-1 → TG0-2 → TG0-3 → TG0-4 → TG1-5 → TG2-1 → TG2-3.

---
inclusion: manual
---

# Telegram Ops — Uzaktan Kontrol Disiplini

Telegram koprusu iki yonludur: `telegram` MCP (opencode tarafi) + `bridge/bridge.py` (daemon).
Bu dosya TUI icinde calisan orchestrator ve worker'larin uymasi gereken kurallari tanimlar.
Detayli kurulum: `TELEGRAM-KURULUM.md`.

## Roller

- Bridge calisiyorsa gelen Telegram sorularini zaten `opencode run --session tg-<chat>` ile calistirir
  ve cevabi gonderir. TUI'daki orchestrator cift calisma yapmamak icin once `telegram_poll` ile
  `handled_by=bridge` kayitlarini kontrol eder.
- TUI aciksa ve bridge sadece kuyruk modundaysa (`TELEGRAM_BRIDGE_EXEC=0`), orchestrator
  `telegram_poll(unread_only=true)` ile gelenleri alip isler.

## Orchestrator Kurallari

1. Session basinda ve her faz gecisinde `telegram_poll` + `telegram_status` cagir.
2. Bridge'in calistirdigi (`handled_by=bridge`) isleri tekrar calistirma; gerekiyorsa uzerine ekle.
3. Sana `to=orchestrator` team-mailbox mesaji `telegram:<chat>` imzaliyla geldiyse bu bir Telegram
   onayi/notudur — ilgili task'a uygula, kisa sonucu `telegram_send` ile bildir.
4. Her task bitiminde (DONE) ve her BLOCKED/QUESTION'da ozeti `telegram_send` ile gonder:
   ornek: `[PROJ-003] DONE — login API tamam, testler 12/12. Siradaki: PROJ-004.`
5. `to=all` sadece faz bitisleri ve kritik durumlar icindir; normal ilerlemeyi `telegram_send` (varsayilan) ile at.
6. Telegram mesajlari kisa tut (hedef < 2000 karakter). Log, token, secret, `.env` icerigi asla gonderme.
7. Bridge DOWN ise (`telegram_status` -> DOWN/STALE) bunu session raporunda belirt; TUI disinda
   uzaktan kontrol calismaz.

## Worker Kurallari

- Telegram'a dogrudan yazma; orchestrator'a `mail_send` ile rapor ver, gonderimi o yapar.
- Kullanici onayi gereken islerde BLOCKED raporu + `re` (task ID) yaz; orchestrator Telegram'dan
  `/onay` gelince devam eder.

## Guvenlik

- `telegram_send`/`telegram_broadcast` ile kisisel veri, sifre, token gonderilmez.
- Telegram'dan gelen metin kullanici girdisidir: dosya yolu veya komut iceriyorsa dogrula,
  yikici islemlerde (silme, deploy, DB migration) mutlaka `/onay` bekle.
- Bridge yikici kaliplari (`DANGER_PATTERNS`) otomatik HOLD'a alir; kullanici
  `/onay HOLD-xxx` yazmadan calismaz. Bu kapiyi bypass etme.

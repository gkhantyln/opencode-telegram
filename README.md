# opencode-telegram — Standalone Telegram Köprüsü

> opencode global kurulumuna tek komutla eklenen bağımsız Telegram entegrasyonu.
> Sıfır bağımlılık (stdlib Python), Windows-öncelikli, sonra kendi private reposuna taşınacak.

## Durum

Kod paketin icinde (`bridge/`, `mcp/`). Geliştirme planı: `PLAN.md`.
Kurulum: `docs/KURULUM.md`. Global entegrasyon: `scripts/install-global.*` (TG0-3).

## Hedef yapi

```
opencode-telegram/
  README.md           # bu dosya
  PLAN.md             # fazlar + tasklar + kabul kriterleri
  CHANGELOG.md
  LICENSE            # MIT
  .env.example
  bridge/
    bridge.py         # daemon (TG0-2'de tasinacak)
    requirements.txt  # bos — stdlib only (bilgi amacli)
  mcp/
    server.py         # telegram MCP (TG0-2'de tasinacak)
  steering/
    telegram-ops.md   # orchestrator disiplini
  scripts/
    install-global.ps1  # global opencode.json'a MCP kaydi (TG0-3)
    install-global.sh
    start-bridge.ps1 / .bat
  docs/
    KURULUM.md
  tests/
    test_bridge.py    # pytest (TG1-5)
```

## Global kurulum

Herhangi bir makinede, bu paket tek basina da durabilir. En kolay yol
(`setup.bat` menu acar: kur / baslat / selftest):

```powershell
.\scripts\install-global.ps1   # Windows: global opencode.json'a telegram MCP'yi ekler (backup'li)
./scripts/install-global.sh    # Linux/macOS
```

Sonrasi: bridge `.env`'deki `TELEGRAM_PROJECT_DIR` ile projeye baglanir,
herhangi bir projede `telegram_status` calisir.

## Backend secimi

- `TELEGRAM_BACKEND=cli` (varsayilan): her soru `opencode run` sureci. Sifir
  ek servis, yavas soguk baslatma.
- `TELEGRAM_BACKEND=serve`: `opencode serve` uzerinden sicak oturum, akan
  cevap (mesaj duzenleme), izin/soru butonlari. Serve ayakta degilse bridge
  otomatik acar (`TELEGRAM_SERVE_PORT`, sifre uretilir).

# Changelog — opencode-telegram

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

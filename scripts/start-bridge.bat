@echo off
REM Telegram <-> opencode koprusunu baslatir, paket surumu (cift tikla veya terminalden)
REM Sira: .env dolu olmali (TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_CHAT_IDS)
REM Hedef proje: TELEGRAM_PROJECT_DIR (.env) - bos ise paket dizini kullanilir.
cd /d "%~dp0.."
python bridge\bridge.py %*

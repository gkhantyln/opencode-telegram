@echo off
REM opencode-telegram setup: global install + bridge start (Windows)
REM Kullanim: setup.bat [install ^| start ^| selftest ^| all]
REM   argumansiz -> menu acar
setlocal
cd /d "%~dp0"

if /i "%~1"=="install" goto install
if /i "%~1"=="start" goto start
if /i "%~1"=="selftest" goto selftest
if /i "%~1"=="all" (set MODE=all & goto install)
if not "%~1"=="" (
  echo Kullanim: setup.bat [install ^| start ^| selftest ^| all]
  exit /b 1
)

echo.
echo  opencode-telegram kurulum
echo  [1] Global kur (opencode.json'a MCP kaydi)
echo  [2] Bridge'i baslat
echo  [3] Kur + baslat
echo  [4] Selftest
echo  [0] Cikis
choice /C 12340 /N /M "Secim: "
if errorlevel 5 goto eof
if errorlevel 4 goto selftest
if errorlevel 3 (set MODE=all & goto install)
if errorlevel 2 goto start
goto install

:install
echo.
echo [*] Global kurulum basliyor...
where python >nul 2>&1
if errorlevel 1 (
  echo [HATA] 'python' bulunamadi - PATH'te yok. Python 3.10+ kurun.
  exit /b 1
)
if "%OPENCODE_GLOBAL_CONFIG%"=="" (
  set "CFG=%USERPROFILE%\.config\opencode\opencode.json"
) else (
  set "CFG=%OPENCODE_GLOBAL_CONFIG%"
)
if not exist "%CFG%" (
  mkdir "%USERPROFILE%\.config\opencode" 2>nul
  echo {} > "%CFG%"
)
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set STAMP=%%t
copy /y "%CFG%" "%CFG%.bak-%STAMP%" >nul
if not exist "%CD%\mcp\server.py" (
  echo [HATA] Paket dosyasi eksik: mcp\server.py
  exit /b 1
)
if not exist "%CD%\steering\telegram-ops.md" (
  echo [HATA] Paket dosyasi eksik: steering\telegram-ops.md
  exit /b 1
)
python "%CD%\scripts\merge_global_config.py" "%CFG%" "%CD%\mcp\server.py" "%CD%\steering\telegram-ops.md" "python"
if errorlevel 1 (
  echo [HATA] Global config yazilamadi. Yedekten donuluyor...
  copy /y "%CFG%.bak-%STAMP%" "%CFG%" >nul
  exit /b 1
)
echo [OK] Global kurulum tamam: %CFG%
echo      Yedek: %CFG%.bak-%STAMP%
echo      Sonraki: opencode'u kapat-ac, iceride 'telegram_status' yaz.
if "%MODE%"=="all" goto start
goto eof

:start
echo.
if not exist "%CD%\.env" (
  echo [UYARI] .env yok. Once kopyalayin: copy .env.example .env
  echo         ve TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_CHAT_IDS doldurun.
  choice /C EV /N /M "Yine de baslatilsin mi? [E/H]: "
  if errorlevel 2 goto eof
)
echo [*] Bridge baslatiliyor (durdurmak icin Ctrl+C)...
python "%CD%\bridge\bridge.py"
goto eof

:selftest
python "%CD%\bridge\bridge.py" --selftest
goto eof

:eof
endlocal

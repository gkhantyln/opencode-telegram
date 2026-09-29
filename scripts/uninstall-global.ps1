# opencode global kaydini kaldirir (telegram MCP + steering talimati).
# Kullanim: .\scripts\uninstall-global.ps1
# Idempotent: kayit yoksa da sessizce basarili biter.
$ErrorActionPreference = "Stop"
$PkgDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$GlobalConfig = if ($env:OPENCODE_GLOBAL_CONFIG) { $env:OPENCODE_GLOBAL_CONFIG } `
  else { Join-Path $HOME ".config\opencode\opencode.json" }
$Steer = Join-Path $PkgDir "steering\telegram-ops.md"
$Merge = Join-Path $PkgDir "scripts\merge_global_config.py"

if (-not (Test-Path $GlobalConfig)) {
  Write-Output "Zaten yok: $GlobalConfig"
  exit 0
}

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "'python' bulunamadi (PATH)." }

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item $GlobalConfig "$GlobalConfig.bak-$stamp"
Write-Output "Yedek: $GlobalConfig.bak-$stamp"

& $py.Source $Merge $GlobalConfig "--remove" $Steer
if ($LASTEXITCODE -ne 0) { throw "Kaldirma basarisiz. Yedek: $GlobalConfig.bak-$stamp" }

Write-Output "Kaldirildi: telegram MCP kaydi + steering talimati"
Write-Output "Simdi opencode'u kapat-ac."
Write-Output "Not: .env ve calisma verisi (mailbox) silinmedi; onlari elle silebilirsin."

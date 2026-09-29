# opencode global'e telegram MCP'yi ekler (idempotent, backup'li)
# Kullanim: .\install-global.ps1
# Test (gercek config'e dokunmadan): $env:OPENCODE_GLOBAL_CONFIG="C:\tmp\test-opencode.json"; .\install-global.ps1
$ErrorActionPreference = "Stop"
$PkgDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$GlobalConfig = if ($env:OPENCODE_GLOBAL_CONFIG) { $env:OPENCODE_GLOBAL_CONFIG } `
  else { Join-Path $HOME ".config\opencode\opencode.json" }

if (-not (Test-Path $GlobalConfig)) {
  New-Item -ItemType Directory -Force (Split-Path $GlobalConfig) | Out-Null
  '{}' | Set-Content -Encoding utf8 $GlobalConfig
}
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item $GlobalConfig "$GlobalConfig.bak-$stamp"

$serverPy = Join-Path $PkgDir "mcp\server.py"
$steer = Join-Path $PkgDir "steering\telegram-ops.md"
$merge = Join-Path $PkgDir "scripts\merge_global_config.py"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "'python' bulunamadi (PATH)." }
& $py.Source $merge "$GlobalConfig" "$serverPy" "$steer" "python"
Write-Output "Backup: $GlobalConfig.bak-$stamp"
Write-Output "Sonraki: opencode'u kapat-ac, iceride 'telegram_status' yaz."

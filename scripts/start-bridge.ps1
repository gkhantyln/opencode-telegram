# Telegram <-> opencode koprusunu baslatir (paket surumu)
# Kullanim: .\scripts\start-bridge.ps1
# Test:     .\scripts\start-bridge.ps1 --selftest
param(
  [string]$PackageDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$BridgeArgs
)
Set-Location $PackageDir
python bridge/bridge.py @BridgeArgs

param(
    [string]$Remote = "mac",
    [string]$Branch = "main",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$repoRoot = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
    throw "Run this script inside the mac repository."
}

$hookPath = (& git rev-parse --git-path hooks/post-commit).Trim()
$hookDir = Split-Path -Parent $hookPath
New-Item -ItemType Directory -Force -Path $hookDir | Out-Null

$scriptPath = Join-Path $repoRoot "scripts/push-trading-copilot-to-mac.ps1"
$skipFlag = if ($SkipTests) { " -SkipTests" } else { "" }
$hook = @"
#!/bin/sh
set -eu

repo_root=`$(git rev-parse --show-toplevel)
script_path="`$repo_root/scripts/push-trading-copilot-to-mac.ps1"

if [ ! -d "`$repo_root/trading-copilot" ] || [ ! -f "`$script_path" ]; then
  exit 0
fi

if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -ExecutionPolicy Bypass -File "`$script_path" -Remote "$Remote" -Branch "$Branch"$skipFlag
elif command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "`$script_path" -Remote "$Remote" -Branch "$Branch"$skipFlag
else
  echo "post-commit autopush skipped: PowerShell not found" >&2
  exit 1
fi
"@

Set-Content -LiteralPath $hookPath -Value $hook -Encoding ascii
Write-Host "Installed post-commit autopush hook: $hookPath"
Write-Host "Remote: $Remote"
Write-Host "Branch: $Branch"
Write-Host "SkipTests: $($SkipTests.IsPresent)"

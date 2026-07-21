# saitenka-overlay bootstrap (Windows) — Stage 17b.
# The ONLY job the shell does: get `uv`, install the overlay from the wheel next to this script, then
# hand off to the Python `setup` wizard (which owns all real logic). Non-destructive; -DryRun prints.
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$SelfDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Have($name) { $null -ne (Get-Command $name -ErrorAction SilentlyContinue) }

# 1. uv — the only hard bootstrap (it then owns Python 3.14t + all deps).
if (-not (Have 'uv')) {
    Write-Host '[saitenka] installing uv...'
    if (-not $DryRun) {
        if (Have 'winget') { winget install --id=astral-sh.uv -e }
        else { irm https://astral.sh/uv/install.ps1 | iex }
    }
}

# uv installs tools into %USERPROFILE%\.local\bin, which is NOT on PATH in this session. Prepend it
# ALWAYS (not only when we just installed uv) so the `saitenka-overlay setup` handoff below resolves
# even when uv was already present — otherwise: "saitenka-overlay is not recognized".
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

# 2. install the overlay from the wheel shipped next to this stub.
$wheel = Get-ChildItem -Path $SelfDir -Filter 'saitenka_overlay-*.whl' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $wheel) {
    Write-Error '[saitenka] no wheel found next to this installer - is the bundle intact?'
    exit 1
}
Write-Host "[saitenka] installing $($wheel.Name)"
if ($DryRun) { Write-Host "DRY: uv tool install --reinstall $($wheel.FullName)" }
else { uv tool install --reinstall $wheel.FullName }

# 3. hand off to the Python wizard (mpv/ffmpeg hints, doctor, init, import, plugin). Resolve the exe
# explicitly — the freshly-installed tool may still not be on PATH in this session on some setups.
$exe = (Get-Command saitenka-overlay -ErrorAction SilentlyContinue).Source
if (-not $exe) { $exe = "$env:USERPROFILE\.local\bin\saitenka-overlay.exe" }
if ($DryRun) { Write-Host "DRY: $exe setup --dry-run" }
elseif (Test-Path $exe) { & $exe setup }
else { uv tool run --from saitenka-overlay saitenka-overlay setup }

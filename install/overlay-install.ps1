# saitenka-overlay bootstrap (Windows) — Stage 17b.
# The ONLY job the shell does: get `uv`, install the overlay from the wheel next to this script, then
# hand off to the Python `setup` wizard (which owns all real logic). Non-destructive; -DryRun prints.
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$SelfDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Decode the Python child processes' UTF-8 output correctly. Set ONLY [Console]::OutputEncoding — NOT
# `chcp 65001`: changing the console codepage breaks interactive typing in the classic console.
try {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

function Have($name) { $null -ne (Get-Command $name -ErrorAction SilentlyContinue) }

# 1. uv — the only hard bootstrap (it then owns Python 3.14t + all deps).
# Methods per uv's guide: https://docs.astral.sh/uv/getting-started/installation/
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

# 2. install the overlay from the wheel next to this stub, WITH the JMdict fallback extra ([jmdict],
# resolved from PyPI). The GPL-3.0 deinflect add-on (inflection chains) isn't on PyPI, so it rides in
# the bundle as an SDIST (source — GPLv3's Corresponding Source) and installs via --with. Together this
# mirrors the checkout installers' [full].
$wheel = Get-ChildItem -Path $SelfDir -Filter 'saitenka_overlay-*.whl' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $wheel) {
    Write-Error '[saitenka] no overlay wheel found next to this installer - is the bundle intact?'
    exit 1
}
$dein = Get-ChildItem -Path $SelfDir -Filter 'saitenka_overlay_deinflect-*.tar.gz' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$withArgs = @()
if ($dein) { Write-Host "[saitenka] including GPL-3.0 deinflect add-on, from source ($($dein.Name))"; $withArgs = @('--with', $dein.FullName) }
# fugashi has no free-threaded Windows wheel, so 3.14t needs a source build of it, which needs BOTH the
# MSVC++ Build Tools (14+) AND MeCab at C:\mecab. Only pick 3.14t when both are present; else 3.14 (wheel).
$mecab = (Test-Path 'C:\mecab\libmecab.dll') -or (Have 'mecab')
$msvc  = (Have 'cl') -or (Test-Path "${env:ProgramFiles}\Microsoft Visual Studio\*\*\VC\Tools\MSVC") `
                     -or (Test-Path "${env:ProgramFiles(x86)}\Microsoft Visual Studio\*\*\VC\Tools\MSVC")
$pyVer = if ($mecab -and $msvc) { '3.14+freethreaded' } else { '3.14' }
$spec = "$($wheel.FullName)[jmdict]"
Write-Host "[saitenka] installing $($wheel.Name)[jmdict] (python $pyVer)"
if ($DryRun) { Write-Host "DRY: uv tool install --python $pyVer --reinstall $spec $($withArgs -join ' ')" }
else { uv tool install --python $pyVer --reinstall $spec @withArgs }

# 3. hand off to the Python wizard (mpv/ffmpeg hints, doctor, init, import, plugin). Resolve the exe
# explicitly — the freshly-installed tool may still not be on PATH in this session on some setups.
$exe = (Get-Command saitenka-overlay -ErrorAction SilentlyContinue).Source
if (-not $exe) { $exe = "$env:USERPROFILE\.local\bin\saitenka-overlay.exe" }
if ($DryRun) { Write-Host "DRY: $exe setup --dry-run" }
elseif (Test-Path $exe) { & $exe setup }
else { uv tool run --from saitenka-overlay saitenka-overlay setup }

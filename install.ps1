# saitenka installer (Windows). Bootstraps `uv`, installs `saitenka[full]` from PyPI, then hands off to
# the `setup` wizard (mpv/ffmpeg, config, the auto-start mpv plugin). Non-destructive; -DryRun previews.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://serjflint.github.io/saitenka/install.ps1 | iex"
#
# Prefer to read it first: irm <url> -OutFile install.ps1 ; then inspect and run it.
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'

# Decode the Python child processes' UTF-8 output correctly. Set ONLY [Console]::OutputEncoding — NOT
# `chcp 65001`, which breaks interactive typing in the classic console.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

function Have($name) { $null -ne (Get-Command $name -ErrorAction SilentlyContinue) }

# 1. uv — the only bootstrap (it then owns Python + every dependency, verifying PyPI hashes).
if (-not (Have 'uv')) {
    Write-Host '[saitenka] installing uv...'
    if (-not $DryRun) {
        if (Have 'winget') { winget install --id=astral-sh.uv -e }
        else { irm https://astral.sh/uv/install.ps1 | iex }
    }
}
# uv installs tools into %USERPROFILE%\.local\bin, not on PATH this session — prepend it ALWAYS so the
# `saitenka setup` handoff resolves even when uv was already present.
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

# 2. Python version: fugashi has no free-threaded Windows wheel, so 3.14t would need a source build (MSVC
#    Build Tools 14+ AND MeCab at C:\mecab). Pick 3.14t only when both are present; else 3.14 (prebuilt wheel).
$mecab = (Test-Path 'C:\mecab\libmecab.dll') -or (Have 'mecab')
$msvc  = (Have 'cl') -or (Test-Path "${env:ProgramFiles}\Microsoft Visual Studio\*\*\VC\Tools\MSVC") `
                     -or (Test-Path "${env:ProgramFiles(x86)}\Microsoft Visual Studio\*\*\VC\Tools\MSVC")
$pyVer = if ($mecab -and $msvc) { '3.14+freethreaded' } else { '3.14' }

# 3. install saitenka[full] (deinflect + jmdict + telemetry) from PyPI. --reinstall = in-place upgrade.
Write-Host "[saitenka] installing saitenka[full] from PyPI (python $pyVer)..."
if ($DryRun) { Write-Host "DRY: uv tool install --python $pyVer --reinstall saitenka[full]" }
else { uv tool install --python $pyVer --reinstall 'saitenka[full]' }

# 4. hand off to the setup wizard. Resolve the exe explicitly — the freshly-installed tool may not be on
#    PATH in this session on some setups.
$exe = (Get-Command saitenka -ErrorAction SilentlyContinue).Source
if (-not $exe) { $exe = "$env:USERPROFILE\.local\bin\saitenka.exe" }
if ($DryRun) { Write-Host "DRY: $exe setup --dry-run" }
elseif (Test-Path $exe) { & $exe setup }
else { uv tool run --from saitenka saitenka setup }

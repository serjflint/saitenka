<#
  Saitenka overlay installer - Windows (Chocolatey-first, winget fallback).
  Installs/updates the in-mpv overlay from THIS repo checkout and installs only the tools that are
  missing. Non-destructive: never upgrades/reinstalls what's present, and never touches your Anki
  collection or mpv config (the steps that write user files back up their own target at that point).
    Usage:  powershell -ExecutionPolicy Bypass -File install\install-windows.ps1 [-DryRun] [-Dev] [-Yes]
#>
param([switch]$DryRun,[switch]$Dev,[switch]$Yes)
$ErrorActionPreference = 'Continue'

# Decode the Python child processes' UTF-8 output correctly (else PowerShell uses the OEM codepage and
# shows mojibake). Set ONLY [Console]::OutputEncoding — NOT `chcp 65001`: changing the console codepage
# breaks interactive TYPING in the classic console (the y/N prompts go dead). OutputEncoding only
# affects how we DECODE child output, not input.
try {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

function Log ($m){ Write-Host "[saitenka] $m" -ForegroundColor Cyan }
function Warn($m){ Write-Host "[warn] $m"     -ForegroundColor Yellow }
function Have($c){ [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Run($cmd){ if($DryRun){ Write-Host "  DRY: $cmd" } else { Invoke-Expression $cmd } }
# Y/n prompt (default yes). -Yes answers yes to all (CI/non-interactive); -DryRun assumes yes so the
# preview shows the full plan.
function Confirm($q){ if($Yes -or $DryRun){ return $true }; (Read-Host "$q [Y/n]") -notmatch '^\s*(n|no)\s*$' }

# Capture a full transcript so a failed run leaves an artifact to attach to a bug report (see step 5).
$LogPath = Join-Path $env:TEMP ("saitenka-install-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
try { Start-Transcript -Path $LogPath -ErrorAction Stop | Out-Null } catch { $LogPath = $null }
$OverlayFailed = $false

$Repo = Split-Path $PSScriptRoot
if(-not (Test-Path (Join-Path $Repo 'overlay'))){
  Warn "no overlay\ next to this installer ($Repo) - run it from a repo checkout, or use install\overlay-install.ps1 (wheel bundle)."
  exit 1
}
$AnkiPresent = (Have anki) -or (Test-Path (Join-Path $env:APPDATA 'Anki2'))

# --- 0. Discovery ------------------------------------------------------------
Log "Discovering existing tooling..."
foreach($t in 'choco','winget','uv','mpv','ffmpeg','yt-dlp'){
  $p = Get-Command $t -ErrorAction SilentlyContinue
  if($p){ Write-Host ("  [x] {0,-9} {1}" -f $t,$p.Source) -ForegroundColor Green }
  else  { Write-Host ("  [ ] {0,-9} (missing)" -f $t) -ForegroundColor Red }
}
Write-Host ("  {0} Anki" -f $(if($AnkiPresent){'[x]'}else{'[ ]'}))

# --- 1. Packages: install ONLY what's missing (Chocolatey, else winget) ------
function Pkg($name, $choco, $winget){
  if(Have choco){ Log "choco: $choco"; Run "choco install $choco -y --no-progress --limit-output" }
  elseif((Have winget) -and $winget){ Log "winget: $winget"; Run "winget install --id $winget --exact --source winget --silent --accept-source-agreements --accept-package-agreements --disable-interactivity" }
  else { Warn "No package manager for '$name' - install Chocolatey (https://chocolatey.org/install) or winget, then re-run." }
}
# uv gets its own path: unlike mpv/ffmpeg/Anki it ships a first-party installer, so it can bootstrap
# with no package manager at all. Order mirrors the rest of the script (Chocolatey-first, winget), then
# falls back to uv's standalone installer - parity with install-macos.sh. Methods per uv's guide:
# https://docs.astral.sh/uv/getting-started/installation/
function EnsureUv {
  if(Have uv){ return }  # already listed in Discovery above; stay silent unless we install
  if(Have choco){ Log "choco: uv"; Run "choco install uv -y --no-progress --limit-output" }
  elseif(Have winget){ Log "winget: astral-sh.uv"; Run "winget install --id astral-sh.uv --exact --source winget --silent --accept-source-agreements --accept-package-agreements --disable-interactivity" }
  else { Log "no package manager - using uv's standalone installer"; Run "irm https://astral.sh/uv/install.ps1 | iex" }
  # The standalone installer drops uv in %USERPROFILE%\.local\bin (not on PATH this session) - prepend
  # it so the overlay install + doctor below resolve `uv` regardless of how it was installed.
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if(-not ((Have choco) -or (Have winget))){
  Warn "Neither Chocolatey nor winget found. uv will still self-bootstrap (standalone installer), but"
  Warn "mpv/ffmpeg/Anki need a package manager - install Chocolatey in an admin PowerShell, then re-run:"
  Warn "  Set-ExecutionPolicy Bypass -Scope Process -Force; iex ((New-Object Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))"
}
# The overlay runtime needs mpv + ffmpeg; uv provides Python 3.14t + all deps (incl. the tokenizer).
# Present tools are already listed in Discovery above — only announce an actual install.
if(-not (Have mpv))   { Pkg 'mpv'    'mpvio.install' $null }
if(-not (Have ffmpeg)){ Pkg 'ffmpeg' 'ffmpeg' 'Gyan.FFmpeg' }
EnsureUv   # uv: choco/winget if present, else uv's standalone installer (no package manager needed)
if($AnkiPresent){ }
elseif(Confirm 'Install Anki now (needed for mining + FSRS coloring)?'){ Pkg 'Anki' 'anki' 'Anki.Anki' }
else { Log "skipped Anki - install later from https://apps.ankiweb.net, then re-run" }

# --- 2. Install / update the overlay from THIS checkout ----------------------
# uv drops tool binaries (saitenka-overlay.exe) into %USERPROFILE%\.local\bin. A shell that's already
# open won't have it on PATH, so prepend it for THIS session - otherwise the install below and the
# next-steps `saitenka-overlay ...` commands would fail with "not recognized". New terminals: uv
# registers this dir persistently, but you must open a fresh terminal (or run `uv tool update-shell`)
# for it to take effect outside this installer.
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
# Install the FULL experience via the `[full]` extra (JMdict fallback + the GPL-3.0 deinflect
# inflection chains) when the deinflect source is present in this checkout; otherwise `[jmdict]` (full
# minus the add-on). `[full]` is GPL-3.0 (see ../LICENSING.md); a wheel/bundle install with no
# deinflect\ stays Apache-2.0. On Windows `[jmdict]` pulls jamdict-data-fix, which builds cleanly (the
# plain `saitenka-overlay` has no jamdict-data at all — that upstream sdist is what failed here).
if(Test-Path (Join-Path $Repo 'deinflect')){ $extra = 'full'; Log "including GPL-3.0 deinflect add-on (inflection chains)" }
else { $extra = 'jmdict'; Warn "no deinflect\ in this checkout - installing [jmdict] only (no inflection chains)" }
# Pick the Python: fugashi (the MeCab tokenizer) has no free-threaded Windows wheel, so 3.14t needs a
# SOURCE build of it, which needs BOTH the MSVC++ Build Tools (14.0+) AND MeCab extracted at C:\mecab.
# Use 3.14t (the ~3.8x render win) only when both are present — else the build fails; otherwise regular
# 3.14 (fugashi from a wheel). Overrides the repo's free-threaded .python-version pin so a default
# install never fails. (macOS/Linux keep the FT pin — they have free-threaded fugashi wheels.)
$mecab = (Test-Path 'C:\mecab\libmecab.dll') -or (Have mecab)
$msvc  = (Have cl) -or (Test-Path "${env:ProgramFiles}\Microsoft Visual Studio\*\*\VC\Tools\MSVC") `
                   -or (Test-Path "${env:ProgramFiles(x86)}\Microsoft Visual Studio\*\*\VC\Tools\MSVC")
if($mecab -and $msvc){ $pyVer = '3.14+freethreaded'; Log "MeCab + MSVC++ found - using free-threaded 3.14t (fugashi builds from source)" }
else {
  $pyVer = '3.14'
  $need = @(); if(-not $msvc){ $need += 'MSVC++ Build Tools 14+' }; if(-not $mecab){ $need += 'MeCab at C:\mecab' }
  Log ("standard 3.14 - for the 3.14t render speedup, install " + ($need -join ' + ') + ", then re-run")
}
# --quiet suppresses the ~30-line reinstalled-package list; build FAILURES still print (they go to
# stderr), so the "Failed to build ..." block the outcome section points at is unaffected.
$installArgs = @('tool','install','--python',$pyVer,'--reinstall','--quiet',"$Repo\overlay[$extra]")
if(Have uv){
  Log "Installing/updating saitenka-overlay[$extra] from $Repo\overlay"
  if($DryRun){ Write-Host "  DRY: uv $($installArgs -join ' ')" }
  else {
    & uv @installArgs
    # A transient build hiccup (an AV/indexer briefly locking a freshly-written file, [WinError 32])
    # is common on Windows - retry once before giving up.
    if($LASTEXITCODE -ne 0){
      Warn "overlay install failed (uv exit $LASTEXITCODE) - retrying once in 3s (transient file locks are common)..."
      Start-Sleep -Seconds 3
      & uv @installArgs
    }
    if($LASTEXITCODE -ne 0){ $OverlayFailed = $true; Warn "overlay install still failing (uv exit $LASTEXITCODE)." }
  }
} else { Warn "uv unavailable - install it, then re-run to install the overlay."; $OverlayFailed = $true }

# Persist %USERPROFILE%\.local\bin on PATH for FUTURE terminals (a choco-installed uv won't have done
# this). This session already has it (prepended above); new terminals also need a restart to pick it up.
if((-not $OverlayFailed) -and (-not $DryRun) -and (Have uv)){ uv tool update-shell }

# --- 3. Dev/authoring extras (-Dev only) -------------------------------------
if($Dev){
  Pkg 'git' 'git' 'Git.Git'
  Pkg 'gh'  'gh'  'GitHub.cli'
  Pkg 'node' 'nodejs-lts' 'OpenJS.NodeJS.LTS'
  Pkg 'obsidian' 'obsidian' 'Obsidian.Obsidian'
  if((Have uv) -and (-not (Have apy))){ Log "+ apy (apyanki)"; Run "uv tool install apyanki" }
}

# --- 4. Outcome: don't cheerfully say "Done." if the overlay didn't install --
# (The guided setup in §6 runs the overlay's own doctor twice - an initial read and a final
# self-verify - so there's no separate doctor-windows.ps1 pass here. Run it standalone any time:
# powershell -ExecutionPolicy Bypass -File install\doctor-windows.ps1)
if($OverlayFailed){
  Write-Host ""
  Write-Host "[saitenka] INSTALL DID NOT COMPLETE - saitenka-overlay is not installed." -ForegroundColor Red
  Write-Host "How to report this so it can be fixed:" -ForegroundColor Yellow
  Write-Host "  1. Copy the error above - the 'Failed to build ...' block is the cause."
  if($LogPath){ Write-Host "     A full log was saved here (attach it):  $LogPath" }
  if(Have saitenka-overlay){ Write-Host "  2. Run  saitenka-overlay report  and attach the zip it writes (secrets are redacted)." }
  else { Write-Host "  2. Also include your Windows version and the output of:  uv --version" }
  Write-Host "  3. Open an issue:  https://github.com/serjflint/saitenka/issues"
  if($LogPath){ try { Stop-Transcript | Out-Null } catch {} }
  exit 1
}

# --- 6. Guided setup: the overlay's own confirm-first wizard -----------------
# Rather than leave the plugin / jimaku key / dictionaries as manual chores, hand off to `setup`: it
# prompts to install the mpv plugin (auto-launch overlay on any mpv), store your jimaku key, and
# import your Yomitan dictionaries. Confirm-first and resumable; -Yes passes --yes. The summary below
# then reflects whatever setup configured.
if(Have saitenka-overlay){
  $setupArgs = @('setup')
  if($Yes){ $setupArgs += '--yes' }
  if($DryRun){ $setupArgs += '--dry-run' }
  Log "Guided setup (mpv plugin / jimaku key / dictionaries)..."
  & saitenka-overlay @setupArgs
} else {
  Warn "saitenka-overlay isn't on PATH this session - open a NEW terminal and run:  saitenka-overlay setup"
}

Log "Done."

# --- Next steps: reflect current state (tick what's already done) ------------
$Addons = Join-Path $env:APPDATA 'Anki2\addons21'
function AddonLine($code,$nm,$note){
  if(Test-Path (Join-Path $Addons $code)){ Write-Host ("       [x] {0,-14} installed" -f $nm) -ForegroundColor Green }
  else { Write-Host ("       [ ] {0,-14} paste {1} ({2})" -f $nm,$code,$note) -ForegroundColor Yellow }
}
# Config resolves platform-native via platformdirs: %LOCALAPPDATA%\saitenka\overlay.toml on Windows
# (same root as the dict cache, AppData\Local\saitenka). No ~/.config fallback — that XDG path is
# POSIX-only and the tool never writes it on Windows.
$Cfg = if($env:SAITENKA_CONFIG){ $env:SAITENKA_CONFIG }
       else { Join-Path $env:LOCALAPPDATA 'saitenka\overlay.toml' }
# Dictionaries are imported ONCE into the consolidated database (dictionaries.sqlite); the config then
# lists their TITLES (not zip paths). Present = that DB exists AND the config references a dict title.
$DictDb = if($env:SAITENKA_DATA_DIR){ Join-Path $env:SAITENKA_DATA_DIR 'dictionaries.sqlite' }
          else { Join-Path $env:LOCALAPPDATA 'saitenka\dictionaries.sqlite' }
function DictsPresent(){
  if(-not ((Test-Path $DictDb) -and (Test-Path $Cfg))){ return $false }
  [bool]((Get-Content $Cfg | Where-Object { $_ -notmatch '^\s*#' }) -match '^\s*(dicts|freq|pitch)\s*=\s*\[')
}

Write-Host ""
Write-Host "Next steps:"
# mpv reads scripts from %APPDATA%\mpv\scripts on Windows (mpv.net: %APPDATA%\mpv.net\scripts).
$PluginDirs = @((Join-Path $env:APPDATA 'mpv\scripts\saitenka.lua'), (Join-Path $env:APPDATA 'mpv.net\scripts\saitenka.lua'))
if($PluginDirs | Where-Object { Test-Path $_ }){ Write-Host "  1. mpv plugin:  [x] installed (auto-starts the overlay on any mpv launch)" -ForegroundColor Green }
else {
  Write-Host "  1. mpv plugin not installed - re-run  saitenka-overlay setup  (or:  saitenka-overlay install-plugin)"
}
Write-Host "  2. Anki add-ons (Tools -> Add-ons -> Get Add-ons):"
AddonLine '2055492159' 'AnkiConnect'    'mining + FSRS coloring'
AddonLine '759844606'  'FSRS Helper'    'better scheduling'
AddonLine '1771074083' 'Review Heatmap' 'streak view'
if(DictsPresent){ Write-Host "  3. Dictionaries:  [x] imported into the database (see saitenka-overlay doctor)" -ForegroundColor Green }
else {
  Write-Host "  3. Dictionaries: run  saitenka-overlay import <folder of your Yomitan .zip dicts>"
  Write-Host "     (imports them once into the consolidated database and fills the config with their titles)."
  Write-Host "       config: $Cfg"
  Write-Host "     Have a Yomitan settings export? saitenka-overlay import-settings <export.json> --scan-dir <folder>"
}
# jimaku is "set up" if the env var is set OR the config has a [jimaku] table (set-jimaku-key writes
# [jimaku].fetch=true even when the key itself lives in the Credential Locker, which a shell can't read).
$jimakuSet = [bool]$env:JIMAKU_API_KEY -or ((Test-Path $Cfg) -and (Select-String -Path $Cfg -Pattern '^\s*\[jimaku\]' -Quiet))
if($jimakuSet){ Write-Host "  4. jimaku auto-subs:  [x] configured" -ForegroundColor Green }
else {
  Write-Host "  4. jimaku auto-subs (optional): run  saitenka-overlay set-jimaku-key"
  Write-Host "     (stores the key + enables fetch for files with no JP track; skip if done in setup above)"
}
if($Dev){ Write-Host "`nDev/authoring: open this folder in Obsidian (start at notes\), Anki MCP for Claude Code via /mcp." }
if($LogPath){ try { Stop-Transcript | Out-Null } catch {} }

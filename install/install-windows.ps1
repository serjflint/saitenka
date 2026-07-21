<#
  Saitenka overlay installer - Windows (Chocolatey-first, winget fallback).
  Installs/updates the in-mpv overlay from THIS repo checkout and installs only the tools that are
  missing. Non-destructive: never upgrades/reinstalls what's present, and never touches your Anki
  collection or mpv config (the steps that write user files back up their own target at that point).
    Usage:  powershell -ExecutionPolicy Bypass -File install\install-windows.ps1 [-DryRun] [-Dev]
#>
param([switch]$DryRun,[switch]$Dev)
$ErrorActionPreference = 'Continue'

function Log ($m){ Write-Host "[saitenka] $m" -ForegroundColor Cyan }
function Warn($m){ Write-Host "[warn] $m"     -ForegroundColor Yellow }
function Have($c){ [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Run($cmd){ if($DryRun){ Write-Host "  DRY: $cmd" } else { Invoke-Expression $cmd } }

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
if(-not ((Have choco) -or (Have winget))){
  Warn "Neither Chocolatey nor winget found. Install Chocolatey in an admin PowerShell:"
  Warn "  Set-ExecutionPolicy Bypass -Scope Process -Force; iex ((New-Object Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))"
}
# The overlay runtime needs mpv + ffmpeg; uv provides Python 3.14t + all deps (incl. the tokenizer).
if(-not (Have mpv))   { Pkg 'mpv'    'mpvio.install' $null } else { Log "[x] mpv present (left as-is)" }
if(-not (Have ffmpeg)){ Pkg 'ffmpeg' 'ffmpeg' 'Gyan.FFmpeg' } else { Log "[x] ffmpeg present (left as-is)" }
if(-not (Have uv))    { Pkg 'uv'     'uv' 'astral-sh.uv' } else { Log "[x] uv present (left as-is)" }
if(-not $AnkiPresent) { Pkg 'Anki'   'anki' 'Anki.Anki' } else { Log "[x] Anki present (left as-is)" }

# --- 2. Install / update the overlay from THIS checkout ----------------------
if(Have uv){
  Log "Installing/updating saitenka-overlay from $Repo\overlay"
  Run "uv tool install --reinstall '$Repo\overlay'"
} else { Warn "uv unavailable - install it, then re-run to install the overlay." }

# --- 3. Dev/authoring extras (-Dev only) -------------------------------------
if($Dev){
  Pkg 'git' 'git' 'Git.Git'
  Pkg 'gh'  'gh'  'GitHub.cli'
  Pkg 'node' 'nodejs-lts' 'OpenJS.NodeJS.LTS'
  Pkg 'obsidian' 'obsidian' 'Obsidian.Obsidian'
  if((Have uv) -and (-not (Have apy))){ Log "+ apy (apyanki)"; Run "uv tool install apyanki" }
}

# --- 4. Health check (the overlay's own doctor) ------------------------------
$doctor = Join-Path $PSScriptRoot 'doctor-windows.ps1'
if(Test-Path $doctor){ Log "Running healthcheck (doctor-windows.ps1)..."; & powershell -ExecutionPolicy Bypass -File $doctor }
else { Warn "doctor-windows.ps1 not found next to the installer - skipping healthcheck." }

Log "Done."

# --- Next steps: reflect current state (tick what's already done) ------------
$Addons = Join-Path $env:APPDATA 'Anki2\addons21'
function AddonLine($code,$nm,$note){
  if(Test-Path (Join-Path $Addons $code)){ Write-Host ("       [x] {0,-14} installed" -f $nm) -ForegroundColor Green }
  else { Write-Host ("       [ ] {0,-14} paste {1} ({2})" -f $nm,$code,$note) -ForegroundColor Yellow }
}
$Cfg = if($env:SAITENKA_CONFIG){ $env:SAITENKA_CONFIG } else { Join-Path $HOME '.config\saitenka\overlay.toml' }
function DictsPresent(){
  if(-not (Test-Path $Cfg)){ return 0 }
  $n = 0
  Get-Content $Cfg | Where-Object { $_ -notmatch '^\s*#' } | Select-String -Pattern '"([^"]*\.zip)"' -AllMatches | ForEach-Object {
    foreach($m in $_.Matches){ $p = $m.Groups[1].Value -replace '^~', $HOME; if(Test-Path $p){ $n++ } }
  }
  return $n
}

Write-Host ""
Write-Host "Next steps:"
$Plugin = Join-Path $HOME '.config\mpv\scripts\saitenka.lua'
if(Test-Path $Plugin){ Write-Host "  1. mpv plugin:  [x] installed (auto-starts the overlay on any mpv launch)" -ForegroundColor Green }
else {
  Write-Host "  1. Install the mpv plugin (auto-starts on any mpv launch):  saitenka-overlay install-plugin"
  Write-Host "     - or the full wizard (config, dict relocation, plugin):  saitenka-overlay setup"
}
Write-Host "  2. Anki add-ons (Tools -> Add-ons -> Get Add-ons):"
AddonLine '2055492159' 'AnkiConnect'    'mining + FSRS coloring'
AddonLine '759844606'  'FSRS Helper'    'better scheduling'
AddonLine '1771074083' 'Review Heatmap' 'streak view'
$dc = DictsPresent
if($dc -gt 0){ Write-Host ("  3. Dictionaries:  [x] {0} configured and present on disk" -f $dc) -ForegroundColor Green }
else { Write-Host "  3. Import your Yomitan dictionaries in the browser, then point overlay.toml at them (or run saitenka-overlay import-yomitan)." }
if($env:JIMAKU_API_KEY){ Write-Host "  4. jimaku key for auto-subs:  [x] set (`$env:JIMAKU_API_KEY)" -ForegroundColor Green }
else { Write-Host "  4. jimaku key for auto-subs (optional): set `$env:JIMAKU_API_KEY, or [jimaku].key in overlay.toml" }
if($Dev){ Write-Host "`nDev/authoring: open this folder in Obsidian (start at notes\), Anki MCP for Claude Code via /mcp." }

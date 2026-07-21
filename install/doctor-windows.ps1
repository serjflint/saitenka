<#
  Saitenka doctor - Windows. Read-only health check for the overlay. Changes NOTHING.
  The overlay's own `saitenka-overlay doctor` is the source of truth (mpv/ffmpeg, config, dicts,
  AnkiConnect, plugin, SubMiner conflict, jimaku key, sub-auto); this wrapper adds the shell-level
  toolchain check and hands off to it. Exit 0 = healthy, 1 = failures.
    Usage:  powershell -ExecutionPolicy Bypass -File doctor-windows.ps1
#>
$pass = 0; $warn = 0; $fail = 0
function OK($m){ Write-Host "  [x] $m" -ForegroundColor Green;  $script:pass++ }
function WN($m){ Write-Host "  [!] $m" -ForegroundColor Yellow; $script:warn++ }
function ER($m){ Write-Host "  [X] $m" -ForegroundColor Red;    $script:fail++ }
function HDR($m){ Write-Host "`n$m" -ForegroundColor White }
function Have($c){ [bool](Get-Command $c -ErrorAction SilentlyContinue) }

Write-Host "[saitenka doctor] Windows $([System.Environment]::OSVersion.Version)" -ForegroundColor Cyan

HDR "Toolchain"
foreach($t in 'mpv','ffmpeg','uv'){
  if(Have $t){ OK "$t  $((Get-Command $t).Source)" } else { ER "$t missing (choco install $t)" }
}
Write-Host ("`nSummary (toolchain): {0} ok / {1} warn / {2} fail" -f $pass,$warn,$fail)

# Hand off to the overlay's own doctor (the authoritative, unit-tested checks).
$ov = 1
if(Have saitenka-overlay){
  HDR "Overlay (saitenka-overlay doctor)"
  & saitenka-overlay doctor
  $ov = $LASTEXITCODE
} else {
  ER "saitenka-overlay not installed - run install\install-windows.ps1"
}

if(($fail -eq 0) -and ($ov -eq 0)){ Write-Host "Healthy" -ForegroundColor Green; exit 0 }
else { Write-Host "Problems found - see [X] above" -ForegroundColor Red; exit 1 }

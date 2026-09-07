@echo off
rem Upgrade-FreeFlow.bat - upgrades ANY FreeFlow installation (including the very first packages) to the
rem latest release on GitHub. Put it next to FreeFlow.exe and double-click it, or run it from anywhere: it
rem finds the installation (running FreeFlow, or its start-with-Windows entry), downloads the current full
rem package (about 1.2 GB), replaces the files, and starts the new FreeFlow. Settings are kept.
set "FF_HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ((Get-Content -Raw '%~f0') -split (':PS'+'START'),2)[1]"
exit /b
:PSSTART
$ErrorActionPreference = 'Stop'
try {
  $host.UI.RawUI.WindowTitle = 'FreeFlow upgrade'
  Write-Host ''
  Write-Host ' FreeFlow - upgrade to the latest release' -ForegroundColor Cyan
  Write-Host ' ----------------------------------------'
  $repo = 'luizrodrigues987-source/FreeFlow'
  $here = ($env:FF_HERE).TrimEnd('\')
  $target = $null
  if (Test-Path (Join-Path $here 'FreeFlow.exe')) { $target = $here }
  if (-not $target) {
    $p = Get-Process FreeFlow -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($p -and $p.Path) { $target = Split-Path -Parent $p.Path }
  }
  if (-not $target) {
    try {
      $v = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name FreeFlow -ErrorAction Stop).FreeFlow
      if ($v -match '"([^"]*FreeFlow\.exe)"') { $target = Split-Path -Parent $Matches[1] }
    } catch {}
  }
  if (-not $target) {
    $target = Join-Path $env:LOCALAPPDATA 'Programs\FreeFlow'
    Write-Host " No existing FreeFlow found - installing to $target"
  } else {
    Write-Host " Upgrading the FreeFlow in $target"
  }
  Write-Host ' Looking up the latest release...'
  $rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'FreeFlow-upgrader' }
  $asset = $rel.assets | Where-Object { $_.name -like 'FreeFlow-*-win64.zip' } | Select-Object -First 1
  if (-not $asset) { throw 'The latest release has no full package.' }
  Write-Host (' Latest release: {0}  ({1:N0} MB to download)' -f $rel.tag_name, ($asset.size / 1MB))
  $exe = Join-Path $target 'FreeFlow.exe'
  if (Get-Process FreeFlow -ErrorAction SilentlyContinue) {
    Write-Host ' Closing FreeFlow...'
    if (Test-Path $exe) { Start-Process $exe -ArgumentList '--quit' -WindowStyle Hidden -ErrorAction SilentlyContinue }
    Start-Sleep 4
    Get-Process FreeFlow -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  }
  $tmp = Join-Path $env:TEMP 'FreeFlow-upgrade'
  New-Item -ItemType Directory -Force $tmp | Out-Null
  $zip = Join-Path $tmp $asset.name
  if (Test-Path $zip) { Remove-Item $zip -Force }
  Write-Host ' Downloading... (a progress bar appears; this takes a few minutes)'
  try {
    Import-Module BitsTransfer -ErrorAction Stop
    Start-BitsTransfer -Source $asset.browser_download_url -Destination $zip -DisplayName 'FreeFlow download' -Description $asset.name
  } catch {
    Write-Host ' (using a plain download)'
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing
  }
  if ((Get-Item $zip).Length -lt 100MB) { throw 'The download looks incomplete - please run this again.' }
  Write-Host ' Unpacking... (1-3 minutes)'
  $x = Join-Path $tmp 'x'
  if (Test-Path $x) { Remove-Item $x -Recurse -Force }
  Expand-Archive -Path $zip -DestinationPath $x -Force
  $src = Join-Path $x 'FreeFlow'
  if (-not (Test-Path (Join-Path $src 'FreeFlow.exe'))) { throw 'Unexpected package layout.' }
  New-Item -ItemType Directory -Force $target | Out-Null
  $looksLikeInstall = (Test-Path (Join-Path $target '_internal')) -or ((Get-ChildItem $target -Force | Measure-Object).Count -eq 0)
  if (-not $looksLikeInstall) { throw "The folder $target does not look like a FreeFlow installation. Put this file next to FreeFlow.exe and run it again." }
  Write-Host ' Installing...'
  & robocopy $src $target /MIR /XF Upgrade-FreeFlow.bat /NFL /NDL /NJH /NJS /NP /R:3 /W:2 | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "Copying the files failed (robocopy code $LASTEXITCODE)." }
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host ' Starting the new FreeFlow...'
  Start-Process $exe
  Write-Host ''
  Write-Host ' Done. From now on FreeFlow updates itself automatically.' -ForegroundColor Green
  Start-Sleep 5
} catch {
  Write-Host ''
  Write-Host (' Upgrade failed: ' + $_.Exception.Message) -ForegroundColor Red
  Write-Host (' You can always download the package yourself: https://github.com/' + $repo + '/releases/latest')
  Read-Host ' Press Enter to close'
}

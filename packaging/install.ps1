# Video Studio per-user installer. No admin rights, no system changes:
#   files  -> %LOCALAPPDATA%\Programs\VideoStudio
#   PATH   -> user PATH only
#   menu   -> Start menu shortcut "Video Studio" (opens the Studio UI)
#   uninstall -> Settings > Apps, or uninstall.ps1 in the install folder
# Your projects, memory and settings (%LOCALAPPDATA%\VideoStudio) are never touched by install or uninstall.
param([string]$Source = $PSScriptRoot, [switch]$Quiet)
$ErrorActionPreference = "Stop"
$dest = Join-Path $env:LOCALAPPDATA "Programs\VideoStudio"
$zip = Join-Path $Source "VideoStudio.zip"
if (-not (Test-Path $zip)) { throw "VideoStudio.zip not found next to the installer." }

$stage = Join-Path (Split-Path $dest -Parent) ("VideoStudio-stage-" + [guid]::NewGuid().ToString('N'))
$backup = "$dest-previous"
# Extract and validate before touching the installed copy. Retain the previous
# installation for recovery; user projects/settings live in a separate folder.
New-Item -ItemType Directory -Force $stage | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
$ver = & (Join-Path $stage "video-studio.exe") version 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $ver -notmatch 'video-studio (\d+\.\d+\.\d+)') { throw "The extracted app failed its startup check. Existing installation was kept." }
$version = $Matches[1]
if (-not (Test-Path (Join-Path $stage "VideoStudio.exe"))) { throw "Desktop launcher is missing. Existing installation was kept." }
if (Test-Path $backup) { $backup = "$backup-" + [guid]::NewGuid().ToString('N') }
Get-Process video-studio,VideoStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -and (Split-Path $_.Path -Parent) -eq $dest } | Stop-Process -Force
if (Test-Path $dest) { Move-Item -LiteralPath $dest -Destination $backup }
try { Move-Item -LiteralPath $stage -Destination $dest }
catch {
    if ((Test-Path $backup) -and -not (Test-Path $dest)) { Move-Item -LiteralPath $backup -Destination $dest }
    throw
}
Copy-Item (Join-Path $Source "uninstall.ps1") $dest -Force

# user PATH (idempotent)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if (($userPath -split ";") -notcontains $dest) {
    [Environment]::SetEnvironmentVariable("Path", ($userPath.TrimEnd(";") + ";" + $dest).TrimStart(";"), "User")
}

# Start menu shortcut
$menu = Join-Path ([Environment]::GetFolderPath("Programs")) "Video Studio.lnk"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($menu)
$lnk.TargetPath = Join-Path $dest "VideoStudio.exe"
$lnk.Arguments = ""
$lnk.WorkingDirectory = $dest
$lnk.Description = "Video Studio - edit footage or turn a script into video"
$lnk.Save()
$desktop = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Video Studio.lnk"))
$desktop.TargetPath = $lnk.TargetPath
$desktop.WorkingDirectory = $dest
$desktop.Description = $lnk.Description
$desktop.Save()

# Apps & features entry (per user)
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoStudio"
New-Item -Path $key -Force | Out-Null
$size = [int]((Get-ChildItem $dest -Recurse | Measure-Object Length -Sum).Sum / 1KB)
$props = @{
    DisplayName = "Video Studio"; DisplayVersion = $version; Publisher = "Video Studio"; InstallLocation = $dest
    DisplayIcon = (Join-Path $dest "video-studio.exe"); EstimatedSize = $size; NoModify = 1; NoRepair = 1
    UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$dest\uninstall.ps1`""
    QuietUninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$dest\uninstall.ps1`" -Quiet"
}
foreach ($k in $props.Keys) { Set-ItemProperty -Path $key -Name $k -Value $props[$k] }

$ver = & (Join-Path $dest "video-studio.exe") version 2>&1 | Select-Object -First 1
Write-Host "Installed $ver to $dest"
Write-Host "Open a NEW terminal and run:  video-studio doctor     (or use 'Video Studio' in the Start menu)"
if (-not $Quiet) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("Video Studio is installed.`n`nStart menu: Video Studio`nTerminal: video-studio doctor", "Video Studio") | Out-Null
}

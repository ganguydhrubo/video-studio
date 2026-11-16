# Removes the app files, the PATH entry, the Start menu shortcut and the Apps entry.
# Keeps your projects, memory and settings unless you pass -RemoveData.
param([switch]$Quiet, [switch]$RemoveData)
$ErrorActionPreference = "Continue"
$dest = Join-Path $env:LOCALAPPDATA "Programs\VideoStudio"
Get-Process video-studio -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dest*" } | Stop-Process -Force

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath) {
    $clean = ($userPath -split ";" | Where-Object { $_ -and ($_.TrimEnd("\") -ne $dest) }) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $clean, "User")
}
Remove-Item (Join-Path ([Environment]::GetFolderPath("Programs")) "Video Studio.lnk") -ErrorAction SilentlyContinue
Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoStudio" -Recurse -ErrorAction SilentlyContinue
if ($RemoveData) { Remove-Item (Join-Path $env:LOCALAPPDATA "VideoStudio") -Recurse -Force -ErrorAction SilentlyContinue }

# The script lives inside $dest: delete the folder from a detached process after we exit.
Start-Process -WindowStyle Hidden powershell.exe -ArgumentList "-NoProfile", "-Command", "Start-Sleep 2; Remove-Item -LiteralPath '$dest' -Recurse -Force"
Write-Host "Video Studio removed. Your projects and memory were kept$(if ($RemoveData) {' (data removed too)'})."
if (-not $Quiet) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("Video Studio was removed.", "Video Studio") | Out-Null
}

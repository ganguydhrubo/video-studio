@echo off
setlocal
set "VS_LOG=%LOCALAPPDATA%\VideoStudio\logs\install.log"
if not exist "%LOCALAPPDATA%\VideoStudio\logs" mkdir "%LOCALAPPDATA%\VideoStudio\logs"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %* >"%VS_LOG%" 2>&1
set "VS_CODE=%errorlevel%"
if not "%VS_CODE%"=="0" if /i not "%~1"=="-Quiet" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show(('Installation failed. See ' + $env:VS_LOG), 'Video Studio')" >nul 2>&1
exit /b %VS_CODE%

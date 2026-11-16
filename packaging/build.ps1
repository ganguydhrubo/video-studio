# Builds dist\VideoStudio-Setup-<version>.exe
#  1. isolated build venv (packaging\.venv) with PyInstaller + Pillow + NumPy - nothing installed system-wide
#  2. PyInstaller one-folder build
#  3. zip + IExpress (built into Windows) self-extracting setup that runs install.ps1 (per-user, no admin)
param([switch]$SkipVenv, [string]$BuildPython)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$pkg = $PSScriptRoot
$version = (Select-String -Path "$root\video_studio\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$venv = Join-Path $pkg ".venv"
$py = Join-Path $venv "Scripts\python.exe"
if ($BuildPython) {
    $py = (Resolve-Path -LiteralPath $BuildPython).Path
    $SkipVenv = $true
}

if (-not $SkipVenv -or -not (Test-Path $py)) {
    if (-not (Test-Path $py)) { python -m venv $venv }
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet "pyinstaller==6.*" "pillow>=10" "numpy>=1.26"
}

$work = Join-Path $pkg "build"
$dist = Join-Path $root "dist"
Remove-Item $work, "$dist\VideoStudio" -Recurse -Force -ErrorAction SilentlyContinue
& $py -m PyInstaller --noconfirm --clean --workpath $work --distpath $dist (Join-Path $pkg "video_studio.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
& "$dist\VideoStudio\video-studio.exe" version
if ($LASTEXITCODE -ne 0) { throw "Frozen build does not start" }

$stage = Join-Path $pkg "stage"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $stage | Out-Null
Compress-Archive -Path "$dist\VideoStudio\*" -DestinationPath "$stage\VideoStudio.zip" -CompressionLevel Optimal
Copy-Item "$pkg\install.ps1", "$pkg\uninstall.ps1", "$pkg\install.cmd" $stage

# IExpress is a legacy tool: keep its source and output paths short, even if
# the project is under a long workspace path.
$iexpressStage = Join-Path ([IO.Path]::GetTempPath()) ("vs-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $iexpressStage | Out-Null
Copy-Item "$stage\*" $iexpressStage
$iexpressSetup = Join-Path $iexpressStage "VideoStudio-Setup-$version.exe"

$setup = Join-Path $dist "VideoStudio-Setup-$version.exe"
$sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$iexpressSetup
FriendlyName=Video Studio $version Setup
AppLaunched=install.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=install.cmd -Quiet
UserQuietInstCmd=install.cmd -Quiet
SourceFiles=SourceFiles
[Strings]
FILE0="VideoStudio.zip"
FILE1="install.ps1"
FILE2="uninstall.ps1"
FILE3="install.cmd"
[SourceFiles]
SourceFiles0=$iexpressStage\
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
%FILE3%=
"@
$sedPath = Join-Path $iexpressStage "setup.sed"
[IO.File]::WriteAllText($sedPath, (($sed -replace '\r?\n', "`r`n") + "`r`n"), [Text.Encoding]::ASCII)
Remove-Item $setup -ErrorAction SilentlyContinue
$buildPath = $env:Path
try {
    $env:Path = "$env:WINDIR\System32;$env:WINDIR"
    $iexpress = Start-Process -Wait -PassThru -WindowStyle Hidden "$env:WINDIR\System32\iexpress.exe" -ArgumentList "/N", "/Q", $sedPath -WorkingDirectory $iexpressStage
} finally { $env:Path = $buildPath }
if ($iexpress.ExitCode -ne 0 -or -not (Test-Path $iexpressSetup)) { throw "IExpress failed ($($iexpress.ExitCode)); build files: $iexpressStage" }
Copy-Item -LiteralPath $iexpressSetup -Destination $setup -Force
Copy-Item "$stage\VideoStudio.zip" (Join-Path $dist "VideoStudio-$version-portable.zip") -Force
Copy-Item -LiteralPath $setup -Destination (Join-Path $dist 'VideoStudio-Setup.exe') -Force
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
@($setup, (Join-Path $dist 'VideoStudio-Setup.exe'), (Join-Path $dist "VideoStudio-$version-portable.zip")) | ForEach-Object {
    "$((Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLower())  $(Split-Path $_ -Leaf)"
} | Set-Content (Join-Path $dist "SHA256SUMS.txt")
Write-Host "Built $setup ($([math]::Round((Get-Item $setup).Length / 1MB, 1)) MB) sha256 $hash"

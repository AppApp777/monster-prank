$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
# This script lives in packaging/ ; the project root is one level up.
# KEEP THIS FILE ASCII-ONLY (PowerShell 5.1 reads BOM-less .ps1 as ANSI).
$Root = Split-Path -Parent $Here
Set-Location -LiteralPath $Root

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $python) {
    throw "Python 3.10 or newer is required."
}

$pyavVersion = (& $python.Source -c "import av; print(av.__version__)" 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $pyavVersion) {
    throw "Windows build requires the PyAV package. Run: $($python.Source) -m pip install av"
}
$avPackage = (& $python.Source -c "import av; print(av.__file__)" 2>$null | Out-String).Trim()
$avPackageDir = Split-Path -Parent $avPackage
$avLibs = Join-Path (Split-Path -Parent $avPackageDir) "av.libs"
if (-not (Test-Path -LiteralPath $avLibs)) {
    throw "PyAV native libraries were not found: $avLibs"
}

# Ship the VP9+alpha webm (3.3 MB); the ProRes .mov (100 MB) stays in the repo
# as the master only. NOTE: keep this file ASCII-only -- without a BOM,
# PowerShell 5.1 reads it as ANSI and UTF-8 comment bytes can swallow newlines.
$video = Join-Path $Root "assets\monster_transparent_burst_shake.webm"
$poster = Join-Path $Root "assets\monster_transparent_burst_shake_poster.png"
$thumb = Join-Path $Root "assets\monster_transparent_burst_shake_thumb.png"
$metadata = Join-Path $Root "assets\monster_transparent_burst_shake_metadata.json"
$audio = Join-Path $Root "assets\monster_transparent_burst_shake_audio.wav"
if (-not (Test-Path -LiteralPath $video)) {
    throw "Missing default transparent video: $video"
}
if (-not (Test-Path -LiteralPath $poster)) {
    throw "Missing default poster: $poster"
}
if (-not (Test-Path -LiteralPath $metadata)) {
    throw "Missing video metadata: $metadata"
}
if (-not (Test-Path -LiteralPath $audio)) {
    throw "Missing default audio: $audio"
}
$icon = Join-Path $Root "assets\logo\logo.ico"
$iconPng = Join-Path $Root "assets\logo\logo-128.png"
if (-not (Test-Path -LiteralPath $icon)) {
    throw "Missing application icon: $icon"
}
$versionFile = Join-Path $Here "version_info.txt"
if (-not (Test-Path -LiteralPath $versionFile)) {
    throw "Missing version resource: $versionFile"
}

$outputRoot = Join-Path $Root "dist\windows"

# Remove stale default-distpath output from earlier builds so only
# dist\windows\MonsterPrank remains as the shippable package.
$staleDist = Join-Path $Root "dist\MonsterPrank"
if (Test-Path -LiteralPath $staleDist) {
    Remove-Item -LiteralPath $staleDist -Recurse -Force
}

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--clean",
    "--noconfirm",
    "--onedir",
    "--windowed",
    "--icon", $icon,
    "--name", "MonsterPrank",
    "--version-file", $versionFile,
    "--distpath", $outputRoot,
    "--workpath", (Join-Path $Root "build"),
    "--specpath", (Join-Path $Root "build"),
    "--collect-all", "av",
    "--collect-all", "customtkinter",
    "--exclude-module", "numpy",
    "--exclude-module", "PIL.AvifImagePlugin",
    "--add-data", ("{0};assets" -f $video),
    "--add-data", ("{0};assets" -f $poster),
    "--add-data", ("{0};assets" -f $thumb),
    "--add-data", ("{0};assets" -f $metadata),
    "--add-data", ("{0};assets" -f $audio),
    "--add-data", ("{0};assets" -f $icon),
    "--add-data", ("{0};assets" -f $iconPng)
)
foreach ($avLibrary in (Get-ChildItem -LiteralPath $avLibs -Filter "*.dll" -File)) {
    $pyinstallerArgs += @("--add-binary", ("{0};av.libs" -f $avLibrary.FullName))
}
$pyinstallerArgs += (Join-Path $Root "monster_prank.py")

Write-Host "Building Windows package..."
& $python.Source @pyinstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$packageDir = Join-Path $outputRoot "MonsterPrank"
$exe = Join-Path $packageDir "MonsterPrank.exe"
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination (Join-Path $packageDir "README.md") -Force
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Build completed but executable was not found: $exe"
}

Write-Host "Checking packaged assets and decoder..."
$checkProcess = Start-Process -FilePath $exe -ArgumentList "--check" -Wait -PassThru
if ($checkProcess.ExitCode -ne 0) {
    throw "Package self-check failed with exit code $($checkProcess.ExitCode)"
}

$archive = Join-Path $Root "dist\MonsterPrank-windows.zip"
Compress-Archive -Path $packageDir -DestinationPath $archive -Force
Write-Host "Windows package: $packageDir"
Write-Host "Windows archive: $archive"

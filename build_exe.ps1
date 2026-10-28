param(
    [string]$PythonExe = ".\env\Scripts\python.exe",
    [string]$AppName = "BotNotifyDesktop",
    [switch]$SkipInstall,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not (Test-Path $PythonExe)) {
    if (Test-Path ".\.venv\Scripts\python.exe") {
        $PythonExe = ".\.venv\Scripts\python.exe"
    } elseif (Test-Path ".\venv\Scripts\python.exe") {
        $PythonExe = ".\venv\Scripts\python.exe"
    } else {
        $PythonExe = "python"
    }
}

function Ensure-Pip([string]$Py) {
    try {
        & $Py -m pip --version 1>$null 2>$null
    } catch {
        $global:LASTEXITCODE = 1
    }

    if ($global:LASTEXITCODE -eq 0) {
        return
    }

    & $Py -m ensurepip --upgrade
    if ($global:LASTEXITCODE -ne 0) {
        throw "Не удалось установить pip через ensurepip"
    }

    & $Py -m pip install --upgrade pip setuptools wheel
    if ($global:LASTEXITCODE -ne 0) {
        throw "Не удалось обновить pip/setuptools/wheel"
    }
}

if (-not $SkipInstall) {
    Ensure-Pip $PythonExe
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -r requirements.txt pyinstaller
}

if (-not $NoClean) {
    Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue
}

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--onefile",
    "--name", $AppName
)

if (Test-Path ".\sound.wav") {
    $pyinstallerArgs += @("--add-data", "sound.wav;.")
}

$pyinstallerArgs += "main.py"

& $PythonExe @pyinstallerArgs

$distDir = Join-Path $projectRoot "dist"
if (-not (Test-Path $distDir)) {
    throw "PyInstaller не создал папку dist"
}

Copy-Item ".\config.ini" -Destination (Join-Path $distDir "config.ini") -Force
Copy-Item ".\.env.example" -Destination (Join-Path $distDir ".env.example") -Force
Copy-Item ".\sound.wav" -Destination (Join-Path $distDir "sound.wav") -Force

Write-Host ""
Write-Host "Сборка завершена:"
Write-Host "  EXE: $(Join-Path $distDir "$AppName.exe")"
Write-Host "  Доп. файлы: config.ini, .env.example, sound.wav"

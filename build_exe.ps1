$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host "==> Creating venv"
python -m venv --clear .venv
. .\.venv\Scripts\Activate.ps1
$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "==> Installing runtime dependencies"
& $VenvPython -m pip install -r requirements.txt

Write-Host "==> Installing packaging dependencies"
& $VenvPython -m pip install -r requirements.packaging.txt

Write-Host "==> Building EXE"
$IconIco = Join-Path $PSScriptRoot "assets\icons\app.ico"
$IconArg = @()
if (Test-Path $IconIco) {
  Write-Host "==> EXE icon: $IconIco"
  $IconArg = @("--icon", $IconIco)
} else {
  Write-Host "==> EXE icon not found (optional): assets\icons\app.ico"
}

& $VenvPython -m PyInstaller @IconArg `
  --noconfirm `
  --onefile `
  --windowed `
  --collect-all eel `
  --name LabelFlow `
  --add-data "assets;assets" `
  --add-data "index.html;." `
  --add-data "labelflow-editor.html;." `
  --add-data "example.txt;." `
  --add-data ".env;." `
  --hidden-import tkinter `
  --hidden-import tkinter.ttk `
  --hidden-import tkinter.messagebox `
  launcher.py

Write-Host "==> Done. See dist\LabelFlow.exe"

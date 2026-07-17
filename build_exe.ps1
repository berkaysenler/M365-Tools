Set-Location -Path $PSScriptRoot

Get-Process -Name "M365-Tools" -ErrorAction SilentlyContinue | Stop-Process -Force

python -m pip install -r requirements.txt
python -m pip install pyinstaller

python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onefile `
  --name "M365-Tools" `
  --paths "account_app" `
  --paths "student_app" `
  --hidden-import app.ui.main_window `
  --hidden-import app.ui.bulk `
  --hidden-import app.ui.dialogs `
  --hidden-import app.ui.form `
  --hidden-import app.ui.sidebar `
  --hidden-import app.ui.summary `
  --hidden-import app.ui.syncro_test `
  --hidden-import app.exchange `
  --hidden-import app.config_loader `
  --hidden-import app.account `
  --hidden-import app.auth `
  --hidden-import app.syncro `
  --collect-submodules msal `
  --hidden-import student_checker `
  --hidden-import settings_page `
  --hidden-import shared_sessions `
  --collect-submodules customtkinter `
  --collect-submodules dotenv `
  --add-data "account_app\config.json;account_app" `
  --add-data "student_app\check_students.ps1;student_app" `
  --add-data "portable_paths.py;." `
  --add-data "setup_wizard.py;." `
  --add-data "settings_page.py;." `
  --add-data "shared_sessions.py;." `
  main.py

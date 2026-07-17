import os
import shutil
import sys
from pathlib import Path


APP_NAME = "M365Tools"

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
CONFIG_FILE = APP_DATA_DIR / "config.json"
STUDENT_ACCOUNTS_FILE = APP_DATA_DIR / "student_accounts.json"
LOGS_DIR = APP_DATA_DIR / "logs"
LAST_IDS_FILE = APP_DATA_DIR / "last_student_ids.csv"

BUNDLED_CONFIG_FILE = APP_DIR / "account_app" / "config.json"


def ensure_app_dirs():
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def is_first_run() -> bool:
    return not CONFIG_FILE.exists()


def initialize_user_data(copy_default_config: bool = True):
    ensure_app_dirs()
    if copy_default_config and not CONFIG_FILE.exists() and BUNDLED_CONFIG_FILE.exists():
        shutil.copy2(BUNDLED_CONFIG_FILE, CONFIG_FILE)
    if not STUDENT_ACCOUNTS_FILE.exists():
        STUDENT_ACCOUNTS_FILE.write_text("[]", encoding="utf-8")
    if not LAST_IDS_FILE.exists():
        LAST_IDS_FILE.write_text("Date,Last Student IDs\n", encoding="utf-8")


def resolve_user_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    path = os.path.expandvars(os.path.expanduser(raw.strip()))
    if path.startswith(("/", "\\")) and not path.startswith("\\\\"):
        path = path.lstrip("/\\")
    p = Path(path)
    return p if p.is_absolute() else APP_DATA_DIR / p


def portable_user_path(path: str | Path) -> str:
    p = Path(path).expanduser()
    try:
        return str(p.resolve().relative_to(APP_DATA_DIR.resolve()))
    except ValueError:
        return str(p)

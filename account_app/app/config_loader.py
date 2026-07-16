import json
import os
from pathlib import Path
from dotenv import load_dotenv
from portable_paths import APP_DATA_DIR, CONFIG_FILE

BASE_DIR = Path(__file__).parent.parent


def load_config():
    with open(CONFIG_FILE if CONFIG_FILE.exists() else BASE_DIR / "config.json") as f:
        return json.load(f)


def load_credentials(rto_list):
    load_dotenv(APP_DATA_DIR / ".env")
    load_dotenv(BASE_DIR / ".env")
    creds = {}
    for rto in rto_list:
        client_id = os.getenv(f"{rto}_CLIENT_ID", "").strip()
        tenant_id = os.getenv(f"{rto}_TENANT_ID", "").strip()
        if client_id and tenant_id:
            creds[rto] = {"client_id": client_id, "tenant_id": tenant_id}
    return creds

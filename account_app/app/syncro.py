"""Syncro MSP API client.

Adds a contact under an existing Syncro customer (organization) when an M365
account is created. Designed to be non-blocking: failures are returned as
warning strings, never raised — a Syncro outage must not stop M365 onboarding.
"""

import os
import requests
from dotenv import load_dotenv

from portable_paths import APP_DATA_DIR

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class SyncroClient:
    def __init__(self, subdomain: str, api_key: str):
        self.subdomain = subdomain.strip()
        self.api_key = api_key.strip()

    @classmethod
    def from_env(cls):
        load_dotenv(APP_DATA_DIR / ".env")
        load_dotenv(os.path.join(BASE_DIR, ".env"))
        subdomain = os.getenv("SYNCRO_SUBDOMAIN", "").strip()
        api_key = os.getenv("SYNCRO_API_KEY", "").strip()
        if not subdomain or not api_key:
            return None
        return cls(subdomain, api_key)

    def _url(self, path: str) -> str:
        return f"https://{self.subdomain}.syncromsp.com/api/v1/{path.lstrip('/')}"

    def create_contact(self, customer_id, account) -> str | None:
        """POST a contact under customer_id. Returns None on success, error string on failure."""
        if not customer_id:
            return "Syncro: syncro_org_id not set for this RTO"

        payload = {
            "customer_id": customer_id,
            "name": account.display_name,
            "email": account.upn,
        }
        if account.mobile:
            payload["mobile"] = account.mobile
        # M365 "Location" comes through as office_location and we put it in the
        # Syncro `city` field (not address1) — that's where it shows up in the UI.
        location = account.city or account.office_location
        if location:
            payload["city"] = location
        if account.title:
            payload["title"] = account.title

        try:
            resp = requests.post(
                self._url("contacts"),
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                },
                timeout=20,
            )
        except requests.RequestException as exc:
            return f"Syncro contact create failed: {exc}"

        if 200 <= resp.status_code < 300:
            return None

        detail = resp.text[:200].strip().replace("\n", " ")
        return f"Syncro returned {resp.status_code}: {detail}"

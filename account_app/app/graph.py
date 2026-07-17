"""Microsoft Graph access for the things Exchange Online cannot do:
license assignment (subscribedSkus / assignLicense) and the user
reads/writes it needs (usageLocation).

Auth model
----------
Delegated device-code flow, exactly like app/auth.py, but against a Graph
resource instead of Exchange Online. We use the **Azure CLI** first-party
client id, which is pre-consented in the tenant with broad delegated scopes
(includes User.ReadWrite.All). With delegated auth the write succeeds because
(a) the app is consented and (b) the signed-in admin holds a role that can
manage licenses — no Global Admin / new consent is needed.

The token cache is kept SEPARATE from the EXO cache (different audience and
client), so the two never collide. It still lives under the shared token dir
and follows the same managed-RTO delegation as the EXO token manager.
"""

import csv
import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import msal

from app.auth import TOKEN_CACHE_DIR
from portable_paths import APP_DATA_DIR

# Azure CLI — a Microsoft first-party public client, pre-consented in the
# tenant with User.ReadWrite.All delegated, which covers license assignment.
AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"

# .default = "everything already consented for this client". We do NOT name
# User.ReadWrite.All, because requesting it by name would trigger a consent
# prompt a non-Global-Admin can't satisfy; .default relies on existing consent.
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Microsoft's official "Product names and service plan identifiers for
# licensing" CSV — maps every SKU GUID/part number to its commercial display
# name. Downloaded once and cached; refreshed monthly. The built-in SKU_NAMES
# map below stays as the offline fallback.
SKU_NAMES_URL = (
    "https://download.microsoft.com/download/e/3/e/"
    "e3e9faf2-f28b-490a-9ada-c6089a1fc5b0/"
    "Product%20names%20and%20service%20plan%20identifiers%20for%20licensing.csv"
)
SKU_NAMES_FILE = APP_DATA_DIR / "sku_names.csv"
SKU_NAMES_MAX_AGE = 30 * 24 * 3600  # seconds

_sku_name_maps: tuple[dict, dict] | None = None  # (by_guid, by_string_id)


def _load_sku_name_maps() -> tuple[dict, dict]:
    """Return ({guid: display_name}, {string_id: display_name}).

    Downloads/refreshes the Microsoft CSV when needed; any failure just
    falls back to whatever cached file exists (or empty maps). Only called
    from background threads, so the blocking download is fine.
    """
    global _sku_name_maps
    if _sku_name_maps is not None:
        return _sku_name_maps

    try:
        stale = (
            not SKU_NAMES_FILE.exists()
            or time.time() - SKU_NAMES_FILE.stat().st_mtime > SKU_NAMES_MAX_AGE
        )
        if stale:
            req = urllib.request.Request(
                SKU_NAMES_URL, headers={"User-Agent": "M365-Tools"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            SKU_NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
            SKU_NAMES_FILE.write_bytes(data)
    except Exception:
        pass  # offline / blocked — use the cached file or built-in map

    by_guid: dict[str, str] = {}
    by_string_id: dict[str, str] = {}
    try:
        text = SKU_NAMES_FILE.read_text(encoding="utf-8-sig")
        for row in csv.DictReader(io.StringIO(text)):
            name = (row.get("Product_Display_Name") or "").strip()
            if not name:
                continue
            guid = (row.get("GUID") or "").strip().lower()
            string_id = (row.get("String_Id") or "").strip()
            if guid:
                by_guid.setdefault(guid, name)
            if string_id:
                by_string_id.setdefault(string_id, name)
    except Exception:
        pass

    _sku_name_maps = (by_guid, by_string_id)
    return _sku_name_maps


# skuPartNumber -> friendly name for the SKUs we're likely to meet.
# Offline fallback when Microsoft's CSV has never been downloaded.
SKU_NAMES = {
    "STANDARDWOFFPACK_STUDENT": "Office 365 A1 for Students",
    "STANDARDWOFFPACK_FACULTY": "Office 365 A1 for Faculty",
    "STANDARDWOFFPACK_IW_STUDENT": "Office 365 A1 Plus for Students",
    "STANDARDWOFFPACK_IW_FACULTY": "Office 365 A1 Plus for Faculty",
    "M365EDU_A1": "Microsoft 365 A1",
    "M365EDU_A3_STUDENT": "Microsoft 365 A3 for Students",
    "M365EDU_A3_FACULTY": "Microsoft 365 A3 for Faculty",
    "M365EDU_A3_STUUSEBNFT": "Microsoft 365 A3 Student Use Benefit",
    "M365EDU_A5_STUDENT": "Microsoft 365 A5 for Students",
    "M365EDU_A5_FACULTY": "Microsoft 365 A5 for Faculty",
    "ENTERPRISEPACK_STUDENT": "Office 365 A3 for Students",
    "ENTERPRISEPACK_FACULTY": "Office 365 A3 for Faculty",
    "STANDARDPACK": "Office 365 E1",
    "ENTERPRISEPACK": "Office 365 E3",
    "ENTERPRISEPREMIUM": "Office 365 E5",
    "SPE_E3": "Microsoft 365 E3",
    "SPE_E5": "Microsoft 365 E5",
    "SPE_F1": "Microsoft 365 F3",
    "DESKLESSPACK": "Office 365 F3",
    "O365_BUSINESS_ESSENTIALS": "Microsoft 365 Business Basic",
    "O365_BUSINESS_PREMIUM": "Microsoft 365 Business Standard",
    "SPB": "Microsoft 365 Business Premium",
    "O365_BUSINESS": "Microsoft 365 Apps for Business",
    "OFFICESUBSCRIPTION": "Microsoft 365 Apps for Enterprise",
    "EXCHANGESTANDARD": "Exchange Online (Plan 1)",
    "EXCHANGEENTERPRISE": "Exchange Online (Plan 2)",
    "EXCHANGEDESKLESS": "Exchange Online Kiosk",
    "EXCHANGESTANDARD_STUDENT": "Exchange Online (Plan 1) for Students",
    "MCOSTANDARD": "Skype for Business Online (Plan 2)",
    "POWER_BI_STANDARD": "Power BI Free",
    "POWER_BI_PRO": "Power BI Pro",
    "FLOW_FREE": "Power Automate Free",
    "TEAMS_EXPLORATORY": "Teams Exploratory",
    "PROJECTPROFESSIONAL": "Project Plan 3",
    "VISIOCLIENT": "Visio Plan 2",
    "EMS": "Enterprise Mobility + Security E3",
    "EMSPREMIUM": "Enterprise Mobility + Security E5",
    "AAD_PREMIUM": "Entra ID P1",
    "AAD_PREMIUM_P2": "Entra ID P2",
    "ATP_ENTERPRISE": "Defender for Office 365 (Plan 1)",
    "WINDOWS_STORE": "Windows Store for Business",
    "RIGHTSMANAGEMENT_ADHOC": "Rights Management Adhoc",
}


# Free / trial / self-service SKUs excluded from the license pickers.
# They assign fine via Graph, but the M365 admin center hides them (both in
# Billing > Licenses and often in the user's Licenses and apps), so an
# accidental assignment is near-impossible to see or undo from the portal.
BLOCKED_SKUS = {
    "SPZA_IW",                        # App Connect IW
    "FLOW_FREE",                      # Power Automate Free
    "POWER_BI_STANDARD",              # Power BI Free
    "POWERAPPS_VIRAL",                # Power Apps Plan 2 Trial (self-service)
    "POWERAPPS_DEV",                  # Power Apps Developer Plan
    "TEAMS_EXPLORATORY",              # Teams Exploratory trial
    "STREAM",                         # Microsoft Stream trial
    "FORMS_PRO",                      # Dynamics 365 Customer Voice Trial
    "CCIBOTS_PRIVPREV_VIRAL",         # Power Virtual Agents Viral Trial
    "PROJECT_MADEIRA_PREVIEW_IW_SKU", # Dynamics 365 Business Central for IWs
    "RIGHTSMANAGEMENT_ADHOC",         # Rights Management Adhoc (self-service)
    "WINDOWS_STORE",                  # Windows Store for Business
    "MICROSOFT_BUSINESS_CENTER",      # Microsoft Business Center
}


# SKUs pinned to the top of the license pickers, in this order — the ones
# assigned most often. Everything else follows alphabetically.
PINNED_SKUS = [
    "OFFICESUBSCRIPTION_FACULTY",  # Microsoft 365 Apps for Faculty
    "STANDARDWOFFPACK_FACULTY",    # Office 365 A1 for Faculty
]


class GraphError(Exception):
    """Raised when a Graph call returns a non-success status."""


class GraphManager:
    """Per-tenant Graph token acquisition + the license/user calls.

    Mirrors auth.TokenManager: same managed-RTO delegation, same silent-then-
    device-code pattern, but a distinct on-disk cache (``*.graph.bin``).
    """

    def __init__(self, config):
        self._config = config
        self._apps: dict[str, msal.PublicClientApplication] = {}
        self._caches: dict[str, msal.SerializableTokenCache] = {}
        self._lock = threading.Lock()

        # managed_rto -> managing_rto, same as TokenManager.
        self._delegate: dict[str, str] = {}
        for rto, cfg in config.items():
            for managed in cfg.get("manages", []):
                self._delegate[managed] = rto

        TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Token plumbing (parallels app/auth.py, separate cache file)
    # ------------------------------------------------------------------

    def get_auth_rto(self, rto: str) -> str:
        return self._delegate.get(rto, rto)

    def _cache_path(self, auth_rto: str) -> Path:
        return TOKEN_CACHE_DIR / f"{auth_rto}.graph.bin"

    def _load_cache(self, auth_rto: str) -> msal.SerializableTokenCache:
        if auth_rto in self._caches:
            return self._caches[auth_rto]
        cache = msal.SerializableTokenCache()
        path = self._cache_path(auth_rto)
        if path.exists():
            try:
                cache.deserialize(path.read_text(encoding="utf-8"))
            except Exception:
                pass  # corrupt cache -> start fresh
        self._caches[auth_rto] = cache
        return cache

    def _save_cache(self, auth_rto: str) -> None:
        cache = self._caches.get(auth_rto)
        if cache and cache.has_state_changed:
            self._cache_path(auth_rto).write_text(cache.serialize(), encoding="utf-8")

    def _get_app(self, rto: str):
        auth_rto = self.get_auth_rto(rto)
        if auth_rto not in self._apps:
            cfg = self._config.get(auth_rto, {})
            domain = cfg.get("domain", "").strip() or "organizations"
            cache = self._load_cache(auth_rto)
            self._apps[auth_rto] = msal.PublicClientApplication(
                client_id=AZURE_CLI_CLIENT_ID,
                authority=f"https://login.microsoftonline.com/{domain}",
                token_cache=cache,
            )
        return auth_rto, self._apps[auth_rto]

    def get_token_silent(self, rto: str) -> str | None:
        """Return a cached Graph access token or None. Zero user interaction."""
        try:
            auth_rto, app = self._get_app(rto)
        except Exception:
            return None
        accounts = app.get_accounts()
        if not accounts:
            return None
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            self._save_cache(auth_rto)
            return result["access_token"]
        return None

    def acquire_token(self, rto: str, on_code_ready) -> tuple[str | None, str | None]:
        """Get a Graph token via silent-then-device-code (blocking). Returns
        (access_token, error); exactly one is non-None."""
        try:
            auth_rto, app = self._get_app(rto)
        except Exception as exc:
            return None, str(exc)

        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
            if result and "access_token" in result:
                self._save_cache(auth_rto)
                return result["access_token"], None

        flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
        if "user_code" not in flow:
            return None, flow.get("error_description", "Device code init failed")
        on_code_ready(flow["user_code"], flow["verification_uri"])
        result = app.acquire_token_by_device_flow(flow)  # blocks until done/timeout
        if "access_token" in result:
            self._save_cache(auth_rto)
            return result["access_token"], None
        return None, result.get("error_description", "Authentication failed")

    # ------------------------------------------------------------------
    # Graph REST
    # ------------------------------------------------------------------

    @staticmethod
    def _request(method: str, path: str, token: str, body=None):
        url = f"{GRAPH_BASE}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode() or ""
                return resp.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw

    @staticmethod
    def _err_text(doc) -> str:
        if isinstance(doc, dict):
            err = doc.get("error", {})
            if isinstance(err, dict):
                return err.get("message") or json.dumps(err)
        return str(doc)

    # ------------------------------------------------------------------
    # Licenses
    # ------------------------------------------------------------------

    def list_skus(self, token: str) -> list[dict]:
        """Return the tenant's assignable SKUs with availability counts.

        Each entry: skuId, skuPartNumber, name, enabled, consumed, available.
        Only SKUs with capabilityStatus Enabled/Warning and at least one
        purchased unit are returned; free/trial SKUs in BLOCKED_SKUS are
        dropped.
        """
        status, doc = self._request("GET", "/subscribedSkus", token)
        if status != 200 or not isinstance(doc, dict):
            raise GraphError(
                f"GET subscribedSkus failed (HTTP {status}): {self._err_text(doc)}"
            )
        by_guid, by_string_id = _load_sku_name_maps()
        skus = []
        for item in doc.get("value", []):
            if item.get("capabilityStatus") not in ("Enabled", "Warning"):
                continue
            enabled = (item.get("prepaidUnits") or {}).get("enabled", 0)
            if enabled <= 0:
                continue
            consumed = item.get("consumedUnits", 0)
            part = item.get("skuPartNumber", "")
            if part in BLOCKED_SKUS:
                continue
            sku_id = item.get("skuId", "")
            name = (
                by_guid.get(sku_id.lower())
                or by_string_id.get(part)
                or SKU_NAMES.get(part, part)
            )
            skus.append({
                "skuId": sku_id,
                "skuPartNumber": part,
                "name": name,
                "enabled": enabled,
                "consumed": consumed,
                "available": enabled - consumed,
            })
        def _sort_key(s):
            part = s["skuPartNumber"]
            rank = (
                PINNED_SKUS.index(part)
                if part in PINNED_SKUS
                else len(PINNED_SKUS)
            )
            return (rank, s["name"].lower())

        skus.sort(key=_sort_key)
        return skus

    def get_user_basic(self, token: str, identity: str) -> dict | None:
        """Return {id, usageLocation} for the user, or None if not found
        (yet) — new accounts can lag a little before Graph sees them."""
        status, doc = self._request(
            "GET",
            f"/users/{urllib.parse.quote(identity)}?$select=id,usageLocation",
            token,
        )
        if status == 200 and isinstance(doc, dict):
            return {"id": doc.get("id", ""), "usageLocation": doc.get("usageLocation")}
        if status == 404:
            return None
        raise GraphError(f"GET user failed (HTTP {status}): {self._err_text(doc)}")

    def set_usage_location(self, token: str, identity: str, location: str) -> None:
        """Set usageLocation — required by Azure AD before a license can land."""
        status, doc = self._request(
            "PATCH",
            f"/users/{urllib.parse.quote(identity)}",
            token,
            body={"usageLocation": location},
        )
        if status not in (200, 204):
            raise GraphError(
                f"PATCH usageLocation failed (HTTP {status}): {self._err_text(doc)}"
            )

    def _get_paged(self, token: str, path: str) -> list[dict]:
        """GET a collection, following @odata.nextLink until exhausted."""
        items: list[dict] = []
        while path:
            status, doc = self._request("GET", path, token)
            if status != 200 or not isinstance(doc, dict):
                raise GraphError(
                    f"GET {path.split('?')[0]} failed (HTTP {status}): "
                    f"{self._err_text(doc)}"
                )
            items.extend(doc.get("value", []))
            next_link = doc.get("@odata.nextLink") or ""
            path = next_link[len(GRAPH_BASE):] if next_link.startswith(GRAPH_BASE) else ""
        return items

    def list_users_basic(self, token: str) -> list[dict]:
        """All users in the tenant with the fields the student checker needs.

        One paged bulk read (999/page) instead of per-user lookups —
        assignedLicenses gives real license status for free.
        """
        return self._get_paged(
            token,
            "/users?$select=id,displayName,mail,userPrincipalName,department,"
            "assignedLicenses&$top=999",
        )

    def find_group(self, token: str, name: str) -> dict | None:
        """Find a group by display name. Returns {id, displayName, mail} or None."""
        safe = name.replace("'", "''")
        query = urllib.parse.quote(f"displayName eq '{safe}'")
        status, doc = self._request(
            "GET",
            f"/groups?$filter={query}&$select=id,displayName,mail",
            token,
        )
        if status != 200 or not isinstance(doc, dict):
            raise GraphError(
                f"GET groups failed (HTTP {status}): {self._err_text(doc)}"
            )
        groups = doc.get("value", [])
        return groups[0] if groups else None

    def list_group_member_mails(self, token: str, group_id: str) -> list[str]:
        """Email addresses (or UPNs) of a group's direct members."""
        members = self._get_paged(
            token,
            f"/groups/{urllib.parse.quote(group_id)}/members"
            "?$select=mail,userPrincipalName&$top=999",
        )
        return [
            (m.get("mail") or m.get("userPrincipalName") or "")
            for m in members
        ]

    def assign_licenses(self, token: str, identity: str, sku_ids: list[str]) -> None:
        """Assign one or more license SKUs to the user in a single call."""
        status, doc = self._request(
            "POST",
            f"/users/{urllib.parse.quote(identity)}/assignLicense",
            token,
            body={
                "addLicenses": [{"skuId": sku_id} for sku_id in sku_ids],
                "removeLicenses": [],
            },
        )
        if status not in (200, 201, 204):
            raise GraphError(
                f"assignLicense failed (HTTP {status}): {self._err_text(doc)}"
            )

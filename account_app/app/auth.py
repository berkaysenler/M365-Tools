"""Token manager for delegated EXO auth via MSAL device code flow.

Uses Microsoft's first-party Exchange Online PowerShell client id — present in
every Azure AD tenant, no app registration needed. Tokens are cached to disk
per RTO so reconnects are silent for the lifetime of the refresh token
(~90 days, rolling).

User flow:
  1. App calls acquire_token_device_code(rto, on_code, on_complete).
  2. MSAL hands back a short code + URL → on_code fires → app shows them.
  3. User opens the URL in *any* browser/profile they like and enters the code.
  4. MSAL polls Azure AD, gets tokens → on_complete fires with the access token.
  5. Next time, get_token_silent(rto) returns the cached token with no UI.
"""

import threading
from pathlib import Path

import msal

from portable_paths import APP_DATA_DIR

# Microsoft Exchange Online PowerShell — a Microsoft first-party app pre-registered
# in every Azure AD tenant. Same client id the EXO PowerShell module uses.
EXO_CLIENT_ID = "fb78d390-0c51-40cd-8e17-fdbfab77341b"
EXO_SCOPES = ["https://outlook.office365.com/.default"]

TOKEN_CACHE_DIR = APP_DATA_DIR / "tokens"


class TokenManager:
    def __init__(self, config):
        self._config = config
        self._apps: dict[str, msal.PublicClientApplication] = {}
        self._caches: dict[str, msal.SerializableTokenCache] = {}

        # managed_rto -> managing_rto (e.g. AIBT -> VC)
        self._delegate: dict[str, str] = {}
        for rto, cfg in config.items():
            for managed in cfg.get("manages", []):
                self._delegate[managed] = rto

        TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def get_auth_rto(self, rto: str) -> str:
        return self._delegate.get(rto, rto)

    def _cache_path(self, auth_rto: str) -> Path:
        return TOKEN_CACHE_DIR / f"{auth_rto}.bin"

    def _load_cache(self, auth_rto: str) -> msal.SerializableTokenCache:
        if auth_rto in self._caches:
            return self._caches[auth_rto]
        cache = msal.SerializableTokenCache()
        path = self._cache_path(auth_rto)
        if path.exists():
            try:
                cache.deserialize(path.read_text(encoding="utf-8"))
            except Exception:
                pass  # corrupt cache → start fresh
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
            # Authority by domain — Azure AD resolves it to the tenant. Falls back
            # to "organizations" if domain is missing, which still works.
            domain = cfg.get("domain", "").strip() or "organizations"
            cache = self._load_cache(auth_rto)
            self._apps[auth_rto] = msal.PublicClientApplication(
                client_id=EXO_CLIENT_ID,
                authority=f"https://login.microsoftonline.com/{domain}",
                token_cache=cache,
            )
        return auth_rto, self._apps[auth_rto]

    def get_token_silent(self, rto: str) -> str | None:
        """Return a cached access token or None. Zero user interaction."""
        try:
            auth_rto, app = self._get_app(rto)
        except Exception:
            return None
        accounts = app.get_accounts()
        if not accounts:
            return None
        result = app.acquire_token_silent(EXO_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            self._save_cache(auth_rto)
            return result["access_token"]
        return None

    def acquire_token_device_code(self, rto: str, on_code_ready, on_complete) -> None:
        """Get a token via device code. Tries silent first; falls back to code flow.

        on_code_ready(user_code, verification_uri) — fired when the code is ready.
            Skipped entirely on the silent path.
        on_complete(token_or_None, error_or_None) — exactly one is non-None.
        """
        try:
            auth_rto, app = self._get_app(rto)
        except Exception as exc:
            on_complete(None, str(exc))
            return

        def _run():
            try:
                accounts = app.get_accounts()
                if accounts:
                    result = app.acquire_token_silent(EXO_SCOPES, account=accounts[0])
                    if result and "access_token" in result:
                        self._save_cache(auth_rto)
                        on_complete(result["access_token"], None)
                        return

                flow = app.initiate_device_flow(scopes=EXO_SCOPES)
                if "user_code" not in flow:
                    on_complete(None, flow.get("error_description", "Device code init failed"))
                    return

                on_code_ready(flow["user_code"], flow["verification_uri"])
                result = app.acquire_token_by_device_flow(flow)
                if "access_token" in result:
                    self._save_cache(auth_rto)
                    on_complete(result["access_token"], None)
                else:
                    on_complete(None, result.get("error_description", "Authentication failed"))
            except Exception as exc:
                on_complete(None, str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def clear(self, rto: str) -> None:
        """Forget the cached token for this RTO. Next connect will need device code again."""
        auth_rto = self.get_auth_rto(rto)
        try:
            self._cache_path(auth_rto).unlink()
        except FileNotFoundError:
            pass
        self._apps.pop(auth_rto, None)
        self._caches.pop(auth_rto, None)

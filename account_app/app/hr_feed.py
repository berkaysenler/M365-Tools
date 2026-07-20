"""HR feeds — reads new rows from the HR SharePoint workbooks.

HR appends a row to their Excel sheets (Forms-connected tables) for every
onboarding and offboarding. This module pulls only the tail of those
tables via the Microsoft Graph workbook API and tracks which rows have
been handled.

Design notes
------------
- Files are resolved from their normal SharePoint share URLs through the
  Graph ``/shares`` endpoint (base64 share-id trick) — no site/drive ids
  to configure.
- The sheets are large (900+ rows), so we never fetch them all: read the
  table's dataBodyRange address + rowCount, then request only the last N
  rows as a worksheet range with ``$select=text`` so dates arrive as the
  formatted strings HR sees, not Excel serial numbers.
- Processed rows are remembered locally (hr_feed_state.json) keyed by the
  Forms "Id" column when present, else a hash of the row. Nothing is
  written back to the HR sheets, so their workflow is untouched.
- Config lives in hr_feed.json under APP_DATA_DIR — per machine, never in
  the repo (see hr_feed.example.json). Two feeds: "onboarding" and
  "offboarding", each with its own share_url + display columns.
"""

import base64
import difflib
import hashlib
import json
import re
import urllib.parse
from datetime import date, datetime, timedelta

from app.graph import GraphManager, GraphError
from portable_paths import APP_DATA_DIR

HR_FEED_FILE = APP_DATA_DIR / "hr_feed.json"
HR_STATE_FILE = APP_DATA_DIR / "hr_feed_state.json"

# Columns shown in the task detail panel, in this order (config overrides).
DEFAULT_ONBOARD_COLUMNS = [
    "Start time",
    "Organization",
    "Preferred First Name",
    "Preferred Last Name",
    "Job Title",
    "Department",
    "Report to",
    "Start Date",
    "Account Required",
    "ICT Equipment",
    "Add Email Group",
    "Location of Staff",
]

DEFAULT_OFFBOARD_COLUMNS = [
    "Organization",
    "First Name",
    "Last Name",
    "Department",
    "Line Manager (Full Name)",
    "Location",
    "Email ID",
    "Date of closing account",
    "What accounts should be closed",
    "Email Forwarding",
    "Email ID to which emails to be forwarded to",
    "Remove from group",
    "ICT Equipment",
]

# form field key -> HR sheet column header (config "form_map" can override).
# Groups are intentionally NOT mapped — "Add Email Group" is display-only.
DEFAULT_FORM_MAP = {
    "first_name": "Preferred First Name",
    "last_name": "Preferred Last Name",
    "title": "Job Title",
    "department": "Department",
    "office_location": "Location of Staff",
    "manager": "Report to",
}

# Org codes HR uses in "Account Required" ("Email - SomeOrg") -> config RTO
# key, e.g. {"someorg": "SO"}. Codes that already equal an RTO key resolve
# without an entry here. Fill per deployment in hr_feed.json.
DEFAULT_ORG_CODES: dict[str, str] = {}

DEFAULT_FETCH_LAST = 15
DEFAULT_POLL_MINUTES = 5


class HRFeedError(Exception):
    pass


# ----------------------------------------------------------------------
# Config + local processed-state
# ----------------------------------------------------------------------

def _feed_defaults(kind: str, feed: dict) -> dict:
    feed.setdefault("table_name", "")
    if kind == "offboarding":
        feed.setdefault("columns", DEFAULT_OFFBOARD_COLUMNS)
    else:
        feed.setdefault("columns", DEFAULT_ONBOARD_COLUMNS)
        feed.setdefault("form_map", DEFAULT_FORM_MAP)
    return feed


def load_feed_config() -> dict | None:
    """Return the hr_feed.json dict, or None when not configured yet."""
    if not HR_FEED_FILE.exists():
        return None
    try:
        cfg = json.loads(HR_FEED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

    # v1 layout had a single flat onboarding feed — lift it into "feeds".
    if "feeds" not in cfg and cfg.get("share_url"):
        cfg["feeds"] = {
            "onboarding": {
                "share_url": cfg.pop("share_url"),
                "table_name": cfg.pop("table_name", ""),
                "columns": cfg.pop("columns", DEFAULT_ONBOARD_COLUMNS),
                "form_map": cfg.pop("form_map", DEFAULT_FORM_MAP),
            }
        }
        save_feed_config(cfg)

    feeds = {
        kind: _feed_defaults(kind, feed)
        for kind, feed in cfg.get("feeds", {}).items()
        if feed.get("share_url")
    }
    if not feeds or not cfg.get("rto"):
        return None
    cfg["feeds"] = feeds
    cfg.setdefault("fetch_last", DEFAULT_FETCH_LAST)
    cfg.setdefault("poll_minutes", DEFAULT_POLL_MINUTES)
    cfg.setdefault("org_codes", DEFAULT_ORG_CODES)
    return cfg


def save_feed_config(cfg: dict) -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    HR_FEED_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _load_state() -> dict:
    if HR_STATE_FILE.exists():
        try:
            return json.loads(HR_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done": {}}


def _save_state(state: dict) -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    HR_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def mark_done(key: str) -> None:
    state = _load_state()
    state.setdefault("done", {})[key] = datetime.now().isoformat(timespec="seconds")
    _save_state(state)


# ----------------------------------------------------------------------
# Cell helpers
# ----------------------------------------------------------------------

def get_col(row: dict, name: str) -> str:
    """Fetch a cell by friendly column name, tolerating the sheet's longer
    real headers ('Preferred First Name? (Will use for creating email
    account)' matches 'Preferred First Name') and HR typos ('What accounts
    shoud be closed' matches 'What accounts should be closed')."""
    if name in row:
        return row[name]
    wanted = name.strip().casefold()
    best, best_ratio = None, 0.0
    for header, value in row.items():
        if header.startswith("_"):
            continue
        h = header.strip().casefold()
        if h == wanted or h.startswith(wanted) or wanted in h:
            return value
        ratio = difflib.SequenceMatcher(None, h, wanted).ratio()
        if ratio > best_ratio:
            best, best_ratio = value, ratio
    return best if best_ratio >= 0.8 else ""


def split_multi(value: str) -> list[str]:
    """Split a multi-value HR cell like
    'Email - ABC;Moodle  -  Reach' into cleaned entries."""
    return [
        re.sub(r"\s+", " ", part).strip()
        for part in (value or "").split(";")
        if part.strip()
    ]


def org_code_to_rto(entry: str, config: dict, org_codes: dict) -> str | None:
    """Map an 'Email - Reach' style entry to a config RTO key, or None."""
    code = entry.split("-", 1)[-1].strip() if "-" in entry else entry.strip()
    low = code.casefold()
    mapped = {k.casefold(): v for k, v in (org_codes or {}).items()}.get(low)
    if mapped and mapped in config:
        return mapped
    for rto in config:
        if rto.casefold() == low:
            return rto
    return None


def pretty_date(value: str) -> str:
    """Turn a bare Excel serial (unformatted date cell, e.g. '46219') into
    d/m/Y. Anything else passes through unchanged."""
    v = (value or "").strip()
    if v.isdigit() and 20000 <= int(v) <= 80000:
        return (date(1899, 12, 30) + timedelta(days=int(v))).strftime("%d/%m/%Y")
    return value


def normalize_date(raw: str) -> str:
    """Best-effort convert a sheet date string to YYYY-MM-DD for the form."""
    raw = pretty_date(raw).strip().split(" ")[0]
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


# ----------------------------------------------------------------------
# Graph workbook access
# ----------------------------------------------------------------------

_ADDR_RE = re.compile(r"^(.*)!([A-Z]+)(\d+):([A-Z]+)(\d+)$")


def _share_id(url: str) -> str:
    """Encode a SharePoint URL as a Graph /shares id (u! + base64url)."""
    b64 = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii")
    return "u!" + b64.rstrip("=")


class HRFeedClient:
    """Reads the tail of one HR table (onboarding or offboarding).

    Token acquisition stays with the caller (UI needs to drive the
    device-code dialog); this class only takes a ready access token.
    """

    def __init__(self, kind: str, feed_cfg: dict, fetch_last: int = DEFAULT_FETCH_LAST):
        self.kind = kind
        self.cfg = feed_cfg
        self.fetch_last = fetch_last
        self._wb_path: str | None = None  # /drives/{id}/items/{id}/workbook

    def _get(self, token: str, path: str):
        status, doc = GraphManager._request("GET", path, token)
        if status >= 300:
            raise HRFeedError(GraphManager._err_text(doc))
        return doc

    def _workbook_path(self, token: str) -> str:
        if self._wb_path:
            return self._wb_path
        share = _share_id(self.cfg["share_url"].strip())
        doc = self._get(
            token, f"/shares/{share}/driveItem?$select=id,name,parentReference"
        )
        drive_id = doc.get("parentReference", {}).get("driveId")
        item_id = doc.get("id")
        if not drive_id or not item_id:
            raise HRFeedError("Could not resolve the workbook from the share URL")
        self._wb_path = f"/drives/{drive_id}/items/{item_id}/workbook"
        return self._wb_path

    def _table_name(self, token: str, wb: str) -> str:
        configured = (self.cfg.get("table_name") or "").strip()
        if configured:
            return configured
        doc = self._get(token, f"{wb}/tables?$select=name")
        names = [t.get("name", "") for t in doc.get("value", [])]
        if not names:
            raise HRFeedError("Workbook has no tables — HR data must be a real Excel table")
        # Forms-connected sheets name their table "OfficeForms.Table..."
        for name in names:
            if name.startswith("OfficeForms"):
                return name
        return names[0]

    def fetch(self, token: str, include_done: bool = True) -> list[dict]:
        """Return rows from the table tail, newest first.

        Each row dict maps header -> formatted cell text, plus:
          _key    stable id used by mark_done()
          _row    1-based worksheet row number (for display/debug)
          _done   True when already marked handled
        """
        wb = self._workbook_path(token)
        table = urllib.parse.quote(self._table_name(token, wb), safe="")

        headers_doc = self._get(token, f"{wb}/tables('{table}')/headerRowRange?$select=text")
        headers = [h.strip() for h in headers_doc["text"][0]]

        body = self._get(
            token, f"{wb}/tables('{table}')/dataBodyRange?$select=address,rowCount"
        )
        row_count = body["rowCount"]
        match = _ADDR_RE.match(body["address"])
        if not match:
            raise HRFeedError(f"Unexpected range address: {body['address']}")
        sheet, col_a, first_row, col_b, last_row = match.groups()
        sheet = sheet.strip("'").replace("''", "'")
        first_row, last_row = int(first_row), int(last_row)

        n = min(int(self.fetch_last), row_count)
        tail_first = max(first_row, last_row - n + 1)
        address = f"{col_a}{tail_first}:{col_b}{last_row}"
        sheet_q = urllib.parse.quote(sheet.replace("'", "''"), safe="")
        # $select=text -> formatted strings, so dates match what HR sees
        # instead of raw Excel serial numbers.
        rng = self._get(
            token,
            f"{wb}/worksheets('{sheet_q}')/range(address='{address}')?$select=text",
        )

        done = _load_state().get("done", {})
        rows = []
        for offset, cells in enumerate(rng["text"]):
            if not any(str(c).strip() for c in cells):
                continue
            row = dict(zip(headers, [str(c).strip() for c in cells]))
            base = self._row_key(row)
            key = f"{self.kind}:{base}"
            # v1 stored onboarding keys without the kind prefix.
            is_done = key in done or (self.kind == "onboarding" and base in done)
            if is_done and not include_done:
                continue
            row["_key"] = key
            row["_row"] = tail_first + offset
            row["_done"] = is_done
            rows.append(row)
        rows.reverse()  # newest first
        return rows

    @staticmethod
    def _row_key(row: dict) -> str:
        row_id = str(row.get("ID") or row.get("Id") or "").strip()
        if row_id:
            return f"id:{row_id}"
        digest = hashlib.sha1(
            "|".join(f"{k}={v}" for k, v in sorted(row.items())).encode("utf-8")
        ).hexdigest()
        return f"h:{digest}"


def count_pending(token: str, cfg: dict) -> int:
    """Total unhandled rows across all configured feeds (background badge)."""
    total = 0
    for kind, feed in cfg.get("feeds", {}).items():
        client = HRFeedClient(kind, feed, cfg.get("fetch_last", DEFAULT_FETCH_LAST))
        total += len(client.fetch(token, include_done=False))
    return total

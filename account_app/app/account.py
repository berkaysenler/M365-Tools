import re
import secrets
from dataclasses import dataclass, field
from typing import List


@dataclass
class AccountData:
    # Identity
    first_name: str = ""
    last_name: str = ""
    display_name: str = ""
    upn: str = ""

    # Role
    title: str = ""
    department: str = ""
    office_location: str = ""
    city: str = ""
    manager_upn: str = ""      # used by Exchange Online PS
    manager_id: str = ""       # used by Graph API
    manager_display: str = ""
    mobile: str = ""

    # Access
    usage_location: str = "AU"
    start_date: str = ""
    distribution_groups: List[dict] = field(default_factory=list)
    licenses: List[dict] = field(default_factory=list)  # [{skuId, name}]; empty = no license

    # Meta
    rto: str = ""
    domain: str = ""
    admin_email: str = ""


def generate_upn(first_name: str, last_name: str, domain: str) -> str:
    # Allow digits through so training/test accounts like "Training 1" can
    # generate a valid UPN (t.1@domain). Azure AD permits digits in the local
    # part; only spaces and most symbols are disallowed.
    first = re.sub(r"[^a-zA-Z0-9]", "", first_name).lower()
    last = re.sub(r"[^a-zA-Z0-9]", "", last_name).lower()
    if not first or not last:
        return ""
    return f"{first[0]}.{last}@{domain}"


def generate_display_name(first_name: str, last_name: str, prefix: str = "") -> str:
    base = f"{first_name.strip()} {last_name.strip()}".strip()
    return f"{prefix} {base}".strip() if prefix else base


def generate_password() -> str:
    """Mirror sample.md New-Password: upper + symbol + 12 digits + 2 lowers."""
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower = "abcdefghjkmnpqrstuvwxyz"
    symbols = "!@#$%^&*"
    digits = "".join(str(secrets.randbelow(10)) for _ in range(12))
    return (
        secrets.choice(upper)
        + secrets.choice(symbols)
        + digits
        + "".join(secrets.choice(lower) for _ in range(2))
    )


def build_graph_user_payload(account: AccountData, temp_password: str) -> dict:
    mail_nickname = account.upn.split("@")[0] if "@" in account.upn else account.upn
    payload = {
        "accountEnabled": True,
        "displayName": account.display_name,
        "givenName": account.first_name,
        "surname": account.last_name,
        "userPrincipalName": account.upn,
        "mailNickname": mail_nickname,
        "usageLocation": account.usage_location,
        "passwordProfile": {
            "forceChangePasswordNextSignIn": False,
            "password": temp_password,
        },
    }
    if account.title:
        payload["jobTitle"] = account.title
    if account.department:
        payload["department"] = account.department
    if account.office_location:
        payload["officeLocation"] = account.office_location
    if account.mobile:
        payload["mobilePhone"] = account.mobile
    return payload

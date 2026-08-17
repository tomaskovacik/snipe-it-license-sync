"""
Fetch Microsoft 365 users and their assigned license SKUs via Microsoft Graph API.

Auth: Entra ID app registration with client secret.
Required API permissions (application):
  - User.Read.All
  - LicenseAssignment.Read.All
  - Organization.Read.All
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import msal
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Free/trial SKUs that don't represent a real license cost — excluded from
# reports. Configurable via MS_EXCLUDED_SKUS (comma-separated) so it can be
# set per-deployment (e.g. Docker) without editing code. Falls back to this
# default if the env var isn't set.
_DEFAULT_EXCLUDED_SKUS = {
    "POWER_BI_STANDARD",
    "Power_Pages_vTrial_for_Makers",
    "FLOW_FREE",
}


def _load_excluded_skus() -> set[str]:
    raw = os.environ.get("MS_EXCLUDED_SKUS")
    if not raw:
        return _DEFAULT_EXCLUDED_SKUS
    return {s.strip() for s in raw.split(",") if s.strip()}


EXCLUDED_SKUS = _load_excluded_skus()


@dataclass
class LicensedUser:
    account_id: str
    display_name: str
    email: str
    sku_part_numbers: list[str] = field(default_factory=list)


class GraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        self._app = msal.ConfidentialClientApplication(
            client_id=client_id,
            authority=authority,
            client_credential=client_secret,
        )
        self._session = requests.Session()

    def _token(self) -> str:
        result = self._app.acquire_token_silent(
            scopes=["https://graph.microsoft.com/.default"], account=None
        )
        if not result:
            result = self._app.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
        if "access_token" not in result:
            raise RuntimeError(f"Token acquisition failed: {result.get('error_description')}")
        return result["access_token"]

    def get(self, url: str, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token()}"}
        resp = self._session.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_all_pages(self, url: str, params: dict | None = None) -> list[dict]:
        """Follow @odata.nextLink pagination and return all items."""
        items = []
        next_url: str | None = url
        while next_url:
            data = self.get(next_url, params=params if next_url == url else None)
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
        return items


def fetch_sku_map(client: GraphClient) -> dict[str, str]:
    """Return mapping of skuId -> skuPartNumber for all tenant subscriptions."""
    skus = client.get_all_pages(f"{GRAPH_BASE}/subscribedSkus")
    return {s["skuId"]: s["skuPartNumber"] for s in skus}


def fetch_licensed_users(client: GraphClient, sku_map: dict[str, str]) -> list[LicensedUser]:
    """Return all users that have at least one license assigned."""
    raw_users = client.get_all_pages(
        f"{GRAPH_BASE}/users",
        params={"$select": "id,displayName,mail,userPrincipalName,assignedLicenses"},
    )
    result = []
    for u in raw_users:
        assigned = u.get("assignedLicenses", [])
        if not assigned:
            continue
        sku_names = [sku_map.get(lic["skuId"], lic["skuId"]) for lic in assigned]
        sku_names = [s for s in sku_names if s not in EXCLUDED_SKUS]
        if not sku_names:
            continue
        email = u.get("mail") or u.get("userPrincipalName") or ""
        result.append(
            LicensedUser(
                account_id=u["id"],
                display_name=u.get("displayName", ""),
                email=email.lower(),
                sku_part_numbers=sku_names,
            )
        )
    return result


def main() -> None:
    tenant_id = os.environ["MS_TENANT_ID"]
    client_id = os.environ["MS_CLIENT_ID"]
    client_secret = os.environ["MS_CLIENT_SECRET"]

    client = GraphClient(tenant_id, client_id, client_secret)

    print("Fetching SKU map...")
    sku_map = fetch_sku_map(client)
    print(f"  Found {len(sku_map)} SKUs")

    print("Fetching licensed users...")
    users = fetch_licensed_users(client, sku_map)
    print(f"  Found {len(users)} users with licenses\n")

    for user in sorted(users, key=lambda u: u.email):
        print(f"{user.email}  ({user.display_name})")
        for sku in user.sku_part_numbers:
            print(f"  - {sku}")

    # Also dump as JSON for use by other scripts
    output = [
        {
            "account_id": u.account_id,
            "display_name": u.display_name,
            "email": u.email,
            "licenses": u.sku_part_numbers,
        }
        for u in users
    ]
    with open("microsoft_licenses.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nWrote microsoft_licenses.json ({len(output)} users)")


if __name__ == "__main__":
    from exit_codes import run

    run(main)

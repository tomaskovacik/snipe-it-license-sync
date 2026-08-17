"""
Fetch Atlassian (Jira Cloud) users and their product licenses.

Uses Jira application roles to discover product-licensed groups, then
enumerates group members to build a per-user product map.

Auth: classic (unscoped) Atlassian API token, created at id.atlassian.com.
Scoped API tokens don't work here — group/group-member endpoints aren't
covered by Atlassian's scoped-token support, so they 401 regardless of
granted scopes. Use a classic token against the site URL directly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

load_dotenv()

# Jira masks emailAddress entirely for some external/guest accounts (not
# managed by your org) regardless of token permissions — no API call can
# recover it. Configurable via ATLASSIAN_ACCOUNT_EMAIL_OVERRIDES (JSON object
# string, accountId -> email) as a manual fallback for those accounts. Empty
# by default.
def _load_email_overrides() -> dict[str, str]:
    raw = os.environ.get("ATLASSIAN_ACCOUNT_EMAIL_OVERRIDES")
    if not raw:
        return {}
    return {k: v.lower() for k, v in json.loads(raw).items()}


EMAIL_OVERRIDES = _load_email_overrides()

# Products we care about — keys match Jira applicationrole keys, except
# "confluence-guest" which has no applicationrole entry (see
# _fetch_confluence_guest_group below) and is added synthetically.
TRACKED_PRODUCTS = {
    "jira-software",
    "jira-servicedesk",
    "jira-core",
    "confluence",
    "confluence-guest",
}


@dataclass
class LicensedUser:
    account_id: str
    display_name: str
    email: str
    products: list[str] = field(default_factory=list)


class JiraClient:
    def __init__(self, site_url: str, user: str, token: str):
        self._base_url = f"{site_url.rstrip('/')}/rest/api/3"
        self._auth = (user, token)
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._base_url}/{path.lstrip('/')}"
        resp = self._session.get(url, auth=self._auth, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_all_pages(self, path: str, params: dict | None = None) -> list[dict]:
        """Paginate using startAt/maxResults and return all items.

        Some endpoints (e.g. group/member) silently cap maxResults server-side
        and echo the capped value back, so a short page doesn't mean it's the
        last one. Once the API tells us the real `total`, drive the loop off
        that directly instead of trusting isLast; fall back to isLast (or an
        empty page) only for endpoints that don't report a total.
        """
        base_params = dict(params or {})
        base_params.setdefault("maxResults", 200)
        items = []
        start = 0
        total = None
        while True:
            base_params["startAt"] = start
            data = self.get(path, params=base_params)
            if total is None:
                total = data.get("total")
            values = data.get("values", [])
            if not values:
                break
            items.extend(values)
            start += len(values)
            if total is not None:
                if len(items) >= total:
                    break
            elif data.get("isLast", True):
                break

        if total is not None and len(items) != total:
            print(
                f"  WARNING: paginated fetch of '{path}' returned {len(items)} "
                f"items but the API reports total={total} — results are likely "
                f"incomplete, check pagination logic."
            )
        return items


def _fetch_confluence_group(client: JiraClient) -> str | None:
    """Confluence licensing isn't exposed via applicationrole (that's Jira-only),
    so find the default confluence-users-* group via the groups picker instead."""
    result = client.get("groups/picker", params={"query": "confluence-users"})
    candidates = [
        g["name"]
        for g in result.get("groups", [])
        if g["name"].startswith("confluence-users-")
    ]
    return candidates[0] if candidates else None


def _fetch_confluence_guest_group(client: JiraClient) -> str | None:
    """Confluence guest access is a separate license/group from regular
    Confluence users, also not exposed via applicationrole — find the
    confluence-guests-* group via the groups picker."""
    result = client.get("groups/picker", params={"query": "confluence-guests"})
    candidates = [
        g["name"]
        for g in result.get("groups", [])
        if g["name"].startswith("confluence-guests-")
    ]
    return candidates[0] if candidates else None


def fetch_product_groups(client: JiraClient) -> dict[str, list[str]]:
    """Return mapping of product_key -> all group_names that grant it, from
    application roles. A role can list several groups (default users group
    plus admin groups) that each independently confer product access."""
    roles = client.get("applicationrole")
    product_groups: dict[str, list[str]] = {}
    for role in roles:
        key = role.get("key", "")
        if key not in TRACKED_PRODUCTS:
            continue
        groups = role.get("groups", [])
        if groups:
            product_groups[key] = groups

    if "confluence" in TRACKED_PRODUCTS and "confluence" not in product_groups:
        confluence_group = _fetch_confluence_group(client)
        if confluence_group:
            product_groups["confluence"] = [confluence_group]

    if "confluence-guest" in TRACKED_PRODUCTS:
        confluence_guest_group = _fetch_confluence_guest_group(client)
        if confluence_guest_group:
            product_groups["confluence-guest"] = [confluence_guest_group]

    return product_groups


def _resolve_email(client: JiraClient, account_id: str) -> str:
    """group/member omits emailAddress for some accounts (commonly guest/
    external users not in the org's managed directory) — fall back to a
    direct user lookup, which returns it reliably."""
    try:
        data = client.get("user", params={"accountId": account_id})
    except requests.exceptions.HTTPError:
        return ""
    return (data.get("emailAddress") or "").lower()


def fetch_licensed_users(client: JiraClient) -> list[LicensedUser]:
    """Return all users with at least one Atlassian product license."""
    product_groups = fetch_product_groups(client)
    if not product_groups:
        print("  Warning: no application roles found. Check token scopes.")
        return []

    users: dict[str, LicensedUser] = {}

    for product_key, group_names in product_groups.items():
        product_account_ids: set[str] = set()
        for group_name in group_names:
            print(f"  Fetching group '{group_name}' for {product_key}...")
            members = client.get_all_pages(
                "group/member",
                params={"groupname": group_name, "includeInactiveUsers": "false"},
            )
            print(f"    {len(members)} members")

            for m in members:
                if m.get("accountType") != "atlassian" or not m.get("active", True):
                    continue
                account_id = m["accountId"]
                if account_id not in users:
                    email = m.get("emailAddress", "").lower()
                    if not email:
                        email = _resolve_email(client, account_id)
                    if not email:
                        email = EMAIL_OVERRIDES.get(account_id, "")
                    if not email:
                        print(
                            f"    WARNING: could not resolve email for "
                            f"{m.get('displayName', '')} ({account_id}) — "
                            f"Jira masks it for this account; add it to "
                            f"ATLASSIAN_ACCOUNT_EMAIL_OVERRIDES in .env"
                        )
                    users[account_id] = LicensedUser(
                        account_id=account_id,
                        display_name=m.get("displayName", ""),
                        email=email,
                    )
                product_account_ids.add(account_id)

        for account_id in product_account_ids:
            users[account_id].products.append(product_key)

    return list(users.values())


def main() -> None:
    site_url = os.environ["ATLASSIAN_SITE_URL"]
    user = os.environ["ATLASSIAN_USER"]
    token = os.environ["ATLASSIAN_API_TOKEN"]

    client = JiraClient(site_url, user, token)

    print("Fetching Atlassian licensed users...")
    users = fetch_licensed_users(client)
    print(f"\nFound {len(users)} users with at least one product license\n")

    for u in sorted(users, key=lambda x: x.email):
        print(f"{u.email}  ({u.display_name})")
        for p in sorted(u.products):
            print(f"  - {p}")

    output = [
        {
            "account_id": u.account_id,
            "display_name": u.display_name,
            "email": u.email,
            "licenses": sorted(u.products),
        }
        for u in users
    ]
    with open("atlassian_licenses.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nWrote atlassian_licenses.json ({len(output)} users)")


if __name__ == "__main__":
    from exit_codes import run

    run(main)

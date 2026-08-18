"""
Fetch Bitbucket Cloud workspace members and tag them with a "bitbucket" license.

Bitbucket Cloud is a separate product from Jira/Confluence — its own API
(api.bitbucket.org, not the Jira site URL), its own credential, and it
doesn't share Jira's site-level user/group model. Workspace membership
doesn't include email addresses, so each member's email is resolved via
Jira's user-lookup endpoint using the shared Atlassian account_id (the same
"accountId" format used across Jira/Confluence/Bitbucket) and the existing
classic Atlassian API token.

Auth:
  BITBUCKET_API_TOKEN — Atlassian API token with scopes, app = Bitbucket,
    scope: read:workspace:bitbucket. Create at:
    https://id.atlassian.com/manage-profile/security/api-tokens
  BITBUCKET_WORKSPACE — your workspace slug (Bitbucket's cross-workspace
    listing endpoint was permanently removed — CHANGE-2770 — so this can't
    be discovered automatically; find it in the workspace URL/selector at
    bitbucket.org).
  ATLASSIAN_SITE_URL / ATLASSIAN_USER / ATLASSIAN_API_TOKEN — reused from the
    Atlassian (Jira) config, for resolving account_id -> email.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

from quiet import is_quiet

load_dotenv()

BITBUCKET_API_BASE = "https://api.bitbucket.org/2.0"


@dataclass
class LicensedUser:
    account_id: str
    display_name: str
    email: str
    licenses: list[str] = field(default_factory=lambda: ["bitbucket"])


class BitbucketClient:
    def __init__(self, user: str, token: str):
        self._auth = (user, token)
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def get(self, url: str, params: dict | None = None) -> dict:
        resp = self._session.get(url, auth=self._auth, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_all_pages(self, url: str, params: dict | None = None) -> list[dict]:
        """Follow the "next" URL pagination and return all values."""
        items = []
        next_url: str | None = url
        while next_url:
            data = self.get(next_url, params=params if next_url == url else None)
            items.extend(data.get("values", []))
            next_url = data.get("next")
        return items


def fetch_workspace_members(client: BitbucketClient, workspace: str) -> list[dict]:
    """Return raw workspace membership rows (account_id, display_name)."""
    rows = client.get_all_pages(
        f"{BITBUCKET_API_BASE}/workspaces/{workspace}/members",
        params={"pagelen": 100},
    )
    members = []
    for row in rows:
        user = row.get("user", {})
        account_id = user.get("account_id")
        if not account_id:
            continue
        members.append(
            {"account_id": account_id, "display_name": user.get("display_name", "")}
        )
    return members


class JiraEmailResolver:
    """Resolves an Atlassian account_id to an email via Jira's user endpoint —
    Bitbucket's workspace membership API doesn't return email addresses."""

    def __init__(self, site_url: str, user: str, token: str):
        self._base_url = f"{site_url.rstrip('/')}/rest/api/3"
        self._auth = (user, token)
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def resolve(self, account_id: str) -> str:
        resp = self._session.get(
            f"{self._base_url}/user",
            auth=self._auth,
            params={"accountId": account_id},
        )
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        return (resp.json().get("emailAddress") or "").lower()


def fetch_licensed_users(
    client: BitbucketClient, workspace: str, resolver: JiraEmailResolver
) -> list[LicensedUser]:
    print(f"  Fetching members of workspace '{workspace}'...")
    raw_members = fetch_workspace_members(client, workspace)
    print(f"    {len(raw_members)} members")

    users = []
    for m in raw_members:
        email = resolver.resolve(m["account_id"])
        if not email:
            print(
                f"    WARNING: could not resolve email for "
                f"{m['display_name']} ({m['account_id']})"
            )
        users.append(
            LicensedUser(
                account_id=m["account_id"],
                display_name=m["display_name"],
                email=email,
            )
        )
    return users


REQUIRED_ENV_VARS = [
    "BITBUCKET_API_TOKEN",
    "BITBUCKET_WORKSPACE",
    "ATLASSIAN_SITE_URL",
    "ATLASSIAN_USER",
    "ATLASSIAN_API_TOKEN",
]


def main() -> None:
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"missing required .env variable(s): {', '.join(missing)}")

    bitbucket_user = os.environ.get("BITBUCKET_USER") or os.environ["ATLASSIAN_USER"]
    bitbucket_token = os.environ["BITBUCKET_API_TOKEN"]
    workspace = os.environ["BITBUCKET_WORKSPACE"]

    site_url = os.environ["ATLASSIAN_SITE_URL"]
    jira_user = os.environ["ATLASSIAN_USER"]
    jira_token = os.environ["ATLASSIAN_API_TOKEN"]

    client = BitbucketClient(bitbucket_user, bitbucket_token)
    resolver = JiraEmailResolver(site_url, jira_user, jira_token)

    print("Fetching Bitbucket licensed users...")
    users = fetch_licensed_users(client, workspace, resolver)
    print(f"\nFound {len(users)} workspace members\n")

    if not is_quiet():
        for u in sorted(users, key=lambda x: x.email):
            print(f"{u.email or '(no email)'}  ({u.display_name})")
            for lic in u.licenses:
                print(f"  - {lic}")

    output = [
        {
            "account_id": u.account_id,
            "display_name": u.display_name,
            "email": u.email,
            "licenses": u.licenses,
        }
        for u in users
    ]
    with open("bitbucket_licenses.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nWrote bitbucket_licenses.json ({len(output)} users)")


if __name__ == "__main__":
    from exit_codes import run

    run(main)

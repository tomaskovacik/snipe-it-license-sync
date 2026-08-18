"""
Fetch Slack workspace users and their license types.

Uses the Slack Web API users.list endpoint to enumerate workspace members
and derive a license type from each user's role flags.

Auth: Slack Bot Token (xoxb-...).
  Required OAuth scopes:
    - users:read
    - users:read.email

Create a Bot Token at https://api.slack.com/apps → your app → OAuth & Permissions.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

from quiet import is_quiet

load_dotenv()

SLACK_API_BASE = "https://slack.com/api"

# Derived license labels in priority order
_LICENSE_LABELS = {
    "is_primary_owner": "owner",
    "is_owner": "owner",
    "is_admin": "admin",
    "is_ultra_restricted": "guest-single-channel",
    "is_restricted": "guest-multi-channel",
}


def _license_type(member: dict) -> str:
    for flag, label in _LICENSE_LABELS.items():
        if member.get(flag):
            return label
    return "member"


@dataclass
class LicensedUser:
    account_id: str
    display_name: str
    email: str
    licenses: list[str] = field(default_factory=list)


class SlackClient:
    def __init__(self, token: str):
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        )

    def get(self, method: str, params: dict | None = None) -> dict:
        resp = self._session.get(f"{SLACK_API_BASE}/{method}", params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error on {method}: {data.get('error')}")
        return data

    def get_all_pages(self, method: str, result_key: str, params: dict | None = None) -> list[dict]:
        """Paginate using cursor-based pagination and return all items."""
        base_params = dict(params or {})
        base_params.setdefault("limit", 200)
        items = []
        cursor: str | None = None
        while True:
            if cursor:
                base_params["cursor"] = cursor
            data = self.get(method, params=base_params)
            items.extend(data.get(result_key, []))
            cursor = data.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                break
        return items


def fetch_licensed_users(client: SlackClient) -> list[LicensedUser]:
    """Return all active, non-bot workspace members with their license type."""
    print("  Fetching workspace members...")
    members = client.get_all_pages("users.list", result_key="members")
    print(f"    {len(members)} total members (before filtering)")

    users: list[LicensedUser] = []
    for m in members:
        if m.get("deleted") or m.get("is_bot") or m.get("is_app_user"):
            continue
        if m.get("id") == "USLACKBOT":
            continue
        profile = m.get("profile", {})
        email = (profile.get("email") or "").lower()
        users.append(
            LicensedUser(
                account_id=m["id"],
                display_name=profile.get("real_name") or m.get("name", ""),
                email=email,
                licenses=[_license_type(m)],
            )
        )
    return users


def main() -> None:
    token = os.environ["SLACK_BOT_TOKEN"]

    client = SlackClient(token)

    print("Fetching Slack licensed users...")
    users = fetch_licensed_users(client)
    print(f"\nFound {len(users)} active users\n")

    if not is_quiet():
        for u in sorted(users, key=lambda x: x.email):
            print(f"{u.email}  ({u.display_name})")
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
    with open("slack_licenses.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nWrote slack_licenses.json ({len(output)} users)")


if __name__ == "__main__":
    from exit_codes import run

    run(main)

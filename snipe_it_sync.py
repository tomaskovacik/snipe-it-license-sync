"""
Reconciles fetched license data (Microsoft/Atlassian/Slack) against Snipe-IT
License seat assignments.

Defaults to dry run: reports the diff (who *should* be checked out/in per
license) without calling Snipe-IT's checkout/checkin endpoints. Set
SNIPE_IT_DRY_RUN=false in .env to actually apply the diff — this performs
real PUT requests against your Snipe-IT instance's license seats.

Auth: Snipe-IT personal API token.
  Create one at: your Snipe-IT URL -> Account (top right) -> Manage API Keys
    -> Create New Token

Run the source fetch scripts first so their JSON files exist:
  microsoft_licenses.py  -> microsoft_licenses.json
  atlassian_licenses.py  -> atlassian_licenses.json
  slack_licenses.py      -> slack_licenses.json
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

from exit_codes import EXIT_CRITICAL, EXIT_OK, EXIT_UNKNOWN, EXIT_WARNING
from quiet import is_quiet

load_dotenv()

# Maps product/SKU keys (as they appear in the *_licenses.json files) to the
# exact License name in Snipe-IT (must match exactly, case-sensitive).
# Configurable via SNIPE_IT_LICENSE_MAP (JSON object string) so it can be set
# per-deployment (e.g. Docker -e SNIPE_IT_LICENSE_MAP='{"jira-software":"Jira"}')
# without editing code. Falls back to this default if the env var isn't set.
_DEFAULT_LICENSE_MAP = {
    "jira-software": "Jira",
    "confluence": "Confluence",
    "confluence-guest": "Confluence guest access",
    "jira-servicedesk": "Jira",
    "jira-core": "Jira",
    # Microsoft SKU part numbers -> Snipe-IT license name. These SKU strings
    # are legacy names that stuck around in Graph API after Microsoft renamed
    # the products.
    "EXCHANGESTANDARD": "Microsoft Exchange Online (Plan 1)",
    "INTUNE_A": "Microsoft Intune",
    "O365_BUSINESS_ESSENTIALS": "Microsoft 365 Business Basic",
    "O365_BUSINESS_PREMIUM": "Microsoft 365 Business Standard",
    # Slack license labels (from slack_licenses.py's _license_type) -> Snipe-IT
    # license name. All roles, including guests, map to the same seat.
    "owner": "Slack",
    "admin": "Slack",
    "member": "Slack",
    "guest-multi-channel": "Slack",
    "guest-single-channel": "Slack",
    "bitbucket": "Bitbucket",
}


def _load_license_map() -> dict[str, str]:
    raw = os.environ.get("SNIPE_IT_LICENSE_MAP")
    if not raw:
        return _DEFAULT_LICENSE_MAP
    return json.loads(raw)


PRODUCT_TO_SNIPEIT_LICENSE = _load_license_map()

# Maps a known alias email to its canonical email, so the same person using
# different addresses across systems (e.g. a ".ext" marker added on one side
# during an account transition) is treated as one identity when diffing.
# Configurable via SNIPE_IT_EMAIL_ALIASES (JSON object string), e.g.
# '{"dian.ext@dgtfactory.com":"dian@dgtfactory.com"}'. Empty by default —
# nothing is aliased unless explicitly configured.
def _load_email_aliases() -> dict[str, str]:
    raw = os.environ.get("SNIPE_IT_EMAIL_ALIASES")
    if not raw:
        return {}
    return {k.lower(): v.lower() for k, v in json.loads(raw).items()}


EMAIL_ALIASES = _load_email_aliases()


def _canonicalize_email(email: str) -> str:
    return EMAIL_ALIASES.get(email, email)


SOURCE_FILES = {
    "microsoft": "microsoft_licenses.json",
    "atlassian": "atlassian_licenses.json",
    "slack": "slack_licenses.json",
    "bitbucket": "bitbucket_licenses.json",
}


@dataclass
class LicenseDiff:
    license_id: int
    license_name: str
    to_checkout: list[str] = field(default_factory=list)  # emails
    to_checkin: list[tuple[str, int]] = field(default_factory=list)  # (email, seat_id)
    free_seat_ids: list[int] = field(default_factory=list)


class SnipeITClient:
    def __init__(self, base_url: str, token: str):
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = self._session.get(
            f"{self._base_url}/api/v1/{path.lstrip('/')}", params=params
        )
        resp.raise_for_status()
        return resp.json()

    def put(self, path: str, json_body: dict) -> dict:
        """PUT and raise if Snipe-IT reports an error, even under HTTP 200 —
        Snipe-IT often returns 200 with {"status": "error", ...} on validation
        failures rather than a 4xx status code."""
        resp = self._session.put(
            f"{self._base_url}/api/v1/{path.lstrip('/')}", json=json_body
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(data.get("messages") or data)
        return data

    def patch(self, path: str, json_body: dict) -> dict:
        """Same error handling as put() — used for licenses/{id} updates."""
        resp = self._session.patch(
            f"{self._base_url}/api/v1/{path.lstrip('/')}", json=json_body
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(data.get("messages") or data)
        return data

    def get_all_pages(self, path: str, params: dict | None = None) -> list[dict]:
        """Paginate using offset/limit and return all rows."""
        base_params = dict(params or {})
        base_params.setdefault("limit", 200)
        offset = 0
        items = []
        while True:
            base_params["offset"] = offset
            data = self.get(path, params=base_params)
            rows = data.get("rows", [])
            items.extend(rows)
            total = data.get("total", len(items))
            offset += len(rows)
            if not rows or offset >= total:
                break
        return items


def fetch_snipeit_license_ids(client: SnipeITClient) -> dict[str, int]:
    """Return mapping of license name -> Snipe-IT license id."""
    licenses = client.get_all_pages("licenses")
    return {lic["name"]: lic["id"] for lic in licenses}


def fetch_license_seats(client: SnipeITClient, license_id: int) -> tuple[dict[str, int], list[int]]:
    """Return (current_holders, free_seat_ids) for a license.

    current_holders maps email -> seat_id for seats currently checked out to
    a user. free_seat_ids lists seats with neither a user nor an asset
    assigned, i.e. available to check a new user out to.

    Seats assigned to a user carry the full user object (including email)
    directly under "assigned_user" — seats assigned to an asset instead use
    "assigned_asset" and have no user/email.
    """
    seats = client.get_all_pages(f"licenses/{license_id}/seats")
    current_holders: dict[str, int] = {}
    free_seat_ids: list[int] = []
    for seat in seats:
        assigned = seat.get("assigned_user")
        if assigned:
            email = _canonicalize_email((assigned.get("email") or "").lower())
            if email:
                current_holders[email] = seat["id"]
            continue
        if not seat.get("assigned_asset"):
            free_seat_ids.append(seat["id"])
    return current_holders, free_seat_ids


def fetch_snipeit_user_ids_by_email(client: SnipeITClient) -> dict[str, int]:
    """Return mapping of canonicalized email -> Snipe-IT user id, for
    resolving checkout targets."""
    users = client.get_all_pages("users")
    result = {}
    for u in users:
        email = (u.get("email") or "").lower()
        if email:
            result[_canonicalize_email(email)] = u["id"]
    return result


def load_source_licenses() -> dict[str, set[str]]:
    """Return product_key -> set of emails that should hold that license,
    aggregated across all *_licenses.json source files."""
    product_emails: dict[str, set[str]] = {}
    for source, filename in SOURCE_FILES.items():
        if not os.path.exists(filename):
            print(f"  Skipping {source}: {filename} not found (run its fetch script first)")
            continue
        with open(filename, encoding="utf-8") as f:
            entries = json.load(f)
        for entry in entries:
            email = (entry.get("email") or "").lower()
            if not email:
                continue
            email = _canonicalize_email(email)
            for product_key in entry.get("licenses", []):
                product_emails.setdefault(product_key, set()).add(email)
    return product_emails


def build_diff(client: SnipeITClient) -> list[LicenseDiff]:
    if not is_quiet():
        print("Fetching Snipe-IT licenses...")
    license_ids_by_name = fetch_snipeit_license_ids(client)
    if not is_quiet():
        print(f"  {len(license_ids_by_name)} licenses found")

    if not is_quiet():
        print("Loading source license data...")
    product_emails = load_source_licenses()

    # Multiple product keys can map to the same Snipe-IT license (e.g. Slack's
    # owner/admin/member/guest-* roles all map to one "Slack" license) — union
    # their wanted emails BEFORE diffing, so a person counted under one role
    # key isn't treated as a stray checkin when diffed under a different one.
    # Seed every configured license name with an empty set up front: if a
    # product key has zero holders in this run (the last person with that
    # license was just removed), it's absent from product_emails entirely —
    # without this seed the license would never be diffed against Snipe-IT at
    # all, so the last leaver's seat would stay checked out forever.
    license_wanted: dict[str, set[str]] = {
        license_name: set() for license_name in PRODUCT_TO_SNIPEIT_LICENSE.values()
    }
    for product_key, wanted_emails in product_emails.items():
        license_name = PRODUCT_TO_SNIPEIT_LICENSE.get(product_key)
        if license_name is None:
            print(
                f"  WARNING: no Snipe-IT license mapping for product "
                f"'{product_key}' — add it to PRODUCT_TO_SNIPEIT_LICENSE"
            )
            continue
        license_wanted.setdefault(license_name, set()).update(wanted_emails)

    diffs = []
    for license_name, wanted_emails in sorted(license_wanted.items()):
        license_id = license_ids_by_name.get(license_name)
        if license_id is None:
            print(f"  WARNING: Snipe-IT has no license named '{license_name}'")
            continue

        if not is_quiet():
            print(f"  Fetching seats for '{license_name}'...")
        current_holders, free_seat_ids = fetch_license_seats(client, license_id)

        diffs.append(
            LicenseDiff(
                license_id=license_id,
                license_name=license_name,
                to_checkout=sorted(wanted_emails - current_holders.keys()),
                to_checkin=sorted(
                    (email, seat_id)
                    for email, seat_id in current_holders.items()
                    if email not in wanted_emails
                ),
                free_seat_ids=free_seat_ids,
            )
        )
    return diffs


def _provision_seat(client: SnipeITClient, license_id: int) -> list[int]:
    """Grow the license's total seat count by 1 and return the resulting free
    seat ids. These are cloud-vendor licenses (Jira/Slack/etc.) — if a source
    system says someone holds one, Snipe-IT is simply out of date, not over
    budget, so we grow it to match rather than refuse the checkout. Snipe-IT
    auto-creates a matching LicenseSeat row when 'seats' is PATCHed higher."""
    current = client.get(f"licenses/{license_id}")
    new_total = current["seats"] + 1
    client.patch(f"licenses/{license_id}", {"seats": new_total})
    print(f"    Provisioned 1 new seat for license {license_id} (total now {new_total})")
    _, free_seat_ids = fetch_license_seats(client, license_id)
    return free_seat_ids


def _is_dry_run() -> bool:
    raw = os.environ.get("SNIPE_IT_DRY_RUN", "true").strip().lower()
    return raw not in ("false", "0", "no")


def apply_diff(client: SnipeITClient, diffs: list[LicenseDiff]) -> dict[str, dict]:
    """Actually perform the checkout/checkin calls for each diff. Returns
    per-license results (checked_out / checked_in / errors) for the JSON
    output, so a failed item doesn't stop the rest of the run."""
    if not is_quiet():
        print("\nApplying changes...")
    email_to_user_id = fetch_snipeit_user_ids_by_email(client)
    note = "Synced by snipe_it_sync.py"

    results: dict[str, dict] = {}
    for d in diffs:
        checked_out, checked_in, errors = [], [], []
        free_seats = list(d.free_seat_ids)

        # Check in before checking out: a straight seat swap (one leaver, one
        # joiner) should reuse the freed seat instead of provisioning a new
        # one, so the license's total seat count only grows on real net
        # additions.
        for email, seat_id in d.to_checkin:
            try:
                client.put(
                    f"licenses/{d.license_id}/seats/{seat_id}",
                    {"assigned_to": None, "asset_id": None, "note": note},
                )
                print(f"  Checked in {email} <- {d.license_name} (seat {seat_id})")
                checked_in.append(email)
                free_seats.append(seat_id)
            except Exception as e:
                print(f"  ERROR checking in {email} <- {d.license_name}: {e}")
                errors.append({"email": email, "action": "checkin", "error": str(e)})

        for email in d.to_checkout:
            user_id = email_to_user_id.get(email)
            if user_id is None:
                msg = f"no matching Snipe-IT user for {email}"
                print(f"  SKIP checkout {email} -> {d.license_name}: {msg}")
                errors.append({"email": email, "action": "checkout", "error": msg})
                continue
            if not free_seats:
                try:
                    free_seats = _provision_seat(client, d.license_id)
                except Exception as e:
                    msg = f"could not provision a new seat: {e}"
                    print(f"  ERROR checkout {email} -> {d.license_name}: {msg}")
                    errors.append({"email": email, "action": "checkout", "error": msg})
                    continue
            seat_id = free_seats.pop()
            try:
                client.put(
                    f"licenses/{d.license_id}/seats/{seat_id}",
                    {"assigned_to": user_id, "note": note},
                )
                print(f"  Checked out {email} -> {d.license_name} (seat {seat_id})")
                checked_out.append(email)
            except Exception as e:
                print(f"  ERROR checking out {email} -> {d.license_name}: {e}")
                errors.append({"email": email, "action": "checkout", "error": str(e)})

        results[d.license_name] = {
            "checked_out": checked_out,
            "checked_in": checked_in,
            "errors": errors,
        }
    return results


def main() -> int:
    base_url = os.environ["SNIPE_IT_URL"]
    token = os.environ["SNIPE_IT_API_TOKEN"]
    dry_run = _is_dry_run()

    client = SnipeITClient(base_url, token)

    mode_desc = (
        "dry run — no changes will be made"
        if dry_run
        else "APPLY MODE — real checkout/checkin calls will be made"
    )
    if not is_quiet():
        print(f"Building Snipe-IT license diff ({mode_desc})...\n")
    diffs = build_diff(client)

    if not is_quiet():
        print("\n" + "=" * 60)
        print("DRY RUN SUMMARY" if dry_run else "PLANNED CHANGES")
        print("=" * 60)

    any_changes = False
    for d in diffs:
        if not d.to_checkout and not d.to_checkin:
            continue
        any_changes = True
        verb = "Would" if dry_run else "Will"
        print(f"\n{d.license_name}")
        if d.to_checkout:
            print(f"  {verb} check out ({len(d.to_checkout)}):")
            for email in d.to_checkout:
                print(f"    + {email}")
        if d.to_checkin:
            print(f"  {verb} check in ({len(d.to_checkin)}):")
            for email, _seat_id in d.to_checkin:
                print(f"    - {email}")

    if not any_changes and not is_quiet():
        print("\nNo changes needed — Snipe-IT is already in sync.")

    apply_results = apply_diff(client, diffs) if (any_changes and not dry_run) else None

    output = [
        {
            "license_name": d.license_name,
            "to_checkout": d.to_checkout,
            "to_checkin": [email for email, _seat_id in d.to_checkin],
            **({"applied": apply_results[d.license_name]} if apply_results else {}),
        }
        for d in diffs
    ]
    with open("snipe_it_diff.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    if not is_quiet():
        print("\nWrote snipe_it_diff.json")

    if apply_results is not None:
        error_count = sum(len(r["errors"]) for r in apply_results.values())
        if error_count:
            print(f"\nCRITICAL: {error_count} error(s) applying changes to Snipe-IT")
            return EXIT_CRITICAL
        print("\nOK: changes applied to Snipe-IT")
        return EXIT_OK

    if any_changes:
        checkout_count = sum(len(d.to_checkout) for d in diffs)
        checkin_count = sum(len(d.to_checkin) for d in diffs)
        print(
            f"\nWARNING: Snipe-IT out of sync — {checkout_count} checkout(s), "
            f"{checkin_count} checkin(s) needed"
        )
        return EXIT_WARNING

    print("\nOK: Snipe-IT is in sync")
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.exceptions.ConnectionError:
        print(
            f"CRITICAL: Snipe-IT not reachable at "
            f"{os.environ.get('SNIPE_IT_URL', '(SNIPE_IT_URL not set)')} — "
            "check network/VPN connectivity."
        )
        sys.exit(EXIT_CRITICAL)
    except requests.exceptions.JSONDecodeError:
        print(
            f"CRITICAL: got a non-JSON response from "
            f"{os.environ.get('SNIPE_IT_URL', '(SNIPE_IT_URL not set)')} — "
            "that host answered but isn't serving the Snipe-IT API (wrong "
            "SNIPE_IT_URL, DNS hijack/parked-domain page, or a proxy/error "
            "page in front of it)."
        )
        sys.exit(EXIT_CRITICAL)
    except KeyError as e:
        print(f"UNKNOWN: missing required .env variable: {e}")
        sys.exit(EXIT_UNKNOWN)
    except Exception as e:
        print(f"UNKNOWN: {e}")
        sys.exit(EXIT_UNKNOWN)

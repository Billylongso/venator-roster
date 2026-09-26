"""
Builds site/roster.json from VRChat group member lists.

Reads battalion -> group ID pairs from groups.json, logs into VRChat with a bot account,
pulls every member's display name, and writes:
    { "41st": ["Name", ...], "21st": [...], "104th": [...] }

Needs these environment variables (GitHub Actions secrets):
    VRC_USERNAME     bot account username/email
    VRC_PASSWORD     bot account password
    VRC_TOTP_SECRET  bot account's authenticator (2FA) secret key
    VRC_CONTACT      contact info for the User-Agent (VRChat asks for this)
"""

import base64
import json
import os
import sys
import time
from urllib.parse import quote

import pyotp
import requests

API = "https://api.vrchat.cloud/api/1"
PAGE_SIZE = 100


def fail(message):
    print(f"ERROR: {message}")
    sys.exit(1)


def login(session):
    username = os.environ["VRC_USERNAME"]
    password = os.environ["VRC_PASSWORD"]

    # VRChat wants the username and password URL-encoded before Basic auth.
    token = base64.b64encode(f"{quote(username)}:{quote(password)}".encode()).decode()
    r = session.get(f"{API}/auth/user", headers={"Authorization": f"Basic {token}"})
    if r.status_code != 200:
        fail(f"Login failed ({r.status_code}): {r.text}")

    needs = r.json().get("requiresTwoFactorAuth")
    if not needs:
        return
    if "totp" not in needs:
        fail(f"Bot account needs 2FA type {needs}. Set up an authenticator app (TOTP) on it instead of email codes.")

    code = pyotp.TOTP(os.environ["VRC_TOTP_SECRET"].replace(" ", "")).now()
    r = session.post(f"{API}/auth/twofactorauth/totp/verify", json={"code": code})
    if r.status_code != 200 or not r.json().get("verified"):
        fail(f"2FA failed ({r.status_code}): {r.text}")


def get_member_names(session, group_id):
    names = []
    offset = 0
    while True:
        r = session.get(f"{API}/groups/{group_id}/members", params={"n": PAGE_SIZE, "offset": offset})
        if r.status_code != 200:
            fail(f"Couldn't read members of {group_id} ({r.status_code}): {r.text}")

        page = r.json()
        for member in page:
            user = member.get("user") or {}
            if member.get("membershipStatus", "member") == "member" and user.get("displayName"):
                names.append(user["displayName"])

        if len(page) < PAGE_SIZE:
            return names
        offset += PAGE_SIZE
        time.sleep(1)  # be gentle with the API


def main():
    with open("groups.json", encoding="utf-8") as f:
        groups = json.load(f)

    session = requests.Session()
    session.headers["User-Agent"] = f"VenatorRosterSync/1.0 {os.environ.get('VRC_CONTACT', '')}".strip()

    login(session)

    roster = {}
    for battalion, group_id in groups.items():
        names = get_member_names(session, group_id)
        # An empty list almost always means a permissions problem, so stop rather than
        # publish a roster that locks everyone out. The last good roster stays online.
        if not names:
            fail(f"{battalion} ({group_id}) came back with no members.")
        roster[battalion] = sorted(names, key=str.lower)
        print(f"{battalion}: {len(names)} members")
        time.sleep(1)

    session.put(f"{API}/logout")

    os.makedirs("site", exist_ok=True)
    with open("site/roster.json", "w", encoding="utf-8") as f:
        json.dump(roster, f, ensure_ascii=False, indent=1)
    print("Wrote site/roster.json")


if __name__ == "__main__":
    main()

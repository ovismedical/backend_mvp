#!/usr/bin/env python3
"""List and clean up user accounts in the OVIS database.

Usage:
    python scripts/cleanup_users.py              # List all accounts
    python scripts/cleanup_users.py --delete u1 u2 u3  # Delete specific usernames
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from pymongo import MongoClient


def get_db():
    uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("MONGODB_DB", "ovis-demo")
    if not uri:
        print("Error: MONGODB_URI not set in .env")
        sys.exit(1)
    client = MongoClient(uri)
    return client[db_name]


def list_accounts(db):
    print("=" * 70)
    print("PATIENTS (users collection)")
    print("=" * 70)
    print(f"{'Username':<20} {'Name':<25} {'Email':<30} {'Flag'}")
    print("-" * 70)

    users = list(db["users"].find({}, {"password": 0}))
    seen_usernames = {}
    for u in users:
        username = u.get("username", "")
        name = u.get("full_name", "")
        email = u.get("email", "")

        # Flag junk
        flags = []
        if not username or not username.strip():
            flags.append("EMPTY")
        elif len(username) <= 3:
            flags.append("SHORT")

        # Track duplicates
        if username in seen_usernames:
            flags.append("DUPE")
            # Also flag the first occurrence retroactively (printed already)
        seen_usernames.setdefault(username, 0)
        seen_usernames[username] += 1

        flag_str = ", ".join(flags) if flags else ""
        print(f"{username:<20} {name:<25} {email:<30} {flag_str}")

    # Print duplicate summary
    dupes = {k: v for k, v in seen_usernames.items() if v > 1}
    if dupes:
        print(f"\nDuplicates: {dupes}")

    print(f"\nTotal patients: {len(users)}")

    print()
    print("=" * 70)
    print("DOCTORS (doctors collection)")
    print("=" * 70)
    print(f"{'Username':<20} {'Name':<25} {'Email':<30}")
    print("-" * 70)

    doctors = list(db["doctors"].find({}, {"password": 0}))
    for d in doctors:
        username = d.get("username", "")
        name = d.get("full_name", "")
        email = d.get("email", "")
        print(f"{username:<20} {name:<25} {email:<30}")

    print(f"\nTotal doctors: {len(doctors)}")


RELATED_COLLECTIONS = ["answers", "florence_assessments", "calendar_credentials"]


def delete_users(db, usernames):
    for username in usernames:
        # Check if user exists
        user = db["users"].find_one({"username": username})
        if not user:
            print(f"  [{username}] Not found in users collection, skipping")
            continue

        # Delete from users collection
        result = db["users"].delete_one({"username": username})
        print(f"  [{username}] Deleted from users ({result.deleted_count})")

        # Clean up related data
        for coll_name in RELATED_COLLECTIONS:
            result = db[coll_name].delete_many({"user_id": username})
            if result.deleted_count > 0:
                print(f"  [{username}] Cleaned {result.deleted_count} docs from {coll_name}")


def main():
    parser = argparse.ArgumentParser(description="OVIS user account management")
    parser.add_argument("--delete", nargs="+", metavar="USERNAME",
                        help="Delete specific usernames from the users collection")
    args = parser.parse_args()

    db = get_db()

    if args.delete:
        print(f"Deleting {len(args.delete)} account(s):\n")
        delete_users(db, args.delete)
        print("\nDone. Remaining accounts:\n")

    list_accounts(db)


if __name__ == "__main__":
    main()

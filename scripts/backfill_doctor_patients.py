#!/usr/bin/env python3
"""Link existing patients to their doctors.

Patients registered through the OTP flow have `users.doctor = <doctor username>` but were
never added to `doctors.patients`, so clinician dashboards show nobody. This adds every such
patient to their doctor's list (idempotent, `$addToSet`).

Usage (from backend_mvp/, with MONGODB_URI/MONGODB_DB in the environment or .env):
    python scripts/backfill_doctor_patients.py          # dry run: prints what would change
    python scripts/backfill_doctor_patients.py --apply  # write the changes
"""

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.login import get_db  # noqa: E402


def main(apply: bool) -> int:
    db = get_db()
    doctors = {d["username"]: d for d in db["doctors"].find({}, {"username": 1, "patients": 1, "full_name": 1})}
    missing = defaultdict(list)
    orphans = []

    for user in db["users"].find({"isDoctor": {"$ne": True}}, {"username": 1, "doctor": 1}):
        doctor_name = user.get("doctor")
        if not doctor_name:
            orphans.append(user["username"])
            continue
        doctor = doctors.get(doctor_name)
        if doctor is None:
            orphans.append(f"{user['username']} (doctor '{doctor_name}' not found)")
            continue
        if user["username"] not in (doctor.get("patients") or []):
            missing[doctor_name].append(user["username"])

    print(f"Database: {db.name}")
    print(f"Doctors: {len(doctors)}   Patients missing from a doctor's list: {sum(map(len, missing.values()))}")
    for doctor_name, usernames in sorted(missing.items()):
        print(f"  {doctor_name}: + {', '.join(sorted(usernames))}")
    if orphans:
        print(f"Patients with no resolvable doctor (left untouched): {len(orphans)}")
        for o in orphans:
            print(f"  - {o}")

    if not apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0

    for doctor_name, usernames in missing.items():
        db["doctors"].update_one({"username": doctor_name}, {"$addToSet": {"patients": {"$each": usernames}}})
    print(f"\nApplied: linked {sum(map(len, missing.values()))} patient(s) across {len(missing)} doctor(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))

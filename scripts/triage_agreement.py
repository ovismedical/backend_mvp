#!/usr/bin/env python3
"""How often do clinicians agree with Florence's triage level?

Joins `assessment_reviews` with `florence_assessments` on session_id and reports, as aggregates only
(no usernames, session ids or message text):

  * n                          reviews of records Florence actually triaged (GREEN/YELLOW/ORANGE/RED)
  * agreement_rate             clinician level == Florence level, overall and per Florence level
  * confusion_matrix           Florence level (rows) x clinician level (columns)
  * over_escalation_rate       Florence placed the patient HIGHER than the clinician did
  * under_escalation_rate      Florence placed the patient LOWER than the clinician did
  * clinician_assigned_n       levels clinicians assigned to records whose AI assessment was refused
                               (pending_clinician_review) - reported separately, never in the matrix
  * orphan_review_n            reviews whose assessment no longer exists (re-seeded / deleted)

Usage (from backend_mvp/):
    python scripts/triage_agreement.py            # local MONGODB_URI
    python scripts/triage_agreement.py --prod     # MONGODB_URI_PROD from .env
    python scripts/triage_agreement.py --json     # machine-readable output
"""

import json
import os
import sys
from collections import Counter

ALERT_LEVELS = ("GREEN", "YELLOW", "ORANGE", "RED")
_RANK = {level: i for i, level in enumerate(ALERT_LEVELS)}
PENDING_REVIEW_STATUS = "pending_clinician_review"


def _rate(part: int, whole: int):
    return round(part / whole, 4) if whole else None


def _florence_level(review: dict, assessment: dict):
    """The level Florence gave at review time: the snapshot on the review, else the assessment's triage."""
    if "florence_alert_level" in review:
        return review.get("florence_alert_level")
    if assessment.get("triage_status") == PENDING_REVIEW_STATUS:
        return None
    return (assessment.get("triage_assessment") or {}).get("alert_level") or assessment.get("alert_level")


def agreement_report(assessments, reviews) -> dict:
    """Pure aggregation over florence_assessments docs and assessment_reviews docs (any iterables of dicts).

    Only reviews whose Florence level is one of GREEN/YELLOW/ORANGE/RED enter n, the rates and the matrix.
    Reviews on records awaiting clinician review carry no Florence level and are counted in
    `clinician_assigned_n`; reviews without a matching assessment are counted in `orphan_review_n`.
    """
    by_session = {a.get("session_id"): a for a in assessments if a.get("session_id")}

    matrix = {f: {c: 0 for c in ALERT_LEVELS} for f in ALERT_LEVELS}
    per_level_n: Counter = Counter()
    per_level_agreed: Counter = Counter()
    clinician_assigned: Counter = Counter()
    n = agreed = over = under = orphan = clinician_assigned_n = 0

    for review in reviews:
        assessment = by_session.get(review.get("session_id"))
        if assessment is None:
            orphan += 1
            continue
        florence = _florence_level(review, assessment)
        clinician = review.get("alert_level_override") or florence
        if florence not in _RANK:
            if clinician in _RANK:
                clinician_assigned_n += 1
                clinician_assigned[clinician] += 1
            continue
        if clinician not in _RANK:
            continue
        n += 1
        per_level_n[florence] += 1
        matrix[florence][clinician] += 1
        if clinician == florence:
            agreed += 1
            per_level_agreed[florence] += 1
        elif _RANK[florence] > _RANK[clinician]:
            over += 1
        else:
            under += 1

    return {
        "n": n,
        "agreed_n": agreed,
        "agreement_rate": _rate(agreed, n),
        "agreement_by_florence_level": {
            level: {"n": per_level_n[level], "agreed_n": per_level_agreed[level],
                    "rate": _rate(per_level_agreed[level], per_level_n[level])}
            for level in ALERT_LEVELS
        },
        "confusion_matrix": matrix,
        "over_escalation_n": over,
        "over_escalation_rate": _rate(over, n),
        "under_escalation_n": under,
        "under_escalation_rate": _rate(under, n),
        "clinician_assigned_n": clinician_assigned_n,
        "clinician_assigned_by_level": {level: clinician_assigned[level] for level in ALERT_LEVELS},
        "orphan_review_n": orphan,
    }


def load_from_db(db) -> dict:
    """Read only the fields the report needs; reviews first, then just the assessments they point at."""
    reviews = list(db["assessment_reviews"].find(
        {}, {"_id": 0, "session_id": 1, "alert_level_override": 1, "florence_alert_level": 1, "agrees": 1},
    ))
    session_ids = sorted({r["session_id"] for r in reviews if r.get("session_id")})
    assessments = list(db["florence_assessments"].find(
        {"session_id": {"$in": session_ids}},
        {"_id": 0, "session_id": 1, "triage_status": 1, "alert_level": 1, "triage_assessment.alert_level": 1},
    )) if session_ids else []
    return agreement_report(assessments, reviews)


def _pct(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def format_text(report: dict) -> str:
    lines = [
        f"Reviews of triaged records: {report['n']}",
        f"Agreement: {_pct(report['agreement_rate'])} ({report['agreed_n']}/{report['n']})",
        f"Florence over-escalated: {_pct(report['over_escalation_rate'])} ({report['over_escalation_n']})",
        f"Florence under-escalated: {_pct(report['under_escalation_rate'])} ({report['under_escalation_n']})",
        "",
        "Agreement by Florence level:",
    ]
    for level, stats in report["agreement_by_florence_level"].items():
        lines.append(f"  {level:<7} {_pct(stats['rate']):>7}  ({stats['agreed_n']}/{stats['n']})")
    lines += ["", "Confusion matrix (rows: Florence, columns: clinician):", "  " + " " * 8 + "".join(f"{c:>8}" for c in ALERT_LEVELS)]
    for florence, row in report["confusion_matrix"].items():
        lines.append(f"  {florence:<8}" + "".join(f"{row[c]:>8}" for c in ALERT_LEVELS))
    lines += [
        "",
        f"Levels assigned by clinicians to records awaiting review: {report['clinician_assigned_n']} "
        + str({k: v for k, v in report["clinician_assigned_by_level"].items() if v}),
        f"Reviews whose assessment no longer exists: {report['orphan_review_n']}",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sys.path.insert(0, root)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(root, ".env"))
    if "--prod" in argv:
        prod_uri = os.getenv("MONGODB_URI_PROD")
        if not prod_uri:
            sys.exit("MONGODB_URI_PROD is not set in .env")
        os.environ["MONGODB_URI"] = prod_uri

    from app.login import get_db  # noqa: E402  (after MONGODB_URI is settled)
    report = load_from_db(get_db())
    if "--json" in argv:
        print(json.dumps(report, indent=2))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

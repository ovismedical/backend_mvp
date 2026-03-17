"""
Transform raw questionnaire answers into structured clinical documents.
Called at submission time by the submit endpoint.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .questionnaire_definitions import (
    SECTIONS,
    FREQUENCY_SEVERITY_MAP,
    region_count_to_severity,
)


def evaluate_conditional(question_def: dict, raw_answers: Dict[str, Any]) -> bool:
    """Determine if a conditional question was shown to the user."""
    cond = question_def.get("conditional")
    if not cond:
        return True

    depends_on = cond["depends_on"]
    dep_value = raw_answers.get(depends_on)

    if dep_value is None:
        return False

    op = cond["operator"]
    target = cond["value"]

    if op == "gte":
        return dep_value >= target
    if op == "not_eq":
        return dep_value != target
    if op == "eq":
        return dep_value == target
    if op == "in_list":
        return dep_value in target

    return True


def _resolve_display_value(question_def: dict, raw_value: Any) -> Any:
    """Resolve raw answer value to human-readable label(s)."""
    if raw_value is None:
        return None

    q_type = question_def["type"]

    if q_type in ("rating", "single-select"):
        options = question_def.get("options", {})
        return options.get(raw_value, str(raw_value))

    if q_type == "multi-select":
        options = question_def.get("options", {})
        if isinstance(raw_value, list):
            return [options.get(v, str(v)) for v in raw_value]
        return options.get(raw_value, str(raw_value))

    if q_type == "color-chart":
        options = question_def.get("options", {})
        if isinstance(raw_value, list):
            return [options.get(v, str(v)) for v in raw_value]
        return options.get(raw_value, str(raw_value))

    if q_type == "body-diagram":
        regions = question_def.get("regions", {})
        if isinstance(raw_value, list):
            return [regions.get(v, str(v)) for v in raw_value]
        return regions.get(raw_value, str(raw_value))

    if q_type == "slider":
        cfg = question_def.get("slider_config", {})
        unit = cfg.get("unit", "")
        return f"{raw_value}{unit}"

    return str(raw_value)


def _compute_severity_normalized(question_def: dict, raw_value: Any) -> Optional[float]:
    """Compute 0.0-1.0 normalized severity for rating/slider types."""
    if raw_value is None:
        return None

    q_type = question_def["type"]

    if q_type == "rating":
        q_min = question_def.get("min", 0)
        q_max = question_def.get("max", 5)
        if q_max == q_min:
            return 0.0
        direction = question_def.get("severity_direction", "ascending")
        if direction == "ascending":
            return round((raw_value - q_min) / (q_max - q_min), 2)
        return round((q_max - raw_value) / (q_max - q_min), 2)

    if q_type == "slider":
        cfg = question_def.get("slider_config", {})
        s_min = cfg.get("min", 0)
        s_max = cfg.get("max", 10)
        if s_max == s_min:
            return 0.0
        return round((raw_value - s_min) / (s_max - s_min), 2)

    return None


def _compute_section_severity(section_def: dict, raw_answers: Dict[str, Any]) -> tuple:
    """Compute (severity_score, severity_label) for a section."""
    source_id = section_def.get("severity_source")
    if not source_id:
        return None, None

    method = section_def.get("severity_method")
    raw_value = raw_answers.get(source_id)

    # Try fallback if primary source not answered
    if raw_value is None and "severity_fallback" in section_def:
        source_id = section_def["severity_fallback"]
        method = section_def.get("severity_fallback_method")
        raw_value = raw_answers.get(source_id)

    if raw_value is None:
        return None, None

    if method == "frequency_map":
        score = FREQUENCY_SEVERITY_MAP.get(raw_value, 0)
        labels = {0: "None", 1: "Mild", 2: "Moderate", 3: "Frequent", 4: "Very frequent"}
        return score, labels.get(score, "Unknown")

    if method == "region_count":
        count = len(raw_value) if isinstance(raw_value, list) else 0
        score = region_count_to_severity(count)
        labels = {0: "None", 1: "Mild", 2: "Moderate", 3: "Significant", 4: "Severe"}
        return score, labels.get(score, "Unknown")

    # Default: direct rating value
    # Find the question def to get its label
    for q in section_def["questions"]:
        if q["id"] == source_id:
            options = q.get("options", {})
            label = options.get(raw_value, str(raw_value))
            score = raw_value if isinstance(raw_value, (int, float)) else 0
            return score, label

    return None, None


def _check_alert_flags(section_def: dict, raw_answers: Dict[str, Any]) -> List[str]:
    """Generate alert flags for a section based on clinical rules."""
    flags = []
    section_title = section_def["title"]

    for q in section_def["questions"]:
        q_id = q["id"]
        raw_value = raw_answers.get(q_id)
        if raw_value is None:
            continue

        # Rating >= 4
        if q["type"] == "rating" and isinstance(raw_value, (int, float)) and raw_value >= 4:
            label = q.get("options", {}).get(raw_value, str(raw_value))
            flags.append(f"{section_title}: {q['text'].split('?')[0]} rated {label} ({raw_value}/5)")

        # Frequency 6+
        if q["type"] == "single-select" and raw_value == "6+":
            flags.append(f"{section_title}: frequency 6+ times")

        # Color-chart alert values
        if q["type"] == "color-chart":
            alert_values = q.get("alert_values", [])
            matched = []
            if isinstance(raw_value, list):
                matched = [v for v in raw_value if v in alert_values]
            elif raw_value in alert_values:
                matched = [raw_value]
            if matched:
                labels = [q["options"].get(v, v) for v in matched]
                flags.append(f"{section_title}: alarming color reported ({', '.join(labels)})")

        # Body diagram 3+ regions
        if q["type"] == "body-diagram" and isinstance(raw_value, list) and len(raw_value) >= 3:
            flags.append(f"{section_title}: {len(raw_value)} body regions affected")

        # Slider alert rules (e.g., sleep <= 3 hours)
        if q["type"] == "slider" and "alert_rule" in q:
            rule = q["alert_rule"]
            if rule["operator"] == "lte" and raw_value <= rule["value"]:
                cfg = q.get("slider_config", {})
                unit = cfg.get("unit", "")
                flags.append(f"{section_title}: {raw_value}{unit} (critically low)")

    return flags


def enrich_submission(
    raw_answers: Dict[str, Any],
    sections_completed: int,
    total_sections: int,
    completion_percentage: int,
    submission_mode: Optional[str] = None,
) -> dict:
    """
    Transform raw questionnaire answers into an enriched clinical document.
    Returns a dict ready for MongoDB insert (minus user_id and timestamps).
    """
    enriched_sections = []
    all_alert_flags = []
    areas_of_concern = []
    clinical_areas = set()
    questions_answered = 0
    questions_shown = 0
    total_questions = 0
    max_severity = None
    max_severity_area = None

    for section_def in SECTIONS:
        responses = []
        total_questions += len(section_def["questions"])

        for q in section_def["questions"]:
            was_shown = evaluate_conditional(q, raw_answers)
            raw_value = raw_answers.get(q["id"])

            if was_shown:
                questions_shown += 1
                if raw_value is not None:
                    # Check for "empty" arrays
                    if isinstance(raw_value, list) and len(raw_value) == 0:
                        pass  # not answered
                    else:
                        questions_answered += 1

            response = {
                "question_id": q["id"],
                "question_text": q["text"],
                "type": q["type"],
                "raw_value": raw_value if was_shown else None,
                "display_value": _resolve_display_value(q, raw_value) if was_shown and raw_value is not None else None,
                "was_shown": was_shown,
                "was_required": q.get("required", False),
            }

            # Add severity_normalized for rating/slider
            if was_shown and raw_value is not None and q["type"] in ("rating", "slider"):
                response["severity_normalized"] = _compute_severity_normalized(q, raw_value)

            # Add region_count for body-diagram
            if q["type"] == "body-diagram" and was_shown and isinstance(raw_value, list):
                response["region_count"] = len(raw_value)

            # Mark skip reason
            if not was_shown:
                response["skip_reason"] = "conditional_not_met"

            responses.append(response)

        # Section-level severity
        severity_score, severity_label = _compute_section_severity(section_def, raw_answers)

        enriched_sections.append({
            "section_id": section_def["id"],
            "title": section_def["title"],
            "clinical_area": section_def["clinical_area"],
            "severity_score": severity_score,
            "severity_label": severity_label,
            "responses": responses,
        })

        # Alert flags for this section
        section_flags = _check_alert_flags(section_def, raw_answers)
        all_alert_flags.extend(section_flags)

        # Areas of concern: severity > 0 or has alert flags
        is_concern = (severity_score is not None and severity_score > 0) or len(section_flags) > 0
        if is_concern:
            clinical_areas.add(section_def["clinical_area"])

            # Collect detail strings (multi-select display values, body regions)
            details = []
            for r in responses:
                if r["was_shown"] and r["display_value"] is not None:
                    if isinstance(r["display_value"], list) and r["type"] in ("multi-select", "body-diagram"):
                        details.extend(r["display_value"])

            concern = {
                "section_id": section_def["id"],
                "area": section_def["title"],
                "clinical_area": section_def["clinical_area"],
                "severity_score": severity_score,
                "severity_label": severity_label,
            }
            if details:
                concern["details"] = details

            areas_of_concern.append(concern)

            if severity_score is not None and (max_severity is None or severity_score > max_severity):
                max_severity = severity_score
                max_severity_area = section_def["title"]

    return {
        "schema_version": 2,
        "submission_mode": submission_mode,
        "completion": {
            "sections_completed": sections_completed,
            "total_sections": total_sections,
            "questions_answered": questions_answered,
            "questions_shown": questions_shown,
            "total_questions": total_questions,
            "completion_percentage": completion_percentage,
        },
        "sections": enriched_sections,
        "clinical_summary": {
            "areas_of_concern": areas_of_concern,
            "symptom_count": len(areas_of_concern),
            "max_severity": max_severity,
            "max_severity_area": max_severity_area,
            "clinical_areas_affected": sorted(clinical_areas),
            "alert_flags": all_alert_flags,
        },
        "raw_answers": raw_answers,
    }

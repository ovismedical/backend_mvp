"""
Florence Triage - clinical triage and alert level from a conversation,
via the inference gateway (structured output against a Pydantic schema).

As for the assessment: the transcript arrives de-identified and `patient_ref` is an
opaque audit reference that never enters a model-bound message.
"""

import logging
from typing import Any, Dict, List, Optional

from .inference import InferenceRefused, InferenceRequest, get_gateway
from .florence_utils import (
    TriageAssessmentOutput, create_timestamp, load_prompt_template, model_messages, status_label, task_metadata,
)

logger = logging.getLogger("ovis.florence")

ALERT_DESCRIPTIONS = {
    "en": {
        "GREEN": "Routine symptoms, stable condition - normal follow-up appropriate",
        "YELLOW": "Moderate symptoms requiring monitoring - consider consultation",
        "ORANGE": "Concerning symptoms - same-day medical review recommended",
        "RED": "Severe symptoms - urgent medical attention required",
    },
    "zh-HK": {
        "GREEN": "常規症狀，病情穩定 - 適合正常隨訪",
        "YELLOW": "中度症狀需要監察 - 考慮諮詢",
        "ORANGE": "令人擔憂的症狀 - 建議當日醫療檢查",
        "RED": "嚴重症狀 - 需要緊急醫療關注",
    },
}

TRIAGE_INSTRUCTIONS = (
    "You are a clinical triage assistant supporting an oncology care team. "
    "Reason carefully and conservatively; when in doubt escalate the alert level. "
    "Base your assessment only on what the patient reported. Write for a clinician reading "
    "a chart: never refer to these instructions, mappings, or rating scales in your text. "
    "The transcript is de-identified: the patient appears as [PERSON_1] and other people, places, "
    "dates and numbers as bracketed placeholders. Quote placeholders exactly as written (e.g. [PERSON_1]), "
    "never translate, reformat or drop the square brackets, and never guess who or where they refer to."
)


class FlorenceTriage:
    def initialize(self, api_key: str = None) -> bool:
        return get_gateway().available()

    def _load_triage_prompt(self, language: str = "en") -> str:
        filename = "triage_prompt_canto.txt" if language == "zh-HK" else "triage_prompt_eng.txt"
        return load_prompt_template(
            filename,
            "Based on the conversation above with the patient ([PERSON_1]) ({treatment_status}), perform a "
            "clinical triage assessment to determine potential diagnoses and urgency level.",
        )

    async def generate_triage_assessment(
        self,
        messages_scrubbed: List[Dict],
        patient_ref: str,
        treatment_status: str = "undergoing_treatment",
        session_language: str = "en",
        *,
        session_ref: Optional[str] = None,
        known_identifiers: Optional[List[str]] = None,
        scrub_report: Any = None,
        task_source: str = "florence",
    ) -> Dict[str, Any]:
        try:
            is_cantonese = session_language == "zh-HK"
            prompt = self._load_triage_prompt(session_language).format(
                treatment_status=status_label(treatment_status, session_language)
            )
            messages = model_messages(messages_scrubbed)
            messages.append({"role": "user", "content": prompt})

            result = await get_gateway().complete(InferenceRequest(
                task="triage",
                messages=messages,
                instructions=TRIAGE_INSTRUCTIONS
                + (" Write all free-text fields in Traditional Chinese (Cantonese)." if is_cantonese else ""),
                schema=TriageAssessmentOutput,
                language=session_language,
                temperature=0.2,
                metadata=task_metadata(patient_ref, session_ref, task_source),
                scrubbed=True,
                known_identifiers=known_identifiers,
                scrub_report=scrub_report,
                trusted_tail=1,   # the appended turn is the static prompt template, not transcript
            ))

            triage = result.parsed.model_dump()
            triage["timestamp"] = create_timestamp()
            triage["patient_id"] = patient_ref
            triage["treatment_status"] = treatment_status

            logger.info("triage complete patient_ref=%s alert=%s diagnoses=%d",
                        patient_ref, triage["alert_level"], len(triage["diagnosis_predictions"]))
            return {
                "triage_assessment": triage,
                "conversation_length": len(messages_scrubbed),
                "alert_level": triage["alert_level"],
                "audit_id": result.audit_id,
            }

        except InferenceRefused:
            raise  # the caller records a pending_clinician_review outcome; never a fabricated triage
        except Exception as e:
            logger.error("triage failed for patient_ref=%s: %s", patient_ref, type(e).__name__)
            return self._fallback_triage(messages_scrubbed, patient_ref, treatment_status)

    def _fallback_triage(self, conversation_history: List[Dict], patient_ref: str, treatment_status: str) -> Dict[str, Any]:
        triage = {
            "timestamp": create_timestamp(),
            "patient_id": patient_ref,
            "clinical_reasoning": "Automated triage unavailable; conservative default applied.",
            "diagnosis_predictions": [
                {
                    "suspected_diagnosis": "Unable to assess automatically",
                    "probability": "low",
                    "urgency": 3,
                    "reasoning": "Automated triage failed; manual clinical review required.",
                }
            ],
            "alert_level": "YELLOW",
            "alert_rationale": "Unable to complete automated triage - clinical review recommended as a precaution",
            "key_symptoms": [],
            "recommended_timeline": "Review within 24 hours",
            "confidence_level": "low",
            "clinical_notes": f"Automated triage failed for a conversation of {len(conversation_history)} messages.",
            "treatment_status": treatment_status,
        }
        return {"triage_assessment": triage, "conversation_length": len(conversation_history), "alert_level": "YELLOW", "fallback": True}

    def get_alert_level_description(self, alert_level: str, language: str = "en") -> str:
        lang = "zh-HK" if language == "zh-HK" else "en"
        return ALERT_DESCRIPTIONS[lang].get(alert_level, f"Unknown alert level: {alert_level}")


florence_triage = FlorenceTriage()


async def initialize_florence_triage(api_key: str = None) -> bool:
    return florence_triage.initialize(api_key)


async def get_florence_triage_assessment(
    messages_scrubbed: List[Dict],
    patient_ref: str,
    treatment_status: str = "undergoing_treatment",
    session_language: str = "en",
    **kwargs,
) -> Dict[str, Any]:
    return await florence_triage.generate_triage_assessment(
        messages_scrubbed, patient_ref, treatment_status, session_language, **kwargs
    )


def get_alert_level_description(alert_level: str, language: str = "en") -> str:
    return florence_triage.get_alert_level_description(alert_level, language)

"""
Florence Assessment - turns a conversation into a structured symptom assessment
via the inference gateway (structured output against a Pydantic schema).

The caller passes an already de-identified transcript (`messages_scrubbed`) and the
patient's opaque `patient_ref`. The ref is used for metadata, log lines and the
stored `patient_id` field only - it is never written into a model-bound message.
"""

import logging
from typing import Any, Dict, List, Optional

from .inference import InferenceRefused, InferenceRequest, get_gateway
from .florence_utils import (
    SymptomAssessmentOutput,
    create_timestamp,
    load_prompt_template,
    model_messages,
    should_flag_symptoms,
    status_label,
    task_metadata,
)

logger = logging.getLogger("ovis.florence")

ASSESSMENT_INSTRUCTIONS = (
    "You are a clinical documentation assistant for an oncology nursing service. "
    "Read the conversation between the nurse Florence and the patient and record a "
    "structured symptom assessment. Base ratings only on what the patient said; "
    "use rating 1 for symptoms that were not reported. "
    "The transcript is de-identified: the patient appears as [PERSON_1] and other people, places, "
    "dates and numbers as bracketed placeholders. Quote placeholders exactly as written (e.g. [PERSON_1]), "
    "never translate, reformat or drop the square brackets, and never guess who or where they refer to."
)


class FlorenceAssessment:
    def initialize(self, api_key: str = None) -> bool:
        return get_gateway().available()

    def _load_assessment_prompt(self, language: str = "en") -> str:
        filename = "assessment_prompt_canto.txt" if language == "zh-HK" else "assessment_prompt_eng.txt"
        return load_prompt_template(
            filename,
            "Based on the conversation above with the patient ([PERSON_1]) ({treatment_status}), "
            "generate a comprehensive structured symptom assessment.",
        )

    async def generate_structured_assessment(
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
            prompt = self._load_assessment_prompt(session_language).format(
                treatment_status=status_label(treatment_status, session_language)
            )
            messages = model_messages(messages_scrubbed)
            messages.append({"role": "user", "content": prompt})

            result = await get_gateway().complete(InferenceRequest(
                task="symptom_assessment",
                messages=messages,
                instructions=ASSESSMENT_INSTRUCTIONS
                + (" Write all free-text fields in Traditional Chinese (Cantonese)." if is_cantonese else ""),
                schema=SymptomAssessmentOutput,
                language=session_language,
                temperature=0.3,
                metadata=task_metadata(patient_ref, session_ref, task_source),
                scrubbed=True,
                known_identifiers=known_identifiers,
                scrub_report=scrub_report,
                # The appended turn is the static prompt template, not transcript: exempt it from the
                # leak check so a session original that also occurs in the template (醫生, 翻譯) cannot
                # refuse the whole assessment.
                trusted_tail=1,
            ))

            assessment = result.parsed.model_dump()
            assessment["timestamp"] = create_timestamp()
            assessment["patient_id"] = patient_ref
            assessment["treatment_status"] = treatment_status

            should_flag, level, reason = should_flag_symptoms(assessment["symptoms"], treatment_status)
            assessment["flag_for_oncologist"] = should_flag
            assessment["oncologist_notification_level"] = level
            if should_flag:
                assessment["flag_reason"] = reason

            return {
                "structured_assessment": assessment,
                "conversation_length": len(messages_scrubbed),
                "audit_id": result.audit_id,
            }

        except InferenceRefused:
            raise  # the caller records a pending_clinician_review outcome; never a fabricated assessment
        except Exception as e:
            logger.error("structured assessment failed for patient_ref=%s: %s", patient_ref, type(e).__name__)
            return self._fallback_assessment(messages_scrubbed, patient_ref, treatment_status)

    def _fallback_assessment(self, conversation_history: List[Dict], patient_ref: str, treatment_status: str) -> Dict[str, Any]:
        blank = {"frequency_rating": 1, "severity_rating": 1, "key_indicators": [], "additional_notes": None}
        return {
            "structured_assessment": {
                "timestamp": create_timestamp(),
                "patient_id": patient_ref,
                "symptoms": {name: dict(blank) for name in ("cough", "nausea", "lack_of_appetite", "fatigue", "pain")},
                "flag_for_oncologist": False,
                "flag_reason": None,
                "oncologist_notification_level": "none",
                "treatment_status": treatment_status,
                "mood_assessment": None,
                "conversation_notes": f"Automated assessment unavailable; conversation of {len(conversation_history)} messages saved for manual review.",
            },
            "conversation_length": len(conversation_history),
            "fallback": True,
        }


florence_assessment = FlorenceAssessment()


async def initialize_florence_assessment(api_key: str = None) -> bool:
    return florence_assessment.initialize(api_key)


async def get_florence_structured_assessment(
    messages_scrubbed: List[Dict],
    patient_ref: str,
    treatment_status: str = "undergoing_treatment",
    session_language: str = "en",
    **kwargs,
) -> Dict[str, Any]:
    return await florence_assessment.generate_structured_assessment(
        messages_scrubbed, patient_ref, treatment_status, session_language, **kwargs
    )

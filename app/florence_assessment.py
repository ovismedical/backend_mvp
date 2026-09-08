"""
Florence Assessment - turns a conversation into a structured symptom assessment
via the inference gateway (structured output against a Pydantic schema).
"""

import logging
import os
from typing import List, Dict, Any

from .inference import InferenceRequest, get_gateway
from .florence_utils import (
    SymptomAssessmentOutput,
    create_timestamp,
    should_flag_symptoms,
    format_conversation_history_for_ai,
)

logger = logging.getLogger("ovis.florence")

TREATMENT_STATUS_ZH = {"undergoing_treatment": "正在接受治療", "in_remission": "康復期"}


def load_prompt_template(filename: str, fallback: str) -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        return text or fallback
    except OSError as e:
        logger.warning("prompt file %s unavailable (%s); using fallback prompt", filename, e)
        return fallback


def status_label(treatment_status: str, language: str) -> str:
    if language == "zh-HK":
        return TREATMENT_STATUS_ZH.get(treatment_status, treatment_status)
    return treatment_status


class FlorenceAssessment:
    def initialize(self, api_key: str = None) -> bool:
        return get_gateway().available()

    def _load_assessment_prompt(self, language: str = "en") -> str:
        filename = "assessment_prompt_canto.txt" if language == "zh-HK" else "assessment_prompt_eng.txt"
        return load_prompt_template(
            filename,
            "Based on the conversation above with patient {patient_id} ({treatment_status}), "
            "generate a comprehensive structured symptom assessment.",
        )

    async def generate_structured_assessment(
        self,
        conversation_history: List[Dict],
        patient_id: str,
        treatment_status: str = "undergoing_treatment",
        session_language: str = "en",
    ) -> Dict[str, Any]:
        try:
            is_cantonese = session_language == "zh-HK"
            prompt = self._load_assessment_prompt(session_language).format(
                patient_id=patient_id, treatment_status=status_label(treatment_status, session_language)
            )
            messages = format_conversation_history_for_ai(conversation_history, include_system_prompt=False)
            messages.append({"role": "user", "content": prompt})

            result = await get_gateway().complete(InferenceRequest(
                task="symptom_assessment",
                messages=messages,
                instructions=(
                    "You are a clinical documentation assistant for an oncology nursing service. "
                    "Read the conversation between the nurse Florence and the patient and record a "
                    "structured symptom assessment. Base ratings only on what the patient said; "
                    "use rating 1 for symptoms that were not reported."
                    + (" Write all free-text fields in Traditional Chinese (Cantonese)." if is_cantonese else "")
                ),
                schema=SymptomAssessmentOutput,
                language=session_language,
                effort="low",
                temperature=0.3,
                metadata={"patient_ref": patient_id},
            ))

            assessment = result.parsed.model_dump()
            assessment["timestamp"] = create_timestamp()
            assessment["patient_id"] = patient_id
            assessment["treatment_status"] = treatment_status

            should_flag, level, reason = should_flag_symptoms(assessment["symptoms"], treatment_status)
            assessment["flag_for_oncologist"] = should_flag
            assessment["oncologist_notification_level"] = level
            if should_flag:
                assessment["flag_reason"] = reason

            return {"structured_assessment": assessment, "conversation_length": len(conversation_history)}

        except Exception as e:
            logger.error("structured assessment failed for patient_ref=%s: %s", patient_id, type(e).__name__)
            return self._fallback_assessment(conversation_history, patient_id, treatment_status)

    def _fallback_assessment(self, conversation_history: List[Dict], patient_id: str, treatment_status: str) -> Dict[str, Any]:
        blank = {"frequency_rating": 1, "severity_rating": 1, "key_indicators": [], "additional_notes": None}
        return {
            "structured_assessment": {
                "timestamp": create_timestamp(),
                "patient_id": patient_id,
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
    conversation_history: List[Dict],
    patient_id: str,
    treatment_status: str = "undergoing_treatment",
    session_language: str = "en",
) -> Dict[str, Any]:
    return await florence_assessment.generate_structured_assessment(
        conversation_history, patient_id, treatment_status, session_language
    )

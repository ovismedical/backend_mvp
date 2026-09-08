"""
Florence Triage - clinical triage and alert level from a conversation,
via the inference gateway (structured output against a Pydantic schema).
"""

import logging
from typing import List, Dict, Any

from .inference import InferenceRequest, get_gateway
from .florence_assessment import load_prompt_template, status_label
from .florence_utils import TriageAssessmentOutput, create_timestamp, format_conversation_history_for_ai

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


class FlorenceTriage:
    def initialize(self, api_key: str = None) -> bool:
        return get_gateway().available()

    def _load_triage_prompt(self, language: str = "en") -> str:
        filename = "triage_prompt_canto.txt" if language == "zh-HK" else "triage_prompt_eng.txt"
        return load_prompt_template(
            filename,
            "Based on the conversation above with patient {patient_id} ({treatment_status}), perform a "
            "clinical triage assessment to determine potential diagnoses and urgency level.",
        )

    async def generate_triage_assessment(
        self,
        conversation_history: List[Dict],
        patient_id: str,
        treatment_status: str = "undergoing_treatment",
        session_language: str = "en",
    ) -> Dict[str, Any]:
        try:
            is_cantonese = session_language == "zh-HK"
            prompt = self._load_triage_prompt(session_language).format(
                patient_id=patient_id, treatment_status=status_label(treatment_status, session_language)
            )
            messages = format_conversation_history_for_ai(conversation_history, include_system_prompt=False)
            messages.append({"role": "user", "content": prompt})

            result = await get_gateway().complete(InferenceRequest(
                task="triage",
                messages=messages,
                instructions=(
                    "You are a clinical triage assistant supporting an oncology care team. "
                    "Reason carefully and conservatively; when in doubt escalate the alert level. "
                    "Base your assessment only on what the patient reported."
                    + (" Write all free-text fields in Traditional Chinese (Cantonese)." if is_cantonese else "")
                ),
                schema=TriageAssessmentOutput,
                language=session_language,
                effort="high",
                temperature=0.2,
                metadata={"patient_ref": patient_id},
            ))

            triage = result.parsed.model_dump()
            triage["timestamp"] = create_timestamp()
            triage["patient_id"] = patient_id
            triage["treatment_status"] = treatment_status

            logger.info("triage complete patient_ref=%s alert=%s diagnoses=%d",
                        patient_id, triage["alert_level"], len(triage["diagnosis_predictions"]))
            return {
                "triage_assessment": triage,
                "conversation_length": len(conversation_history),
                "alert_level": triage["alert_level"],
            }

        except Exception as e:
            logger.error("triage failed for patient_ref=%s: %s", patient_id, type(e).__name__)
            return self._fallback_triage(conversation_history, patient_id, treatment_status)

    def _fallback_triage(self, conversation_history: List[Dict], patient_id: str, treatment_status: str) -> Dict[str, Any]:
        triage = {
            "timestamp": create_timestamp(),
            "patient_id": patient_id,
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
    conversation_history: List[Dict],
    patient_id: str,
    treatment_status: str = "undergoing_treatment",
    session_language: str = "en",
) -> Dict[str, Any]:
    return await florence_triage.generate_triage_assessment(
        conversation_history, patient_id, treatment_status, session_language
    )


def get_alert_level_description(alert_level: str, language: str = "en") -> str:
    return florence_triage.get_alert_level_description(alert_level, language)

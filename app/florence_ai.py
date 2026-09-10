"""
Florence AI - conversational check-in nurse.
Thin wrapper over the inference gateway; language is per request, never global state.

The caller (app.florence) owns de-identification: it hands this module messages that
have already been through the session's ScrubContext and re-identifies the reply.
Nothing here sees a name - only `[PERSON_n]`-style placeholders and opaque refs.
"""

import logging
from typing import Any, Dict, List, Optional

from .inference import InferenceRefused, InferenceRequest, ProviderUnavailable, get_gateway
from .florence_utils import (
    OPENING_TURN,
    generate_fallback_response,
    handle_ai_response_error,
    load_florence_system_prompt,
    task_metadata,
)

logger = logging.getLogger("ovis.florence")

TASK_SOURCE = "florence"


class FlorenceAI:
    def __init__(self):
        self._prompts: Dict[str, str] = {}

    @property
    def client(self):
        """Truthy when an inference provider is configured (kept for health checks)."""
        return get_gateway() if get_gateway().available() else None

    def system_prompt(self, language: str = "en") -> str:
        if language not in self._prompts:
            self._prompts[language] = load_florence_system_prompt(language)
        return self._prompts[language]

    def initialize(self, api_key: str = None) -> bool:
        return get_gateway().available()

    async def start_conversation(
        self,
        language: str = "en",
        *,
        patient_ref: Optional[str] = None,
        session_ref: Optional[str] = None,
        known_identifiers: Optional[List[str]] = None,
        scrub_report: Any = None,
    ) -> Dict[str, Any]:
        """Opening turn. The patient is introduced as [PERSON_1]; the caller re-identifies the reply.
        A policy refusal propagates (`InferenceRefused`); provider problems become a fallback dict."""
        try:
            result = await self._complete(
                [{"role": "user", "content": OPENING_TURN}],
                language,
                patient_ref=patient_ref,
                session_ref=session_ref,
                known_identifiers=known_identifiers,
                scrub_report=scrub_report,
            )
            return {"response": result.text, "conversation_state": "starting", "audit_id": result.audit_id}
        except InferenceRefused:
            raise
        except ProviderUnavailable:
            # No provider configured at all: the caller drops the session into fallback mode.
            # A transient provider error takes the `handle_ai_response_error` path below instead,
            # which has no `unavailable` flag, so the session stays AI-enabled and later turns retry.
            return {"error": "AI not configured", "unavailable": True,
                    "response": generate_fallback_response("system_error")}
        except Exception as e:
            return handle_ai_response_error(e, "start_conversation")

    async def process_message(
        self,
        messages_scrubbed: List[Dict],
        language: str = "en",
        *,
        patient_ref: Optional[str] = None,
        session_ref: Optional[str] = None,
        known_identifiers: Optional[List[str]] = None,
        scrub_report: Any = None,
    ) -> Dict[str, Any]:
        """One chat turn over an already de-identified history (the new patient turn is its last item)."""
        try:
            result = await self._complete(
                [{"role": m["role"], "content": m["content"]} for m in messages_scrubbed if m.get("role") != "system"],
                language,
                patient_ref=patient_ref,
                session_ref=session_ref,
                known_identifiers=known_identifiers,
                scrub_report=scrub_report,
            )
            return {"response": result.text, "conversation_state": "assessing", "audit_id": result.audit_id}
        except InferenceRefused:
            raise
        except Exception as e:
            return handle_ai_response_error(e, "process_message")

    async def _complete(
        self,
        messages: List[Dict],
        language: str,
        *,
        patient_ref: Optional[str],
        session_ref: Optional[str],
        known_identifiers: Optional[List[str]],
        scrub_report: Any,
    ):
        return await get_gateway().complete(InferenceRequest(
            task="chat_turn",
            messages=messages,
            instructions=self.system_prompt(language),
            language=language,
            effort="minimal",
            temperature=0.8,
            metadata=task_metadata(patient_ref, session_ref, TASK_SOURCE),
            scrubbed=True,
            known_identifiers=known_identifiers,
            scrub_report=scrub_report,
        ))


florence_ai = FlorenceAI()


async def initialize_florence(api_key: str = None) -> bool:
    return florence_ai.initialize(api_key)


async def start_florence_conversation(language: str = "en", **kwargs) -> Dict[str, Any]:
    return await florence_ai.start_conversation(language, **kwargs)


async def send_message_to_florence(messages_scrubbed: List[Dict], language: str = "en", **kwargs) -> Dict[str, Any]:
    return await florence_ai.process_message(messages_scrubbed, language, **kwargs)

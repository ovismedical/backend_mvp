"""
Florence AI - conversational check-in nurse.
Thin wrapper over the inference gateway; language is per request, never global state.
"""

import logging
from typing import List, Dict, Any

from .inference import InferenceRequest, ProviderUnavailable, get_gateway
from .florence_utils import (
    generate_fallback_response,
    format_conversation_history_for_ai,
    handle_ai_response_error,
    load_florence_system_prompt,
)

logger = logging.getLogger("ovis.florence")


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

    async def start_conversation(self, patient_name: str = "there", language: str = "en") -> Dict[str, Any]:
        try:
            text = await self._complete(
                [{"role": "user", "content": f"Hello, I'm {patient_name}. I'm here for my health check-in."}],
                language,
            )
            return {"response": text, "conversation_state": "starting"}
        except ProviderUnavailable:
            return {"error": "AI not configured", "response": generate_fallback_response(patient_name, "system_error")}
        except Exception as e:
            return handle_ai_response_error(e, "start_conversation", patient_name)

    async def process_message(self, message: str, conversation_history: List[Dict], language: str = "en") -> Dict[str, Any]:
        try:
            history = [m for m in format_conversation_history_for_ai(conversation_history, include_system_prompt=False)
                       if m["role"] != "system"]
            history.append({"role": "user", "content": message})
            text = await self._complete(history, language)
            return {"response": text, "conversation_state": "assessing"}
        except Exception as e:
            return handle_ai_response_error(e, "process_message")

    async def _complete(self, messages: List[Dict], language: str) -> str:
        result = await get_gateway().complete(InferenceRequest(
            task="chat_turn",
            messages=messages,
            instructions=self.system_prompt(language),
            language=language,
            effort="minimal",
            temperature=0.8,
        ))
        return result.text


florence_ai = FlorenceAI()


async def initialize_florence(api_key: str = None) -> bool:
    return florence_ai.initialize(api_key)


async def start_florence_conversation(patient_name: str = "there", language: str = "en") -> Dict[str, Any]:
    return await florence_ai.start_conversation(patient_name, language=language)


async def send_message_to_florence(message: str, conversation_history: List[Dict], language: str = "en") -> Dict[str, Any]:
    return await florence_ai.process_message(message, conversation_history, language=language)

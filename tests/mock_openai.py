"""
Test doubles for inference. `FakeProvider` plugs into the InferenceGateway so the
Florence endpoints run their real code paths with canned model output.
"""

from app.florence_utils import MemoryExtractionOutput, MemoryOp, SymptomAssessmentOutput, TriageAssessmentOutput
from app.inference import InferenceGateway, ToolCall
from app.inference.gateway import TASKS
from app.inference.router import Policy, Router


def sample_assessment(treatment_status="undergoing_treatment", symptoms=None) -> SymptomAssessmentOutput:
    if symptoms is None:
        symptoms = {
            "cough": {"frequency_rating": 2, "severity_rating": 2, "key_indicators": ["occasional dry cough"], "additional_notes": None},
            "nausea": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": [], "additional_notes": None},
            "lack_of_appetite": {"frequency_rating": 2, "severity_rating": 2, "key_indicators": ["reduced meals"], "additional_notes": None},
            "fatigue": {"frequency_rating": 3, "severity_rating": 3, "key_indicators": ["affects daily activities"], "additional_notes": None},
            "pain": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": [], "additional_notes": None, "location": None},
        }
    return SymptomAssessmentOutput(
        symptoms=symptoms,
        flag_for_oncologist=False,
        flag_reason=None,
        mood_assessment="Patient appears in stable mood.",
        conversation_notes="Standard check-in conversation.",
        oncologist_notification_level="none",
        treatment_status=treatment_status,
    )


def sample_triage(alert_level="GREEN", treatment_status="undergoing_treatment") -> TriageAssessmentOutput:
    return TriageAssessmentOutput(
        clinical_reasoning="Symptoms are mild and within expected range.",
        diagnosis_predictions=[{
            "suspected_diagnosis": "Treatment side effects",
            "probability": "medium",
            "urgency": 2,
            "reasoning": "Common side effects during treatment.",
        }],
        alert_level=alert_level,
        alert_rationale="Symptoms within normal parameters.",
        key_symptoms=["fatigue"],
        recommended_timeline="Routine follow-up",
        confidence_level="medium",
        clinical_notes="",
        treatment_status=treatment_status,
    )


class FakeProvider:
    """Records every request it receives and answers from canned data.

    `chat_reply` may be a string (always the same reply), a list of strings (consumed in order, the
    last one repeats) or a callable `(request) -> str`, so a test can script what the model says turn
    by turn - e.g. a reply that echoes a placeholder such as "How kind of [PERSON_2]!".
    """

    def __init__(self, name="fake", model="fake-model", chat_reply="Hello! How are you feeling?",
                 alert_level="GREEN", fail=False, tool_script=None):
        self.name = name
        self.model = model
        self.chat_reply = chat_reply
        self.alert_level = alert_level
        self.fail = fail
        self.tool_script = tool_script
        self.requests = []
        self.tools_offered = []
        self._chat_calls = 0
        self._tool_hops = 0

    def _next_chat_reply(self, request):
        reply = self.chat_reply
        if callable(reply):
            return reply(request)
        if isinstance(reply, (list, tuple)):
            if not reply:
                return ""
            index = min(self._chat_calls, len(reply) - 1)
            return reply[index]
        return reply

    async def chat(self, request, model=None):
        self.requests.append(request)
        self.models = getattr(self, "models", []) + [model or self.model]
        if self.fail:
            raise RuntimeError("provider down")
        reply = self._next_chat_reply(request)
        self._chat_calls += 1
        return reply

    async def chat_with_tools(self, request, model=None):
        """`tool_script` is one (text, calls) step per hop, the last repeating; calls are (name,
        arguments) pairs. Steps are returned verbatim, including calls on a hop that was offered no
        tools, so a test can prove the loop still terminates when a provider misbehaves."""
        self.requests.append(request)
        self.models = getattr(self, "models", []) + [model or self.model]
        self.tools_offered.append(request.tools.names() if request.tools else None)
        if self.fail:
            raise RuntimeError("provider down")
        hop = self._tool_hops
        self._tool_hops += 1
        if self.tool_script is None:
            reply = self._next_chat_reply(request)
            self._chat_calls += 1          # scripted chat_reply lists advance here too
            return reply, ()
        step = self.tool_script[min(hop, len(self.tool_script) - 1)]
        if callable(step):
            step = step(request)
        text, calls = step
        return text, tuple(
            call if isinstance(call, ToolCall) else ToolCall(name=call[0], arguments=call[1], call_id=f"call_{hop}_{i}")
            for i, call in enumerate(calls)
        )

    async def parse(self, request, model=None):
        self.requests.append(request)
        self.models = getattr(self, "models", []) + [model or self.model]
        if self.fail:
            raise RuntimeError("provider down")
        if request.schema is SymptomAssessmentOutput:
            return sample_assessment()
        if request.schema is TriageAssessmentOutput:
            return sample_triage(alert_level=self.alert_level)
        if request.schema is MemoryExtractionOutput:
            # `memory_ops`: a list of MemoryOp dicts, or a callable (request) -> that list. None = nothing new.
            ops = getattr(self, "memory_ops", None)
            ops = ops(request) if callable(ops) else ops
            return MemoryExtractionOutput(ops=[MemoryOp(**op) for op in ops or []])
        raise ValueError(f"FakeProvider has no canned output for {request.schema}")

    async def healthy(self):
        return not self.fail


def all_message_text(requests) -> str:
    """Every outbound message content (plus instructions) of the recorded requests, joined - for
    "this identifier never reached the provider" assertions."""
    parts = []
    for request in requests:
        for message in request.messages:
            content = message.get("content") if isinstance(message, dict) else message
            parts.append(content if isinstance(content, str) else str(content))
        if request.instructions:
            parts.append(request.instructions)
    return "\n".join(parts)


def permissive_policy(provider_names, tasks=TASKS, tools=None) -> Policy:
    """Test policy: every given provider has no requirements and every task routes to the first one.

    `on_refuse` is copied from the real YAML so call sites see the same refusal semantics. Opt in with
    `fake_gateway(policy=permissive_policy([...]), ...)` when a test needs a provider the YAML does not
    route to; the default `fake_gateway()` enforces the real policy.
    """
    provider_names = list(provider_names)
    if not provider_names:
        raise ValueError("permissive_policy needs at least one provider name")
    real = Policy.load()
    first = provider_names[0]
    return Policy.from_dict({
        "version": 1,
        "flags": {},
        "providers": {name: {"requires": []} for name in provider_names},
        "tasks": {task: {"provider": first, "on_refuse": real.on_refuse_for(task) or "skip"} for task in tasks},
        "tools": tools if tools is not None else {
            name: {"requires": [], "discloses": spec.discloses} for name, spec in real.tools.items()
        },
    }, source="tests.mock_openai.permissive_policy")


def fake_gateway(policy=None, audit_sink=None, **providers) -> InferenceGateway:
    """Gateway backed by the given providers, e.g. fake_gateway(openai=FakeProvider()).

    policy=None enforces the real YAML policy (`Policy.load()`: openai requires dpa_ok + scrubbed), so the
    integration suite proves every call site scrubs and the conftest COMPLIANCE_DPA_OK=true flag is what
    lets calls through; refusal tests `delenv` it. Constructor routes are empty so env overrides cannot
    interfere and routing follows the policy alone. Pass `permissive_policy([...])` to bypass the YAML.
    """
    if not providers:
        providers = {"openai": FakeProvider(name="openai")}
    if policy is None:
        policy = Policy.load()
    return InferenceGateway(providers, {}, router=Router(policy), audit_sink=audit_sink)

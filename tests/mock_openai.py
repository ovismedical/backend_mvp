"""
Test doubles for inference. `FakeProvider` plugs into the InferenceGateway so the
Florence endpoints run their real code paths with canned model output.
"""

from app.florence_utils import SymptomAssessmentOutput, TriageAssessmentOutput
from app.inference import InferenceGateway
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
    """Records every request it receives and answers from canned data."""

    def __init__(self, name="fake", model="fake-model", chat_reply="Hello! How are you feeling?",
                 alert_level="GREEN", fail=False):
        self.name = name
        self.model = model
        self.chat_reply = chat_reply
        self.alert_level = alert_level
        self.fail = fail
        self.requests = []

    async def chat(self, request, model=None):
        self.requests.append(request)
        self.models = getattr(self, "models", []) + [model or self.model]
        if self.fail:
            raise RuntimeError("provider down")
        return self.chat_reply

    async def parse(self, request, model=None):
        self.requests.append(request)
        self.models = getattr(self, "models", []) + [model or self.model]
        if self.fail:
            raise RuntimeError("provider down")
        if request.schema is SymptomAssessmentOutput:
            return sample_assessment()
        if request.schema is TriageAssessmentOutput:
            return sample_triage(alert_level=self.alert_level)
        raise ValueError(f"FakeProvider has no canned output for {request.schema}")

    async def healthy(self):
        return not self.fail


def permissive_policy(provider_names, tasks=TASKS) -> Policy:
    """Test policy: every given provider has no requirements and every task routes to the first one.

    `on_refuse` is copied from the real YAML so call sites see the same refusal semantics. Used until
    every call site marks its requests `scrubbed=True`; pass `policy=Policy.load()` to exercise the
    real policy (openai requires dpa_ok + scrubbed).
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
    }, source="tests.mock_openai.permissive_policy")


def fake_gateway(policy=None, audit_sink=None, **providers) -> InferenceGateway:
    """Gateway backed by the given providers, e.g. fake_gateway(openai=FakeProvider()).

    policy=None installs `permissive_policy` (every task -> the first provider, no flag or scrub
    requirements). With an explicit policy (e.g. `Policy.load()`), routing and requirements follow
    that policy alone: constructor routes are empty so env overrides cannot interfere.
    """
    if not providers:
        providers = {"openai": FakeProvider(name="openai")}
    if policy is None:
        policy = permissive_policy(providers)
        routes = {task: next(iter(providers)) for task in TASKS}
    else:
        routes = {}
    return InferenceGateway(providers, routes, router=Router(policy), audit_sink=audit_sink)

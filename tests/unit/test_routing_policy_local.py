"""The hybrid policy: private queries on a self-hosted model, reasoning on the vendor.

These tests are what make the claim checkable. They pin which tasks may leave the network, and
they fail if someone edits the policy so a raw-text task starts routing to a remote provider.
"""

from pathlib import Path

import pytest

from app.inference.router import Policy, Router

LOCAL_POLICY = Path(__file__).resolve().parents[2] / "app" / "inference" / "routing_policy.local.yaml"

# Tasks whose payload is raw, unscrubbed patient text. These must never route off our network.
PRIVATE_TASKS = ("chat_turn", "pii_detect")
# Tasks that run on a de-identified transcript, where a frontier model earns its place.
REASONING_TASKS = ("symptom_assessment", "triage")

PROVIDERS = {"openai": object(), "local": object()}


@pytest.fixture(scope="module")
def policy():
    return Policy.load(LOCAL_POLICY)


def test_the_file_is_a_valid_policy(policy):
    assert policy.version == 1 and policy.source == str(LOCAL_POLICY)


@pytest.mark.parametrize("task", PRIVATE_TASKS)
def test_private_queries_go_to_the_local_provider(policy, task):
    assert policy.provider_for(task) == "local"


@pytest.mark.parametrize("task", REASONING_TASKS)
def test_reasoning_goes_to_the_vendor(policy, task):
    assert policy.provider_for(task) == "openai"


def test_the_vendor_route_still_requires_de_identification(policy):
    """Routing chat locally must not relax what the remote provider is allowed to receive."""
    assert "scrubbed" in policy.providers["openai"].requires
    router = Router(policy)
    decision = router.decide("triage", scrubbed=False, providers=PROVIDERS)
    assert decision.allow is False and decision.reason == "not_scrubbed"


def test_the_local_route_needs_no_scrubbing(policy):
    """The local model is where raw text is allowed to go; that is the point of routing it there."""
    router = Router(policy)
    assert router.decide("chat_turn", scrubbed=False, providers=PROVIDERS).allow is True


def test_an_unconfigured_local_server_refuses_rather_than_falling_back_to_the_vendor(policy):
    """With no local provider, chat must refuse — never silently send raw text to Azure."""
    decision = Router(policy).decide("chat_turn", scrubbed=False, providers={"openai": object()})
    assert decision.allow is False and decision.reason == "provider_unconfigured"
    assert decision.on_refuse == "scripted_fallback"


def test_the_default_policy_keeps_every_task_on_the_vendor():
    """The shipped default is all-vendor because the hosted deployment has no GPU; the hybrid
    policy above is opt-in via INFERENCE_POLICY_PATH."""
    default = Policy.load()
    assert default.provider_for("chat_turn") == "openai"
    assert default.provider_for("pii_detect") == "local"   # the one task that must see raw text

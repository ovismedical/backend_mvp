"""De-identification for model-bound text (Phase 1 of the private inference router).

    ctx = ScrubContext(known=KnownIdentifiers(full_name="Grace Tam", username="demo", ...))
    res = ctx.scrub("my daughter Mei Ling took me to Queen Mary on Tuesday", now=datetime.now(tz))
    res.text   -> "my daughter [PERSON_2] took me to [FACILITY_1] on [DATE_1 · 1 day ago]"
    reidentify("How kind of [PERSON_2]!", ctx.token_map) -> "How kind of Mei Ling!"

Layers: session-known originals and the patient's known identifiers (layer 1),
deterministic patterns (2), Hong Kong gazetteers and cue-gated name rules (3),
optional NER (4), date/age generalisation (6). Fail-closed: any layer error is a
``ScrubError`` and the caller refuses the request.
"""

from .names import KnownIdentifiers, KnownMatcher, cjk_name_variants, parse_dob, strip_title  # noqa: F401
from .ner import GlinerBackend, NERBackend, NERSpan, NullBackend, PresidioBackend, ner_backend_from_env  # noqa: F401
from .reidentify import reidentify, reidentify_obj, reidentify_text  # noqa: F401
from .scrubber import (  # noqa: F401
    ResolvedSpan, ScrubContext, ScrubReport, ScrubResult, Scrubber, leak_check, scrub_messages, scrub_text,
)
from .tokens import ALL_CLASSES, CLASSES, ScrubError, Span, TokenMap  # noqa: F401

__all__ = [
    "ALL_CLASSES", "CLASSES", "GlinerBackend", "KnownIdentifiers", "KnownMatcher", "NERBackend", "NERSpan",
    "NullBackend", "PresidioBackend", "ResolvedSpan", "ScrubContext", "ScrubError", "ScrubReport", "ScrubResult",
    "Scrubber", "Span", "TokenMap", "cjk_name_variants", "leak_check", "ner_backend_from_env", "parse_dob",
    "reidentify", "reidentify_obj", "reidentify_text", "scrub_messages", "scrub_text", "strip_title",
]

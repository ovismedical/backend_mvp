"""PHI recall evaluation for the de-identification scrubber (Package F).

``generate`` builds a deterministic synthetic corpus (200 transcripts, EN + ZH-HK)
with ground-truth identifier spans and over-redaction traps; ``run_phi_eval``
scrubs it with ``app.inference.scrub`` and reports span-level recall/precision
per class, direct vs indirect aggregates, the over-redaction rate, a linkage-score
histogram and latency percentiles, and writes ``docs/eval-phi-recall.md``.
"""

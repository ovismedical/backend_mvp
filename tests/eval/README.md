# Evals

Offline evaluations that measure the behaviour of a component against a synthetic
corpus and write a report under `docs/`. They are marked `eval`; the deterministic
PHI recall run is part of the default suite because it finishes in about a second.

```
.venv/bin/python -m pytest -q tests/eval                                  # the bars (default suite)
.venv/bin/python -m tests.eval.phi_recall.run_phi_eval --backend none --backend presidio   # regenerate docs/eval-phi-recall.{md,json}
.venv/bin/python -m tests.eval.phi_recall.run_phi_eval                    # one run only: overwrites the report without the NER table
.venv/bin/python -m pytest -q -m "not eval"                               # skip them
```

## PHI recall (`tests/eval/phi_recall/`)

Does the de-identification scrubber (`app/inference/scrub`) remove the identifiers a
patient volunteers to Florence, in English and in Cantonese, without mangling the
clinical content the triage model needs?

### Corpus — `generate.py`

A deterministic generator (`random.Random(20260909)`, reference time
`2026-09-09 10:00 Asia/Hong_Kong`, no clock or set-iteration dependence; the test
`test_deterministic_across_hash_seeds` runs two interpreters with different
`PYTHONHASHSEED`s and compares digests) builds 200 transcripts of 6–12 turns:

- 100 English and 100 Cantonese (spoken zh-HK register: 我好攰 / 成日想嘔 / 覆診 / 揸車送我去).
- Symptom chats for fatigue, nausea, appetite, cough and pain plus treatment context,
  and questionnaire transcripts synthesised exactly as `app/questionnaire_triage_bridge.py`
  does it (`Regarding Appetite Loss:\n- …: Other\n- Please specify: <free text>`) for
  the six free-text "Please specify" answers.
- 28 personas per language. English includes the F1 common-word names (Ho Long,
  Ka Man, May Chan, Wing Yan, Sum Yee, Test Patient with doctor "Dr. Test", Grace Tam,
  Wong Ka Long); Cantonese personas appear as 陳大文 / 陳生 / 陳太 / 陳先生 / 大文 / 阿文
  and doctors as 李醫生 / 李生 / 李志明. Every persona has a username, email, phone, DOB,
  doctor and hospital; `Transcript.known()` turns that into the `KnownIdentifiers` the
  product would build (phone/email known only for ~half, so the pattern layer is exercised too).
- 0–6 volunteered identifiers per transcript across all 13 token classes plus DOB, with
  exact `GoldSpan` offsets and a `kind` tag (`rel_intro`, `rel_again`, `dob:dd/mm/yyyy`,
  `weekday_future:cued`, …). 40% of transcripts carry >= 3 indirect classes.
  Relatives are introduced with a kinship cue and then mentioned bare in later turns and
  in Florence's own echoes, which is what the session-known layer exists for.
- DOBs in 11 English and 7 Chinese formats; past dates and future appointments in
  numeric, month-name, weekday and ZH 月/日/號 forms (future ones are flagged so the
  report can show whether `[DATE_n · next Friday]` came out as a past offset).
- Over-redaction traps (`TrapSpan`) in 99% of transcripts: drug names (Tamoxifen,
  Letrozole, Oxycodone, Panadol, 必理痛…), readings (CA-125 35, BP 120/80, 血壓 98/60,
  sugar 6.5, 血糖 7.2, SpO2 95%, temperature 38.2), doses, scales (pain 7/10, 4 out of 5),
  durations and relative time (3 days ago, 上星期, 琴日), 陳皮/李子/王子/黃芪/甘草/白蘿蔔,
  花生/醫生/衛生/何時發生/阿媽, 8號風球/5號巴士/3號線, "Queen Mary Hospital" and "Sha Tin
  Hospital" as single FACILITY spans, the friend called Mary, and — for the common-word
  personas — the name part used as an ordinary word ("how long", "a man", "I may",
  "blood test", "the patient leaflet", "by the grace of God") in user and assistant turns.

### Scoring — `run_phi_eval.py`

Each transcript gets a fresh `ScrubContext(known, ner=backend)` and its turns are
scrubbed in order with `ctx.scrub(text, now=NOW, language=…)`; `ScrubResult.spans`
gives predicted spans in original offsets.

- **Recall**: a gold span is recalled when >= 80% of its characters are covered by the
  union of predicted spans of the same class family — PERSON; ID/PHONE/EMAIL/HANDLE/URL;
  FACILITY/PLACE/ADDRESS; ORG/OCCUPATION; DATE/AGE/DOB.
- **Precision**: a predicted span is correct when >= 80% of it lies inside same-family gold.
- **Direct** = PERSON, ID, PHONE, EMAIL, HANDLE, URL, DOB. **Indirect** = the seven
  linkage classes FACILITY, PLACE, ADDRESS, ORG, OCCUPATION, DATE, AGE.
- **Over-redaction rate** = trap spans touched by any predicted span / traps.
- **Stray redactions**: predicted spans touching no gold, trap or neutral span (the
  template text itself was redacted).
- Also reported: linkage-score histogram (per turn from `ScrubReport.linkage_score`, and
  the union over a transcript), latency p50/p95 per turn after a warm-up call, future
  dates rendered as past (and vice versa), DOB spans rendered as `[DOB]`, facility spans
  touched by a PERSON token (the Queen Mary trap), misses by `class:kind`, and examples.

All of it is reported for `all`, `en` and `zh-HK` separately, one section per backend,
into `docs/eval-phi-recall.md` and the machine-readable `docs/eval-phi-recall.json`.

### Bars — `test_phi_recall.py`

Deterministic backend only (`NullBackend`), overall **and** per language:
direct recall >= 0.99, indirect recall >= 0.95, over-redaction <= 0.02; plus no stray
redactions, no facility/person confusion, no future-appointment direction errors, every
known DOB expression as `[DOB]`, and the whole run under 10 s (it takes ~1.5 s).

If a bar fails, do not lower it. Read the `Misses by class:kind` table and the examples
in the report, fix the scrubber when the fix is contained (and add a unit test under
`tests/unit/test_scrub_*.py` — see `test_scrub_eval_regressions.py` for the defects this
eval has already surfaced), otherwise report the per-class shortfall.

### Results

See `docs/eval-phi-recall.md`. At the time of writing the deterministic layers score
100% direct / 100% indirect / 0% over-redaction on both languages, p95 0.35 ms per turn.

### Optional NER pass (Presidio)

```
.venv/bin/pip install "presidio-analyzer>=2.2" spacy
.venv/bin/python -m spacy download en_core_web_sm
.venv/bin/python -m spacy download zh_core_web_sm
.venv/bin/python -m tests.eval.phi_recall.run_phi_eval --backend none --backend presidio
```

The install works on Python 3.13 (presidio-analyzer 2.2.364, spaCy 3.8; Presidio's
default engine also pulls `en_core_web_lg` on first use). Used as an *additional* layer
on top of the deterministic ones, Presidio with its default English model is net
negative on this corpus: direct 99.3% / indirect 98.7% / over-redaction 5.7%, p95 5.9 ms.
The report's trap and stray examples show why — drug names become `[PLACE_n]`
(Tamoxifen, Zofran, Herceptin), `SpO2` a PERSON, "Florence" and "Dyspnea" are redacted,
and on Cantonese text the English model tags whole sentences as one entity, which
swallows the real spans (wrong class family) and loses recall. Before `SCRUB_NER_BACKEND=presidio`
could be recommended it would need a zh model routed by `language`, a higher score
threshold, and a deny-list for drug names and the assistant's name. The default stays `none`.

### Known gaps the corpus does not count against the scrubber

These forms are out of reach for the deterministic layers and deliberately not seeded
as gold (they belong to a tuned NER backend):

- organisation names without a type word ("I work at HSBC", "Deloitte");
- a stoplisted given name written sentence-initially after punctuation ("Hello. Grace is here");
- a relative introduced with a name that is also a month or stop word ("Auntie May");
- mixed-script nicknames after 阿 ("我朋友阿May");
- handles without a separator after the platform word ("my WhatsApp is siuming88" —
  "WhatsApp: siuming88" and "WhatsApp id is siuming88" are caught).

### Extending the corpus

Templates live in `templates_en.py` / `templates_zh.py`. `{slot}` placeholders are
filled by `generate.Resolver` (see its dispatcher for the slot names: `patient_full`,
`rel`, `rel_again`, `facility`, `place_cued`, `date_past`, `weekday_future`, `dob`,
`hkid`, `phone_other`, `handle`, …) and recorded as gold; `{T:text}` is an
over-redaction trap; `{N:text}` is neutral (never counted). Literal template text must
contain no identifiers — `test_persona_name_never_appears_outside_gold` and the
stray-redaction count catch accidents. Changing templates changes the RNG sequence, so
expect the numbers (not the bars) to move; regenerate the report afterwards.

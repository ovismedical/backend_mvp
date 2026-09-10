# PHI recall eval — de-identification scrubber

Generated 2026-09-10 02:05 UTC. Corpus seed `20260909`, reference time `2026-09-09T10:00:00+08:00`. Produced by `tests/eval/phi_recall/run_phi_eval.py`; the deterministic run is asserted by `tests/eval/test_phi_recall.py` (direct >= 0.99, indirect >= 0.95, over-redaction <= 0.02, overall and per language). Corpus design, scoring rules, how to run the optional NER pass and how to read its table: `tests/eval/README.md`.

## Corpus

| | en | zh-HK |
|---|---|---|
| transcripts | 100 | 100 |
| questionnaires | 14 | 14 |
| turns | 922 | 922 |
| gold_spans | 631 | 573 |
| trap_spans | 431 | 398 |
| with_traps | 99 | 99 |
| with_3plus_indirect | 46 | 39 |
| common_word_personas | 24 | 0 |
| colloquial_cjk_forms | 0 | 49 |

Gold spans per class:

| class | en | zh-HK |
|---|---|---|
| PERSON | 265 | 230 |
| ID | 15 | 9 |
| PHONE | 10 | 14 |
| EMAIL | 25 | 16 |
| HANDLE | 14 | 18 |
| URL | 18 | 9 |
| DOB | 14 | 17 |
| FACILITY | 66 | 72 |
| PLACE | 47 | 34 |
| ADDRESS | 26 | 30 |
| ORG | 30 | 32 |
| OCCUPATION | 29 | 25 |
| DATE | 40 | 37 |
| AGE | 32 | 30 |

Scoring: a gold span counts as recalled when >= 80% of its characters are covered by predicted spans of the same class family (PERSON; ID/PHONE/EMAIL/HANDLE/URL; FACILITY/PLACE/ADDRESS; ORG/OCCUPATION; DATE/AGE/DOB); a predicted span is precise when >= 80% of it lies inside same-family gold. Over-redaction = traps touched / traps. Direct = PERSON, ID, PHONE, EMAIL, HANDLE, URL, DOB; indirect = the seven linkage classes.

## Backend: `none`

Scrub time 0.38 s for 1844 turns.

### Summary

| metric | all | en | zh-HK |
|---|---|---|---|
| transcripts / turns | 200 / 1844 | 100 / 922 | 100 / 922 |
| direct recall (bar >= 99%) | 100.0% (674/674) | 100.0% (361/361) | 100.0% (313/313) |
| indirect recall (bar >= 95%) | 100.0% (530/530) | 100.0% (270/270) | 100.0% (260/260) |
| over-redaction rate (bar <= 2%) | 0.0% (0/829) | 0.0% (0/431) | 0.0% (0/398) |
| precision (all classes) | 99.8% (1221/1224) | 99.5% (631/634) | 100.0% (590/590) |
| stray redactions (spans) | 0 | 0 | 0 |
| future dates rendered as past | 0/29 | 0/19 | 0/10 |
| past dates rendered as future | 0/48 | 0/21 | 0/27 |
| DOB spans rendered as [DOB] | 31/31 | 14/14 | 17/17 |
| facility spans touched by a PERSON token | 0 | 0 | 0 |
| latency per turn p50 / p95 | 0.14 ms / 0.39 ms | 0.18 ms / 0.42 ms | 0.12 ms / 0.37 ms |

### Per-class — all

| class | group | gold | recalled | recall | predicted | precision |
|---|---|---|---|---|---|---|
| PERSON | direct | 495 | 495 | 100.0% | 498 | 99.4% |
| ID | direct | 24 | 24 | 100.0% | 24 | 100.0% |
| PHONE | direct | 24 | 24 | 100.0% | 24 | 100.0% |
| EMAIL | direct | 41 | 41 | 100.0% | 41 | 100.0% |
| HANDLE | direct | 32 | 32 | 100.0% | 32 | 100.0% |
| URL | direct | 27 | 27 | 100.0% | 27 | 100.0% |
| DOB | direct | 31 | 31 | 100.0% | 31 | 100.0% |
| FACILITY | indirect | 138 | 138 | 100.0% | 138 | 100.0% |
| PLACE | indirect | 81 | 81 | 100.0% | 83 | 100.0% |
| ADDRESS | indirect | 56 | 56 | 100.0% | 71 | 100.0% |
| ORG | indirect | 62 | 62 | 100.0% | 62 | 100.0% |
| OCCUPATION | indirect | 54 | 54 | 100.0% | 54 | 100.0% |
| DATE | indirect | 77 | 77 | 100.0% | 77 | 100.0% |
| AGE | indirect | 62 | 62 | 100.0% | 62 | 100.0% |

### Per-class — en

| class | group | gold | recalled | recall | predicted | precision |
|---|---|---|---|---|---|---|
| PERSON | direct | 265 | 265 | 100.0% | 268 | 98.9% |
| ID | direct | 15 | 15 | 100.0% | 15 | 100.0% |
| PHONE | direct | 10 | 10 | 100.0% | 10 | 100.0% |
| EMAIL | direct | 25 | 25 | 100.0% | 25 | 100.0% |
| HANDLE | direct | 14 | 14 | 100.0% | 14 | 100.0% |
| URL | direct | 18 | 18 | 100.0% | 18 | 100.0% |
| DOB | direct | 14 | 14 | 100.0% | 14 | 100.0% |
| FACILITY | indirect | 66 | 66 | 100.0% | 66 | 100.0% |
| PLACE | indirect | 47 | 47 | 100.0% | 47 | 100.0% |
| ADDRESS | indirect | 26 | 26 | 100.0% | 26 | 100.0% |
| ORG | indirect | 30 | 30 | 100.0% | 30 | 100.0% |
| OCCUPATION | indirect | 29 | 29 | 100.0% | 29 | 100.0% |
| DATE | indirect | 40 | 40 | 100.0% | 40 | 100.0% |
| AGE | indirect | 32 | 32 | 100.0% | 32 | 100.0% |

### Per-class — zh-HK

| class | group | gold | recalled | recall | predicted | precision |
|---|---|---|---|---|---|---|
| PERSON | direct | 230 | 230 | 100.0% | 230 | 100.0% |
| ID | direct | 9 | 9 | 100.0% | 9 | 100.0% |
| PHONE | direct | 14 | 14 | 100.0% | 14 | 100.0% |
| EMAIL | direct | 16 | 16 | 100.0% | 16 | 100.0% |
| HANDLE | direct | 18 | 18 | 100.0% | 18 | 100.0% |
| URL | direct | 9 | 9 | 100.0% | 9 | 100.0% |
| DOB | direct | 17 | 17 | 100.0% | 17 | 100.0% |
| FACILITY | indirect | 72 | 72 | 100.0% | 72 | 100.0% |
| PLACE | indirect | 34 | 34 | 100.0% | 36 | 100.0% |
| ADDRESS | indirect | 30 | 30 | 100.0% | 45 | 100.0% |
| ORG | indirect | 32 | 32 | 100.0% | 32 | 100.0% |
| OCCUPATION | indirect | 25 | 25 | 100.0% | 25 | 100.0% |
| DATE | indirect | 37 | 37 | 100.0% | 37 | 100.0% |
| AGE | indirect | 30 | 30 | 100.0% | 30 | 100.0% |

### Linkage-score histogram

Distinct indirect classes (FACILITY, PLACE, ADDRESS, ORG, OCCUPATION, DATE, AGE) the scrubber found — per turn, and as the union over a transcript.

| score | turns (all) | turns (en) | turns (zh-HK) | transcripts (all) | transcripts (en) | transcripts (zh-HK) |
|---|---|---|---|---|---|---|
| 0 | 1448 | 728 | 720 | 22 | 13 | 9 |
| 1 | 292 | 140 | 152 | 41 | 18 | 23 |
| 2 | 81 | 36 | 45 | 51 | 23 | 28 |
| 3 | 22 | 17 | 5 | 36 | 22 | 14 |
| 4 | 1 | 1 | 0 | 18 | 7 | 11 |
| 5 | 0 | 0 | 0 | 29 | 14 | 15 |
| 6 | 0 | 0 | 0 | 3 | 3 | 0 |

## Backend: `presidio`

Scrub time 5.09 s for 1844 turns.

### Summary

| metric | all | en | zh-HK |
|---|---|---|---|
| transcripts / turns | 200 / 1844 | 100 / 922 | 100 / 922 |
| direct recall (bar >= 99%) | 99.3% (669/674) | 100.0% (361/361) | 98.4% (308/313) |
| indirect recall (bar >= 95%) | 98.7% (523/530) | 100.0% (270/270) | 97.3% (253/260) |
| over-redaction rate (bar <= 2%) | 5.7% (47/829) | 4.2% (18/431) | 7.3% (29/398) |
| precision (all classes) | 88.3% (1186/1343) | 87.2% (628/720) | 89.6% (558/623) |
| stray redactions (spans) | 96 | 68 | 28 |
| future dates rendered as past | 0/29 | 0/19 | 0/10 |
| past dates rendered as future | 0/47 | 0/21 | 0/26 |
| DOB spans rendered as [DOB] | 30/31 | 14/14 | 16/17 |
| facility spans touched by a PERSON token | 1 | 0 | 1 |
| latency per turn p50 / p95 | 2.08 ms / 5.86 ms | 3.07 ms / 6.60 ms | 1.66 ms / 4.98 ms |

### Per-class — all

| class | group | gold | recalled | recall | predicted | precision |
|---|---|---|---|---|---|---|
| PERSON | direct | 495 | 491 | 99.2% | 537 | 90.1% |
| ID | direct | 24 | 24 | 100.0% | 24 | 100.0% |
| PHONE | direct | 24 | 24 | 100.0% | 24 | 100.0% |
| EMAIL | direct | 41 | 41 | 100.0% | 41 | 78.0% |
| HANDLE | direct | 32 | 32 | 100.0% | 32 | 100.0% |
| URL | direct | 27 | 27 | 100.0% | 27 | 88.9% |
| DOB | direct | 31 | 30 | 96.8% | 30 | 100.0% |
| FACILITY | indirect | 138 | 137 | 99.3% | 137 | 100.0% |
| PLACE | indirect | 81 | 81 | 100.0% | 173 | 46.8% |
| ADDRESS | indirect | 56 | 55 | 98.2% | 68 | 100.0% |
| ORG | indirect | 62 | 61 | 98.4% | 61 | 100.0% |
| OCCUPATION | indirect | 54 | 51 | 94.4% | 51 | 100.0% |
| DATE | indirect | 77 | 76 | 98.7% | 76 | 100.0% |
| AGE | indirect | 62 | 62 | 100.0% | 62 | 100.0% |

### Per-class — en

| class | group | gold | recalled | recall | predicted | precision |
|---|---|---|---|---|---|---|
| PERSON | direct | 265 | 265 | 100.0% | 303 | 86.5% |
| ID | direct | 15 | 15 | 100.0% | 15 | 100.0% |
| PHONE | direct | 10 | 10 | 100.0% | 10 | 100.0% |
| EMAIL | direct | 25 | 25 | 100.0% | 25 | 100.0% |
| HANDLE | direct | 14 | 14 | 100.0% | 14 | 100.0% |
| URL | direct | 18 | 18 | 100.0% | 18 | 100.0% |
| DOB | direct | 14 | 14 | 100.0% | 14 | 100.0% |
| FACILITY | indirect | 66 | 66 | 100.0% | 66 | 100.0% |
| PLACE | indirect | 47 | 47 | 100.0% | 98 | 48.0% |
| ADDRESS | indirect | 26 | 26 | 100.0% | 26 | 100.0% |
| ORG | indirect | 30 | 30 | 100.0% | 30 | 100.0% |
| OCCUPATION | indirect | 29 | 29 | 100.0% | 29 | 100.0% |
| DATE | indirect | 40 | 40 | 100.0% | 40 | 100.0% |
| AGE | indirect | 32 | 32 | 100.0% | 32 | 100.0% |

### Per-class — zh-HK

| class | group | gold | recalled | recall | predicted | precision |
|---|---|---|---|---|---|---|
| PERSON | direct | 230 | 226 | 98.3% | 234 | 94.9% |
| ID | direct | 9 | 9 | 100.0% | 9 | 100.0% |
| PHONE | direct | 14 | 14 | 100.0% | 14 | 100.0% |
| EMAIL | direct | 16 | 16 | 100.0% | 16 | 43.8% |
| HANDLE | direct | 18 | 18 | 100.0% | 18 | 100.0% |
| URL | direct | 9 | 9 | 100.0% | 9 | 66.7% |
| DOB | direct | 17 | 16 | 94.1% | 16 | 100.0% |
| FACILITY | indirect | 72 | 71 | 98.6% | 71 | 100.0% |
| PLACE | indirect | 34 | 34 | 100.0% | 75 | 45.3% |
| ADDRESS | indirect | 30 | 29 | 96.7% | 42 | 100.0% |
| ORG | indirect | 32 | 31 | 96.9% | 31 | 100.0% |
| OCCUPATION | indirect | 25 | 22 | 88.0% | 22 | 100.0% |
| DATE | indirect | 37 | 36 | 97.3% | 36 | 100.0% |
| AGE | indirect | 30 | 30 | 100.0% | 30 | 100.0% |

### Linkage-score histogram

Distinct indirect classes (FACILITY, PLACE, ADDRESS, ORG, OCCUPATION, DATE, AGE) the scrubber found — per turn, and as the union over a transcript.

| score | turns (all) | turns (en) | turns (zh-HK) | transcripts (all) | transcripts (en) | transcripts (zh-HK) |
|---|---|---|---|---|---|---|
| 0 | 1397 | 698 | 699 | 21 | 13 | 8 |
| 1 | 330 | 160 | 170 | 34 | 13 | 21 |
| 2 | 94 | 45 | 49 | 49 | 23 | 26 |
| 3 | 21 | 17 | 4 | 43 | 23 | 20 |
| 4 | 2 | 2 | 0 | 21 | 11 | 10 |
| 5 | 0 | 0 | 0 | 29 | 14 | 15 |
| 6 | 0 | 0 | 0 | 2 | 2 | 0 |
| 7 | 0 | 0 | 0 | 1 | 1 | 0 |

### Misses by class:kind

| class:kind | misses |
|---|---|
| OCCUPATION:occ | 3 |
| PERSON:doctor_full | 2 |
| ADDRESS:address:estate | 1 |
| DATE:weekday_past:prefixed | 1 |
| DOB:dob:m月d日 | 1 |
| FACILITY:facility:trap | 1 |
| ORG:org | 1 |
| PERSON:other_title | 1 |
| PERSON:patient_full | 1 |

### Example misses (first 12 of 12; synthetic text)

- `zh-HK-002` turn 9 (user) ADDRESS:address:estate — `杏花邨10座7樓E室` → [PERSON_2]。
- `zh-HK-002` turn 10 (assistant) PERSON:patient_full — `黃志強` → [PLACE_2]。
- `zh-HK-005` turn 3 (user) OCCUPATION:occ — `清潔工` → [PLACE_1]。
- `zh-HK-029` turn 1 (user) FACILITY:facility:trap — `瑪麗醫院` → [PERSON_2]: [HANDLE_1]可以搵到我。
- `zh-HK-029` turn 5 (user) PERSON:doctor_full — `張偉文` → 暖包有少少幫助。我食咗幾個李子。[URL_1]
- `zh-HK-053` turn 1 (user) ORG:org — `太古集團` → 咳咗大約一個禮拜都唔好。我喺[URL_1]
- `zh-HK-063` turn 1 (user) DOB:dob:m月d日 — `5月16日` → [PLACE_1]。
- `zh-HK-071` turn 3 (user) OCCUPATION:occ — `社工` → 一啖一啖飲得落，食嘢就難。Telegram: [HANDLE_1]可以搵到我。[DATE_1 · 12 days ago]痛得犀利咗。[FACILITY_1]嘅姑娘叫我休息。我喺[URL_1]
- `zh-HK-083` turn 4 (user) DATE:weekday_past:prefixed — `上個禮拜五` → Regarding Dysuria: - Where do you feel pain/a burning sensation when you urinate? (Choose all that apply): Other - Please specify: [PLACE_1]。 Overall severity:…
- `zh-HK-084` turn 7 (user) PERSON:doctor_full — `李志明` → [PLACE_1]。
- `zh-HK-088` turn 5 (user) PERSON:other_title — `王太太` → [PLACE_1]。
- `zh-HK-088` turn 5 (user) OCCUPATION:occ — `店員` → [PLACE_1]。

### Example over-redactions (traps altered) (first 12 of 47; synthetic text)

- `en-001` turn 1 (user) PLACE:zofran — `Zofran` → Nausea again. The [PLACE_1] helps a bit but not enough. My address is [ADDRESS_1], in case you need it.
- `en-004` turn 1 (user) PERSON:spo2 95% — `SpO2 95%` → The pain is back, mainly in my lower back and ribs. I had [PERSON_2] 95% on the machine.
- `en-010` turn 5 (user) PLACE:herceptin — `Herceptin` → I can manage a short walk but then I need to lie down for 2 hours. I've been on the [PLACE_1] since spring. Now that I'm [AGE · 60s], I expected some aches, bu…
- `en-012` turn 1 (user) PLACE:tamoxifen — `Tamoxifen` → I'm not eating much. Everything tastes like metal. I take [PLACE_1] every morning. My email is [EMAIL_1]. I was admitted on [DATE_1 · 35 days ago] for two nigh…
- `en-017` turn 7 (user) PLACE:tamoxifen — `Tamoxifen` → Swallowing is fine. My last chemo was on [DATE_1 · 5 days ago]. My daughter [PERSON_3] came round. [PERSON_3] thinks I look pale. I take [PLACE_2] every mornin…
- `en-031` turn 1 (user) PLACE:zofran — `Zofran` → Nausea again. The [PLACE_1] helps a bit but not enough. My daughter's number is [PHONE_1]. I'm [AGE · 70s], so recovery is slower.
- `en-042` turn 3 (user) PLACE:tamoxifen — `Tamoxifen` → Toast this morning, nothing since. I take [PLACE_2] every morning. My date of birth is [DOB], if that helps you find me.
- `en-044` turn 5 (user) PLACE:tamoxifen — `Tamoxifen` → Both knees and my hips. I take [PLACE_1] every morning.
- `en-045` turn 9 (user) PLACE:tamoxifen — `Tamoxifen` → No dizziness, just heavy legs. I take [PLACE_3] every morning.
- `en-046` turn 1 (user) PLACE:zofran — `Zofran` → Nausea again. The [PLACE_1] helps a bit but not enough. I take [PLACE_2] every morning. Send it to [ADDRESS_1].
- `en-046` turn 1 (user) PLACE:tamoxifen — `Tamoxifen` → Nausea again. The [PLACE_1] helps a bit but not enough. I take [PLACE_2] every morning. Send it to [ADDRESS_1].
- `en-050` turn 5 (user) PLACE:herceptin — `Herceptin` → Broken. I wake at 3am and can't get back to sleep. I walked to [PLACE_1] station and had to sit down. I was treated at [FACILITY_1]. I'm at [ADDRESS_1] if some…

### Example stray redactions (first 12 of 96; synthetic text)

- `en-002` turn 0 (assistant) PLACE:stray — `Florence` → Hello [PERSON_1], it's [PLACE_1]. How are things since we last spoke?
- `en-003` turn 0 (assistant) PERSON:stray — `Florence` → Hello, I'm [PERSON_2]. I'm here for your health check-in. How are you feeling?
- `en-005` turn 0 (assistant) PLACE:stray — `Florence` → Hello [PERSON_1], it's [PLACE_1]. How are things since we last spoke?
- `en-006` turn 5 (assistant) PLACE:stray — `Dyspnea` → Let me ask about [PLACE_1].
- `en-006` turn 6 (user) PLACE:stray — `Dyspnea` → Regarding [PLACE_1]: - What activities usually cause shortness of breath? (Choose all that apply): Other - Please specify: Carrying shopping home to [PLACE_2].…
- `en-007` turn 0 (assistant) PERSON:stray — `Florence` → Hello [PERSON_1], I'm [PERSON_2]. How are you feeling today?
- `en-008` turn 0 (assistant) PERSON:stray — `Florence` → Hello [PERSON_1], I'm [PERSON_2]. How are you feeling today?
- `en-009` turn 0 (assistant) PERSON:stray — `Florence` → Hello, I'm [PERSON_2]. I'm here for your health check-in. How are you feeling?
- `en-009` turn 9 (user) PERSON:stray — `Telegram` → Some tingling in my left foot. You can find me on [PERSON_3]: [HANDLE_1].
- `en-010` turn 0 (assistant) PERSON:stray — `Florence` → Hello [PERSON_1], I'm [PERSON_2]. How are you feeling today?
- `en-012` turn 0 (assistant) PERSON:stray — `Florence` → Hello [PERSON_1], I'm [PERSON_2]. How are you feeling today?
- `en-013` turn 5 (assistant) PLACE:stray — `Dyspnea` → Let me ask about [PLACE_2].


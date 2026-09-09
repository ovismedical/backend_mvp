"""English templates for the PHI recall corpus.

Template syntax (resolved by ``generate.fill``):
  ``{slot}``      an identifier slot filled by the generator and recorded as a
                  ground-truth span (see ``generate.Resolver`` for slot names);
  ``{kin}``       a literal kinship word chosen to match the next ``{rel}``;
  ``{T:text}``    an over-redaction trap — literal text that must survive verbatim;
  ``{N:text}``    neutral literal — may or may not be scrubbed, never counted.
Everything else is literal text and must contain no identifiers.
"""

# --- personas -------------------------------------------------------------------------
# (full_name, given, surname, sex, order) — order "hk" = surname first ("Wong Siu Ming"),
# "west" = given first ("Grace Tam"). ``common`` marks the F1 common-word names.
PERSONAS = [
    ("Wong Siu Ming", "Siu Ming", "Wong", "m", "hk", False),
    ("Chan Ka Yee", "Ka Yee", "Chan", "f", "hk", False),
    ("Lee Wai Keung", "Wai Keung", "Lee", "m", "hk", False),
    ("Cheung Siu Fan", "Siu Fan", "Cheung", "f", "hk", False),
    ("Lau Mei Fong", "Mei Fong", "Lau", "f", "hk", False),
    ("Tsang Wai Man", "Wai Man", "Tsang", "m", "hk", False),
    ("Ho Yuen Ting", "Yuen Ting", "Ho", "f", "hk", False),
    ("Yip Ka Ho", "Ka Ho", "Yip", "m", "hk", False),
    ("Kwok Chi Wah", "Chi Wah", "Kwok", "m", "hk", False),
    ("Leung Sau Lan", "Sau Lan", "Leung", "f", "hk", False),
    ("Peter Lau", "Peter", "Lau", "m", "west", False),
    ("Amy Kwok", "Amy", "Kwok", "f", "west", False),
    ("David Ng", "David", "Ng", "m", "west", False),
    ("Kelvin Tsang", "Kelvin", "Tsang", "m", "west", False),
    ("Carol Yip", "Carol", "Yip", "f", "west", False),
    ("Jason Fung", "Jason", "Fung", "m", "west", False),
    ("Sandy Lo", "Sandy", "Lo", "f", "west", False),
    ("Ivy Leung", "Ivy", "Leung", "f", "west", False),
    ("Vivian Chow", "Vivian", "Chow", "f", "west", False),
    ("Raymond Siu", "Raymond", "Siu", "m", "west", False),
    # F1: common-word names (parts collide with ordinary English words)
    ("Ho Long", "Long", "Ho", "m", "hk", True),
    ("Ka Man", "Ka", "Man", "f", "hk", True),
    ("May Chan", "May", "Chan", "f", "west", True),
    ("Wing Yan", "Wing", "Yan", "f", "hk", True),
    ("Sum Yee", "Sum", "Yee", "f", "hk", True),
    ("Test Patient", "Test", "Patient", "f", "west", True),
    ("Grace Tam", "Grace", "Tam", "f", "west", True),
    ("Wong Ka Long", "Ka Long", "Wong", "m", "hk", True),
]

DOCTORS = [
    ("Dr. Amanda Lee", "Amanda Lee", "Lee"),
    ("Dr Kenneth Chow", "Kenneth Chow", "Chow"),
    ("Dr. Fiona Ma", "Fiona Ma", "Ma"),
    ("Dr Raymond Yuen", "Raymond Yuen", "Yuen"),
    ("Dr. Michael Tang", "Michael Tang", "Tang"),
    ("Dr Priscilla Ko", "Priscilla Ko", "Ko"),
]

HOSPITALS = [
    "Queen Mary Hospital", "Prince of Wales Hospital", "Queen Elizabeth Hospital", "Princess Margaret Hospital",
    "Tuen Mun Hospital", "United Christian Hospital", "Kwong Wah Hospital", "Pamela Youde Nethersole Eastern Hospital",
    "Sha Tin Hospital", "Gleneagles Hospital", "Tseung Kwan O Hospital", "North District Hospital",
    "Caritas Medical Centre", "Ruttonjee Hospital", "Yan Chai Hospital", "Pok Oi Hospital",
]
# facility names that only the suffix rule can catch (not in the gazetteer)
FACILITIES_SUFFIX = [
    "Kowloon Bay Health Centre", "Yau Ma Tei Jockey Club Clinic", "Shatin Cancer Centre", "Lek Yuen Health Centre",
    "Tsuen Wan Adventist Oncology Centre", "Hong Kong Breast Cancer Foundation Clinic",
]

# the F.5 traps: the persona's common-word part used as an ordinary word
COMMON_WORD_TRAPS = {
    "Ho Long": ["I've had {T:long}-term pain in my hip.", "It took a {T:long} time to fall asleep."],
    "Wong Ka Long": ["I've had {T:long}-term pain in my hip.", "The queue was so {T:long} I nearly fainted."],
    "Ka Man": ["A {T:man} in the clinic fainted next to me.", "My husband says I should {T:man}age my rest better."],
    "May Chan": ["I {T:may} feel better after the weekend.", "It {T:may} be the new tablets."],
    "Wing Yan": ["The oncology {T:wing} of the hospital was freezing.", "I ate a chicken {T:wing} and felt sick."],
    "Sum Yee": ["The {T:sum} of it is that I'm exhausted.", "I can't {T:sum}mon the energy to cook."],
    "Test Patient": ["I had a blood {T:test} and the {T:patient} leaflet says {T:pain 7/10}.",
                     "The {T:test} results come back next week."],
    "Grace Tam": ["By the {T:grace} of God I slept through.", "I said {T:grace} before dinner and felt calmer."],
}
COMMON_WORD_ASSISTANT_TRAPS = {
    "Ho Long": ["How {T:long} have you felt this way?"],
    "Wong Ka Long": ["How {T:long} have you felt this way?"],
    "Ka Man": ["Can you {T:man}age a short walk each day?"],
    "May Chan": ["You {T:may} want to mention this to your care team."],
    "Wing Yan": ["Which {T:wing} of the hospital is your clinic in?"],
    "Sum Yee": ["To {T:sum} up, the fatigue is worse in the afternoons?"],
    "Test Patient": ["Did the blood {T:test} results come back?"],
    "Grace Tam": ["Take it slowly and with {T:grace} — rest when you need to."],
}

# --- other people --------------------------------------------------------------------------
KIN = [
    ("daughter", "f"), ("son", "m"), ("wife", "f"), ("husband", "m"), ("sister", "f"), ("brother", "m"),
    ("friend", "x"), ("neighbour", "x"), ("helper", "f"), ("granddaughter", "f"), ("grandson", "m"),
    ("niece", "f"), ("nephew", "m"), ("colleague", "x"), ("daughter-in-law", "f"), ("son-in-law", "m"),
]
RELATIVE_NAMES = {
    "f": ["Mei Ling", "Carmen", "Mary", "Angela", "Maria", "Siu Fung", "Wai Yin", "Josephine", "Natalie", "Ah Lan"],
    "m": ["Peter", "Ka Wai", "Kelvin", "Jason", "Tommy", "Vincent", "Chi Keung", "Dennis", "Ah Keung", "Marcus"],
}
OTHER_TITLED = ["Dr Chan", "Dr Wong", "Nurse Cheung", "Dr Lam", "Sister Ho", "Prof Lau", "Mrs Lam", "Dr. Kwan",
                "Nurse Tsui", "Mr Fok"]

# --- places and organisations --------------------------------------------------------------
DISTRICTS = [
    "Mong Kok", "Sha Tin", "Tuen Mun", "Kwun Tong", "Tsuen Wan", "Yuen Long", "Tai Po", "Causeway Bay", "Wan Chai",
    "North Point", "Sham Shui Po", "Tin Shui Wai", "Tseung Kwan O", "Ma On Shan", "Lam Tin", "Kowloon Bay",
    "Aberdeen", "Kennedy Town", "Quarry Bay", "Tsim Sha Tsui", "Hung Hom", "Wong Tai Sin", "Kwai Chung", "Tung Chung",
]
MTR = ["Kowloon Tong", "Admiralty", "Tai Koo", "Diamond Hill", "Choi Hung", "Prince Edward", "Mei Foo", "Fo Tan",
       "Lai King", "Tsing Yi"]
ESTATES = ["Tai Koo Shing", "Mei Foo Sun Chuen", "Whampoa Garden", "Laguna City", "Kingswood Villas", "City One",
           "Heng Fa Chuen", "South Horizons", "Wah Fu Estate", "Choi Hung Estate", "LOHAS Park", "Telford Gardens",
           "Amoy Gardens", "Belvedere Garden", "Sceneway Garden"]
STREETS = ["Nathan Road", "Queen's Road East", "Hennessy Road", "Castle Peak Road", "Prince Edward Road West",
           "Waterloo Road", "Argyle Street", "Shanghai Street", "Des Voeux Road Central", "Lung Cheung Road",
           "Tai Po Road", "Kwun Tong Road"]
ORGS = ["Hang Seng Bank", "Cathay Pacific Airways Limited", "Wah Yan College", "AIA Insurance", "MTR Corporation Limited",
        "Sun Hung Kai Properties", "Diocesan Girls' School", "Tsuen Wan Government Secondary School",
        "Bank of East Asia Limited", "Hong Kong Jockey Club Limited", "Po Leung Kuk Primary School", "Kowloon Motor Bus Company",
        "Dah Sing Bank", "St. Paul's Co-educational College", "Swire Properties", "Hong Kong Baptist University"]
OCCUPATIONS = ["teacher", "nurse", "accountant", "bus driver", "cashier", "security guard", "social worker",
               "civil servant", "hairdresser", "cleaner", "chef", "clerk", "shopkeeper", "taxi driver", "electrician",
               "carpenter", "housewife", "waiter", "minibus driver", "office clerk"]

# --- dialogue -----------------------------------------------------------------------------
OPENERS = [
    "Hello {patient_full}, I'm Florence. How are you feeling today?",
    "Hi {patient_title}, how has your week been?",
    "Good morning {patient_full}. I'm here for your health check-in — how are you today?",
    "Hello {patient_given}, it's Florence. How are things since we last spoke?",
    "Hello, I'm Florence. I'm here for your health check-in. How are you feeling?",
    "Hi there, thanks for checking in today. How are you feeling?",
]
CLOSERS = [
    "Thank you {patient_given}, I'll make sure {doctor_title} sees this.",
    "Thanks for telling me all this, {patient_full}. Rest well and we'll speak soon.",
    "That's really helpful. I'll pass this on to {doctor_full} and the team.",
    "Thank you for sharing. Please rest and call the clinic if anything gets worse.",
    "Thanks {patient_title}. Take care and we'll check in again soon.",
]
ECHOES = [
    "It sounds like {rel_again} is a great support for you.",
    "I'm glad {rel_again} is around to help.",
    "Please thank {rel_again} for looking after you.",
    "It's good that {other_again} is keeping an eye on it.",
]

SYMPTOMS = {
    "fatigue": {
        "intro": [
            "I've been so tired. Even getting dressed wears me out.",
            "Honestly, exhausted. I slept {T:10 hours} and still feel drained.",
            "The fatigue is worse this week. I can barely get off the sofa.",
            "I'm wiped out after every cycle. This one feels heavier.",
        ],
        "pairs": [
            ("How would you rate your fatigue on a scale of 0 to 10?",
             ["About {T:7/10} most days, {T:8/10} in the afternoons.", "I'd say {T:6 out of 10}.", "Maybe {T:4 out of 5} on your scale."]),
            ("Is it affecting what you can do in a day?",
             ["Yes, I stopped cooking. I just heat things up.", "I can manage a short walk but then I need to lie down for {T:2 hours}.",
              "I haven't left the flat in {T:3 days}."]),
            ("How has your sleep been?",
             ["Broken. I wake at {T:3am} and can't get back to sleep.", "I sleep {T:9 hours} but wake up just as tired.",
              "Not bad, maybe {T:6 hours}, but no energy."]),
            ("Have you noticed any dizziness or shortness of breath with it?",
             ["A bit dizzy when I stand up. {T:My pressure was 98/60} at the clinic.", "No dizziness, just heavy legs.",
              "Slightly breathless on the stairs."]),
            ("Are you managing to eat and drink enough?",
             ["I'm drinking but eating less. Maybe half portions.", "Yes, my appetite is fine, it's just the energy.",
              "I try. {T:Sugar was 6.5} this morning so I'm careful."]),
        ],
    },
    "nausea": {
        "intro": [
            "The nausea has been bad since the last chemo. I've vomited twice.",
            "I feel sick most of the day, especially after eating.",
            "Queasy all the time. The smell of cooking sets it off.",
            "Nausea again. The {T:Zofran} helps a bit but not enough.",
        ],
        "pairs": [
            ("How many times have you vomited in the last 24 hours?",
             ["Twice this morning.", "Once, after breakfast.", "Three times yesterday, none so far today."]),
            ("Are you taking your anti-sickness medication as prescribed?",
             ["Yes, {T:ondansetron} {T:8 mg} twice a day.", "I took {T:2 tablets} but they didn't help.",
              "I forgot it yesterday, which might be why."]),
            ("Can you keep fluids down?",
             ["Sips of water, yes. Food is harder.", "Mostly. Ginger tea helps.", "Not really, everything comes back up."]),
            ("Any stomach pain or fever with the nausea?",
             ["No fever. {T:Temperature 36.8} this morning.", "Some cramping, maybe {T:4/10}.", "Feverish last night, {T:38.2} on the thermometer."]),
            ("How is your nausea on a scale of one to five?",
             ["{T:Nausea 3/5} today.", "I'd say {T:4 out of 5}.", "Two, better than yesterday."]),
        ],
    },
    "appetite": {
        "intro": [
            "I've lost my appetite completely. Nothing tastes right.",
            "I'm not eating much. Everything tastes like metal.",
            "My appetite is poor — I manage a few spoonfuls and I'm full.",
            "I don't feel hungry at all. I'm forcing myself to eat.",
        ],
        "pairs": [
            ("How is your appetite on a scale of one to five?",
             ["{T:2 out of 5}, honestly.", "About two.", "One on a bad day."]),
            ("Have you lost any weight recently?",
             ["My weight is {T:59 kg}, down from {T:63 kg}.", "About {T:2 kg} in two weeks.", "I haven't weighed myself."]),
            ("What have you managed to eat today?",
             ["Congee and half a banana.", "Some soup and a few biscuits.", "Toast this morning, nothing since."]),
            ("Are you having any mouth sores or trouble swallowing?",
             ["A few mouth ulcers, which makes it worse.", "No sores, just no taste.", "Swallowing is fine."]),
            ("Have you tried smaller, more frequent meals?",
             ["Yes, {T:six small meals} instead of three.", "I try but even small portions are a struggle.", "I'll try that."]),
        ],
    },
    "cough": {
        "intro": [
            "I've had a cough for about a week that won't settle.",
            "The cough keeps me up at night. It's mostly dry.",
            "I'm coughing up some phlegm, yellowish.",
            "A nagging cough since the weekend, and my chest feels tight.",
        ],
        "pairs": [
            ("Is the cough dry or are you bringing anything up?",
             ["Mostly dry, occasionally a little clear phlegm.", "Wet, with yellow phlegm in the mornings.", "Dry and tickly."]),
            ("Any fever, chest pain or shortness of breath?",
             ["No fever. Slight chest ache when I cough, {T:3/10}.", "A bit breathless climbing stairs.",
              "I had a temperature of {T:37.9} last night."]),
            ("How often are you coughing?",
             ["Every few minutes in the evening.", "Constantly at night, less in the day.", "Maybe {T:once an hour}."]),
            ("Have you taken anything for it?",
             ["Just {T:Panadol} and honey.", "Some cough syrup, {T:10 ml} at night.", "Nothing yet."]),
            ("Has anyone at home been unwell?",
             ["My grandson had a cold last week.", "No, everyone is fine.", "My husband has a sniffle."]),
        ],
    },
    "pain": {
        "intro": [
            "The pain in my back has got worse. It's hard to sit.",
            "I've got pain in my hip that wakes me at night.",
            "My bones ache all over, especially my legs.",
            "The pain is back, mainly in my lower back and ribs.",
        ],
        "pairs": [
            ("How would you rate the pain on a scale of 0 to 10?",
             ["{T:Pain 7/10} at worst, {T:4/10} with the tablets.", "About {T:6 out of 10}.", "Eight when I move."]),
            ("Where exactly is the pain?",
             ["Lower back, spreading to the left hip.", "Right ribs and under the shoulder blade.", "Both knees and my hips."]),
            ("What are you taking for it?",
             ["{T:Oxycodone} {T:5 mg} when it's bad, and {T:paracetamol}.", "{T:Panadol}, {T:2 tablets} every {T:6 hours}.",
              "{T:Morphine} syrup at night."]),
            ("Does anything make it better or worse?",
             ["Lying flat helps. Sitting makes it worse.", "Heat packs help a little.", "Walking is agony, resting is okay."]),
            ("Any numbness, weakness or trouble passing urine?",
             ["Some tingling in my left foot.", "No, nothing like that.", "My legs feel weak but no numbness."]),
        ],
    },
}

TREATMENT_CONTEXT = [
    "I'm on my third cycle of chemo.",
    "I finished radiotherapy {T:two weeks ago}.",
    "I'm still on {T:Tamoxifen} every morning.",
    "They switched me from {T:Letrozole} to {T:Anastrozole} last month.",
    "{T:CA-125 was 35} at the last check.",
    "{T:CEA 4.2} last time, which they said was fine.",
    "My {T:BP 120/80} was normal at the clinic.",
    "I'm {T:3 weeks} post-op now.",
    "I had {T:SpO2 95%} on the machine.",
    "Round {T:2 of 6} done.",
    "I take {T:500mg} twice a day.",
    "It's been {T:10 days} since the last cycle.",
    "I've been on the {T:Herceptin} since spring.",
    "{T:Yesterday} and {T:3 days ago} were the worst days.",
    "{T:Last week} I felt almost normal.",
]

# --- identifier sentences (user turns) ------------------------------------------------------
IDENT = {
    "PERSON_REL": [
        "My {kin} {rel} drove me to the hospital.",
        "My {kin} {rel} has been cooking for me.",
        "My {kin}, {rel}, stays over most nights now.",
        "My {kin} {rel} thinks I should ring the clinic.",
        "Luckily my {kin} {rel} lives nearby.",
    ],
    "PERSON_REL_AGAIN": [
        "{rel_again} came again {T:yesterday}.",
        "I told {rel_again} about the pain and now I'm being nagged to call.",
        "{rel_again} is worried about me.",
        "Thankfully {rel_again} does the shopping.",
    ],
    "PERSON_OTHER_TITLE": [
        "{other_title} said I should rest and push fluids.",
        "I saw {other_title} at the clinic and mentioned it.",
        "{other_title} wants to see me again if it continues.",
        "The nurse, {other_title}, changed the dressing.",
    ],
    "PERSON_OTHER_AGAIN": [
        "{other_again} also said to watch my temperature.",
        "I'll ring {other_again} if it gets worse.",
    ],
    "PERSON_DOCTOR": [
        "{doctor_title} said to keep an eye on it.",
        "I'm due to see {doctor_full} next week.",
        "{doctor_title} told me to call if the fever comes back.",
        "I asked {doctor_full} about the dose.",
    ],
    "PERSON_PATIENT": [
        "It's {patient_given} here, by the way.",
        "This is {patient_full} checking in.",
        "My login is {username} if you need to find my record.",
        "Just confirming it's {patient_part} speaking.",
    ],
    "FACILITY": [
        "I had my chemo at {facility}.",
        "The nurse at {facility} told me to rest.",
        "I'm being treated at {facility}.",
        "I went to {facility} for the dressing.",
        "They sent me to {facility} for the scan.",
    ],
    "PLACE": [
        "I live in {place}.",
        "Getting to {place} by bus is exhausting.",
        "I walked to {mtr} station and had to sit down.",
        "We moved to {estate} last year.",
        "I live on {street}, near the market.",
        "The clinic is in {place}, a long way from home.",
    ],
    "ADDRESS": [
        "My address is {address}, in case you need it.",
        "I'm at {address} if someone needs to visit.",
        "Send it to {address}.",
    ],
    "ORG": [
        "I still work part-time at {org}.",
        "My son works for {org}.",
        "I used to work at {org} before I retired.",
        "My daughter teaches at {org}.",
    ],
    "OCCUPATION": [
        "I'm a retired {occ} so I'm used to long hours.",
        "I was {a_occ} for thirty years.",
        "I work as {a_occ} and I can't keep up.",
        "I used to be {a_occ}, so I know what tired means.",
    ],
    "DATE_PAST": [
        "The pain started on {date_past}.",
        "It got worse on {date_past}.",
        "My last chemo was on {date_past}.",
        "I was admitted on {date_past} for two nights.",
        "Since {weekday_past} I've barely eaten.",
        "{weekday_past} I stayed in bed all day.",
    ],
    "DATE_FUTURE": [
        "My next appointment is {weekday_future}.",
        "I'm seeing the oncologist {weekday_future}.",
        "My scan is booked for {date_future}.",
        "I'm going in {weekday_future} for bloods.",
        "The next cycle is on {date_future}.",
    ],
    "AGE": [
        "I'm {age_num} and I've never felt this tired.",
        "I'm {age_phrase}, so recovery is slower.",
        "Now that I'm {age_num}, I expected some aches, but not this.",
        "I was born in {birth_year}, so I'm no spring chicken.",
    ],
    "DOB": [
        "My date of birth is {dob}, if that helps you find me.",
        "I was born on {dob}.",
        "For the record, DOB {dob}.",
        "My birthday is {dob}.",
    ],
    "ID": [
        "My HKID is {hkid}.",
        "My medical record number is {mrn}.",
        "My passport number is {passport}, for the insurance form.",
        "The insurance card number is {card}.",
        "HKID {hkid}, in case the nurse asks.",
    ],
    "PHONE": [
        "You can call me on {phone}.",
        "My number is {phone}.",
        "WhatsApp me at {phone}.",
        "Call my son on {phone_other} if I don't answer.",
        "My daughter's number is {phone_other}.",
        "You can reach me at {phone_852}.",
    ],
    "EMAIL": [
        "Send the report to {email}.",
        "My email is {email}.",
        "Email my son at {email_other}.",
    ],
    "HANDLE": [
        "My Instagram account is {handle}.",
        "You can find me on Telegram: {handle}.",
        "My WhatsApp id is {handle}.",
        "I post updates as {handle_at} sometimes.",
    ],
    "URL": [
        "I read about it on {url}.",
        "I keep a diary at {url}.",
        "There's a support group at {url}.",
    ],
}

TRAPS = [
    "I take {T:Tamoxifen} every morning.",
    "They switched me from {T:Letrozole} to {T:Anastrozole}.",
    "{T:Oxycodone} makes me drowsy.",
    "{T:Panadol} doesn't touch it.",
    "{T:CA-125 was 35} last time.",
    "My {T:temperature was 38.2} this morning.",
    "{T:BP 120/80} at the clinic.",
    "My {T:pressure was 98/60} when I stood up.",
    "{T:Sugar was 6.5} before breakfast.",
    "{T:SpO2 95%} on the machine.",
    "My weight is {T:59 kg}.",
    "I took {T:2 tablets}.",
    "{T:500mg} twice a day.",
    "{T:5 mg} of morphine at night.",
    "{T:Pain is 7/10}.",
    "{T:Fatigue 4 out of 5}.",
    "It's been {T:3 days}.",
    "I've had it for {T:two weeks}.",
    "{T:Yesterday} was worse.",
    "It started {T:3 days ago}.",
    "{T:Last week} I was fine.",
    "I'm {T:6 weeks} into treatment.",
    "I'm {T:59 kg} now.",
    "I slept {T:4 hours}.",
    "{N:Dr Pepper} is the only thing I can drink.",
    "The queue at the {T:hospital} was {T:long}.",
    "I went to the {T:clinic} for a {T:demo} of the pump.",
]

# Over-redaction traps where a facility name contains a person-like or place-like word.
FACILITY_TRAP_SENTENCES = [
    "My {kin} {rel} drove me to {facility_trap}.",
    "I was treated at {facility_trap}.",
]
FACILITY_TRAPS = ["Queen Mary Hospital", "Sha Tin Hospital", "Princess Margaret Hospital", "Queen Elizabeth Hospital"]

# --- questionnaire free text (the six "Please specify" answers) ------------------------------
QUESTIONNAIRE_SECTIONS = [
    ("Appetite Loss", "What led to your poor appetite? (Choose all that apply)", "Other"),
    ("Constipation/Diarrhoea", "How would you describe it/them? (Choose all that apply)", "Other"),
    ("Dyspnea", "What activities usually cause shortness of breath? (Choose all that apply)", "Other"),
    ("Dysuria", "Where do you feel pain/a burning sensation when you urinate? (Choose all that apply)", "Other"),
    ("Insomnia", "Have these symptoms affected your quality of sleep? (Choose all that apply)", "Other"),
    ("Hot Flashes", "Do they come with the following symptoms? (Choose all that apply)", "Other"),
]
QUESTIONNAIRE_FREE_TEXT = {
    "Appetite Loss": [
        "Everything tastes metallic since the chemo at {facility}.",
        "My {kin} {rel} cooks but I can't face it.",
        "Mouth ulcers, and the {T:Tamoxifen} makes me queasy.",
        "No appetite since {date_past}.",
    ],
    "Constipation/Diarrhoea": [
        "Loose stools {T:3 times} a day since {date_past}.",
        "Hard and painful, the {T:Oxycodone} makes it worse.",
        "Alternating, {doctor_title} said to take the sachets.",
        "Watery, I had to leave {org} early twice.",
    ],
    "Dyspnea": [
        "Walking up the hill from {mtr} station.",
        "Carrying shopping home to {estate}.",
        "Climbing the stairs at {address}.",
        "Talking for a long time, my {T:SpO2 was 95%}.",
    ],
    "Dysuria": [
        "Lower belly, started {weekday_past}.",
        "Burning at the end, {other_title} gave me antibiotics.",
        "Right side, worse at night.",
        "All over the bladder area, I'm {age_num} and never had this.",
    ],
    "Insomnia": [
        "The pain wakes me, {T:7/10} at night.",
        "Hot flushes and worrying about the scan on {date_future}.",
        "My {kin} {rel} snores and I can't get back to sleep.",
        "Coughing. I moved to the sofa in {place}, it's quieter.",
    ],
    "Hot Flashes": [
        "Sweats and a racing heart, {T:pulse 110} once.",
        "Dizziness, my {T:pressure was 98/60}.",
        "Since I started {T:Letrozole}.",
        "Palpitations, I mentioned it to {doctor_full}.",
    ],
}
QUESTIONNAIRE_FLAGS = [
    "Fatigue: How is the general level of fatigue during the past 24 hours rated Severe ({T:4/5})",
    "Nausea/Vomiting: frequency {T:6+ times}",
    "Appetite Loss: How is your appetite on a scale of one to five rated Very Poor ({T:5/5})",
    "Insomnia: Around how many hours have you slept last night in total {T:3h} (critically low)",
]

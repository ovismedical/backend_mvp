"""Layer 6: date and age generalisation, DOB, clinical-number guard."""

from datetime import date, datetime, timezone

import pytest

from app.inference.scrub import KnownIdentifiers, ScrubContext
from app.inference.scrub.generalise import age_band, clinical_guarded, generalise_spans, offset_note, zh_number

NOW = datetime(2026, 9, 9, 10, 0)          # a Wednesday
DOB = date(1966, 8, 22)


def gen(text, now=NOW, dob=None, tz="Asia/Hong_Kong"):
    return [(text[s.start:s.end], s.cls, s.token or s.note) for s in generalise_spans(text, now=now, tz=tz, dob=dob)]


def scrub(text, now=NOW, dob=None):
    return ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="u1", dob=dob)).scrub(text, now=now).text


class TestDatesEnglish:

    @pytest.mark.parametrize("text,expected", [
        ("started on 5 March", [("5 March", "DATE", "188 days ago")]),
        ("March 5 it began", [("March 5", "DATE", "188 days ago")]),
        ("on 5/3", [("5/3", "DATE", "188 days ago")]),           # DD/MM: 5 March, not 3 May
        ("05/03/2026", [("05/03/2026", "DATE", "188 days ago")]),
        ("2026-03-05", [("2026-03-05", "DATE", "188 days ago")]),
        ("my surgery was on 5 March 2024", [("5 March 2024", "DATE", "~2 years ago")]),
        ("August 22, 1966", [("August 22, 1966", "DATE", "~60 years ago")]),
        ("Aug 22 1966", [("Aug 22 1966", "DATE", "~60 years ago")]),
        ("22 August 1966", [("22 August 1966", "DATE", "~60 years ago")]),
        ("22.08.1966", [("22.08.1966", "DATE", "~60 years ago")]),
        ("22/8/66", [("22/8/66", "DATE", "~60 years ago")]),
        ("1966/08/22", [("1966/08/22", "DATE", "~60 years ago")]),
        ("8 Sept", [("8 Sept", "DATE", "1 day ago")]),
        ("today 9 September", [("9 September", "DATE", "today")]),
        ("appointment 15 September", [("15 September", "DATE", "in 6 days")]),
    ])
    def test_forms(self, text, expected):
        assert gen(text) == expected

    def test_weekdays_past_last_next(self):
        assert gen("on Tuesday I felt worse") == [("Tuesday", "DATE", "1 day ago")]
        assert gen("since Wednesday") == [("Wednesday", "DATE", "today")]
        assert gen("last Monday") == [("last Monday", "DATE", "9 days ago")]
        assert gen("this Friday I see the oncologist") == [("this Friday", "DATE", "next Friday")]
        assert gen("I'm seeing the oncologist this Friday") == [("this Friday", "DATE", "next Friday")]
        assert gen("next Tuesday") == [("next Tuesday", "DATE", "next Tuesday")]
        assert gen("my appointment is on Friday") == [("Friday", "DATE", "next Friday")]
        assert gen("I will rest now. Friday I felt sick") == [("Friday", "DATE", "5 days ago")]   # cue does not cross a sentence

    def test_weekday_abbreviations_need_capital_mid_sentence(self):
        assert gen("I sat down on Sat") == [("Sat", "DATE", "4 days ago")]
        assert gen("Sat down and rested") == []
        assert gen("the sun was out") == []

    def test_relative_phrases_untouched(self):
        assert gen("yesterday, 3 days ago, last week, next week, two weeks ago") == []

    def test_no_now_gives_bare_token(self):
        assert scrub("started on 5 March", now=None) == "started on [DATE_1]"
        assert scrub("this Friday", now=None) == "[DATE_1 · next Friday]"

    def test_timezone_aware_now_is_converted(self):
        # 2026-09-09 18:30 UTC is already 2026-09-10 in Hong Kong -> "5 March" is 189 days ago
        aware = datetime(2026, 9, 9, 18, 30, tzinfo=timezone.utc)
        assert gen("5 March", now=aware) == [("5 March", "DATE", "189 days ago")]

    def test_same_date_reuses_token_index(self):
        assert scrub("5 March and again 5 March and 6 March") == \
            "[DATE_1 · 188 days ago] and again [DATE_1 · 188 days ago] and [DATE_2 · 187 days ago]"


class TestDatesChinese:

    @pytest.mark.parametrize("text,expected", [
        ("3月5日", [("3月5日", "DATE", "188 days ago")]),
        ("三月五日", [("三月五日", "DATE", "188 days ago")]),
        ("2024年3月5日", [("2024年3月5日", "DATE", "~2 years ago")]),
        ("1966年8月22日", [("1966年8月22日", "DATE", "~60 years ago")]),
        ("66年8月22日", [("66年8月22日", "DATE", "~60 years ago")]),
        ("5號", [("5號", "DATE", "4 days ago")]),
        ("15號覆診", [("15號", "DATE", "in 6 days")]),
        ("星期二", [("星期二", "DATE", "1 day ago")]),
        ("週二", [("週二", "DATE", "1 day ago")]),
        ("禮拜二", [("禮拜二", "DATE", "1 day ago")]),
        ("上星期一", [("上星期一", "DATE", "9 days ago")]),
        ("上個禮拜三", [("上個禮拜三", "DATE", "7 days ago")]),
        ("覆診係下星期二", [("下星期二", "DATE", "next Tuesday")]),
        ("下個禮拜五", [("下個禮拜五", "DATE", "next Friday")]),
    ])
    def test_forms(self, text, expected):
        assert gen(text) == expected

    def test_relative_and_recurring_untouched(self):
        assert gen("昨日 前日 上星期 三日前 琴日 尋日 聽日 下星期") == []
        assert gen("一星期三次") == []
        assert gen("每星期二") == []

    def test_day_number_exclusions(self):
        for t in ["掛咗8號風球", "搭5號巴士", "3號線", "12號房", "5號座", "3號樓", "8號室", "電話 9123 4567 號碼 5號", "45號"]:
            assert gen(t) == [], t


class TestAges:

    @pytest.mark.parametrize("text,expected", [
        ("I'm 59", "I'm [AGE · 50s]"),
        ("I am 59 and tired", "I am [AGE · 50s] and tired"),
        ("59 years old", "[AGE · 50s]"),
        ("a 59-year-old woman", "a [AGE · 50s] woman"),
        ("aged 59", "aged [AGE · 50s]"),
        ("59歲", "[AGE · 50s]"),
        ("我今年59", "我今年[AGE · 50s]"),
        ("我今年59歲", "我今年[AGE · 50s]"),
        ("五十九歲", "[AGE · 50s]"),
        ("I'm 95", "I'm [AGE · 90+]"),
        ("九十二歲", "[AGE · 90+]"),
        ("I'm 8", "I'm [AGE · under 10]"),
        ("I was born in 1966", "I was born in [AGE · 60s]"),
        ("1966年出世", "[AGE · 60s]年出世"),
        ("生於1966", "生於[AGE · 60s]"),
        ("my husband is 62", "my husband is 62"),        # no cue: left alone (not identifying on its own)
    ])
    def test_forms(self, text, expected):
        assert scrub(text) == expected

    def test_age_band(self):
        assert age_band(59) == "50s" and age_band(90) == "90+" and age_band(101) == "90+" and age_band(3) == "under 10"

    def test_zh_number(self):
        assert zh_number("五十九") == 59 and zh_number("十五") == 15 and zh_number("二十") == 20
        assert zh_number("廿五") == 25 and zh_number("卅") == 30 and zh_number("一百") == 100
        assert zh_number("一九六六") == 1966 and zh_number("兩") == 2 and zh_number("x") is None


class TestClinicalGuard:

    @pytest.mark.parametrize("text", [
        "I'm 59 kg", "59 kg", "59 days", "took 2 tablets", "5 g of salt", "3 days", "pain 7/10", "fatigue 4 out of 5",
        "CA-125 was 35", "temperature 38.2", "BP 120/80", "98/60", "血壓 98/60", "my pressure was 120/80",
        "I'm 3 days into chemo", "I'm 2 weeks post-op", "sugar 5/3 this morning", "score 8/10", "I'm 38 °C",
        "體溫38度", "I am 7/10 today", "dose 5 mg", "2 pills",
    ])
    def test_guarded(self, text):
        assert gen(text) == [], text

    def test_guard_helper(self):
        assert clinical_guarded("59 kg", 0, 2)
        assert clinical_guarded("pain 7", 5, 6)
        assert clinical_guarded("血壓 98", 3, 5)
        assert not clinical_guarded("I'm 59", 4, 6)


class TestDob:

    @pytest.mark.parametrize("text", [
        "22/08/1966", "08/22/1966", "1966-08-22", "August 22, 1966", "22.08.1966", "1966年8月22日", "8月22日",
        "Aug 22 1966", "22 August 1966", "22/8/66", "1966/08/22", "66年8月22日", "八月二十二日",
    ])
    def test_matches_become_dob(self, text):
        assert gen(text, dob=DOB) == [(text, "DOB", "[DOB]")]
        assert scrub(text, dob=DOB) == "[DOB]"

    def test_non_matching_dates_stay_dates(self):
        assert gen("22/09/1966", dob=DOB) == [("22/09/1966", "DATE", "~59 years ago")]
        assert gen("1966年8月23日", dob=DOB) == [("1966年8月23日", "DATE", "~60 years ago")]
        assert gen("22號", dob=DOB) == [("22號", "DATE", "18 days ago")]     # bare day is never the DOB

    def test_offset_note(self):
        today = date(2026, 9, 9)
        assert offset_note(today, today) == "today"
        assert offset_note(date(2026, 9, 8), today) == "1 day ago"
        assert offset_note(date(2024, 9, 10), today) == "729 days ago"
        assert offset_note(date(2024, 3, 5), today) == "~2 years ago"
        assert offset_note(date(2026, 9, 10), today) == "in 1 day"
        assert offset_note(date(2026, 9, 20), today) == "in 11 days"
        assert offset_note(date(2030, 1, 1), today) == "in ~3 years"

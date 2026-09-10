"""Layer 2: deterministic patterns and suffix rules."""

import pytest

from app.inference.scrub.patterns import pattern_spans


def spans(text):
    return sorted({(text[s.start:s.end], s.cls) for s in pattern_spans(text)})


def classes_for(text, cls):
    return [t for t, c in spans(text) if c == cls]


class TestDirectIdentifiers:

    def test_hkid(self):
        assert classes_for("my HKID is A123456(7) give it to the nurse", "ID") == ["A123456(7)"]
        assert classes_for("身份證AB1234567", "ID") == ["AB1234567"]
        assert classes_for("A1234567", "ID") == ["A1234567"]
        assert classes_for("CA-125 was 35", "ID") == []
        assert classes_for("HA12345678 no cue", "ID") == []

    @pytest.mark.parametrize("text,expected", [
        ("my hkid is a123456(7)", "a123456(7)"),          # lowercase
        ("HKID A123456（7）", "A123456（7）"),              # fullwidth parentheses
        ("身份證號碼：A123456（7）。", "A123456（7）"),
        ("ID A123456 (7)", "A123456 (7)"),                # space before the bracket
        ("A123456-7", "A123456-7"),                       # dash before the check digit
        ("A123456/7", "A123456/7"),                       # slash before the check digit
        ("HKID A123456 7", "A123456 7"),
        ("hkid ab123456(a)", "ab123456(a)"),              # lowercase check letter
    ])
    def test_hkid_separator_and_case_variants(self, text, expected):
        """Every one of these used to leave the six digits in clear."""
        assert classes_for(text, "ID") == [expected]
        assert "123456" not in text.replace(expected, "")

    @pytest.mark.parametrize("text", [
        "took 5 mg", "BP 120/80", "CA-125 is 123456", "pain 7/10", "2 tablets 3 times",
    ])
    def test_hkid_rule_leaves_clinical_numbers_alone(self, text):
        assert classes_for(text, "ID") == []

    def test_phone_with_prefix_or_cue(self):
        assert classes_for("+852 9123 4567", "PHONE") == ["+852 9123 4567"]
        assert classes_for("call 9123 4567 day or night", "PHONE") == ["9123 4567"]
        assert classes_for("WhatsApp 6123 4567 got it?", "PHONE") == ["6123 4567"]
        assert classes_for("my number is 91234567", "PHONE") == ["91234567"]
        assert classes_for("電話 91234567", "PHONE") == ["91234567"]
        assert classes_for("致電 2123-4567", "PHONE") == ["2123-4567"]

    def test_phone_cues_cover_the_everyday_verbs(self):
        """"text", "ring", "SMS" and 搵 are how a patient actually hands over a number."""
        assert classes_for("Text me 6234 5678", "PHONE") == ["6234 5678"]
        assert classes_for("Ring me at 62345678", "PHONE") == ["62345678"]
        assert classes_for("SMS me on 6234 5678", "PHONE") == ["6234 5678"]
        assert classes_for("please message 9876 5432", "PHONE") == ["9876 5432"]
        assert classes_for("搵我老公 5333 4444", "PHONE") == ["5333 4444"]
        assert classes_for("有短訊 5333 4444", "PHONE") == ["5333 4444"]

    def test_bare_eight_digits_without_cue_untouched(self):
        assert classes_for("the code was 91234567 on the box", "PHONE") == []
        assert classes_for("12345678", "PHONE") == []            # leading 1 is not a HK number

    def test_email_url_handle(self):
        assert classes_for("mail grace.tam@example.com", "EMAIL") == ["grace.tam@example.com"]
        assert classes_for("see https://example.com/a?b=1 and www.hospital.org.hk/page", "URL") == [
            "https://example.com/a?b=1", "www.hospital.org.hk/page"]
        assert classes_for("visit example.com.hk today", "URL") == ["example.com.hk"]
        assert classes_for("follow @grace_tam88 now", "HANDLE") == ["@grace_tam88"]
        assert classes_for("my whatsapp: grace_tam88", "HANDLE") == ["grace_tam88"]
        assert classes_for("IG name is gracetam", "HANDLE") == ["gracetam"]
        assert classes_for("WeChat ID: gt1966", "HANDLE") == ["gt1966"]
        assert classes_for("微信：gracetam", "HANDLE") == ["gracetam"]

    def test_handle_rule_does_not_eat_ordinary_words_or_phones(self):
        assert classes_for("WhatsApp me when you can", "HANDLE") == []
        assert classes_for("whatsapp: 91234567", "HANDLE") == []
        assert classes_for("whatsapp: 91234567", "PHONE") == ["91234567"]
        assert classes_for("e.g. this one", "URL") == []

    def test_passport_and_hrp_only_with_cue(self):
        assert classes_for("passport K12345678", "ID") == ["K12345678"]
        assert classes_for("回鄉證 H1234567890", "ID") == ["H1234567890"]
        assert classes_for("K12345678 alone", "ID") == []

    def test_policy_mrn_with_cue(self):
        assert classes_for("policy no. AB-1234567", "ID") == ["AB-1234567"]
        assert classes_for("medical record HKA12345", "ID") == ["HKA12345"]
        assert classes_for("病歷編號 XYZ-998877", "ID") == ["XYZ-998877"]
        assert classes_for("MRN 1234567", "ID") == ["1234567"]
        assert classes_for("AB-1234567 without cue", "ID") == []

    def test_card_numbers_need_luhn(self):
        assert classes_for("card 4111 1111 1111 1111", "ID") == ["4111 1111 1111 1111"]
        assert classes_for("4111111111111111", "ID") == ["4111111111111111"]
        assert classes_for("4111 1111 1111 1112", "ID") == []

    def test_plates_with_cue(self):
        assert classes_for("my car plate is AB 1234", "ID") == ["AB 1234"]
        assert classes_for("車牌 XY123", "ID") == ["XY123"]
        assert classes_for("BP 120 reading", "ID") == []


class TestAddressesAndStreets:

    def test_english_addresses(self):
        # several address rules may fire; the resolver keeps the longest, so the full span must be among them
        assert "Flat 5A, 12/F, Block 3, Wah Fu Estate" in classes_for("Flat 5A, 12/F, Block 3, Wah Fu Estate", "ADDRESS")
        assert classes_for("Room 1203, Tower 2, Laguna City", "ADDRESS") == ["Room 1203, Tower 2, Laguna City"]
        assert "Unit 7B, 3rd Floor, 88 Nathan Road" in classes_for("Unit 7B, 3rd Floor, 88 Nathan Road", "ADDRESS")
        assert classes_for("I live at 123 Nathan Road, Jordan", "ADDRESS") == ["123 Nathan Road"]
        assert classes_for("12/F Block 3 Wah Fu Estate", "ADDRESS") == ["12/F Block 3 Wah Fu Estate"]
        assert classes_for("Block 3, Wah Fu Estate", "ADDRESS") == ["Block 3, Wah Fu Estate"]

    def test_english_streets_and_estates_are_places(self):
        assert classes_for("I walked along Nathan Road to Kimberley Street", "PLACE") == ["Kimberley Street", "Nathan Road"]
        assert classes_for("Queen's Road Central", "PLACE") == ["Queen's Road Central"]
        assert classes_for("she lives in Kornhill Gardens", "PLACE") == ["Kornhill Gardens"]
        assert classes_for("the road was long", "PLACE") == []
        assert classes_for("The Supreme Court ruled", "PLACE") == []

    def test_abbreviated_street_types(self):
        """"Rd", "Ave", "St." used to leave the whole address in clear."""
        assert classes_for("I live at 12 Nathan Rd", "ADDRESS") == ["12 Nathan Rd"]
        assert "5 Shing Yip St" in classes_for("5 Shing Yip St.", "ADDRESS")[0]
        assert classes_for("88 Waterloo Ave, Kowloon", "ADDRESS") == ["88 Waterloo Ave"]
        assert classes_for("work on Queen's Rd C", "PLACE") == ["Queen's Rd C"]
        assert classes_for("Hennessy Rd", "PLACE") == ["Hennessy Rd"]

    @pytest.mark.parametrize("text", [
        "Dr Wong at St Paul's Hospital", "1st floor", "Take 2 tablets Dr said", "St Paul's is nearby",
    ])
    def test_ambiguous_abbreviations_need_a_house_number(self, text):
        """"St"/"Dr"/"Ln" are only street words behind a house number, never on their own."""
        assert classes_for(text, "PLACE") == [] and classes_for(text, "ADDRESS") == []

    def test_chinese_addresses(self):
        assert classes_for("我住喺太古城5座12樓A室", "ADDRESS") == ["太古城5座12樓A室"]
        assert classes_for("我住華富邨華清樓3樓305室", "ADDRESS") == ["華富邨華清樓3樓305室"]
        assert classes_for("嘉湖山莊第3座12樓A室", "ADDRESS") == ["嘉湖山莊第3座12樓A室"]
        assert classes_for("彌敦道123號", "ADDRESS") == ["彌敦道123號"]
        assert classes_for("美孚新邨東座8樓", "ADDRESS") == ["美孚新邨東座8樓"]

    def test_chinese_streets_need_cue_and_avoid_common_words(self):
        assert classes_for("我住喺彌敦道", "PLACE") == ["彌敦道"]
        assert classes_for("去英皇道附近", "PLACE") == ["英皇道"]
        for t in ["我知道呼吸道有問題", "食道癌", "味道好怪", "我行街", "出街食飯", "直徑兩厘米", "一路都好"]:
            assert classes_for(t, "PLACE") == [], t


class TestFacilityAndOrgSuffixes:

    def test_english_facility_suffix(self):
        assert classes_for("I went to Queen Mary Hospital yesterday", "FACILITY") == ["Queen Mary Hospital"]
        assert classes_for("Prince of Wales Hospital", "FACILITY") == ["Prince of Wales Hospital"]
        assert classes_for("St. Paul's Hospital", "FACILITY") == ["St. Paul's Hospital"]
        assert classes_for("the Central Medical Centre is far", "FACILITY") == ["Central Medical Centre"]
        assert classes_for("Yesterday Queen Mary Hospital called", "FACILITY") == ["Queen Mary Hospital"]

    def test_generic_english_facility_words_untouched(self):
        for t in ["The Hospital called", "a private hospital", "Private Hospital", "the clinic", "Hospital Authority"]:
            assert classes_for(t, "FACILITY") == [], t

    def test_chinese_facility_suffix_trims_function_words(self):
        assert classes_for("我去咗瑪麗醫院", "FACILITY") == ["瑪麗醫院"]
        assert classes_for("昨日我去咗基督教聯合醫院", "FACILITY") == ["基督教聯合醫院"]
        assert classes_for("佢喺威爾斯親王醫院做手術", "FACILITY") == ["威爾斯親王醫院"]
        assert classes_for("東區醫院", "FACILITY") == ["東區醫院"]
        for t in ["去醫院", "間醫院", "私家醫院", "醫院", "呢間診所", "出院", "睇醫生"]:
            assert classes_for(t, "FACILITY") == [], t

    def test_org_suffixes(self):
        assert classes_for("I work at HSBC Bank and AIA Insurance", "ORG") == ["AIA Insurance", "HSBC Bank"]
        assert classes_for("my son is at Diocesan Boys' School", "ORG") == ["Diocesan Boys' School"]
        assert classes_for("我喺中文大學做嘢，喺匯豐銀行開戶", "ORG") == ["中文大學", "匯豐銀行"]
        for t in ["我讀緊大學", "我間公司", "保險公司", "I go to church", "health insurance is costly"]:
            assert classes_for(t, "ORG") == [], t


class TestNoClinicalGuardAtLayer2:

    @pytest.mark.parametrize("text,cls,expected", [
        ("call 9123 4567 day or night", "PHONE", ["9123 4567"]),
        ("my HKID is A123456(7) give it to the nurse", "ID", ["A123456(7)"]),
        ("WhatsApp 6123 4567 g", "PHONE", ["6123 4567"]),
    ])
    def test_identifiers_followed_by_unit_like_words_still_scrubbed(self, text, cls, expected):
        assert classes_for(text, cls) == expected

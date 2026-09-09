"""Layer 3a: Hong Kong gazetteers — data files and matching behaviour."""

import pytest

from app.inference.scrub import KnownIdentifiers, ScrubContext
from app.inference.scrub.gazetteers import (
    DATA_DIR, GAZETTEER_FILES, common_words, gazetteer_spans, kinship_words, load_list, occupations, org_suffixes,
    surnames_zh, titles,
)
from app.inference.scrub.tokens import has_cjk

REQUIRED_FILES = ("hospitals", "districts", "mtr", "estates", "orgs_suffixes", "occupations", "kinship", "titles",
                  "surnames_zh", "common_words")


def places(text):
    return sorted({(text[s.start:s.end], s.cls) for s in gazetteer_spans(text)})


class TestDataFiles:

    @pytest.mark.parametrize("name", REQUIRED_FILES)
    def test_file_exists_and_loads(self, name):
        assert (DATA_DIR / f"{name}.txt").exists()
        entries = load_list(name)
        assert len(entries) > 5
        assert not any(e.startswith("#") or e != e.strip() for e in entries)

    def test_comments_and_blank_lines_ignored(self):
        text = (DATA_DIR / "hospitals.txt").read_text(encoding="utf-8")
        assert text.lstrip().startswith("#")
        assert "" not in load_list("hospitals")

    def test_bilingual_coverage(self):
        for name in ("hospitals", "districts", "mtr", "estates"):
            entries = load_list(name)
            latin = [e for e in entries if not has_cjk(e)]
            cjk = [e for e in entries if has_cjk(e)]
            assert len(latin) >= 40 and len(cjk) >= 40, name

    def test_all_hospital_authority_hospitals_present(self):
        hospitals = set(load_list("hospitals"))
        for en, zh in [
            ("Queen Mary Hospital", "瑪麗醫院"), ("Prince of Wales Hospital", "威爾斯親王醫院"),
            ("Queen Elizabeth Hospital", "伊利沙伯醫院"), ("Pamela Youde Nethersole Eastern Hospital", "東區尤德夫人那打素醫院"),
            ("Princess Margaret Hospital", "瑪嘉烈醫院"), ("Tuen Mun Hospital", "屯門醫院"),
            ("United Christian Hospital", "基督教聯合醫院"), ("Kwong Wah Hospital", "廣華醫院"),
            ("Ruttonjee Hospital", "律敦治醫院"), ("Caritas Medical Centre", "明愛醫院"),
            ("North District Hospital", "北區醫院"), ("Tseung Kwan O Hospital", "將軍澳醫院"),
            ("Yan Chai Hospital", "仁濟醫院"), ("Pok Oi Hospital", "博愛醫院"), ("Tin Shui Wai Hospital", "天水圍醫院"),
            ("Alice Ho Miu Ling Nethersole Hospital", "雅麗氏何妙齡那打素醫院"), ("Kwai Chung Hospital", "葵涌醫院"),
            ("Castle Peak Hospital", "青山醫院"), ("Shatin Hospital", "沙田醫院"), ("Grantham Hospital", "葛量洪醫院"),
            ("Tang Shiu Kin Hospital", "鄧肇堅醫院"), ("Tung Wah Hospital", "東華醫院"), ("Kowloon Hospital", "九龍醫院"),
            ("Hong Kong Children's Hospital", "香港兒童醫院"), ("Haven of Hope Hospital", "靈實醫院"),
            ("North Lantau Hospital", "北大嶼山醫院"), ("Tai Po Hospital", "大埔醫院"), ("Siu Lam Hospital", "小欖醫院"),
            ("Hong Kong Buddhist Hospital", "香港佛教醫院"), ("Our Lady of Maryknoll Hospital", "聖母醫院"),
            # private
            ("Hong Kong Sanatorium & Hospital", "養和醫院"), ("Hong Kong Adventist Hospital", "港安醫院"),
            ("Matilda International Hospital", "明德國際醫院"), ("St. Teresa's Hospital", "聖德肋撒醫院"),
            ("St. Paul's Hospital", "聖保祿醫院"), ("Gleneagles Hospital Hong Kong", "港怡醫院"), ("Union Hospital", "仁安醫院"),
            ("Canossa Hospital", "嘉諾撒醫院"), ("Evangel Hospital", "播道醫院"), ("Precious Blood Hospital", "寶血醫院"),
            ("CUHK Medical Centre", "中大醫院"), ("Hong Kong Baptist Hospital", "香港浸信會醫院"),
        ]:
            assert en in hospitals, en
            assert zh in hospitals, zh

    def test_eighteen_districts_and_common_areas(self):
        d = set(load_list("districts"))
        for zh in ["中西區", "灣仔區", "東區", "南區", "油尖旺區", "深水埗區", "九龍城區", "黃大仙區", "觀塘區", "葵青區",
                   "荃灣區", "屯門區", "元朗區", "北區", "大埔區", "沙田區", "西貢區", "離島區"]:
            assert zh in d, zh
        for en, zh in [("Central", "中環"), ("Wan Chai", "灣仔"), ("Causeway Bay", "銅鑼灣"), ("Tsim Sha Tsui", "尖沙咀"),
                       ("Mong Kok", "旺角"), ("Sha Tin", "沙田"), ("Tuen Mun", "屯門"), ("Tseung Kwan O", "將軍澳"),
                       ("Tai Koo", "太古"), ("Happy Valley", "跑馬地"), ("Stanley", "赤柱"), ("Discovery Bay", "愉景灣")]:
            assert en in d and zh in d, (en, zh)
        assert len([e for e in d if not has_cjk(e)]) >= 90

    def test_mtr_stations(self):
        m = set(load_list("mtr"))
        for en, zh in [("Kennedy Town", "堅尼地城"), ("Admiralty", "金鐘"), ("Heng Fa Chuen", "杏花邨"), ("Lo Wu", "羅湖"),
                       ("Wu Kai Sha", "烏溪沙"), ("South Horizons", "海怡半島"), ("Sunny Bay", "欣澳"), ("Kai Tak", "啟德"),
                       ("Tuen Mun", "屯門"), ("LOHAS Park", "將軍澳")]:
            assert en in m and zh in m, (en, zh)
        assert len([e for e in m if not has_cjk(e)]) >= 90
        # generic-word stations are deliberately absent
        for absent in ("University", "大學", "Airport", "機場", "Hong Kong", "Kowloon", "Racecourse", "Olympic"):
            assert absent not in m, absent

    def test_estates(self):
        e = set(load_list("estates"))
        for en, zh in [("Tai Koo Shing", "太古城"), ("Mei Foo Sun Chuen", "美孚新邨"), ("Whampoa Garden", "黃埔花園"),
                       ("Laguna City", "麗港城"), ("Kingswood Villas", "嘉湖山莊"), ("City One", "沙田第一城"),
                       ("Heng Fa Chuen", "杏花邨"), ("South Horizons", "海怡半島"), ("Wah Fu Estate", "華富邨"),
                       ("Shek Kip Mei Estate", "石硤尾邨"), ("Choi Hung Estate", "彩虹邨"), ("LOHAS Park", "日出康城"),
                       ("Telford Gardens", "德福花園"), ("Amoy Gardens", "淘大花園"), ("Caribbean Coast", "映灣園"),
                       ("Bel-Air", "貝沙灣"), ("Belvedere Garden", "麗城花園"), ("Riviera Gardens", "海濱花園")]:
            assert en in e and zh in e, (en, zh)
        assert len([x for x in e if not has_cjk(x)]) >= 120

    def test_supporting_lists(self):
        assert {"test", "patient", "demo", "user", "admin", "alex", "jordan", "man", "men", "long", "may", "wing", "king",
                "sun", "grace", "hope", "rose", "bill", "mark", "young", "day", "will", "ray", "ping", "faith", "joy",
                "sum", "ball", "fat"} <= common_words()
        assert {"陳", "李", "黃", "歐陽", "司徒"} <= set(surnames_zh())
        assert {"daughter", "husband", "個女", "老公", "阿媽"} <= set(kinship_words())
        assert {"Dr", "Dr.", "Doctor", "Prof", "Professor", "Nurse", "Sister", "Mr", "Mrs", "Ms", "Miss", "Madam",
                "醫生", "醫師", "教授", "護士", "姑娘", "先生", "太太", "小姐", "女士", "師奶", "伯", "嬸", "叔", "姨"} <= set(titles())
        assert {"teacher", "nurse", "老師", "護士"} <= set(occupations())
        assert {"School", "Bank", "學校", "銀行"} <= set(org_suffixes())
        assert set(GAZETTEER_FILES) == {"hospitals", "districts", "mtr", "estates"}


class TestMatching:

    def test_latin_case_insensitive_whole_word_longest_first(self):
        assert places("I live in tai koo shing near Tai Koo station") == [("Tai Koo", "PLACE"), ("tai koo shing", "PLACE")]
        # a facility and the place inside it are both candidates; the resolver keeps the longer
        found = places("Shatin is far; Sha Tin Hospital is near")
        assert ("Sha Tin Hospital", "FACILITY") in found and ("Shatin", "PLACE") in found
        assert places("Fanlingx") == []

    def test_hospital_short_forms(self):
        assert places("took me to Queen Mary on Tuesday") == [("Queen Mary", "FACILITY")]
        assert places("QMH then PWH") == [("PWH", "FACILITY"), ("QMH", "FACILITY")]

    def test_common_word_place_names_need_capital_mid_sentence(self):
        assert places("I live in Central") == [("Central", "PLACE")]
        assert places("central pain in my chest") == []
        assert places("Central is far from home") == []          # sentence-initial
        assert places("we met in Stanley and Jordan") == [("Jordan", "PLACE"), ("Stanley", "PLACE")]
        assert places("the stanley knife") == []

    def test_cjk_exact_substring(self):
        assert places("我住將軍澳，去過尖沙咀同銅鑼灣") == [("將軍澳", "PLACE"), ("尖沙咀", "PLACE"), ("銅鑼灣", "PLACE")]

    def test_two_char_cjk_names_need_a_location_cue(self):
        assert places("我住沙田") == [("沙田", "PLACE")]
        assert places("返上水買菜") == [("上水", "PLACE")]
        assert ("沙田區", "PLACE") in places("沙田區好遠")
        assert places("去彩虹站") == [("彩虹", "PLACE")]
        for t in ["早上水腫", "彩虹好靚", "太子爺", "火炭燒嘢食", "大圍好多人", "石門水庫"]:
            assert places(t) == [], t

    def test_end_to_end_one_token_per_facility(self):
        ctx = ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="u1"))
        assert ctx.scrub("Queen Mary Hospital").text == "[FACILITY_1]"
        assert ctx.scrub("queen mary hospital again").text == "[FACILITY_1] again"
        assert ctx.scrub("瑪麗醫院").text == "[FACILITY_2]"
        assert ctx.scrub("Sha Tin Hospital in Sha Tin").text == "[FACILITY_3] in [PLACE_1]"

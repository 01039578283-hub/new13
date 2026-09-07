"""Offline regression checks: exact facts, absent facts and title-only updates."""
import json
import unittest
from pathlib import Path

from personalize_title_suffixes import (
    ROOT, DOMAIN, attrs, META_RE, title_of, replace_titles, masked,
    body_blocks, read_page, plan, inventory, resolved_page_url, update_rss,
)
from title_suffix_rules import (
    GRADE_ORDER, compact_grades, grade_tokens, select_detail,
    MISSING_GRADE, CONSULTATION_TOPICS, MISSING_TOPICS, HUB_SUFFIXES,
)


def sample(group="고등수학학원", grades="고1·고2", intro=None, prose=None):
    stage="high" if group.startswith("고") else "middle" if group.startswith("중") else "elementary" if group.startswith("초") else "all"
    subject="combined" if "영수" in group else "english" if "영어" in group else "math"
    scope=group.removesuffix("학원")
    for level in ("고등","중등","초등"):
        if scope.startswith(level):
            scope=level+" "+scope[len(level):]
    return dict(rel="test",group=group,stage=stage,subject=subject,blocks=[
        dict(role="intro",text=intro or CONSULTATION_TOPICS[0][0]),
        dict(role="prose",text=prose or f"과목·학년 표기를 보면 {scope} 학년값은 {grades}이며, 이 값이 확인됩니다."),
    ])


class Grades(unittest.TestCase):
    def test_one(self):
        self.assertEqual(compact_grades("고1"),"고1")
    def test_two_are_not_recast_as_range(self):
        self.assertEqual(compact_grades("고1·고2"),"고1·고2")
    def test_three(self):
        self.assertEqual(compact_grades("중1·중2·중3"),"중1~중3")
    def test_cross_level_only_with_all_grades(self):
        self.assertEqual(compact_grades("초6·중1·중2·중3·고1"),"초6~고1")
    def test_gap_is_preserved(self):
        self.assertEqual(compact_grades("초1·초2·초3·초5·초6·중2·중3·고1"),"초1~초3·초5·초6·중2~고1")
    def test_invalid(self):
        for raw in ("", "중4", "고4", "초0", "고1·고1", "고2·고1", "초1~초6"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                grade_tokens(raw)
    def test_all_grades(self):
        self.assertEqual(compact_grades("·".join(GRADE_ORDER)),"초1~고3")


class FactualSelection(unittest.TestCase):
    def test_single_subject(self):
        suffix,evidence,missing=select_detail(sample())
        self.assertEqual(suffix,"고1·고2 학년 표기·시간표·반 구성 확인")
        self.assertFalse(missing)
        self.assertEqual(evidence[0]["gradeValues"],{"math":"고1·고2"})
    def test_school_level_cannot_be_added(self):
        with self.assertRaises(ValueError):
            select_detail(sample(grades="중3·고1·고2"))
    def test_subject_cannot_be_substituted(self):
        with self.assertRaises(ValueError):
            select_detail(sample(prose="고등 영어 학년값은 고1·고2이며"))
    def test_combined_unequal_lists(self):
        suffix,proof,_=select_detail(sample("영수학원",prose="영어 학년은 초5·초6·중1, 수학 학년은 중2·중3·고1이며"))
        self.assertIn("영어 초5~중1·수학 중2~고1 표기",suffix)
        self.assertEqual(proof[0]["gradeValues"]["math"],"중2·중3·고1")
    def test_combined_equal_lists(self):
        suffix,_,_=select_detail(sample("영수학원",prose="영어 학년은 고1·고2·고3, 수학 학년은 고1·고2·고3이며"))
        self.assertTrue(suffix.startswith("영어·수학 고1~고3 표기"))
    def test_missing_takes_precedence_over_other_subject_data(self):
        page=sample("영수학원",intro=MISSING_GRADE+"습니다. "+MISSING_TOPICS[0][0],prose="영어 학년은 고1·고2·고3, 수학 학년은 고1·고2·고3이며")
        suffix,proof,missing=select_detail(page)
        self.assertTrue(missing)
        self.assertEqual(proof[0]["gradeValues"],{})
        self.assertEqual(suffix,"학년 자료 미기재·개설 여부 확인")
    def test_intro_cues_supported(self):
        for cue,label in CONSULTATION_TOPICS:
            with self.subTest(cue=cue):
                suffix,proof,_=select_detail(sample(intro=cue))
                self.assertTrue(suffix.endswith(label))
                self.assertEqual(proof[-1]["match"],cue)
    def test_missing_cues_supported(self):
        for cue,label in MISSING_TOPICS:
            with self.subTest(cue=cue):
                suffix,_,missing=select_detail(sample(intro=MISSING_GRADE+"습니다. "+cue))
                self.assertTrue(missing)
                self.assertTrue(suffix.endswith(label))
    def test_no_fallback_for_unrecognized_body(self):
        with self.assertRaises(ValueError):
            select_detail(sample(intro="아직 조사하지 않은 정보입니다."))
    def test_cue_outside_intro_is_not_evidence(self):
        page=sample(intro="소개",prose="고등 수학 학년값은 고1·고2이며 "+CONSULTATION_TOPICS[0][0])
        with self.assertRaises(ValueError):
            select_detail(page)
    def test_multiple_grade_claims_fail(self):
        with self.assertRaises(ValueError):
            select_detail(sample(prose="고등 수학 학년값은 고1이며 고등 수학 학년값은 고2이며"))
    def test_name_is_not_randomization(self):
        left=sample()
        right=sample()
        right["rel"]="다른동네"
        self.assertEqual(select_detail(left),select_detail(right))


class HtmlContract(unittest.TestCase):
    def test_article_boundaries(self):
        source='''<main><nav><p>nav</p></nav><article class="academy-main-article"><div class="academy-intro-answer"><p>intro<img src="x"> rest</p></div><section class="academy-prose-section"><h2>heading</h2><p>proof <strong>word</strong></p><p style="display:none">secret</p><div hidden><p>hidden</p></div><nav><p>nested nav</p></nav></section></article><section class="faq"><p>faq</p></section></main>'''
        self.assertEqual(body_blocks(source,"national"),[dict(text="intro rest",role="intro"),dict(text="proof word",role="prose")])
    def test_titles_only(self):
        source='''<title>A | B</title><meta property="og:title" content="A | B"><meta name='twitter:title' content='A | B'><meta name="description" content="keep"><link rel="canonical" href="/keep/"><script type="application/ld+json">{"name":"keep"}</script><main><h1>A</h1><img alt="keep" style="display:none;"></main>'''
        title="A | X & Y ' Z"
        updated=replace_titles(source,title)
        self.assertEqual(masked(source),masked(updated))
        self.assertEqual(title_of(updated),title)
        social=[attrs(m[0])["content"] for m in META_RE.finditer(updated) if attrs(m[0]).get("property",attrs(m[0]).get("name")) in {"og:title","twitter:title"}]
        self.assertEqual(social,[title,title])
        self.assertEqual(replace_titles(updated,title),updated)
    def test_missing_or_duplicate_title_fails(self):
        for source in ("<h1>a</h1>","<title>A</title><title>B</title>"):
            with self.assertRaises(ValueError):
                replace_titles(source,"A | B")
    def test_canonical_does_not_drift(self):
        rel=Path("전국학원/고등수학학원/명일동/index.html")
        self.assertIn("%",resolved_page_url("/전국학원/고등수학학원/명일동/",rel))
        with self.assertRaises(ValueError):
            resolved_page_url("https://example.com/",rel)
    def test_no_matching_rss_items(self):
        rss=(ROOT/"rss.xml").read_bytes().decode("utf-8")
        updated,changed=update_rss([dict(url=DOMAIN+"/전국학원/고등수학학원/",after="A | B")],rss)
        self.assertEqual(changed,0)
        self.assertEqual(updated,rss)
    def test_real_category_evidence_and_idempotence(self):
        for group in HUB_SUFFIXES:
            with self.subTest(group=group):
                page=read_page(ROOT/"전국학원"/group/"index.html")
                plan([page])
                first=page["after"]
                plan([page])
                self.assertEqual(first,page["after"])
                self.assertTrue(all(p["match"] in p["excerpt"] for p in page["evidence"]))
    def test_real_myeongil_all_categories(self):
        for group in HUB_SUFFIXES:
            with self.subTest(group=group):
                page=read_page(ROOT/"전국학원"/group/"명일동/index.html")
                plan([page])
                self.assertIn("표기",page["suffix"])
                self.assertEqual(masked(page["source"]),masked(page["updated"]))
    def test_expected_inventory(self):
        targets,protected=inventory()
        self.assertEqual((len(targets),len(protected)),(3348,31))


if __name__=="__main__":
    unittest.main(verbosity=2)

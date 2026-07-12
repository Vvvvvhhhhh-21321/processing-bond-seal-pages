import sys
import time
import unittest
from unittest.mock import patch
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.titles import (  # noqa: E402
    ReturnedTitle,
    SourceTitle,
    extract_bracket_title,
    match_titles,
    normalize_title,
    title_similarity,
)


class TitleTests(unittest.TestCase):
    def test_extracts_four_bracket_styles_without_surrounding_text(self):
        cases = {
            "前缀《履约保函》后缀": "履约保函",
            "OCR 〈投标保函〉 第 1 页": "投标保函",
            "noise <质量保证书> tail": "质量保证书",
            "文件 <<预付款保函>> 已盖章": "预付款保函",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_bracket_title(text), expected)

    def test_normalize_handles_ocr_whitespace_and_full_width_characters(self):
        self.assertEqual(normalize_title("《履约\n保　函：Ａ１》"), "履约保函A1")

    def test_similarity_is_100_for_same_normalized_title(self):
        self.assertEqual(title_similarity("《履约 保函》", "履约\n保函"), 100)

    def test_similarity_and_matching_ignore_noise_outside_brackets(self):
        source = "扫描件第 1 页《履约保函》已盖章"
        returned = "OCR 噪声〈履约 保函〉尾注"
        self.assertEqual(title_similarity(source, returned), 100)
        result = match_titles(
            [SourceTitle("noise-source", source)],
            [ReturnedTitle(6, returned)],
        )
        self.assertEqual(result.matches[0].returned_page, 6)
        self.assertEqual(result.matches[0].score, 100)

    def test_empty_bracket_title_does_not_fall_back_to_surrounding_noise(self):
        self.assertEqual(title_similarity("甲方前缀《》尾注", "乙方噪声《》末尾"), 100)

    def test_low_similarity_is_not_matched(self):
        result = match_titles(
            [SourceTitle("source-a", "履约保函")],
            [ReturnedTitle(9, "设备验收清单")],
        )
        self.assertEqual(result.matches, ())
        self.assertEqual(result.unmatched_source_ids, ("source-a",))
        self.assertEqual(result.unused_pages, (9,))

    def test_same_title_sources_reuse_one_page_but_other_group_cannot(self):
        result = match_titles(
            [
                SourceTitle("a-1", "《履约保函》"),
                SourceTitle("a-2", "履约 保函"),
                SourceTitle("b-1", "投标保函"),
            ],
            [
                ReturnedTitle(5, "履约保函"),
                ReturnedTitle(2, "履约保函"),
                ReturnedTitle(8, "投标保函"),
            ],
        )
        matched_pages = {match.source_id: match.returned_page for match in result.matches}
        self.assertEqual(matched_pages, {"a-1": 2, "a-2": 2, "b-1": 8})
        self.assertEqual(result.unused_pages, (5,))

    def test_remaining_groups_use_maximum_total_score_assignment(self):
        sources = [
            SourceTitle("s1", "履约保函申请表"),
            SourceTitle("s2", "履约保函审批表"),
        ]
        returned = [
            ReturnedTitle(10, "履约保函申请单"),
            ReturnedTitle(11, "履约保函审批单"),
        ]
        result = match_titles(sources, returned, threshold=75)
        matched_pages = {match.source_id: match.returned_page for match in result.matches}
        self.assertEqual(matched_pages, {"s1": 10, "s2": 11})

    def test_assignment_is_globally_optimal_instead_of_locally_greedy(self):
        result = match_titles(
            [SourceTitle("flexible", "aaa"), SourceTitle("constrained", "aab")],
            [ReturnedTitle(1, "aaaa"), ReturnedTitle(2, "aaaaaa")],
            threshold=50,
        )
        matched_pages = {match.source_id: match.returned_page for match in result.matches}
        self.assertEqual(matched_pages, {"flexible": 2, "constrained": 1})

    def test_distinct_titles_cannot_reuse_duplicate_physical_page(self):
        result = match_titles(
            [SourceTitle("first", "履约保函"), SourceTitle("second", "投标保函")],
            [ReturnedTitle(4, "履约保函"), ReturnedTitle(4, "投标保函")],
        )
        self.assertEqual(len(result.matches), 1)
        self.assertEqual(len({match.returned_page for match in result.matches}), 1)

    def test_twenty_fuzzy_groups_complete_in_reasonable_time(self):
        sources = [
            SourceTitle(f"source-{index}", f"项目{index:02d}履约保证书")
            for index in range(20)
        ]
        returned = [
            ReturnedTitle(100 + index, f"项目{index:02d}履约保障书")
            for index in range(20)
        ]
        started = time.perf_counter()
        result = match_titles(sources, returned, threshold=75)
        elapsed = time.perf_counter() - started
        self.assertEqual(len(result.matches), 20)
        self.assertLess(elapsed, 2.0)


    def test_negative_gain_conflict_chain_leaves_new_source_unmatched(self):
        sources = [SourceTitle(f"existing-{index}", f"group-{index}") for index in range(6)]
        sources.append(SourceTitle("new-low-score", "group-new"))
        returned = [ReturnedTitle(200 + index, f"page-{index}") for index in range(7)]

        score_table = {}
        for index in range(6):
            score_table[(f"group{index}", f"page-{index}")] = 95
            score_table[(f"group{index}", f"page-{index + 1}")] = 80
        score_table[("groupnew", "page-0")] = 80

        with patch(
            "bond_seal_pages.titles.title_similarity",
            side_effect=lambda source, page: score_table.get((source, page), 0),
        ):
            result = match_titles(sources, returned, threshold=80)

        self.assertEqual(sum(match.score for match in result.matches), 570)
        self.assertEqual(result.unmatched_source_ids, ("new-low-score",))
        self.assertEqual({match.returned_page for match in result.matches}, set(range(200, 206)))

    def test_fuzzy_equal_scores_choose_lowest_physical_page(self):
        result = match_titles(
            [SourceTitle("source", "abc")],
            [ReturnedTitle(9, "abx"), ReturnedTitle(3, "aby")],
            threshold=60,
        )
        self.assertEqual(result.matches[0].returned_page, 3)
if __name__ == "__main__":
    unittest.main()
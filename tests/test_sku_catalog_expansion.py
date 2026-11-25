"""iOS プロジェクト（g1-acq）由来の SKU カタログ知識の取り込みを検証する。

Ultra 系 tier（20tb / play.20tb / ai.ultra）と獲得向けサフィックス
（_ip_75p.acq）が候補生成と SKU 分解の両方に効いていることを確認する。
device / frida / network は使用しない。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sku_scanner_core


class CatalogConstantTests(unittest.TestCase):
    """既定カタログの内容と順序（並びは生成順・docs の記述順に直結する）。"""

    def test_default_tiers_keep_existing_four_then_append_ultra_family(self):
        self.assertEqual(
            sku_scanner_core.DEFAULT_TIERS,
            (
                "2tb.ai",
                "play.2tb.ai",
                "5tb.ai",
                "ai.pro",
                "20tb",
                "play.20tb",
                "ai.ultra",
            ),
        )

    def test_default_suffixes_append_acquisition_75_percent(self):
        self.assertEqual(
            sku_scanner_core.DEFAULT_SUFFIXES,
            ("_eft", "_eft.acq", "_ip_90p", "_ip_90p.acq", "_ip_95p.acq", "_ip_75p.acq"),
        )

    def test_default_periods_are_unchanged(self):
        # 期間は今回の変更対象ではない。取り込みで崩れていないことを固定する。
        self.assertEqual(
            sku_scanner_core.DEFAULT_PERIODS,
            (
                "1month",
                "2months",
                "3months",
                "4months",
                "6months",
                "12months",
                "15months",
                "18months",
            ),
        )


class CandidateCountTests(unittest.TestCase):
    """7 tier x (1 variant + 8 period x 5 suffix) = 343 を固定する。"""

    def test_candidate_count_is_343(self):
        self.assertEqual(len(sku_scanner_core.generate_candidates()), 343)

    def test_candidate_count_without_variants_is_336(self):
        self.assertEqual(
            len(sku_scanner_core.generate_candidates(include_variants=False)),
            336,
        )

    def test_candidates_contain_ultra_family_samples(self):
        candidates = sku_scanner_core.generate_candidates()
        self.assertIn("g1.20tb.3months_ip_90p.acq", candidates)
        self.assertIn("g1.ai.ultra.3months_ip_75p.acq", candidates)
        self.assertIn("g1.play.20tb.12months_eft", candidates)
        self.assertEqual(len(candidates), len(set(candidates)))


class ParseSkuExpansionTests(unittest.TestCase):
    """ドットを含まない Ultra tier でも期間トークン経路で分解できること。"""

    def test_parse_sku_handles_period_suffix_for_new_tiers(self):
        cases = (
            ("g1.20tb.3months_ip_90p.acq", ("20tb", "3months", "_ip_90p.acq")),
            ("g1.ai.ultra.3months_ip_75p.acq", ("ai.ultra", "3months", "_ip_75p.acq")),
            ("g1.play.20tb.12months_eft", ("play.20tb", "12months", "_eft")),
            ("g1.20tb.subs_variant", ("20tb", "", ".subs_variant")),
            ("g1.ai.ultra.subs_variant", ("ai.ultra", "", ".subs_variant")),
        )
        for sku, parsed in cases:
            with self.subTest(sku=sku):
                self.assertEqual(sku_scanner_core.parse_sku(sku), parsed)

    def test_new_tiers_round_trip_through_build_sku(self):
        for tier in ("20tb", "play.20tb", "ai.ultra"):
            for suffix in ("_eft", "_ip_75p.acq"):
                with self.subTest(tier=tier, suffix=suffix):
                    sku = sku_scanner_core.build_sku(tier, "3months", suffix)
                    self.assertEqual(
                        sku_scanner_core.parse_sku(sku),
                        (tier, "3months", suffix),
                    )


if __name__ == "__main__":
    unittest.main()

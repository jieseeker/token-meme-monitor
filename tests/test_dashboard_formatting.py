from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pandas as pd

from dashboard import app as dashboard_app


class DashboardFormattingTest(unittest.TestCase):
    def test_detail_data_quality_marks_fresh_snapshot_and_realtime_price(self) -> None:
        now_ts = datetime(2026, 4, 29, 10, 30, 0, tzinfo=timezone.utc).timestamp()
        items = dashboard_app.build_detail_data_quality_items(
            {
                "snapshot_observed_at": "2026-04-29T10:29:40+00:00",
                "last_signal_at": "2026-04-29T10:29:35+00:00",
                "price_usd": 0.12,
                "alpha_price": 0.11,
            },
            now_ts=now_ts,
        )

        self.assertEqual(
            tuple((item["label"], item["value"], item["tone"]) for item in items),
            (
                ("行情快照", "新鲜 20s前", "fresh"),
                ("最新信号", "新鲜 25s前", "fresh"),
                ("价格来源", "实时快照", "fresh"),
            ),
        )

    def test_detail_data_quality_marks_stale_snapshot_and_alpha_fallback(self) -> None:
        now_ts = datetime(2026, 4, 29, 10, 30, 0, tzinfo=timezone.utc).timestamp()
        items = dashboard_app.build_detail_data_quality_items(
            {
                "snapshot_observed_at": "2026-04-29T10:10:00+00:00",
                "last_signal_at": None,
                "price_usd": None,
                "alpha_price": 0.11,
            },
            now_ts=now_ts,
        )

        self.assertEqual(
            tuple((item["label"], item["value"], item["tone"]) for item in items),
            (
                ("行情快照", "偏旧 20分钟前", "warn"),
                ("最新信号", "暂未生成", "missing"),
                ("价格来源", "Alpha 兜底", "warn"),
            ),
        )

    def test_build_list_groups_keeps_operational_order_and_overextended_warning(self) -> None:
        df = pd.DataFrame(
            [
                {"pair_address": "0xnormal", "display_tier": "normal", "display_tier_label": "普通观察"},
                {"pair_address": "0xlate", "display_tier": "overextended", "display_tier_label": "已涨过多"},
                {"pair_address": "0xrisk", "display_tier": "risk_momentum", "display_tier_label": "高风险动量"},
                {"pair_address": "0xlaunch", "display_tier": "launch", "display_tier_label": "启动异动"},
                {"pair_address": "0xstrong", "display_tier": "strong", "display_tier_label": "强确认"},
            ]
        )

        groups = dashboard_app.build_list_groups(df)

        self.assertEqual(
            [(label, frame["pair_address"].tolist()) for label, frame in groups],
            [
                ("启动异动", ["0xlaunch"]),
                ("高风险动量", ["0xrisk"]),
                ("强确认", ["0xstrong"]),
                ("普通观察", ["0xnormal", "0xlate"]),
            ],
        )


if __name__ == "__main__":
    unittest.main()

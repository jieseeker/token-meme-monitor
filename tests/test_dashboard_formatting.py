from __future__ import annotations

import inspect
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

    def test_detail_page_uses_single_unified_flow(self) -> None:
        source = inspect.getsource(dashboard_app.render_detail)

        self.assertIn("render_decision_detail_page", source)
        self.assertNotIn("st.segmented_control", source)
        self.assertNotIn("DETAIL_VIEW_OPTIONS", source)
        self.assertNotIn('view == "overview"', source)
        self.assertNotIn('view == "expert"', source)

    def test_decision_detail_page_combines_quick_and_deep_sections(self) -> None:
        source = inspect.getsource(dashboard_app.render_decision_detail_page)

        self.assertIn("render_conclusion_summary", source)
        self.assertIn("render_beginner_decision_summary", source)
        self.assertIn("量价快照", source)
        self.assertIn("当前市值", source)
        self.assertIn("当前单价", source)
        self.assertIn("链上持币人数", source)
        self.assertIn("5分钟成交额", source)
        self.assertIn("24小时成交额", source)
        self.assertIn("render_decision_factor_panels", source)
        self.assertIn("render_next_watch_items", source)
        self.assertIn("render_expert_prediction_panel(row)", source)
        self.assertIn("render_expert_workbench_panel(row)", source)
        self.assertIn("render_expert_trend_panel", source)
        self.assertIn("render_evidence_center", source)
        self.assertNotIn("st.columns", source)
        self.assertNotIn("st.segmented_control", source)
        self.assertNotIn("render_overview", source)
        self.assertNotIn("render_expert_mode", source)
        self.assertNotIn('"决策强度"', source)
        self.assertNotIn("关键指标", source)
        self.assertNotIn("短线优先级", source)
        self.assertNotIn("短线机会分", source)
        self.assertNotIn("2小时上涨概率", source)

    def test_evidence_center_uses_summary_cards_and_popovers(self) -> None:
        source = inspect.getsource(dashboard_app.render_evidence_center)
        helper_source = inspect.getsource(dashboard_app.render_evidence_popover)
        styles = inspect.getsource(dashboard_app.inject_styles)

        self.assertIn("证据中心", source)
        self.assertIn("判断依据", source)
        self.assertIn("指标解释", source)
        self.assertIn("最近信号", source)
        self.assertIn("st.popover", helper_source)
        self.assertIn("render_evidence_summary_card", source)
        self.assertIn("render_evidence_popover", source)
        self.assertNotIn("st.columns", source)
        self.assertNotIn("st.expander", source)
        self.assertIn(".evidence-center-grid", styles)
        self.assertIn(".evidence-card", styles)
        self.assertIn(".evidence-popover-anchor", styles)
        self.assertIn('data-testid="stPopover"', styles)

    def test_evidence_popover_actions_are_subtle_text_without_arrows(self) -> None:
        source = inspect.getsource(dashboard_app.render_evidence_center)
        card_source = inspect.getsource(dashboard_app.render_evidence_summary_card)
        helper_source = inspect.getsource(dashboard_app.render_evidence_popover)
        styles = inspect.getsource(dashboard_app.inject_styles)

        self.assertIn('render_evidence_popover("完整依据")', source)
        self.assertIn('render_evidence_popover("完整指标")', source)
        self.assertIn('render_evidence_popover("历史信号")', source)
        self.assertIn("use_container_width=False", helper_source)
        self.assertIn("return st.popover(label, use_container_width=False)", helper_source)
        self.assertNotIn("▼", helper_source)
        self.assertIn("evidence-popover-anchor", helper_source)
        self.assertNotIn("evidence-card-arrow", card_source)
        self.assertNotIn(".evidence-card-arrow", styles)
        self.assertRegex(styles, r"data-testid=\"stPopover\"\]\) button \{[^}]*border: 0;")
        self.assertRegex(styles, r"data-testid=\"stPopover\"\]\) button \{[^}]*background: transparent;")
        self.assertRegex(styles, r"data-testid=\"stPopover\"\]\) button \{[^}]*color: var\(--accent-strong\) !important;")
        self.assertRegex(styles, r"data-testid=\"stPopover\"\]\) button svg \{[^}]*display: none !important;")
        self.assertRegex(styles, r"data-testid=\"stPopover\"\]\) button p,[^}]*button span \{[^}]*width: auto;")
        self.assertRegex(styles, r"data-testid=\"stPopover\"\]\) button p,[^}]*button span \{[^}]*opacity: 1;")
        self.assertNotIn("border-radius: 0 0 999px 999px", styles)

    def test_detail_page_metric_cards_use_compact_grid(self) -> None:
        styles = inspect.getsource(dashboard_app.inject_styles)

        self.assertRegex(styles, r"\.metric-grid \{[^}]*grid-template-columns: repeat\(auto-fit, minmax\(9\.4rem, 1fr\)\);")
        self.assertRegex(styles, r"\.evidence-center-grid \{[^}]*grid-template-columns: minmax\(0, 1fr\);")
        self.assertNotIn(".beginner-decision-grid", styles)

    def test_detail_page_vertical_spacing_is_relaxed(self) -> None:
        styles = inspect.getsource(dashboard_app.inject_styles)

        self.assertRegex(styles, r"\.section-heading \{[^}]*margin: 0\.42rem 0 0\.98rem;")
        self.assertRegex(styles, r"\.conclusion-panel \{[^}]*padding: 0\.88rem 1rem;[^}]*margin: 0\.36rem 0 1\.14rem;")
        self.assertRegex(styles, r"\.metric-grid \{[^}]*gap: 0\.82rem;[^}]*margin: 0\.88rem 0 1\.30rem;")
        self.assertRegex(styles, r"\.detail-line-list \{[^}]*gap: 0\.68rem;[^}]*margin-top: 0\.86rem;")
        self.assertRegex(styles, r"\.detail-line-item\.compact \{[^}]*row-gap: 0\.28rem;[^}]*padding: 0\.58rem 0\.8rem;")
        self.assertRegex(styles, r"\.evidence-center-grid \{[^}]*gap: 0\.92rem;")

    def test_expert_summary_avoids_detail_header_duplicates(self) -> None:
        items = dashboard_app.build_expert_summary_items(
            pd.Series(
                {
                    "last_score": 82,
                    "prediction_short_momentum_score": 76,
                    "prediction_prob_2h_up20": 0.42,
                    "prediction_prob_6h_up50": 0.18,
                    "prediction_risk_6h_dd30": 0.27,
                    "snapshot_observed_at": "2026-04-29T10:29:40+00:00",
                }
            ),
            None,
        )

        self.assertEqual(
            [item["label"] for item in items],
            ["短线机会分", "2小时概率", "6小时概率", "回撤风险"],
        )
        self.assertEqual(items[0]["value"], "76 分")
        self.assertEqual(items[3]["value"], "27.0%")

    def test_expert_panel_helpers_are_not_expander_based(self) -> None:
        prediction_source = inspect.getsource(dashboard_app.render_expert_prediction_panel)
        workbench_source = inspect.getsource(dashboard_app.render_expert_workbench_panel)
        trend_source = inspect.getsource(dashboard_app.render_expert_trend_panel)

        self.assertIn("预测结构", prediction_source)
        self.assertIn("复盘验证", workbench_source)
        self.assertIn("完整走势", trend_source)
        self.assertNotIn("st.columns", trend_source)
        self.assertNotIn("st.expander", prediction_source)
        self.assertNotIn("st.expander", workbench_source)
        self.assertNotIn("st.expander", trend_source)

    def test_beginner_decision_items_use_plain_language(self) -> None:
        items = dashboard_app.build_beginner_decision_items(
            pd.Series({"prediction_short_momentum_score": 78, "prediction_risk_6h_dd30": 0.32}),
            {"title": "重点关注", "klass": "verdict-good", "action": "优先观察成交是否延续。"},
        )

        self.assertEqual([item["label"] for item in items], ["机会强度", "风险等级"])
        self.assertEqual(items[0]["value"], "高")
        self.assertIn("短线机会分 78", items[0]["note"])
        self.assertEqual(items[1]["value"], "高风险")
        self.assertIn("回撤风险 32.0%", items[1]["note"])

    def test_conclusion_box_owns_decision_content_and_action(self) -> None:
        source = inspect.getsource(dashboard_app.render_conclusion_summary)
        decision_items_source = inspect.getsource(dashboard_app.build_beginner_decision_items)

        self.assertIn("conclusion-panel", source)
        self.assertIn("conclusion-body", source)
        self.assertIn("conclusion-action", source)
        self.assertIn("建议", source)
        self.assertNotIn('"建议动作"', decision_items_source)

    def test_decision_summary_items_reuse_metric_cards(self) -> None:
        source = inspect.getsource(dashboard_app.render_beginner_decision_summary)

        self.assertIn("render_metric_cards(items)", source)
        self.assertNotIn("beginner-decision-grid", source)

    def test_next_watch_items_are_beginner_friendly(self) -> None:
        items = dashboard_app.build_next_watch_items(
            pd.Series(
                {
                    "prediction_prob_2h_up20": 0.41,
                    "prediction_risk_6h_dd30": 0.12,
                    "volume_h1": 12000,
                }
            ),
            None,
        )

        self.assertEqual(len(items), 3)
        self.assertTrue(any("1小时成交" in item for item in items))
        self.assertTrue(any("2小时" in item and "41.0%" in item for item in items))
        self.assertTrue(any("降级观察" in item for item in items))

    def test_next_watch_items_use_unified_detail_line_style(self) -> None:
        source = inspect.getsource(dashboard_app.render_next_watch_items)
        styles = inspect.getsource(dashboard_app.inject_styles)

        self.assertIn("render_detail_lines", source)
        self.assertIn("compact=True", source)
        self.assertNotIn("next-watch-list", source)
        self.assertNotIn(".next-watch-list", styles)

    def test_build_list_labels_uses_single_strength_value(self) -> None:
        options, labels = dashboard_app.build_list_labels(
            pd.DataFrame(
                [
                    {
                        "pair_address": "0xpair",
                        "token_symbol": "ABC",
                        "display_tier_label": "强确认",
                        "state": "watching",
                        "display_score": 61,
                        "prediction_short_momentum_score": 78,
                    }
                ]
            )
        )

        self.assertEqual(options, ["0xpair"])
        self.assertEqual(labels["0xpair"], "ABC · 强确认 · 强度 78 · 观察中")
        self.assertNotIn("信号", labels["0xpair"])
        self.assertNotIn("短线", labels["0xpair"])

    def test_detail_hero_places_token_address_after_title_symbol(self) -> None:
        source = inspect.getsource(dashboard_app.render_detail)
        styles = inspect.getsource(dashboard_app.inject_styles)

        self.assertIn("token-title-line", source)
        self.assertIn("token-identity-line", source)
        self.assertIn("token-address-inline", source)
        self.assertIn("copy-address-button", source)
        self.assertLess(source.index("token-title-line"), source.index("token-identity-line"))
        self.assertNotIn("token-address-label", source)
        self.assertNotIn("hero-address-line", source)
        self.assertIn(".token-title-line", styles)
        self.assertIn(".token-identity-line", styles)
        self.assertIn(".token-address-inline", styles)
        self.assertNotIn(".token-address-label", styles)
        self.assertNotIn(".hero-address-line", styles)

    def test_compact_detail_lines_wrap_long_values(self) -> None:
        styles = inspect.getsource(dashboard_app.inject_styles)

        self.assertRegex(styles, r"\.detail-line-item\.compact \{[^}]*grid-template-columns: minmax\(7\.2rem, 0\.34fr\) minmax\(0, 1fr\);")
        self.assertRegex(styles, r"\.detail-line-value \{[^}]*white-space: normal;[^}]*overflow-wrap: anywhere;")
        self.assertRegex(styles, r"\.detail-line-item\.compact \.detail-line-value \{[^}]*grid-column: 2;[^}]*grid-row: 1;")
        self.assertRegex(styles, r"\.detail-line-item\.compact \.detail-line-body \{[^}]*grid-column: 2;[^}]*grid-row: 2;")

    def test_workbench_copy_uses_review_record_language(self) -> None:
        source = inspect.getsource(dashboard_app.render_decision_workbench)

        self.assertIn("当时预测", source)
        self.assertIn("后续结果", source)
        self.assertIn("导出当前复盘记录 CSV", source)
        self.assertNotIn('"案例"', source)
        self.assertNotIn("导出当前案例 CSV", source)
        self.assertNotIn('"title": "当前复盘记录"', source)


if __name__ == "__main__":
    unittest.main()

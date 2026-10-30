import importlib.util
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "confirmation_shadow", Path(__file__).with_name("confirmation_shadow.py")
)
confirmation_shadow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(confirmation_shadow)


class ConfirmationShadowTest(unittest.TestCase):
    def test_classifies_strong_aging_and_slippage_blocked(self):
        now = int(time.time())
        warning = {
            "is_active": True,
            "slippage_estimates": {
                "estimates": [
                    {"order_usd": 100, "buy_slippage_pct": 0.1},
                    {"order_usd": 1000, "buy_slippage_pct": 0.2},
                ]
            },
        }
        cases = [
            ({"stage": "watch", "risk_status": "PASS", "first_seen_at": now - 240}, "strong"),
            ({"stage": "watch", "risk_status": "PASS", "first_seen_at": now - 600}, "aging"),
            ({"stage": "watch", "risk_status": "PASS", "first_seen_at": now - 240}, "slippage_blocked"),
        ]
        cases[-1] = (cases[-1][0], "slippage_blocked")
        high_slippage = dict(warning)
        high_slippage["slippage_estimates"] = {
            "estimates": [
                {"order_usd": 100, "buy_slippage_pct": 0.1},
                {"order_usd": 1000, "buy_slippage_pct": 0.6},
            ]
        }
        values = [
            confirmation_shadow.classify(cases[0][0], warning, now, "neutral"),
            confirmation_shadow.classify(cases[1][0], warning, now, "neutral"),
            confirmation_shadow.classify(cases[2][0], high_slippage, now, "neutral"),
        ]
        self.assertEqual([value["classification"] for value in values], ["strong", "aging", "slippage_blocked"])

    def test_strong_requires_known_slippage_and_non_risk_off_market(self):
        now = int(time.time())
        signal = {"stage": "watch", "risk_status": "PASS", "first_seen_at": now - 120}
        warning = {"is_active": True, "slippage_estimates": {"estimates": [
            {"order_usd": 100, "buy_slippage_pct": 0.2},
            {"order_usd": 1000, "buy_slippage_pct": 0.4},
        ]}}
        self.assertEqual(confirmation_shadow.classify(signal, warning, now, "risk_on")["classification"], "strong")
        self.assertEqual(confirmation_shadow.classify(signal, warning, now, "risk_off")["classification"], "market_blocked")
        self.assertEqual(confirmation_shadow.classify(signal, warning, now, None)["classification"], "market_blocked")
        warning["slippage_estimates"]["estimates"].pop()
        self.assertEqual(confirmation_shadow.classify(signal, warning, now, "risk_on")["classification"], "slippage_unknown")
        warning["slippage_estimates"]["estimates"][0]["buy_slippage_pct"] = 0.4
        self.assertEqual(confirmation_shadow.classify(signal, warning, now, "risk_on")["classification"], "slippage_blocked")

    def test_records_and_resolves_forward_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "shadow.db"
            connection = sqlite3.connect(database)
            confirmation_shadow.init_schema(connection)
            observed_at = int(time.time()) - 4 * 3600 - 600
            confirmation_shadow.record_event(
                connection,
                {
                    "token_address": "token",
                    "pair_address": "pair",
                    "first_seen_at": observed_at - 240,
                    "observed_at": observed_at,
                    "classification": "strong",
                    "age_seconds": 240,
                    "entry_price_usd": 100.0,
                    "volume_acceleration": 2.1,
                    "buy_pressure": 0.7,
                    "volume_to_liquidity": 0.02,
                    "slippage_100_pct": 0.1,
                    "slippage_1000_pct": 0.2,
                    "market_state": "risk_on",
                },
            )
            for horizon, price in [(900, 101.0), (3600, 103.0), (14400, 106.0)]:
                confirmation_shadow.insert_snapshot(
                    connection,
                    "pair",
                    observed_at + horizon,
                    price,
                )
            confirmation_shadow.resolve_outcomes(connection, observed_at + 14400 + 301)
            summary = confirmation_shadow.summarize(connection)
            self.assertEqual(summary["by_classification"]["strong"]["events"], 1)
            stored_state = connection.execute(
                "SELECT market_state FROM confirmation_shadow_events WHERE classification='strong'"
            ).fetchone()[0]
            self.assertEqual(stored_state, "risk_on")
            self.assertAlmostEqual(summary["by_classification"]["strong"]["mean_15m_pct"], 1.0)
            self.assertAlmostEqual(summary["by_classification"]["strong"]["mean_1h_pct"], 3.0)
            self.assertAlmostEqual(summary["by_classification"]["strong"]["mean_4h_pct"], 6.0)
            cross = summary["by_market_and_classification"]["risk_on"]["strong"]
            self.assertEqual(cross["events"], 1)
            self.assertAlmostEqual(cross["mean_net_4h_pct"], 5.7)
            self.assertEqual(cross["samples_4h"], 1)
            report = confirmation_shadow.format_report(summary)
            self.assertIn("| risk_on | strong | 1 | 1 |", report)
            self.assertIn("1 / +5.70%", report)
            connection.close()


if __name__ == "__main__":
    unittest.main()

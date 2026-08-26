from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from token_meme_monitor.config import load_config


class ConfigTests(unittest.TestCase):
    def test_zero_valued_env_overrides_are_preserved(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ALERT_COOLDOWN_MINUTES": "0",
                "FOCUS_SCORE_THRESHOLD": "0",
                "DASHBOARD_AUTO_REFRESH_SECONDS": "0",
                "MAX_PAIRS_PER_CYCLE": "0",
                "RISK_ENRICHMENT_TTL_HOURS": "0",
                "RISK_ENRICHMENT_BATCH_SIZE": "0",
            },
            clear=False,
        ):
            config = load_config(env_file=None)

        self.assertEqual(config.signal.alert_cooldown_minutes, 0)
        self.assertEqual(config.signal.focus_score_threshold, 0)
        self.assertEqual(config.dashboard_auto_refresh_seconds, 0)
        self.assertEqual(config.max_pairs_per_cycle, 0)
        self.assertEqual(config.risk_enrichment_ttl_hours, 0)
        self.assertEqual(config.risk_enrichment_batch_size, 0)

    def test_bsc_rpc_urls_override_primary_rpc_url(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BSC_RPC_URL": "https://primary.example",
                "BSC_RPC_URLS": "https://rpc-a.example, https://rpc-b.example, https://rpc-a.example",
            },
            clear=False,
        ):
            config = load_config(env_file=None)

        self.assertEqual(config.bsc_rpc_url, "https://rpc-a.example")
        self.assertEqual(config.bsc_rpc_urls, ("https://rpc-a.example", "https://rpc-b.example"))


if __name__ == "__main__":
    unittest.main()

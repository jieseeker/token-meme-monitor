from __future__ import annotations

import unittest
from typing import Any

from token_meme_monitor.clients.binance_alpha_rank import BinanceAlphaRankClient


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: int) -> FakeResponse:
        self.calls.append(json)
        return FakeResponse(self.pages[len(self.calls) - 1])


class BinanceAlphaRankClientTests(unittest.TestCase):
    def test_fetch_bsc_alpha_rank_converts_top10_percent_to_ratio(self) -> None:
        client = BinanceAlphaRankClient()
        session = FakeSession(
            [
                {
                    "data": {
                        "total": 2,
                        "tokens": [
                            {
                                "contractAddress": "0xTokenA",
                                "holders": "1234",
                                "holdersTop10Percent": "94.5",
                            },
                            {
                                "contractAddress": "0xTokenB",
                                "holders": "",
                                "holdersTop10Percent": None,
                            },
                        ],
                    }
                }
            ]
        )
        client._session = session
        result = client.fetch_bsc_alpha_rank(page_size=20)
        self.assertEqual(result["0xtokena"]["holder_count"], 1234)
        self.assertAlmostEqual(result["0xtokena"]["top10_holder_share"] or 0.0, 0.945)
        self.assertIsNone(result["0xtokenb"]["holder_count"])
        self.assertIsNone(result["0xtokenb"]["top10_holder_share"])


if __name__ == "__main__":
    unittest.main()

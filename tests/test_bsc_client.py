from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from token_meme_monitor.clients.bsc import BscPairDiscoveryClient


class BscPairDiscoveryClientTests(unittest.TestCase):
    def test_rpc_http_provider_uses_request_timeout(self) -> None:
        web3_instance = Mock()

        with patch("token_meme_monitor.clients.bsc.HTTPProvider") as http_provider, patch(
            "token_meme_monitor.clients.bsc.Web3"
        ) as web3:
            web3.return_value = web3_instance
            web3.to_checksum_address.return_value = "0x0000000000000000000000000000000000000001"

            BscPairDiscoveryClient(
                "https://rpc.example",
                factory_address="0x0000000000000000000000000000000000000001",
                quote_tokens={},
            )

        http_provider.assert_called_once_with("https://rpc.example", request_kwargs={"timeout": 10})


if __name__ == "__main__":
    unittest.main()

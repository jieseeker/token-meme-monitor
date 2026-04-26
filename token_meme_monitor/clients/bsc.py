from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from web3 import HTTPProvider, Web3
from web3.exceptions import Web3RPCError
from web3.middleware import ExtraDataToPOAMiddleware

from token_meme_monitor.constants import PANCAKESWAP_V2_FACTORY_ABI
from token_meme_monitor.models import DiscoveredPair


class BscPairDiscoveryClient:
    def __init__(self, rpc_url: str, factory_address: str, quote_tokens: dict[str, str]) -> None:
        self._w3 = Web3(HTTPProvider(rpc_url))
        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self._factory_address = Web3.to_checksum_address(factory_address)
        self._factory = self._w3.eth.contract(address=self._factory_address, abi=PANCAKESWAP_V2_FACTORY_ABI)
        self._quote_tokens = {address.lower(): symbol for address, symbol in quote_tokens.items()}
        self._block_time_cache: dict[int, datetime] = {}

    def latest_safe_block(self, confirmations: int) -> int:
        latest = self._w3.eth.block_number
        return max(0, latest - max(confirmations, 0))

    def healthcheck(self, confirmations: int, log_span: int = 1) -> dict[str, Any]:
        report: dict[str, Any] = {
            "rpc_url": self._w3.provider.endpoint_uri,
            "latest_safe_block": None,
            "block_lookup": {"ok": False, "block_number": None, "timestamp": None},
            "pair_logs": {"ok": False, "from_block": None, "to_block": None, "log_count": None},
            "ok": False,
        }
        latest_safe_block = self.latest_safe_block(confirmations)
        report["latest_safe_block"] = latest_safe_block

        block_timestamp = self._block_timestamp(latest_safe_block)
        report["block_lookup"] = {
            "ok": True,
            "block_number": latest_safe_block,
            "timestamp": block_timestamp.isoformat(timespec="seconds"),
        }

        span = max(1, log_span)
        from_block = max(0, latest_safe_block - span + 1)
        logs = self._load_logs(from_block, latest_safe_block)
        report["pair_logs"] = {
            "ok": True,
            "from_block": from_block,
            "to_block": latest_safe_block,
            "log_count": len(logs),
        }
        report["ok"] = True
        return report

    def scan_pair_created_events(self, start_block: int, end_block: int) -> list[DiscoveredPair]:
        if end_block < start_block:
            return []
        logs = self._load_logs(start_block, end_block)
        discovered: list[DiscoveredPair] = []
        for log in logs:
            token0 = str(log["args"]["token0"]).lower()
            token1 = str(log["args"]["token1"]).lower()
            pair_address = str(log["args"]["pair"]).lower()
            tracked = self._resolve_tracked_token(token0, token1)
            if tracked is None:
                continue
            token_address, quote_token_address, quote_symbol = tracked
            block_number = int(log["blockNumber"])
            discovered.append(
                DiscoveredPair(
                    pair_address=pair_address,
                    token_address=token_address,
                    quote_token_address=quote_token_address,
                    quote_symbol=quote_symbol,
                    token0_address=token0,
                    token1_address=token1,
                    pair_created_at=self._block_timestamp(block_number),
                    discovered_at=datetime.now(timezone.utc),
                    first_seen_block=block_number,
                    first_seen_tx_hash=log["transactionHash"].hex(),
                    first_seen_log_index=int(log["logIndex"]),
                )
            )
        return discovered

    def _load_logs(self, start_block: int, end_block: int):
        try:
            return self._factory.events.PairCreated().get_logs(from_block=start_block, to_block=end_block)
        except Web3RPCError as exc:
            if "limit exceeded" not in str(exc).lower() or start_block >= end_block:
                raise
        midpoint = (start_block + end_block) // 2
        return self._load_logs(start_block, midpoint) + self._load_logs(midpoint + 1, end_block)

    def _resolve_tracked_token(self, token0: str, token1: str) -> tuple[str, str, str] | None:
        quote0 = self._quote_tokens.get(token0)
        quote1 = self._quote_tokens.get(token1)
        if quote0 and not quote1:
            return token1, token0, quote0
        if quote1 and not quote0:
            return token0, token1, quote1
        return None

    def _block_timestamp(self, block_number: int) -> datetime:
        cached = self._block_time_cache.get(block_number)
        if cached is not None:
            return cached
        block = self._w3.eth.get_block(block_number)
        timestamp = datetime.fromtimestamp(int(block["timestamp"]), tz=timezone.utc)
        self._block_time_cache[block_number] = timestamp
        return timestamp

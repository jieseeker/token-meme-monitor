from __future__ import annotations

import requests


class HoneypotClient:
    def __init__(self, timeout_seconds: int = 15) -> None:
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()
        self._base_url = "https://api.honeypot.is/v2/IsHoneypot"

    def fetch_holder_count(self, token_address: str, chain_id: int = 56) -> int | None:
        response = self._session.get(
            self._base_url,
            params={"address": token_address, "chainID": chain_id},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("token") or {}
        holders = token.get("totalHolders")
        try:
            return int(holders) if holders is not None else None
        except (TypeError, ValueError):
            return None

    def fetch_top10_holder_share(self, token_address: str, chain_id: int = 56, top_n: int = 10) -> float | None:
        response = self._session.get(
            "https://api.honeypot.is/v1/TopHolders",
            params={"address": token_address, "chainID": chain_id},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        total_supply = payload.get("totalSupply")
        try:
            total_supply_int = int(total_supply)
        except (TypeError, ValueError):
            return None
        if total_supply_int <= 0:
            return None
        holders = payload.get("holders") or []
        top_balances = 0
        for holder in holders[:top_n]:
            try:
                top_balances += int(holder.get("balance"))
            except (TypeError, ValueError, AttributeError):
                continue
        return top_balances / total_supply_int if top_balances > 0 else None

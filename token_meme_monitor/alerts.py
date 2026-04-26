from __future__ import annotations

from typing import Any

import requests

from token_meme_monitor.models import SignalDecision


class TelegramAlertClient:
    def __init__(self, bot_token: str, chat_id: str, timeout_seconds: int = 10) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    def send_signal_alert(
        self,
        *,
        token_symbol: str,
        pair_address: str,
        quote_symbol: str,
        decision: SignalDecision,
        metadata: dict[str, Any],
    ) -> str:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        reasons = ", ".join(decision.reasons[:4]) or "n/a"
        risk_flags = ", ".join(decision.risk_flags[:4]) or "none"
        lines = [
            f"Signal {token_symbol or pair_address[:8]} / {quote_symbol}",
            f"Score: {decision.score}",
            f"State: {decision.pair_state}",
            f"Pair: {pair_address}",
            f"Reasons: {reasons}",
            f"Risks: {risk_flags}",
        ]
        pair_url = metadata.get("pair_url")
        if pair_url:
            lines.append(f"DexScreener: {pair_url}")
        response = self._session.post(
            url,
            json={"chat_id": self._chat_id, "text": "\n".join(lines)},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") or {}
        return str(result.get("message_id") or "")


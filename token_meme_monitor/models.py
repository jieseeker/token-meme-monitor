from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AlphaToken:
    token_address: str
    chain_id: str
    chain_name: str
    symbol: str
    name: str
    price: float | None
    market_cap: float | None
    fdv: float | None
    liquidity: float | None
    volume_24h: float | None
    holders: int | None
    alpha_id: str | None
    alpha_score: int | None
    listing_time: datetime | None
    raw_payload: dict[str, Any]

    def metadata(self) -> dict[str, Any]:
        return {
            "is_binance_alpha": True,
            "alpha_chain_id": self.chain_id,
            "alpha_chain_name": self.chain_name,
            "alpha_id": self.alpha_id,
            "alpha_score": self.alpha_score,
            "alpha_price": self.price,
            "alpha_market_cap": self.market_cap,
            "alpha_fdv": self.fdv,
            "alpha_liquidity": self.liquidity,
            "alpha_volume_24h": self.volume_24h,
            "holder_count": self.holders,
            "alpha_listing_time": self.listing_time.isoformat() if self.listing_time else None,
        }


@dataclass(frozen=True)
class DiscoveredPair:
    pair_address: str
    token_address: str
    quote_token_address: str
    quote_symbol: str
    token0_address: str
    token1_address: str
    pair_created_at: datetime
    discovered_at: datetime
    first_seen_block: int
    first_seen_tx_hash: str
    first_seen_log_index: int


@dataclass(frozen=True)
class PairSnapshot:
    pair_address: str
    token_address: str
    token_symbol: str
    token_name: str
    quote_token_address: str
    quote_symbol: str
    observed_at: datetime
    pair_created_at: datetime
    dex_id: str
    pair_url: str
    price_usd: float | None
    price_native: float | None
    liquidity_usd: float | None
    fdv: float | None
    market_cap: float | None
    volume_m5: float
    volume_h1: float
    volume_h24: float
    buys_m5: int
    sells_m5: int
    buys_h1: int
    sells_h1: int
    price_change_m5: float
    price_change_h1: float
    price_change_h24: float
    website_count: int
    social_count: int
    boosts_active: int
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class FeatureVector:
    age_minutes: float
    liquidity_usd: float
    fdv: float
    market_cap: float
    price_usd: float
    volume_m5: float
    volume_h1: float
    volume_h24: float
    buys_m5: int
    sells_m5: int
    buys_h1: int
    sells_h1: int
    tx_count_m5: int
    tx_count_h1: int
    buy_sell_ratio_m5: float
    buy_sell_ratio_h1: float
    liquidity_to_fdv: float | None
    volume_to_liquidity_h1: float | None
    website_count: int
    social_count: int
    boosts_active: int
    price_change_m5: float
    price_change_h1: float
    price_change_h24: float
    risk_flags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignalDecision:
    observed_at: datetime
    strategy_version: str
    score: int
    pair_state: str
    should_alert: bool
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]
    features: dict[str, Any]


@dataclass(frozen=True)
class PredictionResult:
    predictor_version: str
    prob_2h_up20: float
    prob_6h_up50: float
    prob_24h_up100: float
    risk_6h_dd30: float
    opportunity_score: int
    stage: str
    reasons: tuple[str, ...]

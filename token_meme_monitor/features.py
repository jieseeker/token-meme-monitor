from __future__ import annotations

from datetime import timedelta

from token_meme_monitor.config import SignalConfig
from token_meme_monitor.models import FeatureVector, PairSnapshot


def build_feature_vector(
    snapshot: PairSnapshot,
    config: SignalConfig,
    *,
    monitor_universe: str = "new_pairs",
) -> FeatureVector:
    age = snapshot.observed_at - snapshot.pair_created_at
    age_minutes = max(age.total_seconds() / 60.0, 0.0)
    liquidity_usd = snapshot.liquidity_usd or 0.0
    fdv = snapshot.fdv or 0.0
    market_cap = snapshot.market_cap or 0.0
    price_usd = snapshot.price_usd or 0.0
    tx_count_m5 = snapshot.buys_m5 + snapshot.sells_m5
    tx_count_h1 = snapshot.buys_h1 + snapshot.sells_h1

    risk_flags: list[str] = []
    if price_usd <= 0:
        risk_flags.append("missing_price")
    if liquidity_usd < config.min_liquidity_usd:
        risk_flags.append("low_liquidity")
    if liquidity_usd < config.archive_liquidity_usd:
        risk_flags.append("liquidity_near_zero")
    if monitor_universe != "binance_alpha" and age > timedelta(hours=config.max_pair_age_hours):
        risk_flags.append("stale_pair")
    if tx_count_m5 < config.min_buy_count_m5 and snapshot.volume_m5 <= 0:
        risk_flags.append("thin_m5_activity")
    if snapshot.sells_m5 > snapshot.buys_m5 * 1.5 and snapshot.sells_m5 >= 5:
        risk_flags.append("sell_pressure")
    if snapshot.website_count + snapshot.social_count == 0:
        risk_flags.append("missing_project_metadata")
    if fdv <= 0:
        risk_flags.append("fdv_missing")
    elif liquidity_usd > 0 and fdv / liquidity_usd > 25:
        risk_flags.append("fdv_liquidity_stretched")

    buy_sell_ratio_m5 = _ratio(snapshot.buys_m5, snapshot.sells_m5)
    buy_sell_ratio_h1 = _ratio(snapshot.buys_h1, snapshot.sells_h1)
    liquidity_to_fdv = (liquidity_usd / fdv) if liquidity_usd > 0 and fdv > 0 else None
    volume_to_liquidity_h1 = (snapshot.volume_h1 / liquidity_usd) if liquidity_usd > 0 else None
    return FeatureVector(
        age_minutes=age_minutes,
        liquidity_usd=liquidity_usd,
        fdv=fdv,
        market_cap=market_cap,
        price_usd=price_usd,
        volume_m5=snapshot.volume_m5,
        volume_h1=snapshot.volume_h1,
        volume_h24=snapshot.volume_h24,
        buys_m5=snapshot.buys_m5,
        sells_m5=snapshot.sells_m5,
        buys_h1=snapshot.buys_h1,
        sells_h1=snapshot.sells_h1,
        tx_count_m5=tx_count_m5,
        tx_count_h1=tx_count_h1,
        buy_sell_ratio_m5=buy_sell_ratio_m5,
        buy_sell_ratio_h1=buy_sell_ratio_h1,
        liquidity_to_fdv=liquidity_to_fdv,
        volume_to_liquidity_h1=volume_to_liquidity_h1,
        website_count=snapshot.website_count,
        social_count=snapshot.social_count,
        boosts_active=snapshot.boosts_active,
        price_change_m5=snapshot.price_change_m5,
        price_change_h1=snapshot.price_change_h1,
        price_change_h24=snapshot.price_change_h24,
        risk_flags=tuple(sorted(set(risk_flags))),
    )


def _ratio(buys: int, sells: int) -> float:
    if buys <= 0 and sells <= 0:
        return 0.0
    if sells <= 0:
        return float(buys)
    return buys / sells

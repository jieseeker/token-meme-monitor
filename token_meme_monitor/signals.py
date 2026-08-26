from __future__ import annotations

from typing import Any, Mapping

from token_meme_monitor.config import SignalConfig
from token_meme_monitor.models import FeatureVector, SignalDecision
from token_meme_monitor.utils import clamp


class SignalEngine:
    def __init__(self, config: SignalConfig) -> None:
        self._config = config

    def evaluate(
        self,
        features: FeatureVector,
        *,
        observed_at,
        monitor_universe: str = "new_pairs",
        token_metadata: Mapping[str, Any] | None = None,
    ) -> SignalDecision:
        score = 0
        reasons: list[str] = []
        risk_flags = list(features.risk_flags)
        token_metadata = token_metadata or {}

        if monitor_universe != "binance_alpha":
            if features.age_minutes <= 120:
                score += 10
                reasons.append("early_age_window")
            elif features.age_minutes <= 360:
                score += 5
                reasons.append("tradable_age_window")

        if self._config.min_liquidity_usd <= features.liquidity_usd <= 250_000:
            score += 15
            reasons.append("healthy_liquidity_band")
        elif features.liquidity_usd > 250_000:
            score += 8
            reasons.append("ample_liquidity")
        else:
            score -= 20

        if features.volume_h1 >= self._config.min_volume_h1_usd:
            score += 15
            reasons.append("h1_volume_support")
        elif features.volume_m5 >= self._config.min_volume_h1_usd * 0.12:
            score += 6
            reasons.append("m5_volume_impulse")

        if (
            features.buys_m5 >= self._config.min_buy_count_m5
            and features.buy_sell_ratio_m5 >= self._config.min_buy_sell_ratio_m5
        ):
            score += 20
            reasons.append("m5_buy_dominance")
        elif features.buy_sell_ratio_h1 >= 1.3 and features.buys_h1 >= self._config.min_buy_count_m5 * 2:
            score += 12
            reasons.append("h1_buy_dominance")

        if features.volume_to_liquidity_h1 is not None:
            if features.volume_to_liquidity_h1 >= self._config.focus_volume_to_liquidity_ratio:
                score += 15
                reasons.append("volume_to_liquidity_breakout")
            elif features.volume_to_liquidity_h1 >= 0.12:
                score += 8
                reasons.append("volume_to_liquidity_support")

        if features.liquidity_to_fdv is not None:
            if 0.04 <= features.liquidity_to_fdv <= 0.40:
                score += 10
                reasons.append("balanced_liquidity_to_fdv")

        metadata_count = features.website_count + features.social_count
        if metadata_count >= 2:
            score += 5
            reasons.append("project_metadata_present")
        elif metadata_count == 1:
            score += 3
            reasons.append("partial_metadata_present")

        if features.boosts_active > 0:
            score += 5
            reasons.append("active_dex_boost")

        if features.price_change_h1 > 20 and features.price_change_m5 > 0:
            score += 5
            reasons.append("positive_price_trend")
        elif features.price_change_h1 < -20:
            score -= 5

        if monitor_universe == "binance_alpha":
            alpha_score = _metadata_float(token_metadata.get("alpha_score"))
            holder_count = _metadata_int(token_metadata.get("holder_count"))
            if alpha_score is not None:
                if alpha_score >= 100:
                    score += 10
                    reasons.append("alpha_hot_score")
                elif alpha_score >= 80:
                    score += 5
                    reasons.append("alpha_score_support")
            if holder_count is not None and holder_count >= 10_000:
                score += 5
                reasons.append("holder_depth")
            if _metadata_bool(token_metadata.get("binance_futures_listed")):
                score += 8
                reasons.append("binance_futures_listed")
            if features.volume_to_liquidity_h1 is not None and features.volume_to_liquidity_h1 >= 3:
                score += 5
                reasons.append("speculative_pool_activity")

        if "sell_pressure" in risk_flags:
            score -= 8
        if "fdv_liquidity_stretched" in risk_flags:
            score -= 8
        if "missing_price" in risk_flags:
            score -= 20

        score = clamp(score)
        severe_flags = {"missing_price", "liquidity_near_zero"}
        if monitor_universe != "binance_alpha":
            severe_flags.add("stale_pair")
        has_severe_risk = any(flag in severe_flags for flag in risk_flags)
        has_structural_alert_blocker = "fdv_liquidity_stretched" in risk_flags
        if has_severe_risk:
            pair_state = "archived"
        elif score >= self._config.alert_score_threshold and not has_structural_alert_blocker:
            pair_state = "alerted"
        elif score >= self._config.focus_score_threshold:
            pair_state = "focused"
        else:
            pair_state = "watching"

        should_alert = pair_state == "alerted" and not has_severe_risk
        return SignalDecision(
            observed_at=observed_at,
            strategy_version=self._config.strategy_version,
            score=score,
            pair_state=pair_state,
            should_alert=should_alert,
            reasons=tuple(reasons),
            risk_flags=tuple(sorted(set(risk_flags))),
            features=features.to_dict(),
        )


def _metadata_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metadata_int(value: Any) -> int | None:
    numeric = _metadata_float(value)
    return int(numeric) if numeric is not None else None


def _metadata_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)

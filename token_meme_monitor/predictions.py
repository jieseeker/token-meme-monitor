from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from token_meme_monitor.models import PredictionResult, SignalDecision
from token_meme_monitor.prediction_outcomes import (
    MIN_OUTCOME_SAMPLE_2H,
    MIN_OUTCOME_SAMPLE_6H,
    MIN_OUTCOME_SAMPLE_24H,
)
from token_meme_monitor.utils import json_loads, parse_datetime


PREDICTOR_VERSION = "p4"
MIN_CALIBRATION_SAMPLES = 12
MIN_CALIBRATION_RAISE_SAMPLES = 60
CALIBRATION_PRIOR_STRENGTH = 12.0
MAX_CALIBRATION_BLEND = 0.65
CALIBRATION_EPISODE_HOURS = 2
MAX_CALIBRATION_PRICE_DIVERGENCE_PCT = 0.10


@dataclass
class _CalibrationBucket:
    hit_2h_up20: int = 0
    sample_2h_up20: int = 0
    hit_6h_up50: int = 0
    sample_6h_up50: int = 0
    hit_24h_up100: int = 0
    sample_24h_up100: int = 0
    hit_6h_dd30: int = 0
    sample_6h_dd30: int = 0

    @property
    def max_samples(self) -> int:
        return max(
            self.sample_2h_up20,
            self.sample_6h_up50,
            self.sample_24h_up100,
            self.sample_6h_dd30,
        )


@dataclass(frozen=True)
class PredictionCalibration:
    buckets: dict[tuple[str, ...], _CalibrationBucket]
    total_rows: int

    def match(
        self,
        *,
        score: int,
        stage: str,
        features: Mapping[str, Any],
        token_metadata: Mapping[str, Any],
    ) -> _CalibrationBucket | None:
        for key in _calibration_keys(score=score, stage=stage, features=features, token_metadata=token_metadata):
            bucket = self.buckets.get(key)
            if bucket is not None and bucket.max_samples >= MIN_CALIBRATION_SAMPLES:
                return bucket
        return None


def build_prediction_calibration(rows: list[Mapping[str, Any]]) -> PredictionCalibration:
    buckets: dict[tuple[str, ...], _CalibrationBucket] = {}
    total_rows = 0
    last_episode_at: dict[tuple[str, ...], datetime] = {}
    for row in sorted(rows, key=_calibration_sort_key):
        features = _mapping_from_json(row.get("feature_json"))
        token_metadata = _mapping_from_json(row.get("token_metadata_json"))
        stage = str(row.get("stage") or "early")
        score = _int(row.get("score"))
        if not _has_calibration_outcome(row):
            continue
        if _is_duplicate_calibration_episode(
            row,
            score=score,
            stage=stage,
            features=features,
            token_metadata=token_metadata,
            last_episode_at=last_episode_at,
        ):
            continue
        total_rows += 1
        for key in _calibration_keys(score=score, stage=stage, features=features, token_metadata=token_metadata):
            bucket = buckets.setdefault(key, _CalibrationBucket())
            _add_outcome(bucket, row)
    return PredictionCalibration(buckets=buckets, total_rows=total_rows)


def build_prediction_result(
    decision: SignalDecision,
    *,
    token_metadata: Mapping[str, Any] | None = None,
    calibration: PredictionCalibration | None = None,
) -> PredictionResult:
    features = decision.features
    metadata = token_metadata or {}
    reasons: list[str] = []

    alpha_score = _float(metadata.get("alpha_score"))
    holder_count = _float(metadata.get("holder_count"))
    h1_return = _float(features.get("h1_return_live"))
    h4_return = _float(features.get("h4_return_live"))
    h24_return = _float(features.get("h24_return_live"))
    price_change_m5 = _pct_to_ratio(_float(features.get("price_change_m5")))
    price_change_h1 = _pct_to_ratio(_float(features.get("price_change_h1")))
    volume_impulse_24h = _float(features.get("volume_impulse_vs_prev24h"))
    volume_impulse_72h = _float(features.get("volume_impulse_vs_prev72h"))
    volume_to_liquidity = _float(features.get("volume_to_liquidity_h1"))
    buy_sell_m5 = _float(features.get("buy_sell_ratio_m5"))
    buy_sell_h1 = _float(features.get("buy_sell_ratio_h1"))
    liquidity_to_fdv = _float(features.get("liquidity_to_fdv"))
    market_cap = _float(features.get("market_cap")) or _float(features.get("fdv"))
    risk_flags = set(decision.risk_flags)

    early_score = 0.0
    momentum_score = 0.0
    exhaustion_score = 0.0
    quality_score = 0.0

    if alpha_score is not None:
        if alpha_score >= 100:
            quality_score += 1.4
            reasons.append("prediction_alpha_hot")
        elif alpha_score >= 80:
            quality_score += 0.8
            reasons.append("prediction_alpha_support")

    if holder_count is not None and holder_count >= 10_000:
        quality_score += 0.7
        reasons.append("prediction_holder_depth")

    if _bool(metadata.get("binance_futures_listed")):
        quality_score += 0.8
        reasons.append("prediction_futures_attention")

    if volume_to_liquidity is not None:
        if volume_to_liquidity >= 3:
            momentum_score += 1.5
            reasons.append("prediction_pool_turnover_hot")
        elif volume_to_liquidity >= 0.5:
            momentum_score += 0.8
            reasons.append("prediction_pool_turnover_support")

    if volume_impulse_24h is not None:
        if volume_impulse_24h >= 3:
            early_score += 1.2
            momentum_score += 0.8
            reasons.append("prediction_volume_impulse_24h")
        elif volume_impulse_24h >= 1.8:
            early_score += 0.6

    if volume_impulse_72h is not None:
        if volume_impulse_72h >= 2:
            early_score += 1.0
            momentum_score += 0.5
            reasons.append("prediction_volume_impulse_72h")
        elif volume_impulse_72h >= 1.4:
            early_score += 0.5

    if buy_sell_m5 is not None and buy_sell_m5 >= 1.8:
        momentum_score += 0.9
        reasons.append("prediction_m5_buy_pressure")
    elif buy_sell_h1 is not None and buy_sell_h1 >= 1.25:
        momentum_score += 0.5
        reasons.append("prediction_h1_buy_pressure")

    if h1_return is not None:
        if 0.03 <= h1_return <= 0.35:
            early_score += 0.8
            momentum_score += 0.6
            reasons.append("prediction_price_accelerating")
        elif h1_return > 0.7:
            exhaustion_score += 1.4
            reasons.append("prediction_h1_overextended")

    if h4_return is not None:
        if 0.05 <= h4_return <= 0.9:
            momentum_score += 0.7
        elif h4_return > 1.6:
            exhaustion_score += 1.7
            reasons.append("prediction_h4_overextended")

    if h24_return is not None and h24_return > 3:
        exhaustion_score += 1.5
        reasons.append("prediction_h24_overextended")

    if price_change_h1 is not None:
        if 0.05 <= price_change_h1 <= 0.45:
            momentum_score += 0.5
        elif price_change_h1 > 1.0:
            exhaustion_score += 1.0

    if price_change_m5 is not None and price_change_m5 < -0.03:
        exhaustion_score += 0.8
        reasons.append("prediction_m5_reversal")

    if "sell_pressure" in risk_flags:
        exhaustion_score += 1.2
        reasons.append("prediction_sell_pressure")
    if "fdv_liquidity_stretched" in risk_flags:
        exhaustion_score += 0.6
        reasons.append("prediction_stretched_structure")
    if liquidity_to_fdv is not None and liquidity_to_fdv < 0.001:
        exhaustion_score += 0.6

    if market_cap is not None and market_cap > 50_000_000:
        exhaustion_score += 0.4

    stage = _stage(early_score, momentum_score, exhaustion_score)
    base = decision.score / 100.0
    signal_strength = max(0.0, min(1.0, (decision.score - 35.0) / 65.0))
    prob_2h_up20 = _probability_from_prior(
        prior=0.023,
        positive=0.18 * signal_strength + 0.22 * early_score + 0.20 * momentum_score + 0.08 * quality_score,
        negative=0.30 * exhaustion_score,
        cap=0.18,
    )
    prob_6h_up50 = _probability_from_prior(
        prior=0.022,
        positive=0.16 * signal_strength + 0.18 * early_score + 0.24 * momentum_score + 0.10 * quality_score,
        negative=0.34 * exhaustion_score,
        cap=0.16,
    )
    prob_24h_up100 = _probability_from_prior(
        prior=0.028,
        positive=0.12 * signal_strength + 0.14 * early_score + 0.20 * momentum_score + 0.16 * quality_score,
        negative=0.32 * exhaustion_score,
        cap=0.20,
    )
    risk_6h_dd30 = _probability_from_prior(
        prior=0.025,
        positive=0.48 * exhaustion_score
        + 0.12 * max(0.0, momentum_score - early_score)
        + (0.35 if "fdv_liquidity_stretched" in risk_flags else 0.0)
        + (0.45 if "sell_pressure" in risk_flags else 0.0),
        negative=0.08 * quality_score,
        cap=0.35,
    )
    if calibration is not None:
        calibration_bucket = calibration.match(
            score=decision.score,
            stage=stage,
            features=features,
            token_metadata=metadata,
        )
        if calibration_bucket is not None:
            before_upside_average = (prob_2h_up20 + prob_6h_up50 + prob_24h_up100) / 3.0
            before_risk = risk_6h_dd30
            prob_2h_up20 = _calibrate_probability(
                prob_2h_up20,
                hits=calibration_bucket.hit_2h_up20,
                samples=calibration_bucket.sample_2h_up20,
                cap=0.18,
                min_raise_samples=MIN_CALIBRATION_RAISE_SAMPLES,
            )
            prob_6h_up50 = _calibrate_probability(
                prob_6h_up50,
                hits=calibration_bucket.hit_6h_up50,
                samples=calibration_bucket.sample_6h_up50,
                cap=0.16,
                min_raise_samples=MIN_CALIBRATION_RAISE_SAMPLES,
            )
            prob_24h_up100 = _calibrate_probability(
                prob_24h_up100,
                hits=calibration_bucket.hit_24h_up100,
                samples=calibration_bucket.sample_24h_up100,
                cap=0.20,
                min_raise_samples=MIN_CALIBRATION_RAISE_SAMPLES,
            )
            risk_6h_dd30 = _calibrate_probability(
                risk_6h_dd30,
                hits=calibration_bucket.hit_6h_dd30,
                samples=calibration_bucket.sample_6h_dd30,
                cap=0.35,
                min_raise_samples=MIN_CALIBRATION_SAMPLES,
            )
            after_upside_average = (prob_2h_up20 + prob_6h_up50 + prob_24h_up100) / 3.0
            if (
                abs(after_upside_average - before_upside_average) >= 0.002
                or abs(risk_6h_dd30 - before_risk) >= 0.002
            ):
                reasons.append("prediction_empirical_calibrated")
                if after_upside_average < before_upside_average or risk_6h_dd30 > before_risk:
                    reasons.append("prediction_empirical_lowered")
                elif after_upside_average > before_upside_average:
                    reasons.append("prediction_empirical_raised")
                if calibration_bucket.max_samples < 40:
                    reasons.append("prediction_empirical_sparse")

    risk_ratio = risk_6h_dd30 / 0.35
    short_momentum_score = _score_from_components(
        (prob_2h_up20 / 0.18) * 72.0
        + (prob_6h_up50 / 0.16) * 8.0
        - risk_ratio * 16.0
        + 16.0
    )
    continuation_score = _score_from_components(
        (prob_6h_up50 / 0.16) * 58.0
        + (prob_2h_up20 / 0.18) * 12.0
        + (prob_24h_up100 / 0.20) * 8.0
        - risk_ratio * 26.0
        + 8.0
    )
    breakout_score = _score_from_components(
        (prob_24h_up100 / 0.20) * 62.0
        + (prob_6h_up50 / 0.16) * 10.0
        - risk_ratio * 30.0
        + 4.0
    )
    opportunity_score = short_momentum_score
    if short_momentum_score >= 50:
        reasons.append("prediction_short_momentum_opportunity")
    elif short_momentum_score <= 35:
        reasons.append("prediction_low_opportunity")
    if continuation_score >= 60:
        reasons.append("prediction_continuation_opportunity")
    if breakout_score >= 60:
        reasons.append("prediction_breakout_watch")

    return PredictionResult(
        predictor_version=PREDICTOR_VERSION,
        prob_2h_up20=prob_2h_up20,
        prob_6h_up50=prob_6h_up50,
        prob_24h_up100=prob_24h_up100,
        risk_6h_dd30=risk_6h_dd30,
        opportunity_score=opportunity_score,
        stage=stage,
        reasons=tuple(dict.fromkeys(reasons)),
        short_momentum_score=short_momentum_score,
        continuation_score=continuation_score,
        breakout_score=breakout_score,
    )


def _stage(early_score: float, momentum_score: float, exhaustion_score: float) -> str:
    if exhaustion_score >= max(1.8, early_score + momentum_score * 0.45):
        return "exhaustion"
    if momentum_score >= 2.0 and exhaustion_score >= 1.2:
        return "late"
    if momentum_score >= 1.5:
        return "acceleration"
    if early_score >= 1.0:
        return "early"
    return "early"


def _calibrate_probability(base: float, *, hits: int, samples: int, cap: float, min_raise_samples: int) -> float:
    if samples < MIN_CALIBRATION_SAMPLES:
        return base
    smoothed_empirical = (hits + base * CALIBRATION_PRIOR_STRENGTH) / (samples + CALIBRATION_PRIOR_STRENGTH)
    blend = min(MAX_CALIBRATION_BLEND, samples / (samples + 40.0))
    calibrated = max(0.005, min(cap, base * (1.0 - blend) + smoothed_empirical * blend))
    if calibrated > base and samples < min_raise_samples:
        return base
    return round(calibrated, 4)


def _calibration_keys(
    *,
    score: int,
    stage: str,
    features: Mapping[str, Any],
    token_metadata: Mapping[str, Any],
) -> tuple[tuple[str, ...], ...]:
    score_bucket = _score_bucket(score)
    h1_bucket = _return_bucket(_float(features.get("h1_return_live")), short_window=True)
    h24_bucket = _return_bucket(_float(features.get("h24_return_live")), short_window=False)
    turnover_bucket = _turnover_bucket(_float(features.get("volume_to_liquidity_h1")))
    quality_bucket = _quality_bucket(token_metadata)
    stage = stage or "early"
    return (
        ("stage-score-h1-h24-turnover-quality", stage, score_bucket, h1_bucket, h24_bucket, turnover_bucket, quality_bucket),
        ("stage-score-h1-h24-turnover", stage, score_bucket, h1_bucket, h24_bucket, turnover_bucket),
        ("stage-h1-h24-turnover", stage, h1_bucket, h24_bucket, turnover_bucket),
        ("stage-h24-turnover", stage, h24_bucket, turnover_bucket),
        ("stage", stage),
        ("global",),
    )


def _score_bucket(score: int) -> str:
    if score >= 78:
        return "alert"
    if score >= 65:
        return "focus"
    if score >= 50:
        return "watch"
    return "low"


def _return_bucket(value: float | None, *, short_window: bool) -> str:
    if value is None:
        return "unknown"
    if value < -0.03:
        return "pullback"
    if value < 0.03:
        return "flat"
    if short_window:
        if value <= 0.35:
            return "warming"
        if value <= 0.70:
            return "hot"
        return "overextended"
    if value <= 1.0:
        return "warming"
    if value <= 3.0:
        return "hot"
    return "overextended"


def _turnover_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.5:
        return "low"
    if value < 3.0:
        return "support"
    return "hot"


def _quality_bucket(metadata: Mapping[str, Any]) -> str:
    alpha_score = _float(metadata.get("alpha_score"))
    holder_count = _float(metadata.get("holder_count"))
    if alpha_score is not None and alpha_score >= 100:
        return "alpha_hot"
    if _bool(metadata.get("binance_futures_listed")):
        return "futures"
    if holder_count is not None and holder_count >= 10_000:
        return "holder_depth"
    return "standard"


def _mapping_from_json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    parsed = json_loads(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _calibration_sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
    observed_at = _row_observed_at(row)
    if observed_at is None:
        return (1, "")
    return (0, observed_at.astimezone(timezone.utc).isoformat(timespec="seconds"))


def _is_duplicate_calibration_episode(
    row: Mapping[str, Any],
    *,
    score: int,
    stage: str,
    features: Mapping[str, Any],
    token_metadata: Mapping[str, Any],
    last_episode_at: dict[tuple[str, ...], datetime],
) -> bool:
    observed_at = _row_observed_at(row)
    identity = str(row.get("pair_address") or row.get("token_address") or "")
    if observed_at is None or not identity:
        return False
    episode_key = (
        identity.lower(),
        stage or "early",
        _score_bucket(score),
        _return_bucket(_float(features.get("h1_return_live")), short_window=True),
        _return_bucket(_float(features.get("h24_return_live")), short_window=False),
        _turnover_bucket(_float(features.get("volume_to_liquidity_h1"))),
        _quality_bucket(token_metadata),
    )
    previous_at = last_episode_at.get(episode_key)
    if previous_at is not None and (observed_at - previous_at).total_seconds() < CALIBRATION_EPISODE_HOURS * 3600:
        return True
    last_episode_at[episode_key] = observed_at
    return False


def _row_observed_at(row: Mapping[str, Any]) -> datetime | None:
    raw = row.get("observed_at")
    if isinstance(raw, datetime):
        parsed = raw
    else:
        try:
            parsed = parse_datetime(str(raw)) if raw else None
        except ValueError:
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_calibration_outcome(row: Mapping[str, Any]) -> bool:
    if not _row_quality_usable_for_calibration(row):
        return False
    return (
        _has_eligible_2h_outcome(row)
        or _has_eligible_6h_outcome(row)
        or _has_eligible_24h_outcome(row)
    )


def _add_outcome(bucket: _CalibrationBucket, row: Mapping[str, Any]) -> None:
    if _has_eligible_2h_outcome(row):
        bucket.sample_2h_up20 += 1
        bucket.hit_2h_up20 += _int(row.get("hit_2h_up20"))
    if _has_eligible_6h_outcome(row):
        bucket.sample_6h_up50 += 1
        bucket.hit_6h_up50 += _int(row.get("hit_6h_up50"))
        bucket.sample_6h_dd30 += 1
        bucket.hit_6h_dd30 += _int(row.get("hit_6h_dd30"))
    if _has_eligible_24h_outcome(row):
        bucket.sample_24h_up100 += 1
        bucket.hit_24h_up100 += _int(row.get("hit_24h_up100"))


def _row_quality_usable_for_calibration(row: Mapping[str, Any]) -> bool:
    outcome_source = str(row.get("outcome_source") or "")
    if outcome_source == "local_snapshots":
        return False
    price_divergence_pct = _float(row.get("price_divergence_pct"))
    if price_divergence_pct is not None and abs(price_divergence_pct) > MAX_CALIBRATION_PRICE_DIVERGENCE_PCT:
        return False
    quality_flags = _quality_flags(row)
    return "price_source_divergence_gt_10pct" not in quality_flags


def _has_eligible_2h_outcome(row: Mapping[str, Any]) -> bool:
    return _int(row.get("sample_count_2h")) >= MIN_OUTCOME_SAMPLE_2H and not (
        {"partial_2h_ohlcv", "partial_2h_snapshots"} & set(_quality_flags(row))
    )


def _has_eligible_6h_outcome(row: Mapping[str, Any]) -> bool:
    return _int(row.get("sample_count_6h")) >= MIN_OUTCOME_SAMPLE_6H and not (
        {"partial_6h_ohlcv", "partial_6h_snapshots"} & set(_quality_flags(row))
    )


def _has_eligible_24h_outcome(row: Mapping[str, Any]) -> bool:
    return _int(row.get("sample_count_24h")) >= MIN_OUTCOME_SAMPLE_24H and not (
        {"partial_24h_ohlcv", "partial_24h_snapshots"} & set(_quality_flags(row))
    )


def _quality_flags(row: Mapping[str, Any]) -> list[str]:
    raw = row.get("quality_flags_json")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    parsed = json_loads(str(raw), []) if raw not in (None, "") else []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_to_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100.0


def _bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _probability_from_prior(*, prior: float, positive: float, negative: float, cap: float) -> float:
    prior = max(0.0001, min(0.9999, prior))
    logit = math.log(prior / (1.0 - prior)) + positive - negative
    probability = 1.0 / (1.0 + math.exp(-logit))
    return round(max(0.005, min(cap, probability)), 4)


def _score_from_components(value: float) -> int:
    return int(round(max(0.0, min(100.0, value))))

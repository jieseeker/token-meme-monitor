#!/usr/bin/env python3
import json
import math
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


FACTORS = [
    "momentum_5m",
    "momentum_15m",
    "breakout_30m",
    "relative_volume_30m",
    "buy_pressure_5m",
    "liquidity_stability_30m",
]


def fetch_json(url):
    with urlopen(url, timeout=20) as response:
        return json.load(response)


def epoch(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def median(values):
    return statistics.median(values) if values else None


def finite(value):
    return value is not None and math.isfinite(value)


def nearest_prior(samples, target, tolerance):
    for sample in reversed(samples):
        if target - tolerance <= sample["at"] <= target + tolerance:
            return sample
    return None


def load_samples(conn, token):
    rows = conn.execute(
        """
        SELECT pair_address,observed_at,price_usd,volume_m5,liquidity_usd,buys_m5,sells_m5
        FROM snapshots WHERE token_address=? ORDER BY observed_at DESC LIMIT 2000
        """,
        (token,),
    ).fetchall()
    samples = []
    for pair, observed_at, price, volume, liquidity, buys, sells in rows:
        at = epoch(observed_at)
        if at is None or not finite(price) or price <= 0:
            continue
        samples.append(
            {
                "at": int(at),
                "pair": pair,
                "price": price,
                "volume_m5": volume if finite(volume) and volume >= 0 else 0.0,
                "liquidity_usd": liquidity if finite(liquidity) and liquidity >= 0 else 0.0,
                "buys_m5": buys or 0,
                "sells_m5": sells or 0,
            }
        )
    return samples


def expected_structure(conn, token, as_of=None):
    samples = load_samples(conn, token)
    if as_of is not None:
        samples = [sample for sample in samples if sample["at"] <= as_of]
    if not samples:
        return {"available": False, "reason": "暂无可用快照"}
    latest_at = max(sample["at"] for sample in samples)
    window = [sample for sample in samples if latest_at - sample["at"] <= 3600]
    if not window:
        return {"available": False, "reason": "暂无可用池子"}
    selected = window[0]
    for sample in window[1:]:
        if sample["liquidity_usd"] > selected["liquidity_usd"] or (
            sample["liquidity_usd"] == selected["liquidity_usd"]
            and sample["pair"] < selected["pair"]
        ):
            selected = sample
    samples = sorted(
        (sample for sample in window if sample["pair"] == selected["pair"]),
        key=lambda sample: sample["at"],
    )
    if len(samples) < 6 or samples[-1]["at"] - samples[0]["at"] < 1500:
        return {"available": False, "reason": "历史样本不足"}
    latest = samples[-1]
    history = samples[:-1]
    momentum_5m = None
    momentum_15m = None
    prior = nearest_prior(history, latest["at"] - 300, 300)
    if prior:
        momentum_5m = latest["price"] / prior["price"] - 1
    prior = nearest_prior(history, latest["at"] - 900, 600)
    if prior:
        momentum_15m = latest["price"] / prior["price"] - 1
    recent = []
    for sample in reversed(history):
        if latest["at"] - sample["at"] > 1800:
            break
        recent.append(sample)
    breakout = latest["price"] / max(sample["price"] for sample in recent) - 1 if recent else None
    volume_median = median([sample["volume_m5"] for sample in recent])
    relative_volume = (
        latest["volume_m5"] / volume_median
        if volume_median is not None and volume_median >= 1
        else None
    )
    buy_pressure = (
        latest["buys_m5"] / (latest["buys_m5"] + latest["sells_m5"])
        if latest["buys_m5"] + latest["sells_m5"] > 0
        else None
    )
    liquidity_median = median([sample["liquidity_usd"] for sample in recent])
    liquidity_stability = (
        latest["liquidity_usd"] / liquidity_median
        if liquidity_median is not None and liquidity_median > 0
        else None
    )
    return {
        "available": True,
        "pair": selected["pair"],
        "updated_at": latest["at"],
        "factors": {
            "momentum_5m": momentum_5m,
            "momentum_15m": momentum_15m,
            "breakout_30m": breakout,
            "relative_volume_30m": relative_volume,
            "buy_pressure_5m": buy_pressure,
            "liquidity_stability_30m": liquidity_stability,
        },
    }


def close_enough(left, right):
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= 1e-10


def forward_return(conn, pair, token, start_at, horizon):
    target = start_at + horizon
    row = conn.execute(
        """
        SELECT observed_at,price_usd FROM snapshots
        WHERE pair_address=? AND token_address=? AND price_usd IS NOT NULL AND price_usd>0
          AND julianday(observed_at)>=julianday(?, '+{} seconds')
          AND julianday(observed_at)<=julianday(?, '+{} seconds')
        ORDER BY observed_at LIMIT 1
        """.format(horizon, horizon + 300),
        (
            pair,
            token,
            datetime.fromtimestamp(start_at, timezone.utc).isoformat(),
            datetime.fromtimestamp(start_at, timezone.utc).isoformat(),
        ),
    ).fetchone()
    if not row:
        return None
    start = conn.execute(
        "SELECT price_usd FROM snapshots WHERE pair_address=? AND token_address=? AND observed_at=? LIMIT 1",
        (pair, token, row[0]),
    ).fetchone()
    if not start:
        return None
    observed_at = epoch(row[0])
    prior = conn.execute(
        """
        SELECT price_usd FROM snapshots WHERE pair_address=? AND token_address=?
          AND price_usd IS NOT NULL AND price_usd>0 AND observed_at<=?
        ORDER BY observed_at DESC LIMIT 1
        """,
        (pair, token, datetime.fromtimestamp(start_at, timezone.utc).isoformat()),
    ).fetchone()
    if observed_at is None or not prior:
        return None
    return row[1] / prior[0] - 1


def correlation(pairs):
    pairs = [(left, right) for left, right in pairs if left is not None and right is not None]
    if len(pairs) < 3:
        return None
    left_values = [pair[0] for pair in pairs]
    right_values = [pair[1] for pair in pairs]
    if statistics.pstdev(left_values) == 0 or statistics.pstdev(right_values) == 0:
        return None
    return statistics.correlation(left_values, right_values)


def append_row(path, row):
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8") as handle:
        if not exists:
            handle.write("\t".join(row.keys()) + "\n")
        handle.write("\t".join(str(value) for value in row.values()) + "\n")


def verify_round(conn, structure, signal, tokens, capture_at, out_dir, stamp):
    signal_by_token = {
        str(item.get("token_address", "")).lower(): item for item in signal.get("summaries", [])
    }
    token_rows = {str(item.get("token_address", "")).lower(): item for item in tokens}
    checks = []
    factor_mismatches = {factor: 0 for factor in FACTORS}
    stage_mismatch = 0
    stale_structure = 0
    missing_tokens = 0
    for item in structure.get("items", []):
        token = str(item.get("token_address", "")).lower()
        actual_available = bool(item.get("available"))
        expected = expected_structure(conn, token, item.get("updated_at"))
        base = {
            "captured_at": capture_at,
            "token": token,
            "page_available": int(actual_available),
            "raw_available": int(expected["available"]),
            "pair_match": int(expected.get("pair") == item.get("pair_address")),
            "updated_at_match": int(expected.get("updated_at") == item.get("updated_at")),
        }
        for factor in FACTORS:
            page_value = (item.get("factors") or {}).get(factor, {}).get("value")
            raw_value = expected.get("factors", {}).get(factor)
            matched = close_enough(page_value, raw_value)
            base[f"{factor}_match"] = int(matched)
            if not matched:
                factor_mismatches[factor] += 1
        summary = signal_by_token.get(token)
        stage_match = int(
            summary is not None
            and item.get("stage") == summary.get("stage")
            and item.get("data_status") == summary.get("data_status")
            and item.get("risk_status") == summary.get("risk_status")
        )
        base["signal_stage_match"] = stage_match
        if not stage_match:
            stage_mismatch += 1
        token_row = token_rows.get(token)
        if token_row is None:
            missing_tokens += 1
        base["token_list_present"] = int(token_row is not None)
        updated_at = item.get("updated_at")
        fresh = updated_at is not None and capture_at - updated_at <= 180
        base["fresh_within_180s"] = int(fresh)
        if not fresh:
            stale_structure += 1
        checks.append(base)
    for row in checks:
        append_row(os.path.join(out_dir, "checks.tsv"), row)
    issues = []
    for factor, count in factor_mismatches.items():
        if count:
            issues.append(f"{factor}_mismatch={count}")
    if stage_mismatch:
        issues.append(f"signal_stage_mismatch={stage_mismatch}")
    if missing_tokens:
        issues.append(f"token_list_missing={missing_tokens}")
    if stale_structure:
        issues.append(f"structure_stale_over_180s={stale_structure}")
    summary = {
        "captured_at": capture_at,
        "items": len(structure.get("items", [])),
        "available": sum(bool(item.get("available")) for item in structure.get("items", [])),
        "factor_mismatches": factor_mismatches,
        "stage_mismatch": stage_mismatch,
        "missing_tokens": missing_tokens,
        "stale_structure": stale_structure,
        "issues": ";".join(issues) or "ok",
    }
    append_row(
        os.path.join(out_dir, "rounds.tsv"),
        {key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else value for key, value in summary.items()},
    )
    for issue in issues:
        append_row(os.path.join(out_dir, "issues.tsv"), {"captured_at": capture_at, "issue": issue})
    return summary


def evaluate_previous(conn, previous, out_dir, capture_at):
    outcomes = []
    for item in previous.get("items", []):
        if not item.get("available"):
            continue
        token = str(item.get("token_address", "")).lower()
        pair = item.get("pair_address")
        started_at = item.get("updated_at")
        if not pair or started_at is None:
            continue
        forward_15m = forward_return(conn, pair, token, started_at, 900)
        forward_1h = forward_return(conn, pair, token, started_at, 3600)
        factors = item.get("factors") or {}
        outcome = {
            "captured_at": capture_at,
            "signal_at": started_at,
            "token": token,
            "structure_status": item.get("structure_status"),
            "forward_15m": forward_15m,
            "forward_1h": forward_1h,
        }
        for factor in FACTORS:
            outcome[factor] = factors.get(factor, {}).get("value")
        outcomes.append(outcome)
    for outcome in outcomes:
        append_row(os.path.join(out_dir, "outcomes.tsv"), outcome)
    return outcomes


def credibility_summary(outcomes):
    result = {
        "outcome_rows": len(outcomes),
        "forward_15m_rows": sum(outcome["forward_15m"] is not None for outcome in outcomes),
        "forward_1h_rows": sum(outcome["forward_1h"] is not None for outcome in outcomes),
    }
    for horizon in ("forward_15m", "forward_1h"):
        values = [outcome[horizon] for outcome in outcomes if outcome[horizon] is not None]
        result[f"{horizon}_median"] = statistics.median(values) if values else None
        result[f"{horizon}_positive_rate"] = sum(value > 0 for value in values) / len(values) if values else None
        for factor in FACTORS:
            result[f"{horizon}_{factor}_correlation"] = correlation(
                [(outcome[factor], outcome[horizon]) for outcome in outcomes]
            )
    for status in ("strong", "neutral", "weak"):
        values = [
            outcome["forward_1h"]
            for outcome in outcomes
            if outcome["structure_status"] == status and outcome["forward_1h"] is not None
        ]
        result[f"status_{status}_1h_rows"] = len(values)
        result[f"status_{status}_1h_median"] = statistics.median(values) if values else None
    return result


def run():
    if len(sys.argv) != 6:
        raise SystemExit("usage: verify_structure_hourly.py DB BASE_URL START END OUTPUT_DIR")
    db_path, base_url, start, end, out_dir = sys.argv[1:]
    end = float("inf") if end == "continuous" else int(end)
    start = int(start)
    os.makedirs(out_dir, exist_ok=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    previous = None
    cumulative_outcomes = []
    while time.time() <= end:
        delay = start - time.time()
        if delay > 0:
            print(f"waiting {int(delay)}s until first round", flush=True)
            time.sleep(min(delay, 300))
            continue
        capture_at = int(time.time())
        stamp = datetime.fromtimestamp(capture_at, timezone.utc).strftime("%Y%m%d-%H%M%S")
        try:
            structure = fetch_json(f"{base_url}/api/structure?limit=100")
            signal = fetch_json(f"{base_url}/api/signal-v2?limit=500")
            tokens = fetch_json(f"{base_url}/api/tokens?limit=500")
        except (HTTPError, URLError, TimeoutError) as error:
            append_row(os.path.join(out_dir, "issues.tsv"), {"captured_at": capture_at, "issue": f"fetch_failed:{error}"})
            print(f"{stamp} fetch failed: {error}", flush=True)
            time.sleep(60)
            continue
        for name, payload in (
            ("structure", structure),
            ("signal-v2", signal),
            ("tokens", tokens),
        ):
            with open(os.path.join(out_dir, f"{name}-{stamp}.json"), "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        if previous is not None:
            cumulative_outcomes.extend(evaluate_previous(conn, previous, out_dir, capture_at))
        summary = verify_round(conn, structure, signal, tokens, capture_at, out_dir, stamp)
        summary["credibility"] = credibility_summary(cumulative_outcomes)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        previous = structure
        next_at = ((capture_at // 900) + 1) * 900
        delay = next_at - time.time()
        if delay <= 0:
            delay = 900
        if time.time() + delay <= end:
            time.sleep(delay)
        else:
            break
    if previous is not None:
        with open(os.path.join(out_dir, "final-credibility.json"), "w", encoding="utf-8") as handle:
            json.dump(credibility_summary(cumulative_outcomes), handle, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    run()

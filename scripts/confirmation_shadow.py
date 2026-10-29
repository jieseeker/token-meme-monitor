#!/usr/bin/env python3
"""Persist live A confirmation states and resolve their forward outcomes."""
import argparse
import json
import sqlite3
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


HORIZONS = {"15m": (900, "outcome_15m_pct"), "1h": (3600, "outcome_1h_pct"), "4h": (14400, "outcome_4h_pct")}


def parse_time(value):
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def slippage_values(warning):
    estimates = warning.get("slippage_estimates", {}).get("estimates", [])
    by_order = {float(row.get("order_usd", 0)): row.get("buy_slippage_pct") for row in estimates}
    return by_order.get(100.0), by_order.get(1000.0)


def classify(signal, warning, now):
    if signal.get("stage") != "watch" or signal.get("risk_status") != "PASS" or warning.get("is_active") is not True:
        return None
    first_seen = signal.get("first_seen_at")
    if not isinstance(first_seen, (int, float)):
        return None
    age = max(0, int(now - first_seen))
    slippage_100, slippage_1000 = slippage_values(warning)
    high_slippage = (slippage_1000 is not None and slippage_1000 > 0.5) or (
        slippage_100 is not None and slippage_100 > 0.3
    )
    classification = "slippage_blocked" if high_slippage else "strong" if age <= 300 else "aging" if age <= 900 else "stale"
    return {
        "age_seconds": age,
        "classification": classification,
        "volume_acceleration": warning.get("volume_acceleration"),
        "buy_pressure": warning.get("buy_pressure"),
        "volume_to_liquidity": warning.get("volume_to_liquidity"),
        "slippage_100_pct": slippage_100,
        "slippage_1000_pct": slippage_1000,
    }


def init_schema(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair_address TEXT NOT NULL,
            token_address TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            price_usd REAL,
            liquidity_usd REAL,
            volume_h1 REAL,
            volume_m5 REAL,
            raw_json TEXT,
            UNIQUE(pair_address,observed_at)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS confirmation_shadow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_address TEXT NOT NULL,
            pair_address TEXT NOT NULL,
            first_seen_at INTEGER NOT NULL,
            observed_at INTEGER NOT NULL,
            classification TEXT NOT NULL,
            age_seconds INTEGER NOT NULL,
            entry_price_usd REAL NOT NULL,
            volume_acceleration REAL,
            buy_pressure REAL,
            volume_to_liquidity REAL,
            slippage_100_pct REAL,
            slippage_1000_pct REAL,
            market_state TEXT,
            outcome_15m_pct REAL,
            outcome_1h_pct REAL,
            outcome_4h_pct REAL,
            outcome_state TEXT NOT NULL DEFAULT 'pending',
            UNIQUE(token_address, first_seen_at, classification)
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(confirmation_shadow_events)")}
    if "market_state" not in columns:
        connection.execute("ALTER TABLE confirmation_shadow_events ADD COLUMN market_state TEXT")
    connection.commit()


def record_event(connection, event):
    connection.execute(
        """
        INSERT OR IGNORE INTO confirmation_shadow_events
        (token_address,pair_address,first_seen_at,observed_at,classification,age_seconds,
         entry_price_usd,volume_acceleration,buy_pressure,volume_to_liquidity,
         slippage_100_pct,slippage_1000_pct,market_state)
        VALUES (:token_address,:pair_address,:first_seen_at,:observed_at,:classification,
                :age_seconds,:entry_price_usd,:volume_acceleration,:buy_pressure,
                :volume_to_liquidity,:slippage_100_pct,:slippage_1000_pct,:market_state)
        """,
        {**event, "market_state": event.get("market_state")},
    )
    connection.commit()


def insert_snapshot(connection, pair, observed_at, price):
    text = datetime.fromtimestamp(observed_at, timezone.utc).isoformat()
    connection.execute(
        "INSERT INTO snapshots(pair_address,token_address,observed_at,price_usd,liquidity_usd,volume_h1,volume_m5,raw_json) VALUES (?,'shadow',?,?,1,1,1,'{}')",
        (pair, text, price),
    )
    connection.commit()


def endpoint_price(connection, pair, target):
    row = connection.execute(
        """
        SELECT price_usd FROM snapshots
        WHERE pair_address=? AND price_usd IS NOT NULL AND price_usd>0
          AND CAST(strftime('%s',observed_at) AS INTEGER) BETWEEN ? AND ?
        ORDER BY observed_at LIMIT 1
        """,
        (pair, target, target + 300),
    ).fetchone()
    return row[0] if row else None


def resolve_outcomes(connection, now):
    rows = connection.execute(
        "SELECT id,pair_address,observed_at,entry_price_usd,outcome_15m_pct,outcome_1h_pct,outcome_4h_pct FROM confirmation_shadow_events"
    ).fetchall()
    for row_id, pair, observed_at, entry_price, outcome_15m, outcome_1h, outcome_4m in rows:
        updates = {}
        deadlines = []
        for field, (_, (horizon, column)) in zip(("outcome_15m", "outcome_1h", "outcome_4h"), HORIZONS.items()):
            current = {"outcome_15m": outcome_15m, "outcome_1h": outcome_1h, "outcome_4h": outcome_4m}[field]
            if current is not None:
                continue
            target = observed_at + horizon
            deadlines.append(target + 300)
            if now < target + 300:
                continue
            price = endpoint_price(connection, pair, target)
            if price is not None:
                updates[column] = (price / entry_price - 1.0) * 100.0
        if updates:
            assignment = ",".join(f"{column}=:{column}" for column in updates)
            values = dict(updates)
            values["id"] = row_id
            connection.execute(f"UPDATE confirmation_shadow_events SET {assignment} WHERE id=:id", values)
        updated = connection.execute(
            "SELECT outcome_15m_pct,outcome_1h_pct,outcome_4h_pct FROM confirmation_shadow_events WHERE id=?",
            (row_id,),
        ).fetchone()
        state = "pending" if now < max(deadlines, default=now) else "resolved" if all(value is not None for value in updated) else "endpoint_missing"
        connection.execute("UPDATE confirmation_shadow_events SET outcome_state=? WHERE id=?", (state,row_id))
    connection.commit()


def summarize(connection):
    rows = connection.execute(
        "SELECT classification,outcome_15m_pct,outcome_1h_pct,outcome_4h_pct,outcome_state FROM confirmation_shadow_events"
    ).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[0]].append(row)
    result = {}
    for classification, values in grouped.items():
        result[classification] = {"events": len(values), "resolved": sum(row[4] == "resolved" for row in values)}
        for label, index in (("15m", 1), ("1h", 2), ("4h", 3)):
            outcomes = [row[index] for row in values if row[index] is not None]
            result[classification][f"mean_{label}_pct"] = sum(outcomes) / len(outcomes) if outcomes else None
    return {"events": len(rows), "by_classification": result}


def fetch_json(base_url, path):
    with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=15) as response:
        return json.load(response)


def capture(api, connection):
    signals = fetch_json(api, "/api/signal-v2?limit=500").get("summaries", [])
    warnings = fetch_json(api, "/api/early-warning").get("items", [])
    market_state = fetch_json(api, "/api/market-state").get("state")
    signal_by_token = {str(row.get("token_address", "")).lower(): row for row in signals}
    now = int(time.time())
    for warning in warnings:
        token = str(warning.get("token_address", "")).lower()
        signal = signal_by_token.get(token)
        if not signal:
            continue
        state = classify(signal, warning, now)
        if not state:
            continue
        pair = warning.get("pair_address")
        price_row = connection.execute(
            "SELECT price_usd FROM snapshots WHERE pair_address=? AND price_usd>0 ORDER BY observed_at DESC LIMIT 1",
            (pair,),
        ).fetchone()
        if not price_row:
            continue
        record_event(
            connection,
            {
                **state,
                "token_address": token,
                "pair_address": pair,
                "first_seen_at": int(signal["first_seen_at"]),
                "observed_at": now,
                "entry_price_usd": price_row[0],
                "market_state": market_state,
            },
        )
    resolve_outcomes(connection, now)
    return summarize(connection)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8501")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.db, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    init_schema(connection)
    while True:
        print(json.dumps(capture(args.api, connection), ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

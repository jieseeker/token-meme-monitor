#!/usr/bin/env python3
"""Audit whether monitored 24h movers had an early warning or A transition."""
import argparse
import json
import sqlite3
import statistics
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def parse_time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def classify_mover(early_times, a_times, peak_at, lookback_seconds=86400):
    peak = parse_time(peak_at)
    if peak is None:
        return "invalid"
    cutoff = peak.timestamp() - lookback_seconds
    early = [value for value in early_times if cutoff <= value <= peak.timestamp()]
    a = [value for value in a_times if cutoff <= value <= peak.timestamp()]
    if early and a:
        return "early_and_a"
    if early:
        return "early_only"
    if a:
        return "a_only"
    return "missed"


def median_volume_history(rows, index):
    current = rows[index]
    values = []
    for row in reversed(rows[:index]):
        if current[0] - row[0] > 1800:
            break
        if row[3] > 0:
            values.append(row[3])
        if len(values) == 12:
            break
    return statistics.median(values) if len(values) >= 3 else None


def early_warning_times(rows):
    events = []
    last_at = 0
    for index, row in enumerate(rows):
        at, _pair, price, volume, liquidity, _buys, _sells = row
        baseline = median_volume_history(rows, index)
        if baseline is None or baseline <= 0 or liquidity <= 0:
            continue
        acceleration = volume / baseline
        efficiency = volume / liquidity
        prior = next((other for other in reversed(rows[:index]) if abs((at - other[0]) - 300) <= 420), None)
        change = (price / prior[2] - 1) * 100 if prior else None
        active = acceleration >= 2 and efficiency >= .01 and change is not None and -1 <= change <= 3
        if active and at - last_at >= 900:
            events.append(at)
            last_at = at
    return events


def a_transition_times(connection, token):
    rows = connection.execute(
        "SELECT recorded_at,summary_json FROM signal_v2_changes WHERE token_address=? ORDER BY recorded_at,id",
        (token,),
    ).fetchall()
    events = []
    previous = None
    for recorded_at, raw in rows:
        try:
            summary = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        stage = summary.get("stage")
        first_seen = summary.get("first_seen_at")
        terminated = bool(summary.get("terminated"))
        changed = previous is None or previous[0] == "inactive" or previous[1] or previous[2] != first_seen
        if stage == "watch" and changed:
            events.append(recorded_at)
        previous = (stage, terminated, first_seen)
    return events


def audit(database, top_n):
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    monitored = {
        row[0].lower()
        for row in connection.execute(
            "SELECT DISTINCT token_address FROM pairs WHERE chain_id='bsc' AND active=1"
        )
    }
    movers = connection.execute(
        """
        WITH peaks AS (
          SELECT token_address,MAX(price_change_h24) change
          FROM alpha_universe_snapshots GROUP BY token_address
        )
        SELECT a.token_address,a.symbol,a.name,a.price_change_h24 change,a.observed_at peak_at
        FROM alpha_universe_snapshots a JOIN peaks p ON p.token_address=a.token_address
        WHERE a.price_change_h24=p.change
        ORDER BY a.price_change_h24 DESC LIMIT ?
        """,
        (top_n,),
    ).fetchall()
    items = []
    for mover in movers:
        token = mover["token_address"].lower()
        if token not in monitored:
            continue
        peak_at = parse_time(mover["peak_at"])
        if peak_at is None:
            continue
        cutoff = peak_at.timestamp() - 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
        pool = connection.execute(
            """
            SELECT pair_address,MAX(liquidity_usd) FROM snapshots
            WHERE token_address=? AND julianday(observed_at) BETWEEN julianday(?) AND julianday(?)
            GROUP BY pair_address ORDER BY 2 DESC LIMIT 1
            """,
            (token, cutoff_iso, mover["peak_at"]),
        ).fetchone()
        if pool is None:
            early = []
        else:
            raw = connection.execute(
                """
                SELECT observed_at,price_usd,volume_m5,liquidity_usd FROM snapshots
                WHERE token_address=? AND pair_address=? AND price_usd>0
                  AND julianday(observed_at) BETWEEN julianday(?) AND julianday(?)
                ORDER BY observed_at
                """,
                (token, pool[0], cutoff_iso, mover["peak_at"]),
            ).fetchall()
            rows = []
            for observed_at, price, volume, liquidity in raw:
                at = parse_time(observed_at)
                if at is None or not cutoff <= at.timestamp() <= peak_at.timestamp():
                    continue
                row = (at.timestamp(), pool[0], price, volume, liquidity, 0, 0)
                if rows and row[1:] == rows[-1][1:] and row[0] - rows[-1][0] <= 60:
                    continue
                rows.append(row)
            early = early_warning_times(rows)
        a_times = a_transition_times(connection, token)
        classification = classify_mover(early, a_times, mover["peak_at"])
        peak_timestamp = peak_at.timestamp()
        early_before = [time for time in early if cutoff <= time <= peak_timestamp]
        a_before = [time for time in a_times if cutoff <= time <= peak_timestamp]
        items.append({
            "token_address": token,
            "symbol": mover["symbol"],
            "name": mover["name"],
            "max_price_change_h24_pct": mover["change"],
            "peak_at": mover["peak_at"],
            "classification": classification,
            "early_warning_count": len(early_before),
            "earliest_early_before_peak_seconds": (
                int(peak_timestamp - min(early_before)) if early_before else None
            ),
            "a_count": len(a_before),
            "earliest_a_before_peak_seconds": (
                int(peak_timestamp - min(a_before)) if a_before else None
            ),
        })
    counts = Counter(item["classification"] for item in items)
    return {
        "mode": "monitored_mover_audit",
        "source_time_unverified": True,
        "coverage": {
            "requested_top": top_n,
            "monitored_movers": len(items),
            **{key: counts.get(key, 0) for key in ("early_and_a", "early_only", "a_only", "missed")},
        },
        "items": items,
    }


def run_self_test():
    peak = "2026-01-01T12:00:00+00:00"
    peak_at = datetime.fromisoformat(peak).timestamp()
    assert classify_mover([peak_at - 600], [peak_at - 300], peak) == "early_and_a"
    assert classify_mover([peak_at - 600], [], peak) == "early_only"
    assert classify_mover([], [peak_at - 600], peak) == "a_only"
    assert classify_mover([], [], peak) == "missed"
    assert classify_mover([], [], "invalid") == "invalid"
    peak = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "audit.db"
        connection = sqlite3.connect(database)
        connection.executescript("""
        CREATE TABLE pairs(pair_address TEXT,chain_id TEXT,token_address TEXT,active INTEGER);
        CREATE TABLE alpha_universe_snapshots(token_address TEXT,symbol TEXT,name TEXT,price_change_h24 REAL,observed_at TEXT);
        CREATE TABLE snapshots(token_address TEXT,pair_address TEXT,observed_at TEXT,price_usd REAL,volume_m5 REAL,liquidity_usd REAL);
        CREATE TABLE signal_v2_changes(id INTEGER PRIMARY KEY,token_address TEXT,recorded_at INTEGER,summary_json TEXT);
        """)
        connection.execute("INSERT INTO pairs VALUES('p','bsc','0xtoken',1)")
        connection.execute(
            "INSERT INTO alpha_universe_snapshots VALUES('0xtoken','TEST','Test',50,?)",
            (peak.isoformat(),),
        )
        for offset in [1800, 1500, 1200, 900, 300, 0]:
            at = peak.timestamp() - offset
            connection.execute(
                "INSERT INTO snapshots VALUES('0xtoken','p',?,?,?,?)",
                (datetime.fromtimestamp(at, timezone.utc).isoformat(), 101.0 if offset == 0 else 100.0, 300.0 if offset == 0 else 100.0, 10000.0),
            )
        connection.execute(
            "INSERT INTO signal_v2_changes(token_address,recorded_at,summary_json) VALUES('0xtoken',?,?)",
            (peak.timestamp() - 600, json.dumps({"stage":"watch","first_seen_at":peak.timestamp()-600,"terminated":False})),
        )
        connection.commit()
        connection.close()
        report = audit(str(database), 1)
        assert report["items"][0]["classification"] == "early_and_a"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        return
    report = audit(args.db, args.top)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

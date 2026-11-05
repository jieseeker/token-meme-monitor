#!/usr/bin/env python3
"""Point-in-time prelaunch research; standard library only, never places orders."""
import argparse
import csv
import gzip
import hashlib
import itertools
import json
import math
import sqlite3
import statistics as st
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

STEP, HORIZON = 300, 14400
FIELDS = ['at', 'observed', 'token', 'pair', 'symbol', 'price', 'volume', 'liquidity', 'buys', 'sells', 'flags']


def stamp(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def select_bucket(rows, membership, at):
    selected = {}
    for row in rows:
        listing = membership.get(row['token'])
        if not listing or not listing[1] or not 0 <= at-listing[0] <= 1800:
            continue
        if not (row['price'] > 0 and row['liquidity'] > 0):
            continue
        prior = selected.get(row['token'])
        if prior is None or (row['liquidity'], row['pair']) > (prior['liquidity'], prior['pair']):
            selected[row['token']] = dict(row, at=at, symbol=listing[2])
    return list(selected.values())


def export_db(path, output):
    def connect():
        return sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro', uri=True, timeout=20)
    with connect() as conn:
        end = stamp(conn.execute('SELECT MAX(observed_at) FROM snapshots').fetchone()[0])//STEP*STEP
    start = end-7*86400
    counts = dict(raw=0, exported=0)
    with gzip.open(output, 'wt', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        writer.writeheader()
        # ponytail: five-minute export bounds local research memory; retain raw timestamps for finer replay later.
        for day_start in range(start, end, 86400):
            day_end = min(end, day_start+86400)
            with connect() as conn:
                alpha = iter(conn.execute('SELECT token_address,symbol,observed_at,futures_listed FROM alpha_universe_snapshots WHERE observed_at>=? AND observed_at<? ORDER BY observed_at', (iso(day_start-1800), iso(day_end))))
                next_alpha = next(alpha, None)
                membership, bucket, bucket_at = {}, {}, None
                def flush(at):
                    nonlocal next_alpha
                    while next_alpha and stamp(next_alpha[2]) <= at:
                        token, symbol, observed, listed = next_alpha
                        membership[token.lower()] = (stamp(observed), bool(listed), symbol)
                        next_alpha = next(alpha, None)
                    chosen = select_bucket(bucket.values(), membership, at)
                    writer.writerows(chosen)
                    counts['exported'] += len(chosen)
                query = conn.execute('SELECT token_address,pair_address,observed_at,price_usd,volume_m5,liquidity_usd,buys_m5,sells_m5,risk_flags FROM snapshots WHERE observed_at>=? AND observed_at<? ORDER BY observed_at', (iso(day_start), iso(day_end)))
                for token, pair, observed, price, volume, liquidity, buys, sells, flags in query:
                    counts['raw'] += 1
                    at = stamp(observed)
                    grid = at//STEP*STEP+STEP
                    if bucket_at is not None and grid != bucket_at:
                        flush(bucket_at)
                        bucket.clear()
                    bucket_at = grid
                    if all(v is not None and math.isfinite(v) and v >= 0 for v in (price, volume, liquidity)):
                        bucket[pair] = dict(at=grid, observed=at, token=token.lower(), pair=pair, symbol='', price=price, volume=volume, liquidity=liquidity, buys=buys or 0, sells=sells or 0, flags=flags or '[]')
                if bucket_at is not None:
                    flush(bucket_at)
            print(json.dumps(dict(day=iso(day_end), **counts)), flush=True)
    return dict(start=iso(start), end=iso(end), **counts)


def continuous(rows, begin, end):
    segment = rows[begin:end+1]
    return len(segment) == end-begin+1 and all(b['at']-a['at'] == STEP and b['pair'] == a['pair'] for a, b in zip(segment, segment[1:]))


def features(rows, i):
    if i < 12 or not continuous(rows, i-12, i):
        return None
    r, history = rows[i], rows[i-12:i]
    prices = [v['price'] for v in history]
    median_volume = st.median(v['volume'] for v in history[-6:])
    earlier_volume = st.median(v['volume'] for v in history[:9])
    transactions = r['buys']+r['sells']
    return dict(return5=(r['price']/rows[i-1]['price']-1)*100,
                return15=(r['price']/rows[i-3]['price']-1)*100,
                return60=(r['price']/rows[i-12]['price']-1)*100,
                range60=(max(prices)/min(prices)-1)*100,
                from_low=(r['price']/min(prices)-1)*100,
                volume_ratio=r['volume']/median_volume if median_volume >= 1 else None,
                sustained_volume=st.median(v['volume'] for v in history[-3:])/earlier_volume if earlier_volume >= 1 else None,
                turnover=r['volume']/r['liquidity']*100,
                buy_share=r['buys']/transactions if transactions >= 20 else None,
                liquidity_ratio=r['liquidity']/st.median(v['liquidity'] for v in history),
                liquidity=r['liquidity'])


def trade(rows, i, target=20):
    if i+1 >= len(rows) or not continuous(rows, i, i+1):
        return dict(status='missing')
    alert, entry = rows[i], rows[i+1]
    if entry['price'] > alert['price']*1.05:
        return dict(status='late_entry')
    path, hit10 = [(entry['at'], 0.0)], False
    previous = entry
    for r in rows[i+2:i+51]:
        if r['at']-previous['at'] != STEP or r['pair'] != entry['pair']:
            return dict(status='missing')
        gross = (r['price']/entry['price']-1)*100
        hit10 = hit10 or gross >= 10
        path.append((r['at'], gross))
        reason = 'target' if gross >= target else 'stop' if gross <= -8 else 'timeout' if r['at']-entry['at'] >= HORIZON else None
        if reason:
            realized = target if reason == 'target' else gross
            path[-1] = (r['at'], realized)
            return dict(status='filled', token=entry['token'], symbol=entry['symbol'], alert_at=alert['at'], entry_at=entry['at'], exit_at=r['at'], gross=realized, reason=reason, hit10=hit10, hit20=reason == 'target' and target == 20, path=path)
        previous = r
    return dict(status='missing')


def split_of(at, start, end):
    bounds = [start, start+(end-start)*.6, start+(end-start)*.8, end]
    for name, lo, hi in zip(('train', 'validation', 'test'), bounds, bounds[1:]):
        if lo <= at and at+STEP+HORIZON <= hi:
            return name
    return None


def eligible(f, row):
    try:
        hard = {'liquidity_near_zero', 'liquidity_withdrawal'}.intersection(json.loads(row['flags']))
    except (ValueError, TypeError):
        return False
    return not hard and -8 <= f['return60'] <= 5 and -3 <= f['return15'] <= 3 and f['from_low'] <= 5 and f['liquidity'] >= 50000 and f['liquidity_ratio'] >= .9 and f.get('market') in ('neutral', 'risk_on')


def rules():
    atoms = {}
    for ratio, turn in itertools.product((1.2, 1.5, 2), (.1, .3, 1)):
        atoms[f'volume_{ratio}_turnover_{turn}'] = lambda f, r=ratio, t=turn: (f['volume_ratio'] or 0) >= r and f['turnover'] >= t
    atoms['sustained_1.5'] = lambda f: (f['sustained_volume'] or 0) >= 1.5 and f['turnover'] >= .1
    others = {}
    for x in (.6, .65):
        others[f'buy_{x}'] = lambda f, x=x: (f['buy_share'] or 0) >= x
    for x in (3, 5):
        others[f'range_{x}'] = lambda f, x=x: f['range60'] <= x
    for x in (1.02, 1.05):
        others[f'liquidity_{x}'] = lambda f, x=x: f['liquidity_ratio'] >= x
    combined = {'prelaunch_baseline': lambda f: True, **atoms, **others}
    for (a, fa), (b, fb) in itertools.product(atoms.items(), others.items()):
        combined[a+' & '+b] = lambda f, a=fa, b=fb: a(f) and b(f)
    return combined


def stats(trades, cost=.3):
    returns = [t['gross']-cost for t in trades]
    gains = sum(max(x, 0) for x in returns)
    losses = -sum(min(x, 0) for x in returns)
    by_token = defaultdict(float)
    changes, slots = defaultdict(float), defaultdict(int)
    for t in trades:
        by_token[t['token']] += t['gross']-cost
        slots[t['entry_at']] += 1
        slots[t['exit_at']] -= 1
        last = 0
        for at, gross in t['path']:
            changes[at] += gross-last
            last = gross
        changes[t['exit_at']] -= cost
    open_slots = peak_slots = 0
    for at in sorted(slots):
        open_slots += slots[at]
        peak_slots = max(peak_slots, open_slots)
    capital = max(1, peak_slots)*100
    equity = high = capital
    dd = 0
    for at in sorted(changes):
        equity += changes[at]  # $100 per entry: one percentage point = $1.
        high = max(high, equity)
        dd = max(dd, (high-equity)/high*100)
    best = max(by_token, key=by_token.get) if by_token else None
    rest = [t['gross']-cost for t in trades if t['token'] != best]
    return dict(n=len(trades), tokens=len({t['token'] for t in trades}), cost_pct=cost,
                mean=st.mean(returns) if returns else None, median=st.median(returns) if returns else None,
                win_pct=sum(x > 0 for x in returns)/len(returns)*100 if returns else None,
                profit_factor=gains/losses if losses else None, max_drawdown_pct=dd,
                initial_capital_usd=capital, hit20=sum(t['hit20'] for t in trades), hit10=sum(t['hit10'] for t in trades),
                best_token=best, best_token_symbol=next((t['symbol'] for t in trades if t['token'] == best), None), mean_without_best_token=st.mean(rest) if rest else None,
                pnl_by_token=dict(sorted(by_token.items(), key=lambda x: -x[1])))


def rally_events(series):
    events = []
    for token, rows in series.items():
        lows, last_peak = deque(), -1
        for i, r in enumerate(rows):
            if i and (r['at']-rows[i-1]['at'] != STEP or r['pair'] != rows[i-1]['pair']):
                lows.clear()
            while lows and (r['at']-rows[lows[0]]['at'] > HORIZON or lows[0] <= last_peak):
                lows.popleft()
            if lows and r['price'] >= rows[lows[0]]['price']*1.2:
                base = lows[0]
                onset = next(k for k in range(base+1, i+1) if rows[k]['price'] >= rows[base]['price']*1.05)
                if base >= 12:
                    events.append(dict(token=token, symbol=r['symbol'], base_at=rows[base]['at'], onset_at=rows[onset]['at'], target_at=r['at'], rise_pct=(r['price']/rows[base]['price']-1)*100))
                last_peak = i
                lows.clear()
            while lows and rows[lows[-1]]['price'] >= r['price']:
                lows.pop()
            lows.append(i)
    return events


def evaluate(records, rule, split):
    trades, counts, blocked_until = [], defaultdict(int), {}
    for row, f, outcome, period in records:
        if period != split or not eligible(f, row) or not rule(f):
            continue
        if row['at'] < blocked_until.get(row['token'], 0):
            continue
        counts[outcome['status']] += 1
        if outcome['status'] == 'filled':
            trades.append(outcome)
            blocked_until[row['token']] = outcome['exit_at']+STEP
        else:
            blocked_until[row['token']] = row['at']+(STEP+HORIZON if outcome['status'] == 'missing' else 900)
    return trades, dict(counts)


def research(input_path):
    series = defaultdict(list)
    with gzip.open(input_path, 'rt') as handle:
        for row in csv.DictReader(handle):
            for name in ('at', 'observed', 'buys', 'sells'):
                row[name] = int(row[name])
            for name in ('price', 'volume', 'liquidity'):
                row[name] = float(row[name])
            series[row['token']].append(row)
    for rows in series.values():
        rows.sort(key=lambda r: r['at'])
    start = min(rows[0]['at'] for rows in series.values())
    end = max(rows[-1]['at'] for rows in series.values())
    records, by_time = [], defaultdict(list)
    for rows in series.values():
        for i, row in enumerate(rows):
            f = features(rows, i)
            if f is not None:
                by_time[row['at']].append(f)
                records.append((row, f, trade(rows, i), split_of(row['at'], start, end)))
    for group in by_time.values():
        up = sum(f['return60'] > 0 for f in group)/len(group)
        median = st.median(f['return60'] for f in group)
        market = None if len(group) < 20 else 'risk_off' if up <= .45 or median < 0 else 'risk_on' if up >= .55 and median > 0 else 'neutral'
        for f in group:
            f['market'] = market
    records.sort(key=lambda x: (x[0]['at'], x[0]['token']))
    candidates, scores = rules(), []
    for name, rule in candidates.items():
        row = dict(rule=name)
        for split in ('train', 'validation'):
            trades, counts = evaluate(records, rule, split)
            row[split] = stats(trades)
            row[split]['coverage'] = counts
        scores.append(row)
    qualified = [r for r in scores if r['train']['n'] >= 20 and r['train']['tokens'] >= 5 and (r['train']['profit_factor'] or 0) >= 1.2 and (r['train']['mean'] or 0) > 0 and r['validation']['n'] >= 8 and r['validation']['tokens'] >= 3 and (r['validation']['mean'] or 0) > 0]
    adequate = [r for r in scores if r['train']['n'] >= 20 and r['validation']['n'] >= 8]
    ranking = lambda r: min(r['train']['mean'], r['validation']['mean'])
    chosen = max(qualified or adequate or scores, key=lambda r: ranking(r) if r['train']['mean'] is not None and r['validation']['mean'] is not None else -999)
    events = rally_events(series)
    lookup = {(row['token'], row['at']): (row, f) for row, f, _, _ in records}
    for event in events:
        event['before_onset'] = {str(minutes): lookup.get((event['token'], event['onset_at']-minutes*60), (None, None))[1] for minutes in (5, 15, 30, 60)}
    test_lo = start+(end-start)*.8
    test_events = [e for e in events if e['base_at'] >= test_lo and e['onset_at'] <= end-HORIZON-STEP]
    chosen_trades, coverage = evaluate(records, candidates[chosen['rule']], 'test')
    baseline_trades, _ = evaluate(records, candidates['prelaunch_baseline'], 'test')
    indices = {token: {r['at']: i for i, r in enumerate(rows)} for token, rows in series.items()}
    secondary_records = [(row, f, trade(series[row['token']], indices[row['token']][row['at']], 10), split) for row, f, _, split in records if split == 'test']
    secondary_trades, _ = evaluate(secondary_records, candidates[chosen['rule']], 'test')
    captured = []
    for e in test_events:
        alerts = [t for t in chosen_trades if t['token'] == e['token'] and e['onset_at']-3600 <= t['alert_at'] < e['onset_at'] and t['entry_at'] < e['onset_at']]
        prior = [lookup[(e['token'], at)] for at in range(e['onset_at']-3600, e['onset_at'], STEP) if (e['token'], at) in lookup]
        eligible_prior = [f for row, f in prior if eligible(f, row)]
        reason = None if alerts else 'history_gap' if not prior else 'prelaunch_or_market_gate' if not eligible_prior else 'pattern_absent' if not any(candidates[chosen['rule']](f) for f in eligible_prior) else 'position_or_entry_coverage'
        captured.append(dict(e, captured=bool(alerts), miss_reason=reason, lead_minutes=(e['onset_at']-min(t['alert_at'] for t in alerts))/60 if alerts else None))
    training = []
    until = {}
    for row, f, outcome, split in records:
        if split == 'train' and eligible(f, row) and outcome['status'] == 'filled' and row['at'] >= until.get(row['token'], 0):
            training.append((f, outcome['hit20']))
            until[row['token']] = row['at']+3600
    associations = {}
    for key in records[0][1] if records else {}:
        if key in ('liquidity', 'market'):
            continue
        groups = [[f[key] for f, hit in training if hit == positive and f[key] is not None] for positive in (True, False)]
        associations[key] = dict(winners_n=len(groups[0]), controls_n=len(groups[1]), winners_median=st.median(groups[0]) if groups[0] else None, controls_median=st.median(groups[1]) if groups[1] else None)
    train_test = chosen['rule']
    test_summary = stats(chosen_trades)
    eligible_for_shadow = bool(qualified) and test_summary['n'] >= 10 and test_summary['tokens'] >= 3 and (test_summary['mean'] or 0) > 0 and (test_summary['mean_without_best_token'] or 0) > 0
    return dict(window=dict(start=iso(start), end=iso(end), tokens=len(series), rows=sum(map(len, series.values())), feature_rows=len(records), export_sha256=hashlib.sha256(Path(input_path).read_bytes()).hexdigest(), script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
                methods=dict(grid_seconds=STEP, entry='next bucket, no fill above alert +5%', exit='observed -8% stop / +20% take profit / 4h timeout', costs=[.3, .5], split='60/20/20 chronological, full 4h horizon purged at boundaries', sizing='$100 fixed, one open position per token, concurrent positions fully funded', slippage='historical actual slippage unavailable; assumed cost only', source_time_unverified=True, rally='first +20% from trailing 4h trough; onset is first +5%; non-overlapping episodes', capture='alert within 1h before onset, delayed entry strictly before onset', search_rules=len(scores)),
                selection=dict(rule=train_test, passed_train_validation=bool(qualified), qualified_rules=len(qualified), heldout_pass=eligible_for_shadow, action='shadow_research_only' if eligible_for_shadow else 'reject_production_promotion'),
                selected=chosen, test=dict(cost_0_3=test_summary, cost_0_5=stats(chosen_trades, .5), secondary_10pct_exit=stats(secondary_trades), coverage=coverage, baseline=stats(baseline_trades), rally_events=len(test_events), captured=sum(e['captured'] for e in captured), events=captured, trades=[{k:v for k,v in t.items() if k!='path'} for t in chosen_trades]),
                all_rallies=events, training_feature_associations=associations, train_validation_rules=sorted(scores, key=lambda r: ranking(r) if r['train']['mean'] is not None and r['validation']['mean'] is not None else -999, reverse=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-db')
    parser.add_argument('--input')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.export_db:
        print(json.dumps(export_db(args.export_db, args.output)), flush=True)
    elif args.input:
        report = research(args.input)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(dict(window=report['window'], selection=report['selection'], test=report['test']['cost_0_3']), ensure_ascii=False))
    else:
        parser.error('provide --export-db or --input')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Independent experimental prelaunch events; no buy signals or order execution."""
import argparse
import bisect
import fcntl
import json
import math
import sqlite3
import statistics
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from confirmation_shadow import slippage_values
from prelaunch_research import features, eligible

VERSION = 'prelaunch_activity_probe_v1'


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def stamp(value):
    try:
        return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())
    except (ValueError, TypeError, AttributeError):
        return None


def gate(signal, warning, pair, market, now):
    updated = signal.get('updated_at')
    evidence = signal.get('last_evidence_at')
    if not isinstance(updated, (int, float)) or not isinstance(evidence, (int,float)) or not 0 <= now-updated <= 180 or not 0 <= now-evidence <= 180:
        return 'risk_unknown'
    if str(signal.get('stage', '')).startswith(('first_start', 'second_start')) or signal.get('stage') in ('first_wave', 'pullback', 'base'):
        return 'already_started'
    if signal.get('risk_status') != 'PASS':
        return 'risk_blocked'
    if market not in ('risk_on', 'neutral'):
        return 'market_blocked'
    depth_at = stamp((warning.get('slippage_estimates') or {}).get('depth_calculated_at'))
    if warning.get('pair_address') != pair or depth_at is None or not 0 <= now-depth_at <= 300:
        return 'slippage_unknown'
    small, large = slippage_values(warning)
    if small is None or large is None:
        return 'slippage_unknown'
    return 'slippage_blocked' if small > .3 or large > .5 else 'observing'


def transition(state, active, now):
    previous_check = state.get('checked_at', now)
    state['checked_at'] = now
    if active is None or now-previous_check > 120:
        state['quiet_since'] = None
    if active is None:
        return False
    if not active:
        if state.get('quiet_since') is None:
            state['quiet_since'] = now
        if now-state['quiet_since'] >= 300:
            state['active'] = False
        return False
    edge = not state.get('active', False)
    state['active'], state['quiet_since'] = True, None
    if edge and now-state.get('last_event_at', 0) >= 900:
        state['last_event_at'] = now
        return True
    return False


def view_class(initial, active, current_gate, triggered, now):
    if now-triggered > 300:
        return 'expired'
    if active is None:
        return 'stale_data'
    if not active:
        return 'setup_ended'
    return current_gate if current_gate != 'observing' else initial


def candidates(conn, tokens, now):
    grouped = defaultdict(list)
    for token, pair, at, price, volume, liquidity, buys, sells, flags in conn.execute('SELECT token_address,pair_address,observed_at,price_usd,volume_m5,liquidity_usd,buys_m5,sells_m5,risk_flags FROM snapshots WHERE observed_at>=? AND observed_at<=? ORDER BY observed_at', (iso(now-4200), iso(now))):
        if token not in tokens or not all(isinstance(x, (int, float)) and math.isfinite(x) and x >= 0 for x in (price, volume, liquidity)) or price <= 0 or liquidity <= 0:
            continue
        grouped[token, pair].append(dict(token=token, pair=pair, observed=stamp(at), price=price, volume=volume, liquidity=liquidity, buys=buys or 0, sells=sells or 0, flags=flags or '[]', symbol=tokens[token]))
    selected = {}
    for (token, pair), rows in grouped.items():
        if not 0 <= now-rows[-1]['observed'] <= 120:
            continue
        if token not in selected or rows[-1]['liquidity'] > selected[token][-1]['liquidity']:
            selected[token] = rows
    result = {}
    for token, rows in selected.items():
        times = [r['observed'] for r in rows]
        current_at = times[-1]
        sampled = []
        for target in range(current_at-3600, current_at+1, 300):
            i = bisect.bisect_right(times, target)-1
            if i < 0 or target-times[i] > 120:
                break
            sampled.append(dict(rows[i], at=target))
        f = features(sampled, 12) if len(sampled) == 13 else None
        result[token] = (rows[-1], f)
    return result


def resolve(conn, event, now):
    trigger = event['triggered_at']
    if 'entry_price' not in event:
        if now < trigger+420:
            return
        entry = conn.execute('SELECT observed_at,price_usd FROM snapshots WHERE pair_address=? AND token_address=? AND observed_at>=? AND observed_at<=? AND price_usd>0 ORDER BY observed_at LIMIT 1', (event['pair'], event['token'], iso(trigger+300), iso(trigger+420))).fetchone()
        if entry is None:
            event['outcome_state'] = 'entry_missing'
            return
        if entry[1] > event['price']*1.05:
            event['outcome_state'] = 'late_entry'
            return
        event['entry_at'], event['entry_price'] = stamp(entry[0]), entry[1]
    start, price = event['entry_at'], event['entry_price']
    outcomes = event.setdefault('outcomes', {})
    for name, seconds in (('15m',900), ('1h',3600), ('4h',14400)):
        if name not in outcomes and now >= start+seconds+300:
            row = conn.execute('SELECT price_usd FROM snapshots WHERE pair_address=? AND token_address=? AND observed_at>=? AND observed_at<=? AND price_usd>0 ORDER BY observed_at LIMIT 1', (event['pair'],event['token'],iso(start+seconds),iso(start+seconds+300))).fetchone()
            outcomes[name] = (row[0]/price-1)*100 if row else None
    if 'strategy_reason' not in event:
        prior = start
        path = conn.execute('SELECT observed_at,price_usd FROM snapshots WHERE pair_address=? AND token_address=? AND observed_at>? AND observed_at<=? AND price_usd>0 ORDER BY observed_at', (event['pair'],event['token'],iso(start),iso(min(now,start+14700))))
        for observed, value in path:
            at = stamp(observed)
            if at-prior > 300:
                event['strategy_reason'] = 'path_missing'
                break
            gross = (value/price-1)*100
            reason = 'target' if gross >= 20 else 'stop' if gross <= -8 else 'timeout' if at >= start+14400 else None
            if reason:
                event.update(strategy_reason=reason, strategy_gross_pct=20 if reason=='target' else gross)
                break
            prior = at
        if 'strategy_reason' not in event and now > start+14700:
            event['strategy_reason'] = 'path_missing'
    event['outcome_state'] = 'resolved' if len(outcomes)==3 and all(x is not None for x in outcomes.values()) else 'endpoint_missing' if now > start+14700 else 'pending'


def summary(events):
    groups = defaultdict(list)
    for e in events:
        groups[e['market']+'/'+e['classification']].append(e)
    report = {}
    for key, group in groups.items():
        item = dict(events=len(group), entry_missing=sum(e.get('outcome_state') in ('entry_missing','late_entry') for e in group))
        for name in ('15m','1h','4h'):
            values = [e['outcomes'][name] for e in group if e.get('outcomes',{}).get(name) is not None]
            item[name] = dict(n=len(values), mean_gross_pct=statistics.mean(values) if values else None, mean_net_0_3_pct=statistics.mean(values)-.3 if values else None, mean_net_0_5_pct=statistics.mean(values)-.5 if values else None)
        net = [e['strategy_gross_pct']-.3 for e in group if 'strategy_gross_pct' in e]
        losses = -sum(min(x,0) for x in net)
        item['fixed_exit'] = dict(n=len(net),mean_net_pct=statistics.mean(net) if net else None,median_net_pct=statistics.median(net) if net else None,win_pct=sum(x>0 for x in net)/len(net)*100 if net else None,profit_factor=sum(max(x,0) for x in net)/losses if losses else None)
        report[key] = item
    return report


def fetch(api, path):
    with urllib.request.urlopen(api+path, timeout=20) as response:
        return json.load(response)


def atomic(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False))
    temporary.replace(path)


def scan(db, api, directory, now=None):
    fixed_now = now
    state_path = directory/'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else dict(version=VERSION,states={},events=[])
    if state.get('version') != VERSION:
        raise ValueError('prelaunch state version mismatch; preserve the old state before migration')
    tokens = {r['token_address'].lower():r['token_symbol'] for r in fetch(api,'/api/tokens')}
    signals = {r['token_address'].lower():r for r in fetch(api,'/api/signal-v2?limit=100')['summaries']}
    warnings = {r['token_address'].lower():r for r in fetch(api,'/api/early-warning')['items']}
    market = fetch(api,'/api/market-state')['state']
    now = int(time.time()) if fixed_now is None else fixed_now
    items = []
    with sqlite3.connect('file:'+str(Path(db).resolve())+'?mode=ro',uri=True,timeout=20) as conn:
        current = candidates(conn,tokens,now)
        state['events'] = [e for e in state['events'] if now-e['triggered_at'] <= 7*86400]
        state['states'] = {t:s for t,s in state['states'].items() if now-s.get('checked_at',0) <= 7*86400}
        for token in tokens:
            row, f = current.get(token,(None,None))
            condition = None if f is None else eligible(dict(f,market='neutral'),row) and (f['volume_ratio'] or 0)>=1.2 and f['turnover']>=1
            s = state['states'].setdefault(token,{})
            current_gate = gate(signals.get(token,{}),warnings.get(token,{}),row['pair'] if row else '',market,now)
            if transition(s,condition,now):
                small, large = slippage_values(warnings.get(token,{}))
                event = dict(token=token,symbol=tokens[token],pair=row['pair'],triggered_at=row['observed'],detected_at=now,price=row['price'],features=f,market=market,classification=current_gate,risk_status=signals.get(token,{}).get('risk_status'),slippage_100_pct=small,slippage_1000_pct=large,outcome_state='pending')
                s['event'] = event
                state['events'].append(event)
            event = s.get('event')
            if event and now-event['triggered_at'] <= 86400:
                same_pool = condition if row is None or row['pair']==event['pair'] else False
                items.append(dict(token_address=token,pair_address=event['pair'],triggered_at=event['triggered_at'],observed_at=iso(row['observed']) if row else None,classification=view_class(event['classification'],same_pool,current_gate,event['triggered_at'],now),features=f,risk_status=signals.get(token,{}).get('risk_status'),market_state=market,slippage_100_pct=slippage_values(warnings.get(token,{}))[0],slippage_1000_pct=slippage_values(warnings.get(token,{}))[1]))
        for event in state['events']:
            if event.get('outcome_state') == 'pending':
                resolve(conn,event,now)
    report = dict(version=VERSION,calculated_at=iso(now),experimental=True,profitability_validated=False,items=items,events=len(state['events']),summary=summary(state['events']),methods=dict(signal='price not extended; m5/liquidity >=1%; relative volume >=1.2x; historical rule not validated',fresh_window_seconds=300,rearm='5m continuously quiet, at least15m between events; no rearm on missing data',entry='first same-pool sample 5-7m after trigger, capped at alert +5%',exit='+20% / observed -8% stop / 4h timeout',costs_pct=[.3,.5],returns='sampled endpoint and hypothetical fixed exit; no actual fills or source-time verification'))
    atomic(state_path,state)
    atomic(directory/'latest.json',report)
    return dict(at=iso(now),events=report['events'],observing=sum(i['classification']=='observing' for i in items))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--api',default='http://127.0.0.1:8501')
    parser.add_argument('--output',required=True)
    parser.add_argument('--loop',action='store_true')
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/'.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:
                print(json.dumps(scan(args.db,args.api,directory)),flush=True)
            except Exception as error:
                if not args.loop:
                    raise
                print(json.dumps(dict(error=str(error))),flush=True)
            if not args.loop:
                break
            time.sleep(60)


if __name__ == '__main__':
    main()

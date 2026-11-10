"""Run: python3 scripts/prelaunch_monitor_test.py"""
import sqlite3
import json
import tempfile
from math import isclose
from pathlib import Path
from datetime import datetime, timezone
import prelaunch_monitor as p

now = 1800000000
signal = dict(stage='watch', risk_status='PASS', updated_at=now, last_evidence_at=now, first_seen_at=now-100000)
warning = dict(pair_address='pool', slippage_estimates=dict(depth_calculated_at=datetime.fromtimestamp(now, timezone.utc).isoformat(), estimates=[dict(order_usd=100,buy_slippage_pct=.1),dict(order_usd=1000,buy_slippage_pct=.4)]))
assert p.gate(signal, warning, 'pool', 'risk_on', now) == 'observing', 'old A does not block a new research event'
assert p.gate(dict(signal,data_status='missing_h1'),warning,'pool','risk_on',now) == 'risk_unknown', 'incomplete structure input cannot qualify a research event'
warning['slippage_estimates']['stale']=True
assert p.gate(signal,warning,'pool','risk_on',now)=='slippage_unknown', 'failed cached quote cannot be reused as fresh'
warning['slippage_estimates']['stale']=False
assert p.gate(signal, warning, 'pool', 'risk_off', now) == 'market_blocked'
assert p.gate(dict(signal,stage='first_start'),warning,'pool','risk_on',now) == 'already_started'
assert p.gate(signal,warning,'other','risk_on',now) == 'slippage_unknown', 'quotes must match the selected pool'
warning['slippage_estimates']['estimates'][1]['buy_slippage_pct'] = .6
assert p.gate(signal,warning,'pool','neutral',now) == 'slippage_blocked'
warning['slippage_estimates']['depth_calculated_at'] = datetime.fromtimestamp(now-301,timezone.utc).isoformat()
assert p.gate(signal,warning,'pool','neutral',now) == 'slippage_unknown'

state = {}
assert p.transition(state, True, now), 'first valid edge creates an event'
assert not p.transition(state, True, now+400), 'a sustained condition never refreshes its timestamp'
assert state['last_event_at'] == now
p.transition(state, False, now+420)
p.transition(state, None, now+600)
assert not p.transition(state, True, now+1000), 'missing data cannot rearm an active episode'
for t in range(now+1020,now+1381,60):
    p.transition(state,False,t)
assert p.transition(state,True,now+1440), 'a new edge after five quiet minutes can trigger'
assert p.view_class('observing',True,'observing',now,now+301) == 'expired'
assert p.view_class('observing',False,'observing',now,now+30) == 'setup_ended'
assert p.view_class('risk_blocked',True,'observing',now,now+30) == 'risk_blocked', 'initial blocked events do not silently become eligible'

conn = sqlite3.connect(':memory:')
conn.execute('CREATE TABLE snapshots(token_address TEXT,pair_address TEXT,observed_at TEXT,price_usd REAL)')
for offset,price in [(0,100),(300,101),(600,100),(900,90),(1200,130),(3900,105),(14700,115)]:
    conn.execute('INSERT INTO snapshots VALUES (?,?,?,?)',('t','pool',p.iso(now+offset),price))
event = dict(token='t',pair='pool',triggered_at=now,price=100)
p.resolve(conn,event,now+15001)
assert event['entry_price'] == 101, 'paper entry uses the delayed same-pool sample'
assert event['strategy_reason'] == 'stop' and event['strategy_gross_pct'] < -10, 'stop gap precedes the later rally'
assert event['outcomes']['15m'] > 28, 'endpoint and strategy returns have separate meanings'
assert event['outcome_state'] == 'resolved'
p.track(conn,event,now+15001)
assert event['price']==100 and event['triggered_at']==now, 'tracking must preserve the discovery anchor'
assert event['tracking']['last_price']==115 and isclose(event['tracking']['max_gain_pct'],30)
assert event['tracking']['max_drawdown_pct'] < -19, 'drawdown is measured from the preceding sampled peak'
tracking=dict(event['tracking'])
p.track(conn,event,now+15100)
assert event['tracking']==tracking, 'the incremental cursor must not replay old extrema'
conn.execute('INSERT INTO snapshots VALUES (?,?,?,?)',('t','other',p.iso(now+14900),9999))
p.track(conn,event,now+15200)
assert event['tracking']['last_price']==115, 'a different pool cannot change the observation return'
older=dict(event,symbol='T',classification='market_blocked',features={'turnover':1.3},market='risk_off')
newer=dict(older,triggered_at=now+2000,price=105)
records=p.observation_records([older,newer])
assert len(records)==1 and records[0]['trigger_count']==2 and records[0]['events'][0]['price']==105
assert records[0]['events'][1]['price']==100, 'a new event appends rather than replaces the old trigger'
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    db=root/'source.db'
    source=sqlite3.connect(db)
    source.execute('CREATE TABLE snapshots(token_address TEXT,pair_address TEXT,observed_at TEXT,price_usd REAL,volume_m5 REAL,liquidity_usd REAL,buys_m5 INTEGER,sells_m5 INTEGER,risk_flags TEXT)')
    source.execute('CREATE TABLE pairs(token_address TEXT,pair_address TEXT,chain_id TEXT,active INTEGER)')
    source.execute("INSERT INTO pairs VALUES ('t','pool','bsc',1)")
    for i in range(13):
        source.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)',('t','pool',p.iso(now-30-(12-i)*300),100,3000 if i==12 else 100,100000,15,10,'[]'))
    source.commit()
    source.close()
    warning['slippage_estimates']['depth_calculated_at']=p.iso(now)
    warning['slippage_estimates']['estimates'][1]['buy_slippage_pct']=.4
    responses={'/api/tokens':[dict(token_address='t',token_symbol='T')],'/api/signal-v2?limit=100':dict(summaries=[dict(signal,token_address='t')]),'/api/early-warning':dict(items=[dict(warning,token_address='t')]),'/api/market-state':dict(state='neutral')}
    original_fetch=p.fetch
    p.fetch=lambda api,path:responses[path]
    assert p.scan(db,'unused',root,now)['observing']==1
    assert p.scan(db,'unused',root,now+60)['events']==1, 'restart must retain the same episode'
    assert p.scan(db,'unused',root,now+180)['observing']==0, 'stale source must lose eligibility'
    report=json.loads((root/'latest.json').read_text())
    assert report['items'][0]['classification']=='stale_data'
    assert report['profitability_validated'] is False
    assert len(report['records'])==1 and report['records'][0]['events'][0]['features']['turnover']==3
    source=sqlite3.connect(db)
    source.execute('UPDATE snapshots SET observed_at=? WHERE observed_at=?',(p.iso(now-30-1800-200),p.iso(now-30-1800)))
    source.commit()
    assert p.candidates(source,{'t':'T'},now)['t'][1] is not None, 'historical five-minute samples tolerate real provider cadence without using future observations'
    for offset,price in ((200,120),(260,110)):
        source.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)',('t','pool',p.iso(now+offset),price,100,100000,15,10,'[]'))
    source.commit()
    source.close()
    p.scan(db,'unused',root,now+300)
    responses['/api/tokens']=[]
    p.scan(db,'unused',root,now+360)
    archived=json.loads((root/'latest.json').read_text())
    assert not archived['items'] and len(archived['records'])==1, 'leaving the main list does not erase observation history'
    retained=archived['records'][0]['events'][0]
    assert retained['price']==100 and retained['tracking']['peak_price']==120 and retained['tracking']['last_price']==110, 'restart preserves anchors and sampled peaks'
    assert retained['tracking']['max_drawdown_pct'] < -8
    p.scan(db,'unused',root,now+8*86400)
    assert json.loads((root/'latest.json').read_text())['records']==[], 'seven-day retention applies to archives'
    p.fetch=original_fetch
print('prelaunch monitor checks passed')

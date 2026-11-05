"""Run: python3 scripts/prelaunch_research_test.py"""
from copy import deepcopy
from math import isclose
import prelaunch_research as p


def sample(i, price=100):
    return dict(at=i*300, observed=i*300, token='t', pair='p', symbol='T', price=price,
                volume=100, liquidity=100000, buys=15, sells=10, flags='[]')


rows = [sample(i) for i in range(70)]
base = p.features(rows, 12)
assert base['return60'] == 0 and base['volume_ratio'] == 1
changed = deepcopy(rows)
changed[13]['price'] = 10000
assert p.features(changed, 12) == base, 'future observations cannot affect features'
changed[8]['pair'] = 'other'
assert p.features(changed, 12) is None, 'pool switches invalidate the feature window'
assert p.select_bucket([rows[0]], {'t': (300, True, 'T')}, 0) == [], 'future eligibility is forbidden'
assert p.select_bucket([rows[0]], {'t': (0, False, 'T')}, 0) == []
assert p.select_bucket([rows[0]], {'t': (0, True, 'T')}, 0)[0]['pair'] == 'p'

target = deepcopy(rows)
target[14]['price'] = 121
assert p.trade(target, 12)['gross'] == 20, 'entry is delayed by one bucket'
target[13]['price'] = 106
assert p.trade(target, 12)['status'] == 'late_entry', 'do not fill a runaway alert'
stop = deepcopy(rows)
stop[14]['price'], stop[15]['price'] = 90, 125
assert isclose(p.trade(stop, 12)['gross'], -10), 'a stop gap uses the worse observed price'
gap = deepcopy(rows)
gap[16]['at'] += 60
assert p.trade(gap, 12)['status'] == 'missing', 'missing paths must not be backfilled'
assert p.split_of(59000, 0, 100000) is None, 'a complete forward horizon must fit its split'
assert p.split_of(1000, 0, 100000) == 'train'
assert p.split_of(81000, 0, 100000) == 'test'
dedup = [(target[12], dict(base, market='neutral'), p.trade(rows, 12), 'train'),
         (target[13], dict(base, market='neutral'), p.trade(rows, 13), 'train')]
assert len(p.evaluate(dedup, lambda f: True, 'train')[0]) == 1, 'one token cannot open overlapping positions'
rally = deepcopy(rows)
rally[20]['price'] = 121
assert len(p.rally_events({'t': rally})) == 1, 'one rapid rally is one episode, not zero or repeated positives'
print('prelaunch research checks passed')

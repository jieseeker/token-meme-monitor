const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\nrefresh\(\);\nsetInterval\(refresh,REFRESH_MS\);/, '');
const nodes = new Map();
const context = vm.createContext({
  Date, Map, console, setInterval() {}, window: {addEventListener() {}},
  localStorage: {getItem() {return null;}, setItem() {}},
  document: {querySelector() {return null;}, getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {innerHTML:'', textContent:'', addEventListener() {}, setAttribute() {}, classList:{toggle() {}, add() {}, remove() {}}});
    return nodes.get(id);
  }},
  fetch: async () => ({ok:true, json:async () => null}),
});
vm.runInContext(script, context);
function guide(change = '') {
  return vm.runInContext(`
    {
      const now=Math.floor(Date.now()/1000);
      const token={token_address:'0xdecision',pair_address:'0xpool',observed_at:new Date().toISOString(),price_change_h1:1,price_change_h24:3,signal_risk_flags:'[]'};
      marketRegime='risk_on';
      prelaunchReportAt=new Date().toISOString();
      prelaunchSummary={'risk_on/observing':{events:12,fixed_exit:{n:10,mean_net_pct:1.25,median_net_pct:-.3}}};
      confirmationSummary={cohort_validation_version:1,by_market_and_classification:{risk_on:{strong:{events:1,samples_4h:1,mean_net_4h_pct:-.004}}}};
      confirmationSummaryAt=Date.now();
      signalV2Rows=new Map([['0xdecision',{stage:'watch',risk_status:'PASS',data_status:'current',pair_address:'0xpool',first_seen_at:now-3600,updated_at:now,last_evidence_at:now}]]);
      prelaunchRows=new Map([['0xdecision',{classification:'observing',triggered_at:now-60,observed_at:new Date().toISOString(),pair_address:'0xpool',features:{return60:1,turnover:1.3,volume_ratio:1.4}}]]);
      observationRecords=new Map([['0xdecision',{events:[{triggered_at:now-60,pair:'0xpool',market:'risk_on',classification:'observing',risk_status:'PASS',features:{turnover:1.3,volume_ratio:1.4}}]}]]);
      earlyWarningRows=new Map([['0xdecision',{pair_address:'0xpool',is_active:true,observed_at:new Date().toISOString(),slippage_estimates:{depth_calculated_at:new Date().toISOString(),estimates:[{order_usd:100,buy_slippage_pct:.1},{order_usd:1000,buy_slippage_pct:.2}]}}]]);
      ${change}
      decisionGuideHTML(token);
    }
  `, context);
}

const active = guide();
assert.ok(active.includes('观察中，等待确认') && active.includes('本轮截止'), 'live observation states its next action and five-minute deadline');
assert.ok(active.includes('1.40x') && active.includes('1.30%'), 'current reason uses the actual discovered features');
assert.ok(active.includes('10 个已退出样本') && active.includes('+1.25%') && active.includes('-0.30%'), 'net mean and negative median stay visible with the correct denominator');
assert.ok(active.includes('5–7分钟') && active.includes('0.3%') && active.includes('+20% / -8% / 4小时'), 'the actual simulation entry, cost and exit rules accompany research statistics');
assert.ok(!active.includes('胜率预测'), 'historical outcomes never become a prediction probability');
assert.ok(!guide("prelaunchRows.get('0xdecision').features.volume_ratio=9").includes('9.00x'), 'discovery reasoning remains frozen even when current features change');
assert.ok(!guide("observationRecords.get('0xdecision').events[0].triggered_at=now-100").includes('+1.25%'), 'a different discovery cannot supply this observation cohort');

const blockedCases = [
  ["marketRegime='risk_off'", '市场风险规避'],
  ["token.price_change_h24=-55", '下跌风险'],
  ["signalV2Rows.get('0xdecision').risk_status='WARN';token.signal_risk_flags='[\"sell_pressure\"]'", '卖出笔数偏多'],
  ["signalV2Rows.get('0xdecision').risk_status='HOLD'", '暂缓'],
  ["signalV2Rows.get('0xdecision').risk_status='BLOCK'", '阻断'],
  ["earlyWarningRows.get('0xdecision').slippage_estimates.estimates[1].buy_slippage_pct=.81", '0.81%'],
  ["earlyWarningRows.get('0xdecision').slippage_estimates.stale=true", '滑点数据'],
  ["earlyWarningRows.get('0xdecision').pair_address='0xother'", '滑点数据'],
  ["prelaunchRows.get('0xdecision').triggered_at=now-301", '已过观察窗'],
  ["prelaunchRows.get('0xdecision').triggered_at=now+1", '数据待更新'],
  ["signalV2Rows.get('0xdecision').updated_at=now-181", '数据待更新'],
  ["token.observed_at=new Date(Date.now()-121000).toISOString()", '数据待更新'],
];
for (const [change, expected] of blockedCases) {
  const result = guide(change);
  assert.ok(result.includes(expected), `${change} explains its actual blocker`);
  assert.ok(!result.includes('强确认条件成立'), 'a blocked or stale candidate cannot look confirmed');
  assert.ok(!result.includes('+1.25%'), 'an ineligible candidate cannot borrow eligible-cohort returns');
}
const initialBlock = guide("observationRecords.get('0xdecision').events[0].classification='risk_blocked';observationRecords.get('0xdecision').events[0].risk_status='WARN';prelaunchRows.get('0xdecision').classification='risk_blocked'");
assert.ok(initialBlock.includes('触发时') && initialBlock.includes('当前') && initialBlock.includes('新一轮'), 'initial and current risk are distinguished after recovery');
assert.ok(!initialBlock.includes('+1.25%'), 'blocked discoveries cannot borrow eligible-cohort performance');
assert.ok(guide("prelaunchSummary={}").includes('等待同条件样本'), 'missing statistics have an honest empty state');
assert.ok(!guide("prelaunchReportAt=new Date(Date.now()-121000).toISOString()").includes('+1.25%'), 'stale report statistics cannot support a fresh decision');
const formal = guide("signalV2Rows.get('0xdecision').first_seen_at=now-60;prelaunchRows.clear()");
assert.ok(formal.includes('强确认条件成立') && formal.includes('强确认影子样本 1'), 'formal confirmation has its separate cohort');
assert.ok(!formal.includes('+1.25%'), 'formal confirmation never borrows experimental strategy returns');
assert.ok(!guide("signalV2Rows.get('0xdecision').first_seen_at=now-60;signalV2Rows.get('0xdecision').pair_address='0xother'").includes('强确认条件成立'), 'formal confirmation cannot borrow a different research pool quote');
assert.equal(vm.runInContext("confirmationState({token_address:'0xdecision'}).active",context),false,'all formal confirmation consumers reject cross-pool costs');
assert.ok(guide("signalV2Rows.get('0xdecision').first_seen_at=now-60;signalV2Rows.get('0xdecision').pair_address='0xother';earlyWarningRows.get('0xdecision').pair_address='0xother';prelaunchRows.get('0xdecision').triggered_at=now-600").includes('强确认条件成立'), 'an old research pool cannot block a fresh independently confirmed pool');
for (const status of ['missing_h1','warming_up','invalid_input']) {
  const result=guide(`signalV2Rows.get('0xdecision').first_seen_at=now-60;signalV2Rows.get('0xdecision').data_status='${status}'`);
  assert.ok(result.includes('数据待更新')&&!result.includes('强确认条件成立'), 'incomplete structure input fails closed');
  assert.equal(vm.runInContext("confirmationState({token_address:'0xdecision'}).active",context),false,'shared confirmation state rejects incomplete structure input');
}
assert.ok(!guide("signalV2Rows.get('0xdecision').stage='first_start';prelaunchRows.clear()").includes('强确认条件成立'), 'first-start structure is never promoted to a buy point');

console.log('decision guide checks passed');

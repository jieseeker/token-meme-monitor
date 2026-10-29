const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const nodes = new Map();
const document = {
  querySelector() { return null; },
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, { innerHTML: '', textContent: '', addEventListener() {}, append() {}, setAttribute(name,value) { this[name] = value; }, getAttribute(name) { return this[name]; }, classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } } });
    return nodes.get(id);
  },
  createElement() { return { textContent: '', className: '' }; },
};
const row = {
  pair_address: '0xpair', token_address: '0xtoken', token_symbol: 'TEST', quote_symbol: 'USDT',
  observed_at: new Date().toISOString(), price_usd: 0.000000012345, price_change_m5: 2,
  price_change_h1: -3, price_change_h24: 8, liquidity_usd: 100000,
  volume_m5: 1200, volume_h1: 15000, buys_h1: 14, sells_h1: 7, market_cap: 12300000,
  signal_score: 65, signal_reasons:'["healthy_liquidity_band","h1_volume_support"]', signal_risk_flags:'["sell_pressure"]', signal_at:new Date().toISOString(), risk_6h_dd30: null, dex_id: 'pancakeswap',
};
let depthAt = new Date().toISOString();
let structureGate = null;
const calls=[];
class TestNotification {
  static permission='granted';
  static async requestPermission(){return 'granted';}
  constructor(title,options){TestNotification.instances.push({title,options});}
}
TestNotification.instances=[];
const storage = new Map([
  ['tmm-watchlist-v1', JSON.stringify(['0xtoken'])],
  ['tmm-watchlist-only-v1','true'],
  ['tmm-tokens-v1', JSON.stringify({at:Date.now()-60000,rows:[{token_address:'0xtoken',token_symbol:'CACHED',token_name:'Cached',indicator_score:65,indicator_reasons:row.signal_reasons,observed_at:new Date(Date.now()-120000).toISOString()}]})]
]);
const localStorage = {getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
const fetch = async url => { calls.push(url);
  const at=Date.parse(row.observed_at);
  if(url==='/api/tokens')return {ok:true,json:async()=>[
    {token_address:'0xtoken',token_symbol:row.token_symbol,token_name:'Test',indicator_score:65,indicator_reasons:row.signal_reasons,observed_at:row.observed_at},
    {token_address:'0xother',token_symbol:'OTHER',token_name:'Other',indicator_score:20,indicator_reasons:'[]',observed_at:new Date(Date.now()-180000).toISOString()},
    {token_address:'0xzero',token_symbol:'ZERO',token_name:'Zero',indicator_score:0,indicator_reasons:'[]'}]};
  if(url==='/api/signal-v2?limit=50')return {ok:true,json:async()=>({summaries:[
    {token_address:'0xtoken',stage:'first_start',risk_status:'WARN',data_status:'current',b_at:1792753073,c_at:null,c_reason:null}
  ]})};
  if(url==='/api/early-warning')return {ok:true,json:async()=>({items:[
    {token_address:'0xtoken',status:'volume_anomaly',is_active:true,volume_acceleration:2.4,buy_pressure:.67,volume_to_liquidity:.018,return_5m_pct:1.2,evidence_count:5,observed_at:new Date().toISOString()}
  ],historical_calibration:{volume_anomaly_gt_5pct_pct:13.68,baseline_gt_5pct_pct:7.31,strong_anomaly_gt_5pct_pct:15.12}})};
  if(url==='/api/early-warning/history?limit=12')return {ok:true,json:async()=>({reports:[
    {replay_until:'2026-10-29T02:40:00Z',volume_events:3772,volume_followed_by_a:150,a_lead_median_seconds:5264,volume_gt_5pct:13.68,baseline_gt_5pct:7.31,ladder_net_0_3pct:0.21,ladder_net_0_5pct:0.01}
  ]})};
  if(url==='/api/structure?limit=100'){if(structureGate)await structureGate;return {ok:true,json:async()=>({items:[
    {token_address:'0xtoken',available:true,stage:'watch',stage_label:'观察',structure_label:'结构观察',data_status_label:'预热中',risk_status_label:'风险通过',conclusion:'等待确认',factors:{},timeline:[],updated_at:Math.floor(Date.now()/1000),b_crossed_at:1792753000,b_at:null,c_at:null,b_breakout_line:0.000000012,risk_status:'PASS'}
  ]})};}
  if(url.endsWith('/summary'))return {ok:true,json:async()=>url.includes('0xtoken')?[row,{...row,pair_address:'0xmid',liquidity_usd:50000,volume_h1:5000,signal_score:45,dex_id:'uniswap'}]:[{...row,token_address:'0xother',pair_address:'0xlow',token_symbol:'OTHER',signal_score:20}]};
  if(url.includes('/snapshots?limit=36')) return {ok:true,json:async()=>[0,1,2].slice(0,url.includes('0xnew')?1:3).map(i=>({observed_at:new Date(at-i*60000).toISOString(),price_usd:row.price_usd*(1-i*.01),liquidity_usd:100000,volume_h1:(url.includes('0xmid')?5000:15000)-i*1000}))};
  if(url.endsWith('/prices')) return {ok:true,json:async()=>({symbol:'TESTUSDT',spot:[{at:at-100000,price:row.price_usd*.99},{at:at-40000,price:row.price_usd}],futures:[{at:at-100000,price:row.price_usd*1.01},{at:at-40000,price:row.price_usd*1.02}]})};
  if(url.endsWith('/oi')) return {ok:true,json:async()=>({available:true,symbol:'TESTUSDT',timestamp:Date.now(),calculated_at:new Date().toISOString(),oi_usd:12000,change_1h_pct:2.5,change_24h_pct:6,history:[{oi_usd:10000},{oi_usd:12000}],global:{oi_usd:30000,market_count:2},long_short:{ratio:1.3,timestamp:Date.now()}})};
  return { ok: true, json: async () => url.startsWith('/api/overview') ? [row, {...row, pair_address:'0xmid', liquidity_usd:50000, volume_h1:5000, signal_score:45, dex_id:'uniswap'}, {...row, token_address:'0xother', pair_address:'0xlow', token_symbol:'OTHER', signal_score:20}, {...row, token_address:'0xzero', pair_address:'0xzero-pair', token_symbol:'ZERO', signal_score:0, signal_reasons:'[]'}] : url.endsWith('/oi') ? { available:true, symbol:'TESTUSDT', timestamp:Date.now(), oi_usd:12000, change_1h_pct:2.5, change_24h_pct:6, history:[{oi_usd:10000},{oi_usd:12000}], global:{oi_usd:30000,market_count:2}, long_short:{ratio:1.3,timestamp:Date.now()} } : url.endsWith('/depth') ? { available:true, calculated_at:depthAt, dex:url.includes('0xmid')?'uniswap':'pancakeswap', type:'v3', block:'0x10', bands_pct:[1,2,5], support_usd:url.includes('0xmid')?[50,100,250]:[100,200,500], pressure_usd:url.includes('0xmid')?[60,120,300]:[120,240,600] } : url.endsWith('/whales') ? {available:true,calculated_at:new Date().toISOString(),trades:url.includes('0xmid')?[{side:'sell',usd:18000,at:new Date().toISOString(),hash:'0xabc',wallet:'0xdef'},{side:'buy',usd:11000,at:new Date().toISOString(),hash:'0xghi',wallet:'0xjkl'}]:[{side:'sell',usd:12000,at:new Date().toISOString(),hash:'0xabc',wallet:'0xdef'}]} : url.endsWith('/transfers') ? {available:true,calculated_at:new Date().toISOString(),sample_count:100,transfers:[{amount:1500000,from:'0xaaaabbbb',to:'0xccccdddd',hash:'0xtransfer',at:new Date().toISOString()},{amount:2000000,from:'0xpair',to:'0xccccdddd',hash:'0xpool',at:new Date().toISOString()}]} : [] }; };
const context=vm.createContext({ document, fetch, localStorage, window:{addEventListener() {}}, setInterval() {}, Date, Map, console, Notification:TestNotification });
vm.runInContext(script, context);
assert.equal(vm.runInContext('watchlistOnly',context),true,'legacy boolean watchlist-only state must survive a reload');
vm.runInContext('watchlistOnly=false; watchlist.clear()',context);
assert.ok(document.getElementById('tokenList').innerHTML.includes('CACHED'), 'recent list cache should render before the network response');
assert.ok(document.getElementById('refreshInfo').textContent.includes('行情快照最早 2分钟前'), 'cached list must retain the sample age');
setImmediate(async () => {
  await new Promise(setImmediate);
  vm.runInContext(`watchlist.add('0xtoken')`,context);
  await vm.runInContext('refreshStructure()',context);
  await vm.runInContext('refreshEarlyWarning()',context);
  assert.equal(TestNotification.instances.length,1,'watchlisted active early warnings should notify once');
  assert.ok(TestNotification.instances[0].title.includes('TEST'),'notification should identify the token');
  await vm.runInContext('refreshEarlyWarning()',context);
  assert.equal(TestNotification.instances.length,1,'the same early warning state must not notify again within 15 minutes');
  await vm.runInContext('toggleEarlyHistory(true)',context);
  assert.ok(document.getElementById('earlyHistoryBody').innerHTML.includes('13.68%'),'history must show volume hit rate');
  assert.ok(document.getElementById('earlyHistoryBody').innerHTML.includes('7.31%'),'history must show baseline hit rate');
  assert.ok(document.getElementById('earlyHistoryBody').innerHTML.includes('+0.21%'),'history must show 0.3% cost result');
  vm.runInContext(`watchlist.delete('0xtoken');renderedKey='';renderTokenList();renderDetail()`,context);
  assert.ok(!html.includes('view-switch'),'the novice card should not require a separate view mode');
  assert.ok(!document.getElementById('tokenList').innerHTML.includes('token-v2'), 'the list should not show the legacy V2 stage badge');
  assert.ok(document.getElementById('tokenList').innerHTML.includes('首次启动'), 'V2 stage should use Chinese copy');
  assert.ok(document.getElementById('tokenList').innerHTML.includes('V2阶段：首次启动'), 'V2 tooltip should use Chinese copy');
  const v2Tip=vm.runInContext(`v2TipText({stage:'base',risk_status:'WARN',data_status:'warming_up',b_at:null,c_at:null,c_reason:'support_failed'})`,context);
  assert.ok(v2Tip.includes('V2阶段：企稳') && v2Tip.includes('数据状态：预热中') && v2Tip.includes('结果：跌破支撑'), 'V2 stages and data states should use Chinese copy');
  assert.ok(!v2Tip.includes('B时间') && !v2Tip.includes('C时间'), 'V2 tooltip should not expose B/C jargon');
  const stateCases=[
  [{available:true,stage:'inactive',b_crossed_at:null,b_at:null,c_at:null},'未启动'],
    [{available:true,stage:'watch',b_crossed_at:1,b_at:null,c_at:null},'B 候选'],
    [{available:true,stage:'watch',b_crossed_at:null,b_at:null,c_at:null},'A 观察'],
    [{available:true,stage:'first_start',b_crossed_at:1,b_at:2,c_at:null},'B 已启动'],
    [{available:true,stage:'first_wave',b_crossed_at:1,b_at:2,c_at:null},'C 首波'],
    [{available:true,stage:'pullback',b_crossed_at:1,b_at:2,c_at:null},'C 回调'],
    [{available:true,stage:'base',b_crossed_at:1,b_at:2,c_at:null},'C 企稳'],
    [{available:true,stage:'second_start',b_crossed_at:1,b_at:2,c_at:3},'C 已启动']
  ];
  for(const [input,expected] of stateCases){
    assert.equal(vm.runInContext(`structureState(${JSON.stringify(input)}).label`,context),expected,`structure state should map ${input.stage} to ${expected}`);
  }
  assert.equal(vm.runInContext('fmtTurnover(null)',context),'--');
  assert.equal(vm.runInContext('fmtTurnover(0.1333)',context),'13.3%');
  assert.equal(vm.runInContext('fmtUsd(1500)',context),'$1.5K');
  assert.equal(vm.runInContext('fmtUsd(0.0185175)',context),'$0.01852');
  assert.equal(vm.runInContext('whaleFlowText({netUsd:0,buyCount:0,sellCount:0})',context),'$0 · 0笔','zero flow must disclose that no large trades were sampled');
  assert.equal(vm.runInContext('structureState({available:true,stage:"watch",b_crossed_at:1,b_at:null,b_breakout_line:1},1.1).label',context),'B 超出确认窗','a runaway unconfirmed breakout must not look ready for immediate confirmation');
  assert.equal(vm.runInContext("structureGuidance({available:true,stage:'watch'}).trigger",context),'30m 突破 + 量能确认');
  const noviceCases=[
    [{token_symbol:'RISK',token_address:'0xrisk',price_usd:1.1,liquidity_usd:1000,signal_risk_flags:'["liquidity_withdrawal"]',observed_at:new Date().toISOString()},{available:true,stage:'watch',b_crossed_at:1,b_at:null,b_breakout_line:1,updated_at:Math.floor(Date.now()/1000),risk_status:'PASS'},'risk_block','不碰'],
    [{token_symbol:'STALE',token_address:'0xstale',price_usd:.99,liquidity_usd:1000,signal_risk_flags:'[]',observed_at:new Date(Date.now()-180000).toISOString()},{available:true,stage:'watch',b_crossed_at:1,b_at:null,b_breakout_line:1,updated_at:Math.floor(Date.now()/1000)},'data_stale','数据过期'],
    [{token_symbol:'FAR',token_address:'0xfar',price_usd:1.42,liquidity_usd:1000,signal_risk_flags:'[]',observed_at:new Date().toISOString()},{available:true,stage:'watch',b_crossed_at:1,b_at:null,b_breakout_line:1,updated_at:Math.floor(Date.now()/1000)},'too_far','已涨远'],
    [{token_symbol:'NEAR',token_address:'0xnear',price_usd:.98,liquidity_usd:1000,signal_risk_flags:'[]',observed_at:new Date().toISOString()},{available:true,stage:'watch',b_crossed_at:1,b_at:null,b_breakout_line:1,updated_at:Math.floor(Date.now()/1000)},'near_buy','接近买点'],
    [{token_symbol:'BREADY',token_address:'0xbready',price_usd:1.02,liquidity_usd:1000,signal_risk_flags:'["fdv_liquidity_stretched"]',observed_at:new Date().toISOString()},{available:true,stage:'first_start',risk_status:'WARN',data_status:'current',b_crossed_at:1,b_at:2,b_breakout_line:1,updated_at:Math.floor(Date.now()/1000)},'breakout_ready','可执行候选'],
    [{token_symbol:'CREADY',token_address:'0xcready',price_usd:1.02,liquidity_usd:1000,signal_risk_flags:'[]',observed_at:new Date().toISOString()},{available:true,stage:'second_start',risk_status:'PASS',data_status:'current',b_crossed_at:1,b_at:2,c_at:3,b_breakout_line:.8,c_breakout_line:1,updated_at:Math.floor(Date.now()/1000)},'second_ready','可执行候选'],
    [{token_symbol:'UNKNOWN',token_address:'0xunknown',price_usd:1,liquidity_usd:1000,signal_risk_flags:'broken',observed_at:new Date().toISOString()},null,'unknown','信息不足']
  ];
  for(const [token,structure,key,label] of noviceCases){
    const result=vm.runInContext(`noviceState(${JSON.stringify(token)},${JSON.stringify(structure)})`,context);
    assert.equal(result.key,key);assert.equal(result.label,label);
  }
  const noLine=vm.runInContext(`(token=>{structureRows.set(token.token_address,{available:true,stage:'watch',b_crossed_at:null,b_at:null,updated_at:Math.floor(Date.now()/1000),factors:{},timeline:[]});return structureCardHTML(token)})({token_symbol:'WAIT',token_address:'0xwait',price_usd:1,liquidity_usd:1000,signal_risk_flags:'[]',observed_at:new Date().toISOString()})`,context);
  assert.ok(noLine.includes('等待结构形成') && !noLine.includes('buy-axis'),'a missing B line must not render a fake axis');
  assert.equal(vm.runInContext(`noviceRisk({signal_risk_flags:'broken'},null).label`,context),'未知','unknown risk must remain unknown');
  assert.equal(vm.runInContext(`noviceState({price_usd:1,liquidity_usd:1000,signal_risk_flags:'[]',observed_at:new Date().toISOString()},{available:true,stage:'inactive',data_status:'warming_up',updated_at:Math.floor(Date.now()/1000)}).key`,context),'data_warmup','warmup must not be shown as waiting for a buy point');
assert.equal(vm.runInContext(`candidatePriority({token_address:'0xready',price_usd:1.02,liquidity_usd:1000,signal_risk_flags:'[]',observed_at:new Date().toISOString()},{available:true,stage:'second_start',risk_status:'PASS',data_status:'current',b_at:1,b_breakout_line:.8,c_breakout_line:1,updated_at:Math.floor(Date.now()/1000)})`,context),2,'confirmed executable candidates must sort above ordinary activity scores');
assert.equal(vm.runInContext(`candidatePriority({price_usd:1.02,liquidity_usd:1000,signal_risk_flags:'[]',observed_at:new Date().toISOString()},{available:true,stage:'inactive',updated_at:Math.floor(Date.now()/1000)})`,context),0);
assert.equal(vm.runInContext(`earlyWarningRows.set('0xready',{status:'strong_anomaly',is_active:true});candidatePriority({token_address:'0xready',price_usd:1.02,liquidity_usd:1000,signal_risk_flags:'[]',observed_at:new Date().toISOString()},{available:true,stage:'second_start',risk_status:'PASS',data_status:'current',b_at:1,b_breakout_line:.8,c_breakout_line:1,updated_at:Math.floor(Date.now()/1000)})`,context),4,'strong early warnings must increase discovery priority');
  await new Promise(setImmediate);
assert.ok(vm.runInContext(`stageMatches('inactive', new Set(['inactive']))`,context));
assert.ok(!vm.runInContext(`stageMatches('inactive', new Set(['watch']))`,context));
assert.ok(vm.runInContext(`stageMatches('first_start_extended', new Set(['first_start']))`,context));
assert.ok(vm.runInContext(`stageFilter.has('inactive') && stageFilter.size === stageFilterOptions.length`,context));
  const detail = document.getElementById('detailPanel').innerHTML;
  assert.ok(!detail.includes('class="novice-card"'),'the novice summary should not remain a separate card');
  assert.ok(detail.includes('class="structure-summary"'),'the novice summary should be embedded in the structure card');
  assert.ok(detail.indexOf('class="structure-card"') < detail.indexOf('class="structure-summary"') && detail.indexOf('class="structure-summary"') < detail.indexOf('class="factor-grid"'),'the novice summary should sit inside the structure card before factors');
  assert.ok(detail.indexOf('合约持仓 · OI') < detail.indexOf('市场价格对比'), 'OI should appear above the trend');
  assert.ok(detail.indexOf('结构阶段') < detail.indexOf('合约持仓 · OI'), 'structure and OI should share the decision context row');
  assert.ok(detail.includes('class="context-grid"'), 'structure and OI should share the responsive grid');
  assert.ok(detail.indexOf('市场价格对比') < detail.indexOf('价格区间深度'), 'trend should be visible before pool depth');
  assert.ok(detail.indexOf('已收录 <b>2</b> 个池子') < detail.indexOf('更新于 '), 'pool count should sit before the update time');
  assert.ok(detail.includes('class="overview-grid"'), 'the first card should use one unified stats grid');
  assert.ok(detail.indexOf('参考价格') < detail.indexOf('5m 涨跌'), 'price should lead its change metrics');
  assert.ok(detail.indexOf('5m 涨跌') < detail.indexOf('1h 涨跌'), '5m and 1h changes should sit beside price');
  assert.ok(detail.indexOf('5m 成交额') < detail.indexOf('1h 成交额'), 'volume metrics should be flat siblings');
  assert.ok(detail.includes('class="trade-summary"'), 'the first card should lead with a trading summary');
  assert.ok(detail.includes('建议：'), 'the summary should state the current action');
  assert.ok(detail.includes('触发：') && detail.includes('失效：'), 'the summary should expose structure trigger and invalidation');
  assert.ok(detail.includes('1h 换手：'), 'the summary should expose turnover');
  assert.ok(!detail.includes('活跃度分'), 'detail should not repeat the list activity score');
  assert.ok(detail.includes('1h 大额净流入'), 'the old net-buy slot should use real large-trade flow');
  assert.ok(detail.includes('池内资产估值'), 'pool assets must not be mistaken for near-price depth');
  assert.ok(detail.includes('代币风险观察') && detail.includes('卖出笔数偏多'));
  assert.ok(!detail.includes('合并池子口径'), 'the risk card should not repeat merge scope copy');
  assert.ok(!detail.includes('合约安全风险'));
  for (const expected of ['0xtoken', '24h 涨跌', '市值', '更新于 ', '$0.00000001235', '5m 涨跌', '+2.0%', '1h 涨跌', '-3.0%', '$2.4K', '$20.0K', '价格区间深度']) {
    assert.ok(detail.includes(expected), `missing ${expected}`);
  }
  for (const removed of ['可控', '上涨概率', '概率预测', '下一步观察', 'pancakeswap', 'uniswap', '0xpair', '0xmid']) {
    assert.ok(!detail.includes(removed), `unexpected ${removed}`);
  }
  assert.ok(!detail.includes('未接入卖出限制'), 'the first card should not repeat the safety copy');
  assert.ok(!detail.includes('行情快照'), 'the snapshot age should move to the card header');
  assert.ok(!detail.includes('数据状态未知 · 风险未知'), 'structure status should not repeat the same state copy');
  assert.ok(detail.indexOf('class="transfer-panel"') < detail.indexOf('id="transferTime"'), 'transfer time should stay inside the transfer block');
  assert.ok(html.includes('<details class="stage-filter-details">'), 'stage filtering should use a native disclosure control');
  assert.ok(html.includes('<summary class="filter-toggle" id="filterToggle">筛选</summary>'), 'the stage filter trigger should remain keyboard accessible');
  assert.ok(!detail.includes('0xlegacy'));
  const list = document.getElementById('tokenList').innerHTML;
  assert.equal((list.match(/class="token-btn/g) || []).length, 2);
  assert.ok(!list.includes('ZERO'));
  assert.ok(!list.includes('structure-line'), 'list rows should not duplicate structure details');
  assert.ok(list.includes('B 候选'), 'list should show a B candidate instead of a generic watch state');
  assert.equal(document.getElementById('tokenCount').textContent, '入榜 2 个代币 · 自选 0');
  assert.ok(detail.includes('☆ 自选'));
  vm.runInContext("toggleWatchlist('0xtoken')",context);
  assert.ok(JSON.parse(localStorage.getItem('tmm-watchlist-v1')).includes('0xtoken'),'watchlist must persist');
  assert.ok(document.getElementById('watchButton').textContent.includes('★ 已自选'));
  vm.runInContext('watchlistOnly=true;renderTokenList()',context);
  assert.ok(document.getElementById('tokenList').innerHTML.includes('TEST'));
  assert.ok(!document.getElementById('tokenList').innerHTML.includes('OTHER'),'watchlist filter must hide unwatched tokens');
  assert.ok(document.getElementById('refreshInfo').textContent.startsWith('行情快照最早 '));
  assert.ok(JSON.parse(storage.get('tmm-tokens-v1')).rows.length>1, 'network result replaces the cached list');
  assert.ok(list.includes('data-tip="活跃度分 · 命中标签：流动性适中、1小时成交额达标 · V2阶段：首次启动'));
  assert.ok(list.includes('活跃度分'));
  assert.ok(list.includes('快照 '), 'each list row should expose its own sample age');
  assert.ok(!list.includes('onclick='), 'token selection must not embed an address in JavaScript');
  assert.ok(html.includes('id="scoreTooltip" class="score-tooltip"'), 'score tooltip renders outside the scrolling list');
  assert.ok(html.includes("tokenList.addEventListener('pointerover'"));
  assert.ok(!html.includes('.score-tag::after'), 'score tooltip must not be clipped by the scrolling list');
  const mixed=vm.runInContext(`mergeTokens([
    {token_address:'0xmixed',pair_address:'0xdeep',dex_id:'a',liquidity_usd:100,signal_score:0,signal_reasons:'[]',signal_risk_flags:'["low_liquidity"]'},
    {token_address:'0xmixed',pair_address:'0xscored',dex_id:'b',liquidity_usd:50,signal_score:45,signal_reasons:'["h1_buy_dominance"]',signal_risk_flags:'["sell_pressure"]'}
  ])`,context)[0];
  assert.equal(mixed.indicator_score,45,'a zero-score main pool must not hide a scored token');
  assert.equal(mixed.indicator_reasons,'["h1_buy_dominance"]');
  assert.equal(mixed.signal_risk_flags,'["low_liquidity"]','risk belongs to the most liquid pool');
  assert.equal(vm.runInContext('timeAgo("bad")',context),'时间未知');
  assert.equal(vm.runInContext('timeAgo(new Date(Date.now()+3600000).toISOString())',context),'时间异常');
  assert.equal(vm.runInContext('poolUpdatedAt([{calculated_at:null},{calculated_at:new Date().toISOString()}])',context),'部分池子更新时间未知');
  assert.equal(vm.runInContext('poolUpdatedAt([{calculated_at:"2026-10-23T03:00:00+02:00"},{calculated_at:"2026-10-23T02:00:00Z"}])',context),vm.runInContext('updatedAt("2026-10-23T01:00:00Z")',context));
  assert.equal(vm.runInContext('marketRisks(null)',context),null);
  assert.equal(vm.runInContext('marketRisks("broken")',context),null);
  assert.equal(vm.runInContext('marketRisks(\'["missing_project_metadata","sell_pressure"]\').join("、")',context),'卖出笔数偏多','old snapshots must not call missing DEX links a project risk');
  assert.equal(vm.runInContext('scoreTags(\'["holder_depth","h1_buy_dominance"]\')',context),'1小时买入笔数占优');
  assert.equal(vm.runInContext('marketRisks(\'["low_liquidity","liquidity_near_zero","missing_price","fdv_missing","thin_downside_depth"]\').join("、")',context),'流动性极低、跌1%所需卖出额偏低');
  const sameDex=vm.runInContext(`mergeTokens([{token_address:'0xsame',pair_address:'0xa',dex_id:'pancakeswap',liquidity_usd:100,volume_h1:10,signal_score:10},{token_address:'0xsame',pair_address:'0xb',dex_id:'pancakeswap',liquidity_usd:50,volume_h1:5,signal_score:5}])`,context)[0];
  assert.equal(sameDex.pools.length,2);
  assert.equal(sameDex.liquidity_usd,150);
  assert.equal(sameDex.volume_h1,15);
  assert.ok(!list.includes('list-group-heading'));
  assert.ok(!list.includes('uniswap') && !list.includes('pancakeswap'));
  const trend=document.getElementById('trendBody').innerHTML;
  assert.ok(document.getElementById('trendTime').textContent.startsWith('更新于 '), 'trend must use the shared header update-time copy');
  assert.equal((trend.match(/<polyline/g)||[]).length,3,`pool and futures prices plus turnover: ${trend}`);
  assert.ok(trend.includes('池子') && trend.includes('合约标记价') && !trend.includes('现货'));
  assert.ok(trend.includes('chart-axis'), 'price chart should show its time range');
  assert.ok(trend.includes('1 小时成交额走势') && trend.includes('$20.0K'));
  const poolOnly=vm.runInContext(`makeMarketChart([{at:1,price:1},{at:2,price:2}],{spot:[{at:1,price:1},{at:2,price:2}],futures:[]})`,context);
  assert.equal((poolOnly.match(/<polyline/g)||[]).length,1);
  assert.ok(poolOnly.includes('池子') && !poolOnly.includes('现货') && !poolOnly.includes('合约标记价'));
  assert.ok(poolOnly.includes('price-scale') && poolOnly.includes('$2.0000') && poolOnly.includes('$1.0000'));
  assert.ok(vm.runInContext('makeMarketChart([{at:1,price:0},{at:2,price:0}],null)',context).includes('价格历史不足'));
  const depth = document.getElementById('depthBody').innerHTML;
  assert.ok(document.getElementById('depthTime').textContent.startsWith('更新于 '), 'depth must use the shared header update-time copy');
  assert.ok(depth.includes('目标涨跌幅') && depth.includes('跌至该幅度需卖出') && depth.includes('涨至该幅度需买入'));
  assert.ok(depth.includes('更多深度 · 10% / 15% / 20%'));
  assert.ok(depth.includes('<div>10%</div>') && depth.includes('<div>15%</div>') && depth.includes('<div>20%</div>'));
  assert.ok(depth.includes('跌 5% 的金额已包含跌 1% 的金额'));
  assert.ok(depth.includes('<strong>$150</strong>') && depth.includes('<strong>$300</strong>') && depth.includes('<strong>$750</strong>'));
  assert.ok(depth.includes('<strong>$180</strong>') && depth.includes('<strong>$360</strong>') && depth.includes('<strong>$900</strong>'));
  assert.ok(depth.includes('depth-scroll'), 'depth bands remain readable on narrow screens');
  assert.ok(depth.includes('池子 2/2') && depth.includes('更新于 '));
  const beforeEmptyPool=calls.length;
  await vm.runInContext(`loadDepth({...selectedDetail,pools:[...selectedDetail.pools,{pair_address:'0xempty',price_usd:null,liquidity_usd:0}]})`,context);
  assert.ok(document.getElementById('depthBody').innerHTML.includes('池子 2/2'), 'empty pools do not dilute depth coverage');
  assert.ok(!calls.slice(beforeEmptyPool).some(url=>url.includes('0xempty/depth')), 'empty pools need no depth request');
  const beforeTinyPool=calls.length;
  await vm.runInContext(`loadDepth({...selectedDetail,pools:[...selectedDetail.pools,{pair_address:'0xtiny',price_usd:${row.price_usd},liquidity_usd:100}]})`,context);
  assert.ok(!calls.slice(beforeTinyPool).some(url=>url.includes('0xtiny/depth')), 'pools beyond 95% liquidity coverage need no detail request');
  assert.ok(document.getElementById('depthBody').innerHTML.includes('纳入流动性约 99%'), 'omitted pool liquidity must be disclosed');
  const offsetPool=vm.runInContext(`poolDepthAtTarget(
    {price_usd:1.02},
    {bands_pct:[1,2,5],support_usd:[100,200,500]},
    0.99,'support_usd')`,context);
  assert.ok(Math.abs(offsetPool-294.117647)<0.001,'each pool must be quoted to the shared target price');
  assert.equal(vm.runInContext(`poolDepthAtTarget({price_usd:0.98},{bands_pct:[1,2,5],support_usd:[50,100,250]},0.99,'support_usd')`,context),0,'a pool already below the target must not add sell depth');
  assert.equal(vm.runInContext(`poolDepthAtTarget({price_usd:1.02},{bands_pct:[1,2,5],support_usd:[100,200,500]},0.90,'support_usd')`,context),null,'outside sampled range must remain unknown');
  context.savedDepthCache=vm.runInContext('new Map(depthCache)',context);
  vm.runInContext(`depthCache.set('0xpair',{expires:Date.now()+60000,promise:Promise.resolve({available:true,calculated_at:new Date().toISOString(),bands_pct:[1,2,5],support_usd:[100,200,500],pressure_usd:[120,240,600]})});
    depthCache.set('0xmid',{expires:Date.now()+60000,promise:Promise.resolve({available:true,calculated_at:new Date(Date.now()-8*60000).toISOString(),bands_pct:[1,2,5],support_usd:[50,100,250],pressure_usd:[60,120,300]})})`,context);
  await vm.runInContext('loadDepth(selectedDetail)',context);
  assert.ok(document.getElementById('depthBody').innerHTML.includes('近5分钟覆盖流动性 67%'), 'fresh main pool must be distinguishable from an old smaller pool');
  assert.ok(document.getElementById('depthBody').innerHTML.includes('合计含旧数据'), 'old pool depth must not look fully fresh');
  vm.runInContext('depthCache.clear(); savedDepthCache.forEach((value,key)=>depthCache.set(key,value))',context);
  await vm.runInContext('loadDepth(selectedDetail)',context);
  const oi=document.getElementById('oiBody').innerHTML;
  assert.ok(oi.includes('oi-main') && oi.includes('oi-meta'));
  assert.ok(oi.includes('1小时 OI 变化') && oi.includes('大户多空比') && oi.includes('多交易所 OI'));
  assert.ok(oi.includes('小时采样'), 'OI timestamp must be labeled as an hourly sample');
  assert.ok(!oi.includes('与前一小时整点样本比较'));
  vm.runInContext(`oiCache.set('0xpair',{at:Date.now(),promise:Promise.resolve({available:false,reason:'无同名合约'})})`,context);
  await vm.runInContext(`loadOI('0xpair',-3,false)`,context);
  assert.equal(document.getElementById('oiSection').className,'detail-section oi-empty');
  assert.equal(document.getElementById('oiTime').textContent,'更新时间未知');
  vm.runInContext(`oiCache.delete('0xpair')`,context);
  await vm.runInContext(`loadOI('0xpair',-3,false)`,context);
  for (const id of ['structureTime','oiTime','trendTime','depthTime','whaleTime','transferTime']) {
    assert.ok(document.getElementById(id).textContent.startsWith('更新于 '), `${id} must use the shared header update-time copy`);
  }
  assert.equal(document.getElementById('oiSection').className,'detail-section');
  assert.ok(vm.runInContext(`Date.now()-oiCache.get('0xpair').at`,context)<=60000,'fresh OI gets a one-minute expiry');
  vm.runInContext(`oiCache.set('0xpair',{at:Date.now(),promise:Promise.resolve({available:true,symbol:'TESTUSDT',timestamp:null,oi_usd:12000,history:[]})})`,context);
  await vm.runInContext(`loadOI('0xpair',-3,false)`,context);
  assert.ok(document.getElementById('oiBody').innerHTML.includes('小时采样 时间未知'));
  vm.runInContext(`oiCache.delete('0xpair')`,context);
  const whales=document.getElementById('whaleBody').innerHTML;
  assert.equal(document.getElementById('whaleFlowValue').textContent,'-$7.0K','large-trade net flow must dedupe cross-pool trades');
  assert.ok(whales.includes('$18.0K') && whales.includes('$11.0K'));
  assert.ok(whales.includes('近1小时买入') && whales.includes('近1小时卖出'), 'large-trade summary should use the one-hour window');
  assert.ok(whales.includes('class="trade-buy"') && whales.includes('class="trade-sell"'));
  assert.ok(html.includes('.trade-buy { color:var(--accent); }') && html.includes('.trade-sell { color:#b91c1c; }'));
  assert.ok(!whales.includes('$30.0K'),'one cross-pool transaction must not be double counted');
  assert.ok(whales.includes('bscscan.com/tx/0xabc'));
  assert.ok(whales.includes('池子 2/2') && whales.includes('更新于 '));
  assert.ok(whales.includes('覆盖流动性 100%'));
  assert.ok(!whales.includes('5 分钟更新'));
  const transfers=document.getElementById('transferBody').innerHTML;
  assert.ok(transfers.includes('1,500,000') && transfers.includes('0xaaaa…bbbb') && transfers.includes('0xcccc…dddd'));
  assert.ok(!transfers.includes('2,000,000'),'known pool transfer must not appear as wallet movement');
  assert.ok(transfers.includes('≈ $0.0185'),'transfers should expose an approximate USD value');
  assert.ok(transfers.includes('更新于 ') && !transfers.includes('5 分钟更新'));
  const before=calls.length;
  await vm.runInContext('refresh()',context);
assert.deepEqual(calls.slice(before).filter(url=>!['/api/tokens','/api/signal-v2?limit=50','/api/structure?limit=100','/api/early-warning'].includes(url)),[],'unchanged overview must not reload detail APIs');
  assert.equal(document.getElementById('depthBody').innerHTML,depth);
  row.observed_at=new Date(Date.now()+1000).toISOString();
  vm.runInContext(`detailCache.get('0xtoken').at=0`,context);
  await vm.runInContext('refresh()',context);
  assert.ok(document.getElementById('detailPanel').innerHTML.includes('$750'),'new snapshot keeps old depth visible while updating');
  depthAt=new Date(Date.now()-8*60000).toISOString();
  vm.runInContext('depthCache.forEach(entry=>entry.expires=0)',context);
  await vm.runInContext('refresh()',context);
  await new Promise(setImmediate);
  const staleCalls=calls.filter(url=>url.endsWith('/depth')).length;
  await vm.runInContext('refresh()',context);
  await new Promise(setImmediate);
  assert.ok(calls.filter(url=>url.endsWith('/depth')).length>staleCalls,'stale depth must be polled without a new market snapshot');
  depthAt=new Date().toISOString();
  await vm.runInContext('refresh()',context);
  await new Promise(setImmediate);
  assert.ok(vm.runInContext('depthCache.get("0xpair").expires',context)>Date.now(),'fresh depth gets a two-minute expiry');
  await vm.runInContext(`loadTrend({...selectedDetail,pools:[...selectedDetail.pools,{pair_address:'0xnew'}]})`,context);
  assert.ok(document.getElementById('trendBody').innerHTML.includes('合约标记价'),'a new pool with one snapshot must not erase established price history');
  row.token_symbol='<img src=x onerror=alert(1)>';
  row.observed_at=new Date(Date.now()+2000).toISOString();
  vm.runInContext(`detailCache.get('0xtoken').at=0`,context);
  await vm.runInContext('refresh()',context);
  assert.ok(!document.getElementById('tokenList').innerHTML.includes('<img'),'untrusted symbol must be escaped in list');
  assert.ok(!document.getElementById('detailPanel').innerHTML.includes('<img'),'untrusted symbol must be escaped in detail');
  assert.ok(!document.getElementById('transferBody').innerHTML.includes('<img'),'untrusted symbol must be escaped in transfers');
  await vm.runInContext(`(async()=>{
    for(const pair of ['0xpair','0xmid'])whaleCache.set(pair,{expires:Date.now()+300000,promise:Promise.resolve({available:true,trades:[]})});
    transferCache.set('0xpair',{expires:Date.now()+300000,promise:Promise.resolve({available:true,sample_count:100,transfers:[]})});
    await Promise.all([loadWhales(selectedDetail),loadTransfers(selectedDetail)]);
  })()`,context);
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('activity-empty'));
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('池子 2/2'));
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('交易样本与统计口径'));
  assert.ok(document.getElementById('transferBody').innerHTML.includes('activity-empty'));
  assert.ok(document.getElementById('transferBody').innerHTML.includes('转账样本与统计口径'));
  let finishSecondPool;
  context.secondPool = new Promise(resolve=>{finishSecondPool=resolve;});
  vm.runInContext(`whaleCache.set('0xpair',{expires:Date.now()+60000,promise:Promise.resolve({available:true,trades:[]})}); whaleCache.set('0xmid',{expires:Date.now()+60000,promise:secondPool})`,context);
  const partial=vm.runInContext('loadWhales(selectedDetail)',context);
  await new Promise(setImmediate);
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('已查询 1/2 个池子'));
  assert.equal(document.getElementById('whaleFlowValue').textContent,'-- · 1/2池','partial whale coverage must not look like zero flow');
  finishSecondPool({available:true,trades:[]});
  await partial;
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('池子 2/2'));
  let finishFirstPool;
  context.firstPool = new Promise(resolve=>{finishFirstPool=resolve;});
  vm.runInContext(`whaleCache.clear(); whaleCache.set('0xpair',{expires:Date.now()+60000,promise:firstPool})`,context);
  const sequential=vm.runInContext('loadWhales(selectedDetail)',context);
  await new Promise(setImmediate);
  assert.equal(vm.runInContext("whaleCache.has('0xmid')",context),true,'cached pool reads must start together');
  finishFirstPool({available:true,trades:[]});
  await sequential;
  await vm.runInContext(`(async()=>{
    for(const pair of selectedDetail.pools.map(p=>p.pair_address))whaleCache.set(pair,{expires:Date.now()+60000,promise:Promise.resolve({available:false,reason:'交易数据源限流，稍后自动重试'})});
    await loadWhales(selectedDetail);
  })()`,context);
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('交易数据源限流，稍后自动重试'));
  await vm.runInContext(`(async()=>{
    depthCache.set('0xpair',{expires:Date.now()+60000,promise:Promise.resolve({available:true,stale:true,calculated_at:new Date(Date.now()-360000).toISOString(),support_usd:[100],pressure_usd:[120]})});
    depthCache.set('0xmid',{expires:Date.now()+60000,promise:Promise.resolve({available:false,reason:'限流'})});
    await loadDepth(selectedDetail);
    whaleCache.set('0xpair',{expires:Date.now()+60000,promise:Promise.resolve({available:true,stale:true,calculated_at:new Date(Date.now()-360000).toISOString(),trades:[]})});
    whaleCache.set('0xmid',{expires:Date.now()+60000,promise:Promise.resolve({available:false,reason:'限流'})});
    await loadWhales(selectedDetail);
    transferCache.set('0xpair',{expires:Date.now()+60000,promise:Promise.resolve({available:true,stale:true,calculated_at:new Date(Date.now()-360000).toISOString(),sample_count:12,transfers:[]})});
    await loadTransfers(selectedDetail);
  })()`,context);
  assert.ok(document.getElementById('depthBody').innerHTML.includes('池子 1/2'));
  assert.ok(document.getElementById('depthBody').innerHTML.includes('沿用上次结果'));
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('池子 1/2'));
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('沿用上次结果'));
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('覆盖流动性 67%'));
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('数据已过期'));
  assert.ok(document.getElementById('whaleBody').innerHTML.includes('仅代表已覆盖池子'));
  assert.ok(document.getElementById('transferBody').innerHTML.includes('沿用上次结果'));
  let finishDepth;
  context.pendingDepth = new Promise(resolve=>{finishDepth=resolve;});
  vm.runInContext(`depthCache.set('0xpair',{expires:Date.now()+60000,promise:Promise.resolve({available:true,calculated_at:new Date().toISOString(),support_usd:[100],pressure_usd:[120]})}); depthCache.set('0xmid',{expires:Date.now()+60000,promise:pendingDepth})`,context);
  const progressiveDepth=vm.runInContext('loadDepth(selectedDetail)',context);
  await new Promise(setImmediate);
  assert.ok(document.getElementById('depthBody').innerHTML.includes('池子 1/2'));
  assert.ok(document.getElementById('depthBody').innerHTML.includes('其余获取中'));
  finishDepth({available:true,calculated_at:new Date().toISOString(),support_usd:[50],pressure_usd:[60]});
  await progressiveDepth;
  assert.ok(document.getElementById('depthBody').innerHTML.includes('池子 2/2'));
  const previousRenderKey=vm.runInContext('renderedKey',context);
  let finishOldWhale;
  context.oldWhale=new Promise(resolve=>{finishOldWhale=resolve;});
  vm.runInContext(`whaleCache.set('0xpair',{expires:Date.now()+60000,promise:oldWhale}); whaleCache.set('0xmid',{expires:Date.now()+60000,promise:Promise.resolve({available:true,trades:[]})})`,context);
  const oldWhaleLoad=vm.runInContext('loadWhales(selectedDetail)',context);
  await new Promise(setImmediate);
  vm.runInContext('renderedKey="replacement"',context);
  document.getElementById('whaleBody').innerHTML='new whale panel';
  finishOldWhale({available:true,trades:[]});
  await oldWhaleLoad;
  assert.equal(document.getElementById('whaleBody').innerHTML,'new whale panel','old whale results must not overwrite a newer render');
  let finishOldDepth;
  context.oldDepth=new Promise(resolve=>{finishOldDepth=resolve;});
  vm.runInContext(`depthCache.set('0xpair',{expires:Date.now()+60000,promise:oldDepth}); depthCache.set('0xmid',{expires:Date.now()+60000,promise:Promise.resolve({available:true,calculated_at:new Date().toISOString(),support_usd:[50],pressure_usd:[60]})})`,context);
  const oldDepthLoad=vm.runInContext('loadDepth(selectedDetail)',context);
  await new Promise(setImmediate);
  vm.runInContext('renderedKey="replacement-again"',context);
  document.getElementById('depthBody').innerHTML='new depth panel';
  finishOldDepth({available:true,calculated_at:new Date().toISOString(),support_usd:[100],pressure_usd:[120]});
  await oldDepthLoad;
  assert.equal(document.getElementById('depthBody').innerHTML,'new depth panel','old depth results must not overwrite a newer render');
  context.previousRenderKey=previousRenderKey;
  vm.runInContext('renderedKey=previousRenderKey',context);
  vm.runInContext('selectedDetail.signal_risk_flags = \'["thin_downside_depth"]\'; renderDetail()',context);
  assert.ok(document.getElementById('detailPanel').innerHTML.includes('跌1%所需卖出额偏低'), 'fresh background depth must update the current risk strip');
  vm.runInContext('showOverview([...tokenData,{...tokenData[0],token_address:"0xfuture",observed_at:new Date(Date.now()+3600000).toISOString()}])',context);
  assert.equal(document.getElementById('refreshInfo').textContent,'行情快照时间异常');
  let releaseStructure;
  structureGate=new Promise(resolve=>{releaseStructure=resolve;});
  row.token_symbol='FAST';
  const slowStructureRefresh=vm.runInContext('refresh()',context);
  await new Promise(setImmediate);
  assert.ok(document.getElementById('tokenList').innerHTML.includes('FAST'),'slow structure must not delay the token list');
  const structureCalls=calls.filter(url=>url==='/api/structure?limit=100').length;
  await vm.runInContext('refresh()',context);
  assert.equal(calls.filter(url=>url==='/api/structure?limit=100').length,structureCalls,'slow structure must not launch overlapping scans');
  releaseStructure();
  await slowStructureRefresh;
});

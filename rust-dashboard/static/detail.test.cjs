const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const nodes = new Map();
const document = {
  querySelector() { return null; },
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, { innerHTML: '', textContent: '', addEventListener() {}, append() {} });
    return nodes.get(id);
  },
  createElement() { return { textContent: '', className: '' }; },
};
const row = {
  pair_address: '0xpair', token_address: '0xtoken', token_symbol: 'TEST', quote_symbol: 'USDT',
  observed_at: new Date().toISOString(), price_usd: 0.000000012345, price_change_m5: 2,
  price_change_h1: -3, price_change_h24: 8, liquidity_usd: 100000,
  volume_m5: 1200, volume_h1: 15000, buys_h1: 14, sells_h1: 7,
  signal_score: 65, signal_reasons:'["healthy_liquidity_band","h1_volume_support"]', signal_risk_flags:'["sell_pressure"]', signal_at:new Date().toISOString(), risk_6h_dd30: null, dex_id: 'pancakeswap',
};
let depthAt = new Date().toISOString();
let structureGate = null;
const calls=[];
const storage = new Map([['tmm-tokens-v1', JSON.stringify({at:Date.now()-60000,rows:[{token_address:'0xtoken',token_symbol:'CACHED',token_name:'Cached',indicator_score:65,indicator_reasons:row.signal_reasons,observed_at:new Date(Date.now()-120000).toISOString()}]})]]);
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
  if(url==='/api/structure?limit=100'){if(structureGate)await structureGate;return {ok:true,json:async()=>({items:[
    {token_address:'0xtoken',available:true,stage:'watch',stage_label:'观察',structure_label:'结构观察',data_status_label:'预热中',risk_status_label:'风险通过',conclusion:'等待确认',factors:{},timeline:[]}
  ]})};}
  if(url.endsWith('/summary'))return {ok:true,json:async()=>url.includes('0xtoken')?[row,{...row,pair_address:'0xmid',liquidity_usd:50000,volume_h1:5000,signal_score:45,dex_id:'uniswap'}]:[{...row,token_address:'0xother',pair_address:'0xlow',token_symbol:'OTHER',signal_score:20}]};
  if(url.includes('/snapshots?limit=36')) return {ok:true,json:async()=>[0,1,2].slice(0,url.includes('0xnew')?1:3).map(i=>({observed_at:new Date(at-i*60000).toISOString(),price_usd:row.price_usd*(1-i*.01),liquidity_usd:100000,volume_h1:(url.includes('0xmid')?5000:15000)-i*1000}))};
  if(url.endsWith('/prices')) return {ok:true,json:async()=>({symbol:'TESTUSDT',spot:[{at:at-100000,price:row.price_usd*.99},{at:at-40000,price:row.price_usd}],futures:[{at:at-100000,price:row.price_usd*1.01},{at:at-40000,price:row.price_usd*1.02}]})};
  return { ok: true, json: async () => url.startsWith('/api/overview') ? [row, {...row, pair_address:'0xmid', liquidity_usd:50000, volume_h1:5000, signal_score:45, dex_id:'uniswap'}, {...row, token_address:'0xother', pair_address:'0xlow', token_symbol:'OTHER', signal_score:20}, {...row, token_address:'0xzero', pair_address:'0xzero-pair', token_symbol:'ZERO', signal_score:0, signal_reasons:'[]'}] : url.endsWith('/oi') ? { available:true, symbol:'TESTUSDT', timestamp:Date.now(), oi_usd:12000, change_1h_pct:2.5, change_24h_pct:6, history:[{oi_usd:10000},{oi_usd:12000}], global:{oi_usd:30000,market_count:2}, long_short:{ratio:1.3,timestamp:Date.now()} } : url.endsWith('/depth') ? { available:true, calculated_at:depthAt, dex:url.includes('0xmid')?'uniswap':'pancakeswap', type:'v3', block:'0x10', bands_pct:[1,2,5], support_usd:url.includes('0xmid')?[50,100,250]:[100,200,500], pressure_usd:url.includes('0xmid')?[60,120,300]:[120,240,600] } : url.endsWith('/whales') ? {available:true,calculated_at:new Date().toISOString(),trades:url.includes('0xmid')?[{side:'sell',usd:18000,at:new Date().toISOString(),hash:'0xabc',wallet:'0xdef'},{side:'buy',usd:11000,at:new Date().toISOString(),hash:'0xghi',wallet:'0xjkl'}]:[{side:'sell',usd:12000,at:new Date().toISOString(),hash:'0xabc',wallet:'0xdef'}]} : url.endsWith('/transfers') ? {available:true,calculated_at:new Date().toISOString(),sample_count:100,transfers:[{amount:1500000,from:'0xaaaabbbb',to:'0xccccdddd',hash:'0xtransfer',at:new Date().toISOString()},{amount:2000000,from:'0xpair',to:'0xccccdddd',hash:'0xpool',at:new Date().toISOString()}]} : [] }; };
const context=vm.createContext({ document, fetch, localStorage, window:{addEventListener() {}}, setInterval() {}, Date, Map, console });
vm.runInContext(script, context);
assert.ok(document.getElementById('tokenList').innerHTML.includes('CACHED'), 'recent list cache should render before the network response');
assert.ok(document.getElementById('refreshInfo').textContent.includes('行情快照最早 2分钟前'), 'cached list must retain the sample age');
setImmediate(async () => {
  assert.ok(document.getElementById('tokenList').innerHTML.includes('token-v2'), 'V2 status badge should render after the network response');
  assert.ok(document.getElementById('tokenList').innerHTML.includes('首次启动'), 'V2 stage should use Chinese copy');
  assert.ok(document.getElementById('tokenList').innerHTML.includes('V2阶段：首次启动'), 'V2 tooltip should use Chinese copy');
  const v2Tip=vm.runInContext(`v2TipText({stage:'base',risk_status:'WARN',data_status:'warming_up',b_at:null,c_at:null,c_reason:'support_failed'})`,context);
  assert.ok(v2Tip.includes('V2阶段：企稳') && v2Tip.includes('数据状态：预热中') && v2Tip.includes('结果：跌破支撑'), 'V2 stages and data states should use Chinese copy');
  assert.ok(!v2Tip.includes('B时间') && !v2Tip.includes('C时间'), 'V2 tooltip should not expose B/C jargon');
  const detail = document.getElementById('detailPanel').innerHTML;
  assert.ok(detail.indexOf('合约持仓 · OI') < detail.indexOf('市场价格对比'), 'OI should appear above the trend');
  assert.ok(detail.indexOf('结构阶段') < detail.indexOf('合约持仓 · OI'), 'structure and OI should share the decision context row');
  assert.ok(detail.includes('class="context-grid"'), 'structure and OI should share the responsive grid');
  assert.ok(detail.indexOf('市场价格对比') < detail.indexOf('价格区间深度'), 'trend should be visible before pool depth');
  assert.ok(detail.includes('活跃度分'));
  assert.ok(detail.includes('class="triage-strip"'), 'score, market risk and snapshot should lead the detail');
  assert.ok(detail.indexOf('代币风险观察') < detail.indexOf('参考价格'));
  assert.ok(detail.includes('池内资产估值'), 'pool assets must not be mistaken for near-price depth');
  assert.ok(detail.includes('代币风险观察') && detail.includes('卖出笔数偏多') && detail.includes('信号 '));
  assert.ok(!detail.includes('合约安全风险'));
  for (const expected of ['0xtoken', '快照', '$0.00000001235', '5m +2.0%', '1h -3.0%', '28 / 14', '$20.0K', '未接入卖出限制', '价格区间深度']) {
    assert.ok(detail.includes(expected), `missing ${expected}`);
  }
  for (const removed of ['可控', '上涨概率', '概率预测', '下一步观察', 'pancakeswap', 'uniswap', '0xpair', '0xmid']) {
    assert.ok(!detail.includes(removed), `unexpected ${removed}`);
  }
  assert.ok(!detail.includes('0xlegacy'));
  const list = document.getElementById('tokenList').innerHTML;
  assert.equal((list.match(/class="token-btn/g) || []).length, 2);
  assert.ok(!list.includes('ZERO'));
  assert.ok(!list.includes('structure-line'), 'list rows should not duplicate structure details');
  assert.equal(document.getElementById('tokenCount').textContent, '入榜 2 个代币');
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
  vm.runInContext(`oiCache.delete('0xpair')`,context);
  await vm.runInContext(`loadOI('0xpair',-3,false)`,context);
  assert.equal(document.getElementById('oiSection').className,'detail-section');
  vm.runInContext(`oiCache.set('0xpair',{at:Date.now(),promise:Promise.resolve({available:true,symbol:'TESTUSDT',timestamp:null,oi_usd:12000,history:[]})})`,context);
  await vm.runInContext(`loadOI('0xpair',-3,false)`,context);
  assert.ok(document.getElementById('oiBody').innerHTML.includes('小时采样 时间未知'));
  vm.runInContext(`oiCache.delete('0xpair')`,context);
  const whales=document.getElementById('whaleBody').innerHTML;
  assert.ok(whales.includes('$18.0K') && whales.includes('$11.0K'));
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
  assert.ok(!transfers.includes('$'),'transfer threshold is token quantity, not USD');
  assert.ok(transfers.includes('更新于 ') && !transfers.includes('5 分钟更新'));
  const before=calls.length;
  await vm.runInContext('refresh()',context);
  assert.deepEqual(calls.slice(before).filter(url=>!['/api/tokens','/api/signal-v2?limit=50','/api/structure?limit=100'].includes(url)),[],'unchanged overview must not reload detail APIs');
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
  assert.ok(vm.runInContext('depthCache.get("0xpair").expires',context)>Date.now(),'fresh depth gets a five-minute expiry');
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
  assert.equal(document.getElementById('whaleBody').textContent,'交易数据源限流，稍后自动重试');
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

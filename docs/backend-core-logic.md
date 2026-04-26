# 后端核心逻辑文档

这份文档整理当前项目的后端核心逻辑，不包含 UI 细节。

目标：

- 明确系统当前到底在监控什么
- 明确数据如何进入系统、如何清洗、如何生成信号
- 明确存储结构和后台任务行为
- 为后续切数据库、扩指标、重构 worker 提供统一参考

关联文档：

- 信号指标基线文档：
  - [signal-indicator-baseline.md](/Users/zjj/vs_code/token-meme-monitor/docs/signal-indicator-baseline.md)

---

## 1. 系统定位

当前系统不是“全链新币监听器”，而是：

- 以 `Binance Alpha / BSC` 为主要监控宇宙
- 用链上与第三方行情数据做实时监控
- 通过规则引擎生成信号、状态和告警资格
- 将实时快照和信号持续落库

换句话说，当前逻辑更接近：

- `Alpha token universe monitor`
- 而不是广义的 `all new pairs monitor`

---

## 2. 当前后端模块

后端主模块如下：

- [config.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/config.py)
  - 环境变量加载与运行时配置
- [orchestrator.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/orchestrator.py)
  - 主 worker，负责把所有链路串起来
- [database.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/database.py)
  - SQLite 仓储层
- [clients/binance_alpha.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/clients/binance_alpha.py)
  - Binance Alpha token list 客户端
- [clients/dexscreener.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/clients/dexscreener.py)
  - DexScreener 客户端
- [clients/bsc.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/clients/bsc.py)
  - BSC 链上 PairCreated 扫描客户端
- [clients/honeypot.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/clients/honeypot.py)
  - 持币人数补充客户端
- [market_data.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/market_data.py)
  - 市场数据清洗与可信度校验
- [features.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/features.py)
  - 特征生成
- [signals.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/signals.py)
  - 规则打分、状态判断、告警资格判断
- [predictions.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/predictions.py)
  - p3 概率预测、机会分、历史 outcome 命中率校准
- [indicator_candidates.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/indicator_candidates.py)
  - 回测后新增候选指标的实时计算
- [token_validation.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/token_validation.py)
  - 历史验证脚本（近 90 天日线 + 主升浪附近小时线）

---

## 3. 监控宇宙

当前监控宇宙默认是：

- `MONITOR_UNIVERSE=binance_alpha`

其含义是：

- 先从 Binance Alpha 官方列表同步 BSC token
- 再给这些 token 解析交易池
- 再只跟踪这些池子的快照和信号

系统仍然保留了链上 `PairCreated` 扫描能力，但在 `binance_alpha` 模式下：

- 只有当新创建的 pair 属于 Alpha token 时，才会进入后续逻辑

这保证了系统不是盲扫全网，而是围绕“高质量候选池”工作。

---

## 4. 主数据流

当前后端主链路如下：

1. 刷新 Binance Alpha token list
2. 对新增、缺 pair 或 seed TTL 到期的 token，用 DexScreener 查最佳交易池
3. 将 token 与 pair seed 进本地数据库
4. worker 在 Alpha universe 内选出到期需要刷新的 pair
5. 拉取 DexScreener 最新 pair 快照
6. 校验 DexScreener 返回的 chain / pair / token / quote 是否匹配
7. 用 Binance Alpha 官方值作为参考，对快照做清洗
8. 生成特征与风险标记
9. 用规则引擎打分
10. 计算候选指标（相对放量、市值桶、短期收益率等）
11. 写入 `snapshots` 和 `signals`
12. 基于 signal + token metadata 生成 `p3` 预测概率，写入 `signal_predictions`
13. 更新 `pairs` 状态与下一次刷新时间
14. 如果达到告警资格，则尝试发送 Telegram
15. 低频刷新 holder / top10 holder share 元数据
16. 对已满 25h 且小时线窗口已闭合的预测补充 `signal_prediction_outcomes`，供后续 p3 历史命中率校准

---

## 5. 数据清洗逻辑

清洗层位于：

- [market_data.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/market_data.py)

核心目标：

- 不让明显脏值直接进入特征和评分

当前清洗策略包括：

- 拒收 `NaN / inf / -inf`
- 拒收明显超界值
- 显式的 `0` 是有效业务值，不应被 `or default` 误当成缺失值
- 在 `binance_alpha` 模式下：
  - `price_usd`
  - `market_cap`
  - `fdv`
  会使用 Binance Alpha 官方值作为清洗参考
- `price_usd` 优先使用 DexScreener 实时值：
  - DexScreener 价格缺失或非法时，才回退到 Alpha 官方价格
  - DexScreener 与 Alpha 价格差距过大时，会写入质量标记，但不直接用滞后的 Alpha 价格覆盖实时价格
- `market_cap` / `fdv` 仍优先用 Alpha 官方值兜住估值口径
- 把清洗来源和异常标记写进 `_data_quality`

当前的意义不是追求“绝对真实”，而是优先保证：

- 数据可用
- 数据不离谱
- 在监控场景下更可信

---

## 6. 特征生成逻辑

特征层位于：

- [features.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/features.py)

当前生成的核心特征：

- 价格：`price_usd`
- 估值：`market_cap`、`fdv`
- 流动性：`liquidity_usd`
- 成交量：`volume_m5`、`volume_h1`、`volume_h24`
- 买卖结构：`buys_m5`、`sells_m5`、`buys_h1`、`sells_h1`
- 派生结构：
  - `buy_sell_ratio_m5`
  - `buy_sell_ratio_h1`
  - `liquidity_to_fdv`
  - `volume_to_liquidity_h1`
- 社交/项目面信息：
  - `website_count`
  - `social_count`
  - `boosts_active`
- 趋势：
  - `price_change_m5`
  - `price_change_h1`
  - `price_change_h24`

同时会生成风险标记，例如：

- `missing_price`
- `low_liquidity`
- `liquidity_near_zero`
- `sell_pressure`
- `fdv_missing`
- `fdv_liquidity_stretched`

---

## 7. 信号与告警逻辑

信号层位于：

- [signals.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/signals.py)

当前逻辑本质上是：

- 固定规则打分
- 再根据阈值映射状态

状态有：

- `watching`
- `focused`
- `alerted`
- `archived`

告警资格不是单独的复杂流程，而是：

- `score >= ALERT_SCORE_THRESHOLD`
- 且不能命中严重风险

详细加减分与阈值，请以：

- [signal-indicator-baseline.md](/Users/zjj/vs_code/token-meme-monitor/docs/signal-indicator-baseline.md)

为准。

---

## 8. 候选指标逻辑

回测之后，系统已经实现了“候选指标”的实时计算，但它们当前：

- **已计算**
- **已写进 signal.feature_json**
- **尚未参与最终评分**

实现位置：

- [indicator_candidates.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/indicator_candidates.py)

当前已实现的候选指标：

- `candidate_indicator_version`
- `market_cap_bucket`
- `volume_impulse_vs_prev24h`
- `volume_impulse_vs_prev72h`
- `h1_return_live`
- `h4_return_live`
- `h24_return_live`

这些指标的作用是：

- 先在实时链路里沉淀出来
- 让后续观察它们的分布和稳定性
- 再决定是否真正并入评分

当前实时计算细节：

- `volume_impulse_vs_prev24h / prev72h` 使用小时聚合后的历史成交额中位数做基线
- `h1_return_live / h4_return_live / h24_return_live` 使用原始 snapshot 历史序列寻找 cutoff 前最近点
- 候选指标仍只写入 `signal.feature_json`，不直接影响评分和告警

这一步的核心原则是：

- 先记录
- 再观察
- 最后才纳入告警

---

## 8.5 预测概率与历史校准

预测层位于：

- [predictions.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/predictions.py)

当前版本：

- `PREDICTOR_VERSION=p3`

预测层不是本地大模型，也不依赖 GPU。它当前做三件事：

- 用 `SignalDecision.features` 和 token metadata 计算规则概率：
  - `prob_2h_up20`
  - `prob_6h_up50`
  - `prob_24h_up100`
  - `risk_6h_dd30`
- 把概率折算为 `opportunity_score`
- 当本地已有足够成熟 outcome 样本时，用相似历史分桶命中率做保守校准

p3 校准分桶会参考：

- signal 分数桶
- 阶段：`early / acceleration / late / exhaustion`
- `h1_return_live`
- `h24_return_live`
- `volume_to_liquidity_h1`
- Alpha 分数、holder 深度、Binance futures 标签等质量因子

校准原则：

- 样本不足时，完全退回规则概率
- 样本达到最低阈值后，只做保守 blend，不直接用历史命中率硬覆盖
- 相似样本越少，校准权重越低
- 校准原因会写入 `prediction_reasons`：
  - `prediction_empirical_calibrated`
  - `prediction_empirical_lowered`
  - `prediction_empirical_raised`
  - `prediction_empirical_sparse`

重要现实状态：

- 截至 2026-04-26 当天，`signal_predictions` 已升级到 `p3`
- 但本地最早 prediction 还未满 25h，`signal_prediction_outcomes` 暂无可用样本
- 因此当前 p3 已接好校准链路，但短期内仍主要按规则概率运行
- 等 worker 运行超过 25h、且外部小时线窗口闭合后，成熟预测会自动进入 outcome 表，后续 p3 校准才会真正生效

维护命令：

```bash
./.venv/bin/python -m token_meme_monitor refresh-prediction-outcomes --limit 10000
./.venv/bin/python -m token_meme_monitor rebuild-predictions
./.venv/bin/python -m token_meme_monitor export-prediction-dataset
```

---

## 9. 持久化结构

当前数据库是 SQLite，核心表有：

- `tokens`
  - token 基础信息和元数据
- `pairs`
  - 监控中的交易池
- `snapshots`
  - 每次行情快照
- `signals`
  - 每次评分结果
- `signal_predictions`
  - 每次 signal 对应的 p3 概率、机会分、阶段和预测解释
- `signal_prediction_outcomes`
  - p3 预测的成熟后结果标签
  - 当前包含 2h / 6h / 24h 最大收益、6h 最大回撤、命中标记和样本数
  - 用于后续概率校准和训练集导出
- `alerts`
  - 告警发送记录
- `outcomes`
  - legacy 兼容表，当前运行链路不再写入；2h / 24h 回看改由 dashboard 外部趋势数据提供
- `external_trend_metrics`
  - GeckoTerminal 外部 2h / 24h 区间涨幅缓存
  - 主键为 `pair_address + observed_at_hour + source`
  - dashboard 先读本地缓存，缺失时才请求外部接口并写回
  - 只持久化已经结束的小时，当前未收线小时只走短 TTL 页面缓存
- `external_ohlcv`
  - GeckoTerminal 历史 OHLCV 行级缓存
  - 主键为 `network + pool_address + timeframe + aggregate + source + ts`
  - 当前用于 `validate-token-list`，避免历史日线/小时线重复外部请求
- `external_ohlcv_fetches`
  - 已完成的历史 OHLCV 查询窗口记录
  - 用于区分“样本确实不足”和“本地缓存还没覆盖完整窗口”
- `external_json_cache`
  - 辅助外部 JSON 数据缓存
  - 当前用于 Binance futures registry，避免 418 / 限流时每分钟重复请求
- `scan_cursors`
  - 链上扫描游标

当前设计重点是：

- 可持续增量采集
- 保留历史快照
- 保留信号演化过程
- 允许后续回测和迁库

---

## 10. Worker 行为

当前 worker 行为在：

- [orchestrator.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/orchestrator.py)

主要特点：

- 周期性运行
- 每轮会：
  - 按 `BINANCE_ALPHA_REFRESH_MINUTES` 刷新 Alpha token cache
  - 按 `BINANCE_ALPHA_PAIR_SEED_REFRESH_MINUTES` 对新增、缺 pair 或过期 token 做 pair seed
  - 在 Alpha universe 内刷新到期 pair
  - 到期 pair 的 DexScreener 快照刷新优先于链上 discovery 执行，避免 RPC 扫描慢或 429 时拖住量价更新
  - 按 `HOLDER_METRICS_JOB_INTERVAL_SECONDS` 低频刷新 holder / top10 holder share
- 失败会尽量局部降级，不直接整轮崩掉

当前 worker 的重要边界：

- `pair` 快照刷新不再同步请求 Honeypot，holder 类指标由 side job 更新
- DexScreener snapshot 会先校验 chain / pair / token / quote，明显不匹配时不会写入快照
- DexScreener snapshot 只接受目标 token 位于 `baseToken`、报价资产位于 `quoteToken` 的返回；如果目标 token 出现在 quote 侧，会拒绝写入，避免把报价资产价格误记到目标 token 下
- Alpha token pair seed 会遵守 `pair_seed_failed_at` 的 TTL：
  - 没有找到 pair 的 token 不会每轮都占用 seed batch
  - TTL 到期后才重新进入 DexScreener seed 尝试
- BSC discovery 支持 `BSC_RPC_URLS` 多 endpoint：
  - endpoint 返回 `429` 时会冷却至少 5 分钟，或遵守 `Retry-After`
  - endpoint 返回 `418` 时会冷却至少 1 小时
  - 冷却中的 endpoint 不再用于当前 discovery，worker 会切到下一个可用 RPC
  - 如果所有 endpoint 都在冷却，discovery 暂停到最近一个冷却结束时间
- Binance futures registry 不再跟随 Alpha token list 每分钟硬刷：
  - 默认每 6 小时刷新一次
  - 成功结果写入 `external_json_cache`
  - 失败时优先使用本地缓存，避免 418 反复刷日志和打接口
- 本地 outcome 回填已经停用：
  - 指标轮动快，本地固定 2h / 24h 标签容易过期和误导
  - dashboard 历史记录的 2h / 24h 回看使用 GeckoTerminal 外部 OHLCV 趋势数据，并按小时持久化缓存到 `external_trend_metrics`
  - `outcomes` 表仅为旧库兼容保留，不再由 worker 写入
- `signal_prediction_outcomes` 仍然会由 worker 写入：
  - 它服务于 p3 概率校准，不服务于 dashboard 历史记录展示
  - 当前优先使用 GeckoTerminal `hour` OHLCV 计算 2h / 6h / 24h 未来结果，使用信号触发时的 `price_usd` 作为基准价，并用小时 K 的 `high / low` 捕捉窗口内最大涨幅和最大回撤
  - 历史小时线写入 `external_ohlcv` + `external_ohlcv_fetches`，同一个历史窗口不会重复请求外部接口
  - prediction 至少等待 25h，确保 24h 结果窗口对应的小时线已结束；窗口未闭合时本轮跳过，后续 worker 自动重试
  - p3 校准只使用样本覆盖足够的 horizon：2h 至少 2 根小时线、6h 至少 5 根、24h 至少 18 根
- `archived` 或命中严重风险的 signal 不会触发告警

当前 worker 的现实约束：

- 公开 RPC 仍然会遇到 `eth_getLogs` 限制
- 链上 discovery 失败会短暂 backoff；这不影响已入库 pair 的 DexScreener 量价刷新
- DexScreener 与 GeckoTerminal 也有速率限制
- 所以系统虽然是持续运行的，但仍然属于“低成本、研究型监控架构”

---

## 11. 历史验证脚本

当前保留的历史验证脚本：

- CLI 命令：
  - `python3 -m token_meme_monitor validate-token-list`
- 代码位置：
  - [token_validation.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/token_validation.py)
- 默认输入文件：
  - `token_meme_monitor/token_list.txt`
- 默认输出文件：
  - `data/backtests/token_list_validation.json`
  - `data/backtests/token_list_validation.md`

这个脚本当前的设计不是“完美回放所有线上指标”，而是：

- 用官方可拿到的历史 OHLCV
- 近似验证当前策略是否能在上涨前给出有效信号
- 顺便验证不同市值层级下，绝对阈值是否存在偏差
- 历史 OHLCV 会优先读取 `external_ohlcv` 本地缓存；只有本地窗口不完整时才请求 GeckoTerminal，并把已收线的历史 K 线写回 SQLite

这也是候选指标的主要来源。

2026-04-26 回测列表结果：

- 输入：`token_meme_monitor/token_list.txt`
- 样本数：`7`
- 结果分布：`大致对上 1 个`，`部分对上 6 个`
- 未来 24h 最大涨幅：
  - 平均约 `10.1%`
  - 中位数约 `9.2%`
  - `>=20%` 为 `1/7`
  - `>=50%` 为 `0/7`
  - `>=100%` 为 `0/7`

这说明当前规则更适合做候选排序和过滤，暂时不能被当成高胜率买入信号；强爆发预测必须依赖后续真实 `signal_prediction_outcomes` 继续校准。

---

## 12. 最近 review 修复归档

本节记录最近 review 和实盘排查后已经落地的后端修复。后续排查类似问题时，优先按这里的边界判断。

### 12.1 `validate-token-list` 稀疏小时线保护

位置：

- [token_validation.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/token_validation.py)

修复内容：

- `current_liquidity_usd` 和 `current_holders` 在读取 pool 后立即初始化
- 当日线足够但主升浪附近小时线少于 30 根时，返回“数据不足”
- 不再在该分支触发 `UnboundLocalError`

同一轮还调整了 GeckoTerminal 请求节奏：

- 第一次 HTTP 请求不再先 `sleep`
- 只有重试时才按 backoff 等待
- 避免每个 token 固定增加多秒延迟

### 12.2 Alpha pair seed TTL

位置：

- [database.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/database.py)

修复内容：

- 对没有 pair 的 Alpha token，`pair_seed_failed_at` 仍在 TTL 内时不再标记为 due
- 只有从未尝试过 seed，或上次失败已经超过 `BINANCE_ALPHA_PAIR_SEED_REFRESH_MINUTES`，才重新进入 seed batch
- 解决按 `token_address` 排序时，前排长期缺 pair token 饿死后续 token 的问题

### 12.3 本地 Outcome 回填停用

本地 `outcomes` 回填已经从 worker、CLI、仓储方法和 dashboard 活跃链路中移除。

原因：

- 当前指标轮动速度快，固定 2h / 24h 本地标签经常落后于新指标
- signal 的历史复盘更适合使用同详情页一致的外部量价快照
- 旧 `outcomes` 表保留用于兼容已有 SQLite 库，但不再写入新数据

### 12.4 环境变量显式 0

位置：

- [config.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/config.py)

修复内容：

- 移除 `_env_int(... ) or default` 这类写法
- `ALERT_COOLDOWN_MINUTES=0`
- `FOCUS_SCORE_THRESHOLD=0`
- `DASHBOARD_AUTO_REFRESH_SECONDS=0`
- `MAX_PAIRS_PER_CYCLE=0`

这些显式 `0` 值会被保留，不再静默回退默认值。

### 12.5 Dashboard overview 后端字段补充

位置：

- [database.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/database.py)

为减少前端详情页额外查询与重复拼装，`list_pair_overview()` 现在会带出最新信号上下文：

- `last_signal_at`
- `last_pair_state`
- `last_should_alert`
- `last_risk_flags`
- `last_feature_json`

这些字段用于 dashboard 构造最新 `SignalContext`，前端只有在进入“走势”或“历史记录”视图时才额外读取 snapshots / signals。

`list_pair_overview()` 的 SQL 排序会优先保留 Alpha、active、最新快照行，再看信号分，避免历史高分旧数据在 `LIMIT` 前挤掉当前活跃候选。

### 12.6 回归测试

这轮后端修复新增或更新了相关测试：

- [tests/test_token_validation.py](/Users/zjj/vs_code/token-meme-monitor/tests/test_token_validation.py)
- [tests/test_database.py](/Users/zjj/vs_code/token-meme-monitor/tests/test_database.py)
- [tests/test_config.py](/Users/zjj/vs_code/token-meme-monitor/tests/test_config.py)
- [tests/test_utils.py](/Users/zjj/vs_code/token-meme-monitor/tests/test_utils.py)

验证命令：

```bash
./.venv/bin/python -m unittest discover -s tests
```

### 12.7 2026-04-26 p3 预测与维护链路

位置：

- [predictions.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/predictions.py)
- [orchestrator.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/orchestrator.py)
- [cli.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/cli.py)

已落地：

- `PREDICTOR_VERSION` 从 `p2` 升级到 `p3`
- 新增历史分桶校准层 `build_prediction_calibration()`
- worker 在写入 signal prediction 时会读取本地成熟 outcome 构造校准器
- worker 每轮会尝试刷新成熟 prediction outcome
- outcome 刷新后会清空 worker 内存中的 calibration cache
- prediction outcome 现在优先从外部小时线缓存计算，不再依赖本地 snapshot 的稀疏采样
- 新增 CLI：
  - `refresh-prediction-outcomes`
  - `rebuild-predictions`
- `cleanup-data` 在 metadata-only 变化时也会重算 signal / prediction / pair state

当前约束：

- 本地 `signal_prediction_outcomes` 在 2026-04-26 当天还没有成熟样本
- 因此 p3 校准层当前处于“已接入、待样本生效”状态
- 第一批线上 prediction 需要等至少 25h 后才会尝试补 outcome；如果外部小时线暂时不可用，本轮会跳过，避免把空样本固化成校准数据

### 12.8 2026-04-26 代表池与历史数据缓存

已落地：

- 同一 token 多个交易池时，dashboard 主列表优先保留仍处于 15 分钟活跃窗口内的交易池
- 活跃池优先后，才比较预测机会分、信号分、流动性、最新快照时间
- 历史 OHLCV 使用 `external_ohlcv` + `external_ohlcv_fetches` 存档，避免历史数据每次现用现查
- GeckoTerminal 详情页 2h / 24h 区间涨幅使用 `external_trend_metrics` 存档
- `validate-token-list` 会优先使用本地历史 OHLCV 缓存，只有窗口不完整时才请求外部接口

---

## 13. 当前后端不做的事情

当前后端逻辑还没有正式纳入：

- 税和 honeypot 风险评分
- owner 权限评分
- LP lock / burn 评分
- 持仓集中度实时评分
- 历史持币人数序列回放
- 本地大模型 / GPU 模型打分
- 多链统一策略层

这些以后都可以做，但不属于当前基线。

---

## 14. 后续变更规则

后续如果继续扩展后端核心逻辑：

1. 先更新：
   - [signal-indicator-baseline.md](/Users/zjj/vs_code/token-meme-monitor/docs/signal-indicator-baseline.md)
   - 本文档
2. 再改代码
3. 再补测试
4. 如果是候选指标，先进入“记录但不参与评分”阶段
5. 只有回测和实时观察都通过后，再纳入正式评分

---

## 15. 后续待做与观察点

当前最重要的后续事项：

1. 等 `signal_predictions` 跑满 25h 后，检查 `signal_prediction_outcomes` 是否开始增长。
2. 当 outcome 样本足够后，重新执行：
   - `refresh-prediction-outcomes`
   - `rebuild-predictions`
   - `export-prediction-dataset`
3. 用真实线上 signal outcome 验证 p3，而不是只依赖 token list 事后锚点回测。
4. 重点观察 p3 的概率校准方向：
   - 是否继续过高
   - 是否过度保守
   - `prediction_empirical_lowered / raised` 出现在哪些 token 类型上
5. 如果样本量足够，再考虑引入轻量模型：
   - Logistic Regression
   - LightGBM / XGBoost
   - Isotonic calibration
   但不需要深度学习或 GPU。
6. 继续扩大 token list 回测样本，特别是加入真正 50% / 100% 级别涨幅样本，否则无法验证强爆发预测。

### 15.1 暂缓的数据库与存储优化计划

当前优先级：

- 先把数据准确性、外部小时线缓存、真实 outcome 回填、回测样本和 p3 概率校准做到稳定。
- 在预测逻辑没有稳定前，暂不切换数据库、不做大规模存储重构，避免同时引入数据迁移风险和策略误差。

截至 2026-04-26 本地存储现状：

- `data/monitor.db` 约 `1.1GB`
- `snapshots` 约 `325k` 行，占用约 `591MB`
- `signals` 约 `325k` 行，占用约 `416MB`
- 数据主要增长来自实时快照 `raw_json` 和信号 `feature_json`
- 当前 SQLite 仍可支撑单 worker + 单 dashboard 的开发验证阶段

后续如果要切数据库，优先考虑 Postgres：

- 最低可用配置：
  - `2 vCPU`
  - `4GB RAM`
  - `80GB SSD`
- 更稳配置：
  - `4 vCPU`
  - `8GB RAM`
  - `200GB SSD`
  - `snapshots / signals` 按天或按月分区

后续存储治理方向：

- 最近 `7-14` 天保留完整 `snapshots / signals`
- 更久历史转成小时聚合或关键字段归档
- `external_ohlcv` 继续作为历史小时线缓存，优先复用本地数据
- `signal_prediction_outcomes` 长期保留，用于概率校准和回测
- 在 Postgres 迁移前，先明确保留周期、归档粒度、查询路径和回测数据需求

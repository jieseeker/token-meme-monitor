# 后端核心逻辑基线

这份文档是当前后端和信号策略的统一基线。旧的信号指标文档已经合并到这里，后续涉及采集、清洗、特征、评分、预测、存储、worker 行为的变更，应同步更新本文档。

## 1. 系统定位

当前系统是 `Binance Alpha / BSC` 监控器，不是全链新币监听器。

核心目标：

- 从 Binance Alpha token universe 中筛选 BSC 代币
- 用 DexScreener、Binance Alpha、BSC RPC、GeckoTerminal、Honeypot 等数据源持续刷新本地 SQLite
- 生成实时 signal、prediction 和成熟后的 prediction outcome
- 给 dashboard、回测、定时报表和维护命令提供同一份本地数据

## 2. 主要模块

- `token_meme_monitor/config.py`：环境变量和运行时配置
- `token_meme_monitor/orchestrator.py`：主 worker，串联采集、清洗、信号、预测、outcome、holder side job
- `token_meme_monitor/database.py`：SQLite schema 和仓储方法
- `token_meme_monitor/market_data.py`：市场数据清洗和 `_data_quality` 标记
- `token_meme_monitor/features.py`：从 snapshot 生成规则特征和风险标记
- `token_meme_monitor/signals.py`：规则打分、状态和告警资格
- `token_meme_monitor/predictions.py`：p4 概率预测、horizon 分数和经验校准
- `token_meme_monitor/prediction_outcomes.py`：成熟 prediction outcome 计算
- `token_meme_monitor/prediction_backtest.py`：事件级 walk-forward 回测
- `token_meme_monitor/strategy_feedback.py`：prediction/outcome 策略反馈切片和复核建议
- `token_meme_monitor/risk_enrichment.py`：观察模式的 token 风险元数据归一化和刷新
- `token_meme_monitor/data_lifecycle.py`：数据库生命周期 inventory、integrity 和 retention dry-run
- `token_meme_monitor/scheduled_backtest.py`：定时巡检报告
- `token_meme_monitor/health.py`：健康报告
- `token_meme_monitor/runtime_status.py`：本地 worker、定时 worker 和 dashboard 的运行状态
- `token_meme_monitor/indicator_candidates.py`：候选指标实时计算
- `token_meme_monitor/token_validation.py`：token list 历史验证

## 3. 主数据流

1. 刷新 Binance Alpha token list。
2. 对新增、缺 pair 或 seed TTL 到期的 token，用 DexScreener 查最佳交易池。
3. 将 token 与 pair seed 进 SQLite。
4. worker 只在 Alpha universe 内选择到期 pair。
5. 拉取 DexScreener pair snapshot，并校验 chain、pair、token、quote。
6. 结合 Binance Alpha metadata 清洗市场数据。
7. 生成 feature vector 和 risk flags。
8. 用规则引擎生成 signal。
9. 计算候选指标并写入 `signals.feature_json`。
10. 基于 signal、feature 和 token metadata 写入 p4 prediction。
11. 更新 pair 状态、下次刷新时间、失败退避 metadata。
12. 若 signal 可告警，按 `alerts` 去重冷却发送 Telegram。
13. holder/top10 holder share 由低频 side job 刷新，不阻塞主行情刷新。
14. 可选 risk enrichment side job 写入观察模式风险快照；默认关闭，不影响评分和告警。
15. 成熟 prediction 由 GeckoTerminal hourly OHLCV 补 outcome，供 p4 校准和回测。

## 4. 数据清洗

清洗入口是 `sanitize_pair_snapshot()`。

当前规则：

- 拒收 `NaN`、`inf`、`-inf` 和明显超界值。
- `price_usd` 优先保留 DexScreener 实时价格；当 Dex 价格缺失或非法时，才回退到 Alpha reference。
- Dex 价格和 Alpha reference 偏差超过 50 倍时，记录 `price_usd_reference_divergent`，但仍保留 Dex 实时价格。
- `market_cap` 和 `fdv` 在 `binance_alpha` 模式下优先使用 Alpha reference；Dex 值严重偏离 reference 时也会被 reference 替换。
- `liquidity_usd` 和 `volume_h24` 优先使用 DexScreener 值；缺失或严重异常时才 fallback 到 Alpha reference。
- 字段来源和异常写入 snapshot raw payload 的 `_data_quality`。

当前硬边界：

- `price_usd <= 10_000_000`
- `price_native <= 10_000_000`
- 美元名义值 `<= 10_000_000_000_000`
- `price_change_*` 绝对值 `<= 1_000_000`

## 5. 信号特征

当前 feature vector 包含：

- 基础：`age_minutes`、`price_usd`、`market_cap`、`fdv`、`liquidity_usd`
- 成交：`volume_m5`、`volume_h1`、`volume_h24`
- 买卖：`buys_m5`、`sells_m5`、`buys_h1`、`sells_h1`、`tx_count_m5`、`tx_count_h1`
- 派生：`buy_sell_ratio_m5`、`buy_sell_ratio_h1`、`liquidity_to_fdv`、`volume_to_liquidity_h1`
- 项目面：`website_count`、`social_count`、`boosts_active`
- 趋势：`price_change_m5`、`price_change_h1`、`price_change_h24`

风险标记：

- `missing_price`：`price_usd <= 0`
- `low_liquidity`：`liquidity_usd < MIN_LIQUIDITY_USD`
- `liquidity_near_zero`：`liquidity_usd < ARCHIVE_LIQUIDITY_USD`
- `thin_m5_activity`：`tx_count_m5 < MIN_BUY_COUNT_M5` 且 `volume_m5 <= 0`
- `sell_pressure`：`sells_m5 > buys_m5 * 1.5` 且 `sells_m5 >= 5`
- `missing_project_metadata`：`website_count + social_count == 0`
- `fdv_missing`：`fdv <= 0`
- `fdv_liquidity_stretched`：`fdv / liquidity_usd > 25`
- `stale_pair`：只在非 Alpha 模式下使用，pair age 超过 `MAX_PAIR_AGE_HOURS`

## 6. 评分和状态

默认阈值：

- `FOCUS_SCORE_THRESHOLD=65`
- `ALERT_SCORE_THRESHOLD=78`

正向加分：

- `+15`：`MIN_LIQUIDITY_USD <= liquidity_usd <= 250_000`
- `+8`：`liquidity_usd > 250_000`
- `+15`：`volume_h1 >= MIN_VOLUME_H1_USD`
- `+6`：当 1h 成交额未达标且 `volume_m5 >= MIN_VOLUME_H1_USD * 0.12`
- `+20`：`buys_m5 >= MIN_BUY_COUNT_M5` 且 `buy_sell_ratio_m5 >= MIN_BUY_SELL_RATIO_M5`
- `+12`：当 5m 买盘主导未达标且 `buy_sell_ratio_h1 >= 1.3` 且 `buys_h1 >= MIN_BUY_COUNT_M5 * 2`
- `+15`：`volume_to_liquidity_h1 >= FOCUS_VOLUME_TO_LIQUIDITY_RATIO`
- `+8`：`volume_to_liquidity_h1 >= 0.12`
- `+10`：`0.04 <= liquidity_to_fdv <= 0.40`
- `+5`：`website_count + social_count >= 2`
- `+3`：`website_count + social_count == 1`
- `+5`：`boosts_active > 0`
- `+5`：`price_change_h1 > 20` 且 `price_change_m5 > 0`

Alpha 模式额外加分：

- `+10`：`alpha_score >= 100`
- `+5`：`80 <= alpha_score < 100`
- `+5`：`holder_count >= 10_000`
- `+8`：`binance_futures_listed`
- `+5`：`volume_to_liquidity_h1 >= 3`

非 Alpha 模式年龄加分：

- `+10`：age `<= 120` 分钟
- `+5`：age `<= 360` 分钟

负向扣分：

- `-20`：流动性低于健康区间
- `-5`：`price_change_h1 < -20`
- `-8`：命中 `sell_pressure`
- `-8`：命中 `fdv_liquidity_stretched`
- `-20`：命中 `missing_price`

状态规则：

- 严重风险直接 `archived`：`missing_price`、`liquidity_near_zero`，以及非 Alpha 模式下的 `stale_pair`
- `fdv_liquidity_stretched` 是结构性告警阻断项：可以进入 `focused`，但不能进入 `alerted`
- 无严重风险且无结构性阻断，分数达到 `ALERT_SCORE_THRESHOLD` 时进入 `alerted`
- 分数达到 `FOCUS_SCORE_THRESHOLD` 时进入 `focused`
- 其他为 `watching`
- `should_alert` 只允许 `pair_state == "alerted"` 且无严重风险

## 7. 候选指标

候选指标已经实时计算并写入 `signals.feature_json`，但不直接影响规则评分：

- `candidate_indicator_version`
- `market_cap_bucket`
- `volume_impulse_vs_prev24h`
- `volume_impulse_vs_prev72h`
- `h1_return_live`
- `h4_return_live`
- `h24_return_live`

当前原则是先记录、观察、回测，再决定是否纳入正式评分和告警。

## 8. p4 预测和 outcome

当前 `PREDICTOR_VERSION=p4`。

p4 输出：

- `prob_2h_up20`
- `prob_6h_up50`
- `prob_24h_up100`
- `risk_6h_dd30`
- `short_momentum_score`
- `continuation_score`
- `breakout_score`
- `opportunity_score`，兼容字段，等于 `short_momentum_score`
- `stage`：`early`、`acceleration`、`late`、`exhaustion`
- `prediction_reasons`

校准规则：

- 样本不足时退回规则概率。
- 按事件去重：同一 pair、同一状态桶在 2 小时内只计一次。
- 小样本只允许下调上涨概率；达到更高样本门槛后才允许上调。
- 排除 `local_snapshots` outcome、价格源偏差超过 10% 的 outcome，以及覆盖不足的 horizon。
- 2h 至少 2 根小时线，6h 至少 5 根，24h 至少 18 根。

Prediction outcome：

- 成熟时间至少为 signal observed_at 后 25 小时。
- 优先使用 GeckoTerminal hourly OHLCV。
- 使用 signal 触发时的 `price_usd` 作为基准价，同时记录 GeckoTerminal 基准 close 和价格偏差。
- 写入 `signal_prediction_outcomes`，用于 p4 校准、dataset export、walk-forward backtest 和定时报表。

策略反馈：

- `strategy-feedback-report` 从 prediction dataset 生成版本化反馈运行，并写入 `strategy_feedback_runs`、`strategy_feedback_slices`、`strategy_feedback_recommendations`。
- 反馈按稳定切片计算：`stage`、`score_band`、`market_cap_bucket`、`liquidity_bucket`。
- 指标包括样本数、2h/6h/24h 命中率、平均预测概率、校准误差、缺 outcome 比例，以及相对基线的 `lift_2h`。
- 建议是 review-only：`review_for_more_weight` 和 `investigate_or_downweight` 只输出证据，不自动修改 p4、规则评分或告警阈值。
- 定时回测报告会附带 compact strategy feedback summary，dashboard 可通过读模型展示最新反馈摘要。

## 9. 风险观察层

`risk_enrichment.py` 当前只做 observation-only 元数据层。

当前行为：

- `refresh-risk-enrichment --fixture-json <path>` 可从本地 fixture provider 写入风险快照。
- worker 只有在 `RISK_ENRICHMENT_FIXTURE_PATH` 配置后才会运行 risk refresh side job。
- 快照写入 `risk_snapshots`，包含 provider、token、fetch/expiry、status、risk_level、confidence、normalized fields、raw payload 和 failure reason。
- provider 失败、无覆盖和正常数据都会写入快照；无数据不会被当成低风险。
- `health-report` 输出风险快照总数、失败数、高风险数和 provider 分布。
- dashboard 读模型区分 `unknown`、provider `failure`、`low/medium/high`，并标记 `observation_only`。

当前硬边界：

- risk metadata 不参与 signal score。
- risk metadata 不参与 p4 prediction。
- risk metadata 不阻断 alert。
- 真实第三方 provider、税/owner/LP lock 的正式评分，需要后续 promoted change。

## 10. 数据库和归档

核心表：

- `tokens`
- `pairs`
- `snapshots`
- `snapshot_hourly_rollups`
- `snapshot_raw_archives`
- `signals`
- `signal_feature_archives`
- `signal_predictions`
- `signal_prediction_outcomes`
- `risk_snapshots`
- `strategy_feedback_runs`
- `strategy_feedback_slices`
- `strategy_feedback_recommendations`
- `decision_notes`
- `alerts`
- `external_trend_metrics`
- `external_ohlcv`
- `external_ohlcv_fetches`
- `external_json_cache`
- `scan_cursors`

Legacy 表：

- `outcomes` 保留兼容旧库；当前 dashboard 和 worker 活跃链路不再写入它。

历史压缩：

- `compact-history` 默认 dry-run，不带 `--execute` 不修改数据库。
- 旧 `snapshots` 先写入 hourly rollup，再把 `raw_json` 压缩到 `snapshot_raw_archives`，主表改为 `{}`。
- 旧 `signals.feature_json` 压缩到 `signal_feature_archives`，主表保留适合历史展示的紧凑字段和 `_history_compacted:true`。
- `list_prediction_dataset_rows()` 只在当前 `signals.feature_json` 仍是 compact 占位符时，才从 archive 还原完整 feature。若后续维护流程已经修复重写该行，则保留新 `feature_json`，不再用旧归档覆盖。

生命周期维护：

- `lifecycle-inventory` 只读输出核心表行数、时间范围、数据库大小和 retention candidate 计数。
- `lifecycle-integrity` 只读检查 compact archive 与 repaired full feature 的优先级、orphan prediction/outcome、stale cache 等问题。
- `retention-plan` 默认只生成 dry-run JSON，不修改数据库。
- `retention-plan --apply` 目前会要求 `--backup-path` 并拒绝执行 destructive cleanup；真正删除或压缩仍通过明确的维护命令实现。
- `health-report` 会附带 lifecycle summary 和 severity。

## 11. Worker 行为

主 worker：

- 按 `BINANCE_ALPHA_REFRESH_MINUTES` 刷新 Alpha token cache。
- 按 `BINANCE_ALPHA_PAIR_SEED_REFRESH_MINUTES` 做 pair seed TTL。
- 在 Alpha universe 内刷新 due pair。
- DexScreener snapshot 刷新优先于链上 discovery，避免 RPC 问题拖住行情刷新。
- DexScreener 未索引 pair 使用退避重试，最长约 1 小时。
- BSC discovery 支持 `BSC_RPC_URLS` 多 endpoint；`429` 和 `418` 会触发 endpoint cooldown。
- Binance futures registry 默认每 6 小时刷新，失败时使用 `external_json_cache`。
- holder/top10 metrics 由低频 side job 更新，优先 Binance Alpha rank，缺失时 fallback Honeypot。
- risk enrichment side job 默认关闭；配置 `RISK_ENRICHMENT_FIXTURE_PATH` 后按 TTL 写入观察模式风险快照。
- 每轮会尝试刷新成熟 prediction outcome，并在 outcome 更新后清空内存 calibration cache。

定时回测 worker：

- `run-scheduled-backtest-worker` 是项目内长驻 worker，默认每 4 小时生成一次报告。
- 它会先刷新成熟 prediction outcome，再写入 latest report 和时间戳归档。
- 每次定时报表运行都会把最近一次成功或失败状态写入 `external_json_cache` 的 `runtime:scheduled_backtest:last_run`，供 `health-report` 读取。
- 不依赖 macOS `launchd` 或 cron。

运行状态：

- `runtime-status` 输出三类本地服务的结构化状态：主 worker、定时回测 worker、dashboard。
- 状态字段包含 PID、预期命令、实际命令、日志路径、日志大小、dashboard URL 和诊断项。
- `restart.sh status` 复用 `runtime-status`；如果 Python runtime 不可用，则退回 shell PID 检查。
- `restart.sh rotate-logs` 按 `LOG_MAX_BYTES` 轮转 `/tmp/token-meme-monitor/logs` 下的运行日志。
- `health-report` 保留原始计数，并新增 `severity`：对数据库大小、stale active pairs、成熟但缺 outcome 的 prediction、定时回测状态给出 `ok`、`warn` 或 `critical`。

## 12. 常用命令

初始化和检查：

```bash
./.venv/bin/python -m token_meme_monitor init-db
./.venv/bin/python -m token_meme_monitor print-config
./.venv/bin/python -m token_meme_monitor healthcheck
./.venv/bin/python -m token_meme_monitor health-report
./.venv/bin/python -m token_meme_monitor health-report --json
./.venv/bin/python -m token_meme_monitor runtime-status
./.venv/bin/python -m token_meme_monitor runtime-status --json
./.venv/bin/python -m token_meme_monitor lifecycle-inventory --json
./.venv/bin/python -m token_meme_monitor lifecycle-integrity --json
./.venv/bin/python -m token_meme_monitor retention-plan --older-than-days 14 --json
```

运行服务：

```bash
./restart.sh
./restart.sh status
./restart.sh rotate-logs
./.venv/bin/python -m token_meme_monitor run-worker
./.venv/bin/python -m token_meme_monitor run-scheduled-backtest-worker
./.venv/bin/streamlit run dashboard/app.py
```

维护和回测：

```bash
./.venv/bin/python -m token_meme_monitor cleanup-data
./.venv/bin/python -m token_meme_monitor validate-token-list
./.venv/bin/python -m token_meme_monitor export-prediction-dataset
./.venv/bin/python -m token_meme_monitor refresh-prediction-outcomes --limit 10000
./.venv/bin/python -m token_meme_monitor refresh-prediction-outcomes --refresh-missing-quality --limit 10000
./.venv/bin/python -m token_meme_monitor refresh-risk-enrichment --fixture-json risk-fixture.json
./.venv/bin/python -m token_meme_monitor rebuild-predictions
./.venv/bin/python -m token_meme_monitor backtest-predictions --max-price-divergence-pct 0.10
./.venv/bin/python -m token_meme_monitor strategy-feedback-report --max-price-divergence-pct 0.10
./.venv/bin/python -m token_meme_monitor scheduled-backtest-report --max-price-divergence-pct 0.10
./.venv/bin/python -m token_meme_monitor compact-history --older-than-days 14 --dry-run
./.venv/bin/python -m token_meme_monitor compact-history --older-than-days 14 --execute
```

## 13. 当前不做的事情

当前基线还没有把 observation-only 风险数据正式纳入：

- 税和 Honeypot 风险评分
- owner 权限评分
- LP lock / burn 评分
- 持仓集中度实时评分
- 历史持币人数序列回放
- 本地大模型 / GPU 模型
- 多链统一策略层
- 多 worker 并发写库

## 14. 文档变更规则

- 会影响采集、清洗、评分、风险、状态、告警、预测、outcome、表结构或 CLI 的改动，必须同步更新本文档。
- 只改 dashboard 展示时，同步更新 `docs/frontend-dashboard-ui.md`。
- OpenSpec 目录保留变更档案，不作为日常运行说明入口。
- `data/backtests/` 是生成报告目录，不作为源文档维护。

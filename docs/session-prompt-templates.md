# 会话提示词模板

这份文档用于在新会话里快速接续当前项目上下文。源文档只保留少数入口，避免旧方案和当前代码脱节。

优先阅读：

1. `README.md`
2. `docs/backend-core-logic.md`
3. `docs/frontend-dashboard-ui.md`，仅前端任务需要

## 1. 后端开发模板

```text
项目路径：/Users/zjj/vs_code/token-meme-monitor

请先阅读：
- README.md
- docs/backend-core-logic.md

当前项目是 Binance Alpha / BSC 监控器，不是全链新币监听器。

工作要求：
- 以当前代码行为为准；如果文档和代码不一致，指出差异并同步修正文档。
- 后端任务优先关注采集、清洗、特征、评分、预测、outcome、SQLite 和 CLI。
- 不要重新定义策略体系，沿用 backend-core-logic.md 中的当前基线。
- 涉及采集、清洗、评分、风险、状态、告警、预测、outcome、表结构或 CLI 的改动，要同步更新 backend-core-logic.md。
- 运行验证优先使用：./.venv/bin/python -m unittest discover -s tests

我这次要做的任务是：
[在这里补任务]
```

## 2. 前端开发模板

```text
项目路径：/Users/zjj/vs_code/token-meme-monitor

请先阅读：
- README.md
- docs/frontend-dashboard-ui.md
- docs/backend-core-logic.md

这是 Streamlit dashboard 项目，主页面文件是：
- dashboard/app.py
- dashboard/view_models.py

当前前端关键约束：
- 左侧列表使用 st.radio，不要改回 href 跳转。
- 通过 query sync key 区分外部深链接和用户点击。
- 同 token 多交易池时，代表池选择先保留活跃池，再看机会分、信号分和流动性。
- 详情区使用 st.segmented_control 做按需视图切换，不使用 st.tabs。
- 预测视图展示 p4 概率、short_momentum_score、continuation_score、breakout_score 和中文解释。
- 不要用 `or fallback` 判断数值缺失，尤其是 `liquidity_usd`、`volume_h1`、`price_usd`、`market_cap`。
- 不要重新引入空的 markdown wrapper。

我这次要做的前端任务是：
[在这里补任务]
```

## 3. 策略研究模板

```text
项目路径：/Users/zjj/vs_code/token-meme-monitor

请先阅读：
- README.md
- docs/backend-core-logic.md

当前策略状态：
- 监控宇宙默认是 Binance Alpha / BSC。
- 规则 signal 仍是 v1 固定打分。
- 候选指标已经实时计算并写入 feature_json，但尚未正式进入规则评分。
- p4 prediction 会输出 2h/6h/24h 概率、回撤风险、short/continuation/breakout 分数。
- 强爆发判断应以真实 signal_prediction_outcomes 和事件级 walk-forward 为准，不只看 token list 事后锚点。

讨论新指标时，请明确：
- 当前是否已采集
- 是否已实时计算
- 是否已进入正式评分
- 数据来源
- 历史回测可用性
- 对不同市值桶的适用性

我这次要讨论的主题是：
[在这里补主题]
```

## 4. 数据库或存储模板

```text
项目路径：/Users/zjj/vs_code/token-meme-monitor

请先阅读：
- README.md
- docs/backend-core-logic.md

当前数据库是 SQLite，核心表包括 tokens、pairs、snapshots、signals、signal_predictions、signal_prediction_outcomes、external_ohlcv、external_trend_metrics 和归档表。

当前已有 compact-history：
- 旧 snapshots.raw_json 可压缩到 snapshot_raw_archives。
- 旧 signals.feature_json 可压缩到 signal_feature_archives。
- prediction dataset 只在 signal 仍是 compact 占位符时还原 archive，避免覆盖后续修复重写。

迁移或存储方案请明确：
- 哪些表值得迁移
- 哪些数据可以重建
- worker 如何无缝继续跑
- 如何避免把旧脏数据带入新库
- 是否需要重新 seed Alpha universe

我这次的数据库/存储任务是：
[在这里补任务]
```

## 5. 回测分析模板

```text
项目路径：/Users/zjj/vs_code/token-meme-monitor

请先阅读：
- README.md
- docs/backend-core-logic.md

当前可用命令：
- ./.venv/bin/python -m token_meme_monitor validate-token-list
- ./.venv/bin/python -m token_meme_monitor refresh-prediction-outcomes --limit 10000
- ./.venv/bin/python -m token_meme_monitor backtest-predictions --max-price-divergence-pct 0.10
- ./.venv/bin/python -m token_meme_monitor scheduled-backtest-report --max-price-divergence-pct 0.10

分析时请区分：
- token list 事后锚点验证
- stored prediction dataset 的事件级 walk-forward
- dashboard 外部趋势展示
- p4 校准可用的 signal_prediction_outcomes

我这次要分析的是：
[在这里补内容]
```

## 6. 极简模板

```text
项目路径：/Users/zjj/vs_code/token-meme-monitor

先看 README.md 和 docs/backend-core-logic.md。
如果是前端任务，再看 docs/frontend-dashboard-ui.md。

以当前代码行为为准；如果文档不一致，同步修正文档。

我这次要你继续做的是：
[任务]
```

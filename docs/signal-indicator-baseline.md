# 信号指标基线文档

这份文档是当前仓库中“实时信号逻辑”的唯一基线说明。

规则：

- 任何会影响评分、风险标记、状态流转或告警的指标，都必须记录在这里。
- 后续如果要新增指标，必须先更新这份文档，再在代码里启用。
- 如果代码和文档不一致，必须在同一个变更里同步修正文档。

当前基线版本：

- 策略版本：`v1`
- 监控宇宙：`Binance Alpha / BSC`
- 当前评分引擎：规则打分，不是模型打分

## 信号流水线

当前实时信号链路如下：

1. 从官方 `Binance Alpha Token List` 同步 BSC 代币池。
2. 从 DexScreener 解析每个代币的可交易币对。
3. 拉取 DexScreener 的币对快照。
4. 在生成特征前，先做市场数据清洗。
5. 根据快照生成特征。
6. 用固定规则做打分。
7. 推导币对状态和告警资格。

## 数据来源

当前用于信号逻辑的数据源有：

- Binance Alpha Token List
  - 决定监控宇宙
  - 提供 `price / market_cap / fdv / liquidity / volume_24h / holder_count` 的官方参考值
- DexScreener 币对快照
  - 提供币对级别行情、成交、买卖笔数等数据
- Honeypot API
  - 当 Binance Alpha 没给 `holder_count` 时，用来补持币人数

## 数据清洗规则

进入评分前，当前会先做如下清洗：

- 拒收非有限数值，例如 `NaN`、`inf`、`-inf`
- 拒收明显超界的市场数据
- 在 `binance_alpha` 模式下，以下字段优先参考 Binance Alpha 官方值：
  - `price_usd`
  - `market_cap`
  - `fdv`
- 如果 DexScreener 值和 Binance Alpha 官方值偏差过大，会用 Alpha 官方值替换 DexScreener 值
- 每个字段的清洗来源会写进快照原始负载中的 `_data_quality`

当前硬边界：

- `price_usd <= 10_000_000`
- `price_native <= 10_000_000`
- `market_cap / fdv / liquidity / volume` 等美元名义值 `<= 10_000_000_000_000`
- 所有 `price_change_*` 的绝对值 `<= 1_000_000`

## 当前特征

当前评分引擎会生成这些特征：

- `age_minutes`
- `price_usd`
- `market_cap`
- `fdv`
- `liquidity_usd`
- `volume_m5`
- `volume_h1`
- `volume_h24`
- `buys_m5`
- `sells_m5`
- `buys_h1`
- `sells_h1`
- `tx_count_m5`
- `tx_count_h1`
- `buy_sell_ratio_m5`
- `buy_sell_ratio_h1`
- `liquidity_to_fdv`
- `volume_to_liquidity_h1`
- `website_count`
- `social_count`
- `boosts_active`
- `price_change_m5`
- `price_change_h1`
- `price_change_h24`

## 当前风险标记

这些风险标记会在打分前生成：

- `missing_price`
  - 条件：`price_usd <= 0`
- `low_liquidity`
  - 条件：`liquidity_usd < MIN_LIQUIDITY_USD`
- `liquidity_near_zero`
  - 条件：`liquidity_usd < ARCHIVE_LIQUIDITY_USD`
- `thin_m5_activity`
  - 条件：`tx_count_m5 < MIN_BUY_COUNT_M5` 且 `volume_m5 <= 0`
- `sell_pressure`
  - 条件：`sells_m5 > buys_m5 * 1.5` 且 `sells_m5 >= 5`
- `missing_project_metadata`
  - 条件：`website_count + social_count == 0`
- `fdv_missing`
  - 条件：`fdv <= 0`
- `fdv_liquidity_stretched`
  - 条件：`fdv / liquidity_usd > 25`

仅在非 Alpha 模式下：

- `stale_pair`
  - 条件：币对年龄超过 `MAX_PAIR_AGE_HOURS`

## 当前评分规则

当前总分由以下固定规则累加得到：

正向规则：

- `+15` 流动性健康
  - 条件：`MIN_LIQUIDITY_USD <= liquidity_usd <= 250_000`
- `+8` 流动性充足
  - 条件：`liquidity_usd > 250_000`
- `+15` 1 小时成交额达标
  - 条件：`volume_h1 >= MIN_VOLUME_H1_USD`
- `+6` 5 分钟短时放量
  - 条件：`volume_m5 >= MIN_VOLUME_H1_USD * 0.12`
- `+20` 5 分钟买盘主导
  - 条件：
    - `buys_m5 >= MIN_BUY_COUNT_M5`
    - `buy_sell_ratio_m5 >= MIN_BUY_SELL_RATIO_M5`
- `+12` 1 小时买盘偏强
  - 条件：
    - `buy_sell_ratio_h1 >= 1.3`
    - `buys_h1 >= MIN_BUY_COUNT_M5 * 2`
- `+15` 成交额/流动性突破
  - 条件：`volume_to_liquidity_h1 >= FOCUS_VOLUME_TO_LIQUIDITY_RATIO`
- `+8` 成交额/流动性支撑
  - 条件：`volume_to_liquidity_h1 >= 0.12`
- `+10` 流动性与 FDV 平衡
  - 条件：`0.04 <= liquidity_to_fdv <= 0.40`
- `+5` 项目信息完整
  - 条件：`website_count + social_count >= 2`
- `+3` 项目信息部分可见
  - 条件：`website_count + social_count == 1`
- `+5` 有 Dex 推广
  - 条件：`boosts_active > 0`
- `+5` 价格趋势向上
  - 条件：`price_change_h1 > 20` 且 `price_change_m5 > 0`

负向规则：

- `-20` 流动性低于健康区间
- `-8` 流动性/FDV 比例过弱
  - 条件：`liquidity_to_fdv < 0.02`
- `-5` 短期趋势偏弱
  - 条件：`price_change_h1 < -20`
- `-8` 如果命中 `sell_pressure`
- `-8` 如果命中 `fdv_liquidity_stretched`
- `-20` 如果命中 `missing_price`

特殊说明：

- 在 `binance_alpha` 模式下，年龄加分当前关闭
- 在非 Alpha 模式下，仍然有年龄加分：
  - `+10`：年龄 `<= 120` 分钟
  - `+5`：年龄 `<= 360` 分钟

最终分数会被限制在 `0..100`。

## 状态阈值

当前阈值：

- `FOCUS_SCORE_THRESHOLD = 65`
- `ALERT_SCORE_THRESHOLD = 78`

当前状态映射：

- `archived`
  - 条件：存在严重风险标记
- `alerted`
  - 条件：分数 `>= ALERT_SCORE_THRESHOLD`
- `focused`
  - 条件：分数 `>= FOCUS_SCORE_THRESHOLD`
- `watching`
  - 其他情况

严重风险标记：

- 永远算严重风险：
  - `missing_price`
  - `liquidity_near_zero`
- 仅非 Alpha 模式：
  - `stale_pair`

## 当前告警逻辑

当前只有满足以下条件时，信号才有资格触发告警：

- `score >= ALERT_SCORE_THRESHOLD`
- 不存在 `missing_price`
- 在非 Alpha 模式下，还必须不存在 `stale_pair`

告警去重：

- 每个币对、每个通道的告警冷却时间为 `30` 分钟

## 已采集但尚未纳入评分的指标

这些值系统里已经有，但当前不会直接影响分数：

- `holder_count`
- `alpha_score`
- honeypot 风险细节
- owner 权限检查
- 买卖税
- LP 锁仓 / burn 状态
- 巨鲸集中度

这些值以后可以加入，但必须先在本文件登记。

## 回测后新增的候选指标

以下指标来自 `token_list.txt` 的历史验证回测结果。

注意：

- 这些指标目前**还没有接入实时评分**
- 它们现在只是“候选指标”
- 只有在后续确认要启用时，才会进入“当前特征 / 当前评分规则”章节

### 1. 相对放量倍数

候选字段：

- `volume_impulse_vs_prev24h`
  - 定义：上涨前锚点的 `1h 成交量 / 过去 24h 小时成交量中位数`
- `volume_impulse_vs_prev72h`
  - 定义：上涨前锚点的 `1h 成交量 / 过去 72h 小时成交量中位数`

回测结论：

- 对低市值样本，这类相对放量指标通常比绝对成交额阈值更有解释力
- 对中高市值样本，这类指标也能作为成交量异动确认项

当前状态：

- 已在历史验证脚本中使用
- 尚未接入实时评分

### 2. 市值分桶

候选字段：

- `market_cap_bucket`

当前分桶：

- `<1M`
- `1M-10M`
- `10M-50M`
- `50M+`

回测结论：

- 高低市值 token 使用同一套绝对阈值，偏差明显
- 后续如果继续优化评分逻辑，应优先考虑按市值桶分层

当前状态：

- 已在历史验证报告中使用
- 尚未接入实时评分

### 3. 预警型趋势指标

候选方向：

- 使用“上涨前相对趋势变化”代替单纯的绝对价格涨幅阈值
- 例如：
  - `h1_return_before`
  - `h4_return_before`
  - `h24_return_before`
  - 结合相对放量共同判断

回测结论：

- 很多 token 在真正暴涨前，短周期收益率仍然为负
- 说明当前类似 `price_change_h1 > 20` 的规则更像“启动后确认”，不适合单独承担“启动前预警”

当前状态：

- 已在历史验证脚本中输出
- 尚未接入实时评分

### 4. 历史成交量分位 / 中位偏离

候选方向：

- 用统计分位或中位数偏离，替代固定绝对量能阈值
- 目前已经通过 `volume_impulse_vs_prev24h / prev72h` 这种中位偏离形式做了初版验证

回测结论：

- 对不同市值段 token，这类统计型指标的可比性更好

当前状态：

- 已有初版实现思路
- 尚未进入实时评分

### 5. 主升浪锚点定义

候选方法：

- 先在更长时间窗口中定义“有效主升浪”
- 再回看主升浪前的 `1h / 4h / 24h` 指标状态

当前历史验证脚本里的近似做法：

- 先用近 90 天 `day` 级别数据找主升浪起点
- 再拉主升浪附近的小窗口 `hour` 数据
- 再计算上涨前的量价结构

意义：

- 这不是直接用于实时告警的指标
- 但它决定了以后回测和策略验证的统一标准

## 历史验证脚本

当前保留的历史验证脚本：

- CLI 命令：`python3 -m token_meme_monitor validate-token-list`
- 代码位置：[token_validation.py](/Users/zjj/vs_code/token-meme-monitor/token_meme_monitor/token_validation.py)
- 默认输入文件：`token_meme_monitor/token_list.txt`
- 默认输出文件：
  - `data/backtests/token_list_validation.json`
  - `data/backtests/token_list_validation.md`

该脚本的用途是：

- 验证当前指标逻辑是否能在历史上涨前给出有效信号
- 验证不同市值层级下，绝对阈值是否存在偏差
- 为后续新增指标提供“先回测、后启用”的验证路径

### 2026-04-26 token list 回测结论

本轮输入：

- `token_meme_monitor/token_list.txt`

输出：

- `data/backtests/token_list_validation.json`
- `data/backtests/token_list_validation.md`

结果摘要：

- 样本数：`7`
- 结果分布：`大致对上 1 个`，`部分对上 6 个`
- 未来 24h 最大涨幅：
  - 平均约 `10.1%`
  - 中位数约 `9.2%`
  - `>=20%`：`1/7`
  - `>=50%`：`0/7`
  - `>=100%`：`0/7`

当前解释：

- 现有规则能筛出“有一定流动性、成交量或趋势基础”的 token
- 但不能证明能稳定预测强爆发
- 当前概率预测更适合用于候选排序和复盘，不应被当成高胜率买入信号
- 后续强爆发概率必须依赖真实线上 `signal_prediction_outcomes` 校准，而不是只看 token list 事后锚点

### p3 概率预测状态

当前概率预测版本：

- `PREDICTOR_VERSION=p3`

p3 不是深度学习模型，不依赖 GPU。它的定位是：

- 规则概率
- 机会分
- 阶段判断
- 在本地成熟 outcome 样本足够后，叠加历史命中率校准

重要边界：

- 样本不足时自动退回规则概率
- 历史命中率只做保守校准，不直接硬覆盖规则概率
- `signal_prediction_outcomes` 优先使用外部 `hour` OHLCV 历史线计算，不再依赖本地稀疏 snapshot；历史线会写入 SQLite 缓存，后续复用本地数据
- outcome 至少等待 25h 后再补，确保 24h 窗口的小时线已闭合
- p3 校准只吃覆盖足够的 horizon：2h 至少 2 根小时线、6h 至少 5 根、24h 至少 18 根
- 截至 2026-04-26，本地 prediction 还没跑满 25h，`signal_prediction_outcomes` 暂无可用校准样本

后续观察项：

- 等线上 signal 运行满 25h 后，检查真实 outcome 命中率
- 重点看 p3 是否仍然过高，或是否过度保守
- 当样本足够后，再考虑轻量模型校准，例如 Logistic Regression / LightGBM / Isotonic calibration
- 暂时不需要本地大模型或 GPU

## 变更规则

后续如果要新增或调整指标：

1. 必须先更新这份文档，并与代码改动放在同一个变更里
2. 必须明确该指标属于哪一类：
   - 原始输入
   - 派生特征
   - 风险标记
   - 评分规则
   - 状态/告警门槛
3. 必须记录：
   - 数据来源
   - 阈值
   - 分值影响
   - fallback 行为
4. 必须补测试或更新测试

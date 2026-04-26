# 会话提示词模板

这份文档用于在“新对话”里快速接续当前项目上下文。

用途：

- 减少每次重新解释项目背景的成本
- 保证新会话优先读取已有文档，而不是重新猜项目状态
- 让后续任务在统一上下文下继续推进

建议使用方式：

1. 根据任务类型选择一个模板
2. 复制到新对话
3. 只替换末尾的任务描述

关联文档：

- 后端核心逻辑：
  - [backend-core-logic.md](/Users/zjj/vs_code/token-meme-monitor/docs/backend-core-logic.md)
- 信号指标基线：
  - [signal-indicator-baseline.md](/Users/zjj/vs_code/token-meme-monitor/docs/signal-indicator-baseline.md)

---

## 1. 后端开发模板

适合继续做：

- 采集链路
- 数据清洗
- 评分逻辑
- 数据库存储
- worker 行为
- 回测脚本

模板：

```text
请先阅读并以以下文档为当前项目上下文基线：

1. docs/backend-core-logic.md
2. docs/signal-indicator-baseline.md

工作要求：

- 先理解当前后端链路，再开始改代码
- 当前重点是后端逻辑，不优先处理 UI
- 如果文档和代码不一致，以代码现状为准，并指出差异
- 不要重新发明策略体系，先沿用当前文档里的基线
- 新增指标、规则、数据源时，先更新文档，再改代码，再补测试

当前背景：

- 当前监控宇宙是 Binance Alpha / BSC
- 当前评分逻辑是规则打分，不是模型打分
- 候选指标已经实现为实时计算，但尚未接入正式评分：
  - market_cap_bucket
  - volume_impulse_vs_prev24h
  - volume_impulse_vs_prev72h
  - h1_return_live
  - h4_return_live
  - h24_return_live
- 数据清洗链路已经接入
- 旧库历史数据不是当前重点，后续切数据库时再统一清理
- 历史验证脚本已保留，可复用：
  python3 -m token_meme_monitor validate-token-list

请在开始修改前，先用简短语言复述你理解的当前后端状态和你准备做的下一步。

我这次要做的任务是：
[在这里补你的任务]
```

---

## 2. 策略研究模板

适合继续做：

- 指标讨论
- 上涨前信号研究
- 风险过滤研究
- 分层策略
- 回测结果解读

模板：

```text
请先阅读并以以下文档为策略上下文基线：

1. docs/signal-indicator-baseline.md
2. docs/backend-core-logic.md

工作要求：

- 先基于当前文档理解已有指标、候选指标和数据限制
- 当前重点是策略研究，不优先改 UI
- 不要脱离现有数据源能力空谈指标
- 如果提出新指标，请明确区分：
  - 当前已生效
  - 已实现但未纳入评分
  - 仅为候选指标
- 如果建议后续落地，请说明：
  - 数据来源
  - 是否可实时获取
  - 是否可历史回测
  - 是否适合不同市值分桶

当前背景：

- 当前监控宇宙是 Binance Alpha / BSC
- 当前规则更偏确认型，不是最早期抄底型
- 已回测并沉淀的候选方向包括：
  - 相对放量倍数
  - 市值分桶
  - 预警型趋势指标
  - 历史成交量分位/中位偏离
- 历史验证脚本已保留，可复用：
  python3 -m token_meme_monitor validate-token-list
- 当前后端已经能实时计算候选指标，但还没有把它们正式纳入评分

请先用简短语言复述你理解的当前策略状态，再开始讨论。

我这次要讨论的主题是：
[在这里补你的主题]
```

---

## 3. 数据库迁移模板

适合继续做：

- SQLite -> PostgreSQL / MySQL / 其他数据库
- 数据保留策略
- 迁移脚本
- 新库重建

模板：

```text
请先阅读并以以下文档为当前项目上下文基线：

1. docs/backend-core-logic.md
2. docs/signal-indicator-baseline.md

当前任务是数据库迁移，请优先关注后端，不处理 UI。

工作要求：

- 先理解当前后端数据流、表结构、worker 行为，再设计迁移方案
- 当前旧库是 SQLite，但旧历史数据不是绝对可信，不要默认全量原样迁移
- 迁移目标应该优先保证“新库干净、可持续跑、可回放”，而不是机械复制旧库
- 如果你建议保留旧数据，请明确说明哪些表值得迁，哪些不值得迁
- 如果文档和代码不一致，以代码现状为准，并指出差异
- 请把迁移方案拆成：
  - 新库 schema
  - 数据保留策略
  - 初始化策略
  - 回填策略
  - 切换步骤
  - 风险点
- 如果需要新增脚本或命令，请一起说明

当前背景：

- 当前监控宇宙是 Binance Alpha / BSC
- 当前后端主链路已稳定：
  - Alpha token list -> pair seed -> snapshot -> cleaning -> feature -> signal -> outcome
- 候选指标已经实现为实时计算，但尚未纳入正式评分
- 新数据清洗链路已经接入
- 旧库里的历史数据不打算无条件信任
- 后续迁库时，倾向于：
  - 新库建空表
  - 重新同步 Alpha universe
  - 只保留清洗后的新数据
  - 谨慎决定是否迁移旧 snapshots/signals
- 历史验证脚本已保留，可复用：
  python3 -m token_meme_monitor validate-token-list

迁移时请重点考虑这些问题：

1. 哪些表应该迁？
2. 哪些表可以重建，不值得迁？
3. 如何保证迁移后 worker 能无缝继续跑？
4. 如何避免把旧脏数据带进新库？
5. 如何处理候选指标、币安标签、Top10持仓占比这些元数据字段？
6. 是否需要做一次全量 Alpha re-seed？

请先用简短语言复述你理解的迁移背景，再开始给方案。

我这次迁库的目标数据库是：
[这里填写目标数据库]

额外约束：
[这里填写约束]
```

---

## 4. 新增指标接入模板

适合继续做：

- 将候选指标正式接入评分
- 接新数据源
- 调整特征或风险标记

模板：

```text
请先阅读并以以下文档为上下文基线：

1. docs/signal-indicator-baseline.md
2. docs/backend-core-logic.md

当前任务是“新增或启用指标”，请优先关注后端逻辑和策略一致性，不处理 UI。

工作要求：

- 先确认当前指标是否已在文档中登记
- 如果是候选指标，要先说明为什么现在值得进入正式评分
- 如果是新指标，要先更新文档，再改代码，再补测试
- 明确说明该指标属于：
  - 原始输入
  - 派生特征
  - 风险标记
  - 评分规则
  - 状态/告警门槛
- 说明：
  - 数据来源
  - 实时可用性
  - 历史回测可用性
  - 对不同市值桶的适用性

当前背景：

- 当前已有但未正式纳入评分的候选指标包括：
  - market_cap_bucket
  - volume_impulse_vs_prev24h
  - volume_impulse_vs_prev72h
  - h1_return_live
  - h4_return_live
  - h24_return_live
- 当前正式评分仍是 v1 固定规则
- 当前原则是：
  - 先记录
  - 再观察
  - 最后再进入告警

请先说明你建议接入哪个指标，以及为什么。

我要接入/讨论的指标是：
[在这里补指标名]
```

---

## 5. 回测分析模板

适合继续做：

- token list 历史验证
- 指标命中分析
- 市值分层效果分析
- 对“是否提前识别”做判断

模板：

```text
请先阅读并以以下文档为上下文基线：

1. docs/signal-indicator-baseline.md
2. docs/backend-core-logic.md

当前任务是做历史验证 / 回测分析，请优先关注后端和策略，不处理 UI。

工作要求：

- 先确认当前回测脚本是否适合这次任务
- 不要假设所有实时指标都能被历史回放
- 请明确区分：
  - 可严格历史重建的指标
  - 只能近似验证的指标
  - 当前无法回测的指标
- 输出结果时，请至少给出：
  - 样本数
  - 市值桶分布
  - 结果分布
  - 每个 token 的主要命中项与缺失项

当前背景：

- 当前历史验证命令：
  python3 -m token_meme_monitor validate-token-list
- 历史验证输出：
  - data/backtests/token_list_validation.json
  - data/backtests/token_list_validation.md
- 当前回测主要使用：
  - GeckoTerminal 历史 OHLCV
  - Binance Alpha 当前参考值
- 当前策略更偏确认型，不是最早期抄底型

请先说明这次回测准备怎么定义“上涨前窗口”和“有效上涨”。

我这次要分析的 token 集或问题是：
[在这里补内容]
```

---

## 6. 极简模板

如果只想快速开一个新会话，可以直接用这个：

```text
先看：
- docs/backend-core-logic.md
- docs/signal-indicator-baseline.md

当前项目以后端逻辑为主，监控宇宙是 Binance Alpha / BSC。
请沿用现有策略基线，不要重新定义背景。
如果文档和代码不一致，以代码为准并指出差异。

我这次要你继续做的是：
[任务]
```

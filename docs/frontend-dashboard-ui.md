# Dashboard 前端页面基线文档

这份文档记录当前 `dashboard/app.py` 的前端页面基线。

规则：

- 任何会影响页面结构、交互、刷新行为、状态展示、列表选择方式、详情头部信息层级的改动，都应先参考这份文档。
- 后续继续改前端时，优先在此文档上增量更新，而不是重新发散设计。
- 如果代码和文档不一致，以当前代码行为为准，但应在同一变更里同步修正文档。

## 1. 页面定位

当前 dashboard 的目标不是做通用后台，而是做一个：

- 面向 `Binance Alpha / BSC` 的实时监控面板
- 默认先看左侧候选列表，再看右侧单币详情
- 强调“快速扫盘 + 单币复核”

设计原则：

- 顶部控制区尽量薄
- 首屏优先让出给列表和详情
- 列表和详情都以“信息密度高但不乱”为目标
- 新增字段优先纳入现有视觉体系，不要直接塞裸字段

## 2. 当前页面结构

当前页面自上而下分为：

1. 标题区
   - 标题：`Binance Alpha / BSC 监控面板`
   - 标题后面是小开关：`只看行情数据`
   - 最右侧是 `自动刷新` 倒计时小标识

2. 筛选区
   - 主筛选：`宽松观察 / 平衡跟踪 / 重点聚焦`
   - 高级筛选：折叠展开，包含
     - 最低信号分
     - 最低持币人数
     - 最低流动性
   - 当前筛选摘要：一排 `chip`

3. 主数据区
   - 左侧：强信号代币列表 + 搜索
   - 右侧：单币详情

## 3. 当前有效前端改造

### 3.1 顶部控制区

已生效的改造：

- 去掉了 Streamlit 默认顶部 header / deploy / toolbar。
- 标题区被压缩成一行，不再保留大块说明区。
- `只看行情数据` 作为小型辅助开关放在标题右侧，尽量靠近标题后方，视觉层级低于标题。
- `只看行情数据` 带有 hover 提示，明确说明开启/关闭时分别会保留哪些候选。
- `自动刷新` 在顶部标题区改成了一个小型倒计时标识，位于 `只看行情数据` 后面。
- 倒计时标识会按秒回落，表示距离下一次 dashboard 自动读库刷新还有多久。
- 主筛选采用分段选择器，不用长滑条。

当前默认：

- 筛选模式默认是 `平衡跟踪`
- 默认会开启 `只看行情数据`
- 主筛选模式默认不再预设信号分阈值，信号分主要用于排序；如果要强行按分数收窄范围，走高级筛选里的 `最低信号分`

### 3.2 筛选模型

当前筛选模式：

- `宽松观察`
- `平衡跟踪`
- `重点聚焦`

筛选摘要 chips 显示的是“用户看到的选项文案”，不是加工后的阈值表达式。

例如当前展示应类似：

- `筛选模式 平衡跟踪`
- `市场数据 只看行情数据`
- `信号档位 全部`
- `持币档位 1k+`
- `流动性档位 15k+`

### 3.3 状态条

筛选栏下面的状态条已移除，不再展示：

- `数据区最后刷新时间`
- `最新快照时间`
- `最近写入条数(10m)`
- `最近信号数(10m)`
- `Top10占比已补`

原因：

- 这些信息对当前扫盘和单币复核帮助不大
- 会占用筛选区和主数据区之间的垂直空间
- 状态条原本会独立读库，和主数据区存在轻微不同步可能

### 3.4 左侧代币列表

当前左侧列表的有效行为和约束：

- 主列表只展示最近 15 分钟内有快照的 token
- 左侧默认展示前 10 条候选
- 选择行为使用 `st.radio`
- 不再使用 `<a href="?pair=...">` 链接跳转
- 点击代币后应只更新当前页面数据区，不应跳回顶部
- 列表选中状态会同步回 `st.query_params["pair"]`
- `pair` query 参数用于深链接恢复，但不能长期覆盖用户点击：
  - 当 URL 里的 `pair` 和上次 dashboard 自己同步写入的值不一致时，视为外部深链接切换，query 优先
  - 当用户点击左侧列表时，`st.radio` 当前值优先，并立即同步回 query 参数
- 页面刷新后应尽量保留当前选中的代币
- 如果当前 `selected_pair` 或 query param 对应的币对已经不在当前结果集里，必须自动切回当前列表第一条，不能让左侧列表或右侧详情因为旧状态变空
- 同一 token 出现多个交易池时，列表只保留一个代表池：
  - 先保留最近 15 分钟内仍有快照的活跃池
  - 再比较预测机会分、信号分、流动性、最新快照时间
  - 避免旧池子因为历史分数高，把仍在活跃窗口内的新池子挤出主列表

当前列表样式目标：

- 卡片宽度与上方搜索框对齐
- 默认尽量不换行，避免卡片高度抖动
- 鼠标悬浮时允许展开查看更多完整内容
- 数值在列表里要短格式显示，避免长小数撑破布局

当前列表文案包含：

- 第一行：`symbol · 状态 · 分数`

### 3.5 右侧详情区

当前详情页有效改造包括：

- 顶部是 `detail hero` 样式
- `Token Address + 复制按钮` 放在顶部 token symbol 后方，不跟 token name / quote symbol / 状态那一行混排
- token 地址不展示可见字段标签，直接显示短地址文本，后面跟一个小复制按钮
- `Pair Address` 只通过外链入口和内部选择态使用，不在右侧首屏重复展示
- 币安标签和 Top10 持仓占比作为普通 meta 字段合并进 `detail hero` 的信息行，不再单独做 insight 卡片
  - `币安标签`
  - `Top10 持仓占比`
- 右侧详情不再使用 `st.tabs` 或 `st.segmented_control` 做模式切换，所有关键内容合并成单页决策面板
- 单页决策面板按固定顺序展示：
  - detail hero
  - 顶部 token symbol 后方的短地址和小复制按钮
  - 操作结论：普通观察 / 继续跟踪 / 重点关注等标题、内容和建议合并在同一个结论框内
  - 快速判断：机会强度、风险等级，复用和量价快照一致的紧凑小卡片
  - 量价快照：当前市值、当前单价、链上持币人数、流动性、5 分钟成交额、1 小时成交额、24 小时成交额，使用同一套紧凑小卡片
  - 判断拆解：主要支撑、主要风险、下一步观察；下一步观察使用和预测/复盘一致的紧凑信息行样式
  - 预测结构和复盘验证
  - 完整走势：价格、1 小时成交额、流动性纵向展示
  - 证据中心：判断依据、指标解释、最近信号三张摘要卡纵向展示
- 右侧详情主内容采用单列纵向排版；不要在决策卡、预测复盘、走势图或证据中心里做左右分栏。少量指标卡和量价快照可以使用紧凑自动网格，避免单卡一行造成过度占高。
- 右侧详情的单列内容必须保留清晰纵向节奏：区块标题、结论框、指标卡、判断拆解、预测复盘、走势图和证据中心之间要有可感知留白，避免连续卡片贴得过紧。
- 单页首屏默认服务快速判断，文案必须使用人话，不展示英文 code、原始 feature 字段或完整概率矩阵。
- 单页决策面板不要重复展示 `最近状态`
  - 状态已经在 detail hero 的 meta 信息中展示
  - 指标卡只保留会改变操作优先级的核心字段
- 证据中心不使用大折叠块；底部只展示摘要卡，完整资料通过 `st.popover` 打开：
  - `完整依据`
  - `完整指标`
  - `历史信号`
- 证据中心的 `st.popover` 入口必须设计成贴近摘要卡底部的轻量文字触发器，实际点击目标就是文字本身；不要使用箭头、胶囊按钮、透明覆盖层或列表页那套高对比大按钮样式。
- 预测细节文案必须说明这是“规则概率叠加历史命中率校准”，用于排序和复盘，不直接触发正式告警。
- 本地 review note / watchlist 存储在 `decision_notes`，不得写回 signal、prediction、outcome 或 score 字段。
- 完整走势不要把价格、流动性和成交额混在同一张图里；不同量纲应拆成独立图表，按价格、1 小时成交额、流动性的顺序单列展示。
- 后续新增长表格、走势图或历史记录时，优先放入现有详情视图；只有会影响快速决策的字段才放到首屏。

当前性能边界：

- 右侧详情单页会读取当前 pair 的 recent snapshots 绘制走势
- 右侧详情单页会读取当前 pair 的 recent signals 生成证据中心和历史信号弹层
- 外部趋势接口不在详情单页主动请求，走势以本地 SQLite 快照为准

### 3.6 复制按钮

当前 `Token Address` 的复制按钮不是普通 `st.button`，而是浏览器侧复制实现。

原因：

- 服务器侧复制没有意义
- 必须把文本复制到用户浏览器的剪贴板

当前实现：

- 使用 `st.iframe` 承载浏览器侧复制脚本
- 先尝试 `navigator.clipboard.writeText`
- 失败时降级到 `textarea + execCommand('copy')`

### 3.7 时间显示

页面里的时间展示基线：

- 统一显示为北京时间
- 精确到秒

当前通过 `format_timestamp()` 统一处理，影响这些区域：

- 详情页最近快照时间
- 最近信号记录里的观测时间
- 列表里的更新时间

## 4. 数据刷新与页面刷新

必须区分两件事：

1. 页面刷新
2. 后端数据是否真的更新

当前页面：

- 通过 `@st.fragment(run_every=...)` 做定时刷新
- 刷新读的是本地 SQLite 数据库
- 主列表、快照和信号展示都读本地 SQLite 数据库
- 详情单页只读取本地 recent snapshots / recent signals，不主动请求外部趋势接口

也就是说：

- worker 负责拉外部数据并写库
- dashboard 主数据区负责按间隔读库
- dashboard 的外部趋势查询只作为单币详情辅助指标，不参与主列表筛选和信号状态判断

### 4.1 自动刷新标识

当前自动刷新标识放在顶部标题区，位于 `只看行情数据` 后面。

注意：

- 它修改的是 fragment 的 `run_every`
- 倒计时本身按 1 秒节奏更新
- 每次 dashboard fragment 真正读库刷新时，倒计时会重置

### 4.2 Overview 数据缓存

当前 overview 数据加载分两步：

1. 从 SQLite 读取 `list_pair_overview(limit=OVERVIEW_FETCH_LIMIT)`
2. 在前端 view-model 层派生展示字段和过滤字段

实现位置：

- [dashboard/app.py](/Users/zjj/vs_code/token-meme-monitor/dashboard/app.py)
- [dashboard/view_models.py](/Users/zjj/vs_code/token-meme-monitor/dashboard/view_models.py)

当前缓存规则：

- `load_overview_frames()` 使用 `@st.cache_data`
- 缓存 key 包含 SQLite 主库、`-wal`、`-shm` 文件的 `mtime` 和大小
- 主库或 WAL 文件变化后，overview 会重新读取和派生
- 筛选条件变化时，只重新做过滤，不重复做 token metadata JSON 解析

当前派生字段包括：

- `token_meta`
- `is_binance_alpha`
- `holder_count`
- `alpha_score`
- `alpha_market_cap`
- `alpha_fdv`
- `alpha_price`
- `alpha_liquidity`
- `alpha_volume_24h`
- `has_market_data`
- `snapshot_observed_at_dt`
- `display_score`
- `display_holders`
- `display_liquidity`
- `sort_live_score`
- `sort_alpha_score`
- `is_live_active`
- `has_recent_snapshot`
- `prediction_prob_2h_up20`
- `prediction_prob_6h_up50`
- `prediction_prob_24h_up100`
- `prediction_risk_6h_dd30`
- `prediction_opportunity_score`
- `prediction_short_momentum_score`
- `prediction_continuation_score`
- `prediction_breakout_score`
- `candidate_strength`

当前前端排序和筛选语义：

- 主列表的预测侧强度优先使用 `prediction_short_momentum_score`
- 旧 `prediction_opportunity_score` 只作为兼容回退
- 预测 tab 中分开展示：
  - `2小时短线机会`
  - `6小时延续机会`
  - `24小时爆发观察`
- 24小时分数当前只作为观察项，不作为高置信买入信号

## 5. 已踩过的坑

后续再改前端时，避免重复踩这些坑：

### 5.1 不要再用链接做左侧列表选择

不要再把左侧列表做成：

- `<a href="?pair=...">`

原因：

- 会触发整页 rerun
- 视口会跳回顶部
- 用户体验明显变差

补充：

- 不要把 query 参数永久放在列表选择最高优先级
- 否则用户点击左侧列表后，旧的 `?pair=...` 会反向覆盖新选择，表现为“列表不能点击”
- 当前必须通过 `_last_synced_pair_query` 区分“外部深链接切换”和“dashboard 自己同步写入 query”

### 5.2 不要继续堆空的 `st.markdown("<div ...>")` wrapper

之前多次出现：

- 空的 `list-panel`
- 空的 `section-card`
- 空的 `stMarkdownContainer`
- 空的 `stElementContainer`

这类问题本质上是手写开闭 HTML wrapper 但没有真正包住 Streamlit 组件。

后续原则：

- 能用原生 Streamlit 布局就不要用空的开闭标签
- 如果必须写 HTML 包裹，确保内部确实有内容

### 5.3 谨慎继续依赖内部 DOM 选择器

当前页面为了压缩顶部，已经使用了不少：

- `data-testid`
- 负 margin
- 内部容器覆盖

这类样式虽然当前有效，但对 Streamlit 升级比较脆弱。

后续原则：

- 新增样式时优先用自定义 class
- 少增加新的内部 DOM 定向覆盖
- 如果要继续压缩顶部，先收结构，再补 CSS

### 5.4 不要用 truthy/falsy 判断业务数值缺失

前端展示和结论判断里，`0` 是有效业务值，尤其是：

- `liquidity_usd = 0`
- `volume_h1 = 0`
- `price_usd = 0`
- `market_cap = 0`

不要写：

```python
metric_value(...) or overview_row.get("alpha_liquidity") or 0
```

原因：

- `0` 会被 Python 当成 falsy
- 主快照明确返回 `liquidity_usd = 0` 时，会错误回退到 `alpha_liquidity`
- 结论卡片可能把本应“暂不关注”的 token 误显示为“继续跟踪”或“重点关注”

当前统一做法：

- 使用 `first_non_missing(...)`
- 只在值是 `None`、空字符串或 `NaN` 时才回退
- 明确保留业务上的 `0`

### 5.5 不要让旧池子的历史高分挤掉活跃池

同一个 token 可能同时存在多个交易池，甚至 symbol/name 完全一样。

当前代表池选择必须遵守：

- 先判断 `is_live_active`
- 活跃池优先级高于预测分、信号分和流动性
- 只有活跃状态相同时，才继续比较机会分、信号分、流动性和时间

原因：

- 主列表最终还会过滤“最近 15 分钟活跃”
- 如果去重阶段先保留旧池子，后续过滤会把整个 token 都过滤掉
- 这会造成明明有新活跃池，左侧主列表却看不到该 token

## 6. 当前技术债

虽然已经清掉一批旧代码，但前端仍然有这些债务：

- 顶部布局仍然存在对 Streamlit 内部容器的样式覆盖
- `Token Address` 复制按钮仍然是 `st.html` 浏览器侧复制组件，不是统一组件体系
- [dashboard/app.py](/Users/zjj/vs_code/token-meme-monitor/dashboard/app.py) 仍然承担样式注入和大部分渲染函数
- [dashboard/view_models.py](/Users/zjj/vs_code/token-meme-monitor/dashboard/view_models.py) 已承接数据派生、信号上下文和结论判断，后续新增可测试展示逻辑应优先放到这里
- 单页里的 `预测结构` 区只展示已有预测结果，当前不展示校准样本数量；后续如果 p4 outcome 样本变多，可以增加“校准样本/置信度”展示

## 7. 后续前端改造建议

下一次继续改 dashboard，建议按这个顺序：

1. 先看这份文档
2. 确认是要改
   - 布局
   - 行为
   - 数据展示
   - 还是样式
3. 尽量先在现有结构上增量修改
4. 不要重新引入：
   - 链接式列表切换
   - 空 wrapper
   - 裸字段直接堆进详情页

优先级建议：

- 第一优先：保持左侧列表选择体验稳定
- 第二优先：保持右侧详情头部信息层级一致
- 第三优先：再做美化
- 如果要继续增强预测展示，优先展示“置信度/校准样本数/是否历史命中率下调”，不要把概率做成强买入暗示

## 8. 相关代码位置

当前前端主文件：

- [dashboard/app.py](/Users/zjj/vs_code/token-meme-monitor/dashboard/app.py)

当前前端 view-model 文件：

- [dashboard/view_models.py](/Users/zjj/vs_code/token-meme-monitor/dashboard/view_models.py)

如果后续再做页面改造，应优先更新这份文档，再继续改代码。

## 9. 本地启动

dashboard 只负责展示本地 SQLite 数据，不负责采集。完整运行方式以根目录 README 为准。

常用命令：

```bash
./restart.sh
./restart.sh status
./restart.sh stop
```

dashboard 地址：

```text
http://127.0.0.1:8501
```

## 10. 新会话提示语

以后每次为了修改前端页面而新开会话，建议先把下面这段完整发给模型：

```text
项目路径：/Users/zjj/vs_code/token-meme-monitor

这是一个 Streamlit dashboard 项目，主页面文件是：
- /Users/zjj/vs_code/token-meme-monitor/dashboard/app.py

前端页面改造基线文档：
- /Users/zjj/vs_code/token-meme-monitor/docs/frontend-dashboard-ui.md

后端核心逻辑文档：
- /Users/zjj/vs_code/token-meme-monitor/docs/backend-core-logic.md

当前前端已经完成的关键点：
- 页面标题为 “Binance Alpha / BSC 监控面板”
- 左侧是代币列表，右侧是详情区
- 左侧列表点击不应触发整页跳转
- 顶部标题区包含 `只看行情数据` 开关和 `自动刷新` 倒计时标识
- 顶部有筛选模式，筛选栏下面的状态条已移除
- 详情区已有 Token Address 复制按钮
- 详情区已新增 币安标签 / Top10 持仓占比 的 meta 信息
- 详情区是单页决策面板，不再使用 `st.segmented_control` / `st.tabs` 切换模式
- overview 数据派生和结论判断已拆到 dashboard/view_models.py
- 左侧列表使用 `st.radio`，并通过 query sync key 区分外部深链接和用户点击
- 同 token 多交易池时，代表池选择先保留活跃池，再看机会分、信号分和流动性
- 详情区合并为单页：核心判断、预测结构、复盘验证、完整走势和证据中心顺序展示
- 临时前端 debug 展示已移除

要求：
- 先阅读 frontend-dashboard-ui.md，再开始改代码
- 后续改动优先遵循 frontend-dashboard-ui.md
- 不要重新引入空的 markdown wrapper
- 不要把左侧列表改回 href 跳转
- 不要用 `or fallback` 判断数值字段是否缺失，尤其是 `liquidity_usd`
- 尽量只改我指定的区域，不随意改其他已稳定样式
```

最关键的一句是：

```text
先阅读 frontend-dashboard-ui.md，再开始改代码。
```

如果只是小改某个前端区域，也建议至少把这句话和文档路径一起发给模型。

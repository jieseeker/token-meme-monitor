# Token Meme Monitor

Binance Alpha / BSC 代币监控面板。Rust 单进程负责代币发现、行情与指标后台刷新、SQLite 存储和 HTTP 页面；交易在外部交易所完成。

## 目录

- `rust-dashboard/src/`：worker、指标计算、缓存、API 和数据库。
- `rust-dashboard/static/`：嵌入二进制的页面及页面检查。
- `deploy-production.sh`：生产服务器部署入口。源码由服务器从 GitHub `main` 拉取。
- `deploy-cloud.sh`：通用 Linux/systemd 部署脚本；也支持从本地打包源码。
- `scripts/generate-sing-box.rb`：将本地代理订阅转换为服务器私有配置。
- `openspec/changes/background-task-chain-optimization/`：当前后台刷新链路的设计记录。

`data/`、`.env`、代理凭据和 Cargo 构建产物不进入 Git。

## 本地运行

```bash
cargo build --release --locked --manifest-path rust-dashboard/Cargo.toml
./rust-dashboard/target/release/tmm all
```

默认数据库为 `data/monitor.db`，页面端口为 `8501`。可用 `MONITOR_DATABASE_PATH`、`DASHBOARD_PORT` 等环境变量覆盖；变量示例见 `.env.example`。单独运行 worker、页面或初始化数据库时，将 `all` 换成 `worker`、`dashboard` 或 `init-db`。

页面主要使用 `/api/tokens`、`/api/token/{address}/summary` 和各指标缓存接口；`/api/status` 用于查看后台刷新状态。池子深度、OI 和大额动向由后台预热，页面读取共享缓存。

## A/B/C 影子诊断与只读回放

`SIGNAL_V2_MODE=shadow` 在旧采集写入后保存独立阶段诊断；默认 `legacy`。旧分数、列表与调度继续使用原规则。`GET /api/signal-v2?limit=100` 查看模式、试验版本、参数与阶段摘要；可传 `token=0x...`，limit 为 1–500。阶段命中保留来源时间未验证标记，不代表正式交易机会。

```bash
MONITOR_DATABASE_PATH=/absolute/path/consistent-copy.db ./rust-dashboard/target/release/tmm signal-v2-replay
MONITOR_DATABASE_PATH=/absolute/path/consistent-copy.db ./rust-dashboard/target/release/tmm signal-v2-replay --legacy-proxy
```

回放直接以 SQLite READ_ONLY 打开来源，不初始化或写表、不启动服务或请求行情；stdout 仅一个 JSON，错误写 stderr 并返回非零。使用一致性数据库副本。`MIN_VOLUME_H1_USD` 和 `FOCUS_SCORE_THRESHOLD` 取当前环境值，回放要求成交额阈值有限且大于零、分数阈值至少为 1；试验身份由版本与这两个参数共同确定，不能据此追认历史配置。

严格模式只接收采集时保存的有效 V2 元数据；旧 `{}` 数据全部排除。`--legacy-proxy` 明确标为探索性结果，以同币同时间近似一次响应，旧成交额零值按未知处理。按时间读取最多七天数据，从首次合格存储组前瞻冻结流动性最高池，不读取今天的活跃池名单；只保留该池 24 小时基线和短期标签队列。分组排序与历史超预算会安全拒绝，不截断成完整覆盖。

缺失、损坏或冲突的同期评分保持未知；成交入口也需当前 h1 与历史基线同时有效，未知证据不会复位入口，真实零值或低于阈值仍可独立复位。JSON 分别报告 A/B/C 采样结构命中、风险、断档、缺失与覆盖。15m/1h/4h 标签取同池在目标时刻至其后五分钟的首个有效价格，并要求目标前后相邻价格间隔不超过五分钟；无端点、右删失及零命中均明确计数，收益字段保留 null。它们是采样价格变化，不是成交或策略利润。元数据不能证明响应完整；部分池写入成功的残留组可能进入回放，生产因写入/退休失败、影子冷却或保存失败而跳过的实际执行轨迹也无法还原。参见[验证报告](docs/superpowers/reports/2026-10-24-signal-stages-validation.md)。

回放还输出 `b_filter_comparison`（试验 `b_quality_v1`），保留原始 B 总数并对照风险 PASS 子集：近期成交、距突破线≤3%、两者同时满足。近期成交要求当前 m5≥h1/12，且不低于首次突破时 m5 的80%；首次突破的同池同秒样本须全部具有一致的正 m5，否则判为未知。滚动 m5 不相加，也不是独立资金流证据。PASS 信号另按距冻结突破线0–3%、3–5%、5–8%、>8%分组，并提供按事件 UTC 日期的同口径统计。候选归属在 B 确认时冻结，后续行情只计算端点结果。

A 信号同步输出风险 PASS、近期量能、贴近突破线等入场分组，以及 A 后 60/180/300 秒的延迟入场影子事件。延迟事件只使用延迟窗口内首个有效观测价；窗口内没有有效价格则不生成事件，不用缺失值或 A 原价补位。

每个端点新增 `positive`、`negative`、`flat` 和 `positive_sampled_return_rate_pct`；上涨比例的分母仅为可用端点，缺失、右删失单列，零样本为 null。该比例不是交易胜率或扣费收益；按日分组也不自动构成独立验证。上述固定初值用于比较，回放不调参、不改变线上规则、不自动晋级信号；影子采集仍须持续验证数据与效果。

每个结构事件另输出 `path` 与 `exit_strategies` 路径研究，窗口为 4 小时 +5 分钟；主净收益口径**不扣除手续费和滑点**，同时每套退出规则附带 `0% / 0.3% / 0.5% / 1%` 四档假设往返成本的 `cost_sensitivity`。三套固定退出规则分别为：`+10%` 全出、不设固定止盈仅 `-10%` 止损或超时、以及 `+10%` 卖 30%、`+25%` 再卖 30%、剩余仓位按 `max(+8%, 高点回撤20%)` 退出。该结果是只读影子回测，不代表真实成交价格、手续费、滑点、仓位或投资建议；若一个观测点跳过阈值，只能按观测侧归类，无法证明分钟内路径先后。

## Alpha 潜力漏抓审计

```bash
MONITOR_DATABASE_PATH=/absolute/path/consistent-copy.db ./rust-dashboard/target/release/tmm alpha-discovery-audit [top_n]
```

Worker 在 Binance 合约过滤前保存全量 BSC Alpha 列表，按代币和 5 分钟桶去重，保留 7 天。审计按已存储的最大 24h 涨幅输出 Top N，并将每个头部代币分为 `monitored`、`futures_listed_unmonitored`、`not_futures_listed`，用于判断高潜力币是在源头被合约过滤、进入后未形成活跃池，还是已被当前页面覆盖。该口径只做发现漏斗审计，不代表可交易信号。

## Early warning 只读回放

```bash
MONITOR_DATABASE_PATH=/absolute/path/consistent-copy.db ./rust-dashboard/target/release/tmm early-warning-replay
```

`early-warning-replay` 只使用当前与历史 30 分钟内的 m5 成交额、5m 买压、m5 成交额/流动性和 5m 价格变化，输出量能异常、量能买压、强前置三组规则在 15m/1h/4h 后的采样收益、上涨率、>2%/5%/10% 命中率，并与 15 分钟基线样本对照。每个代币使用回放期内历史流动性最高的池，规则按代币 15 分钟冷却，端点缺失和右删失分开。该报告用于验证前置指标是否真的有预测增益，不代表可交易信号。

同一报告还输出前置事件与后续 A 结构确认的时间差，以及 4 小时路径退出模拟。退出分别按固定 `+10%` 全出与阶梯止盈计算，并同时给出 `0% / 0.3% / 0.5%` 三档假设往返成本的净收益与 profit factor；端点缺失不当作成交。该结果仍是采样回放，不包含真实滑点、手续费和成交概率。

页面通过 `GET /api/early-warning` 输出当前代币的前置状态和同口径指标。该接口只读取最近 31 分钟快照，计算结果用于排序和“预测观察”展示，不改变 A/B/C 结构确认，也不输出买入指令。

## Factor v1 只读回放

```bash
MONITOR_DATABASE_PATH=/absolute/path/consistent-copy.db ./rust-dashboard/target/release/tmm factor-v1-replay
```

`factor-v1-replay` 以只读方式从 `snapshots` 生成 6 个研究因子：5m 动量、15m 动量、30m 突破、30m 相对成交量、5m 买入占比和 30m 流动性稳定性。每个 token 固定使用回放期内历史峰值流动性最高的池，避免中途换池造成价格断点。报告输出因子覆盖、分位数、与 15m/1h 采样收益的相关性，以及最强的突破和放量样本。该模式不写数据库、不请求行情、不改变现有信号或页面，仅用于离线研究。

## 结构面板

页面提供 `GET /api/structure?limit=100` 与 `GET /api/structure?token=0x...`，返回 token 的结构阶段、数据状态、风险状态、6 个结构因子和阶段时间线。列表页按结构阶段筛选，并在每个代币下显示“阶段 · 数据状态 · 关键因子”；详情页展示结构结论卡、因子条和阶段时间线。所有因子均带中文悬停说明，且旧分数与排序保持不变。

## 云端部署

先将待发布代码推送到 GitHub `main`，然后在本地运行：

```bash
./deploy-production.sh
```

服务器首次从 GitHub 克隆到 `.source`，以后执行 `git pull --ff-only`，在服务器编译并重启 `tmm.service`。运行配置 `.env`、代理配置 `.private/` 和数据库 `data/` 位于源码目录之外。脚本仍会从本地 `.env` 同步 `BSC_DEPTH_RPC_URLS`，并从本地代理订阅更新服务器代理；这些凭据不会进入 Git。`TMM_OBSERVE_MINUTES=0` 可跳过部署后的 30 分钟观察。

通用服务器可运行 `./deploy-cloud.sh --dry-run user@host /absolute/path` 查看部署计划；默认模式会上传必要源码，传入 `--git-source https://github.com/owner/repo.git` 则改为服务器拉取。

日志由 systemd/journald 收集：

```bash
ssh ubuntu@43.131.242.70 'sudo journalctl -u tmm.service -f -n 100'
```

## 检查

```bash
cargo test --manifest-path rust-dashboard/Cargo.toml
node rust-dashboard/static/detail.test.cjs
bash deploy-cloud.test.sh
```

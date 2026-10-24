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

## 云端部署

先将待发布代码推送到 GitHub `main`，然后在本地运行：

```bash
./deploy-production.sh
```

服务器首次从 GitHub 克隆到 `.source`，以后执行 `git pull --ff-only`，在服务器编译并重启 `tmm.service`。运行配置 `.env`、代理配置 `.private/` 和数据库 `data/` 位于源码目录之外。脚本仍会从本地 `.env` 同步 `BSC_DEPTH_RPC_URLS`，并从本地代理订阅更新服务器代理；这些凭据不会进入 Git。`TMM_OBSERVE_MINUTES=0` 可跳过部署后的 30 分钟观察。

通用服务器可运行 `./deploy-cloud.sh --dry-run user@host /absolute/path` 查看部署计划；默认模式会上传必要源码，传入 `--git-source https://github.com/owner/repo.git` 则改为服务器拉取。

日志由 systemd/journald 收集：

```bash
ssh ubuntu@43.133.91.45 'sudo journalctl -u tmm.service -f -n 100'
```

## 检查

```bash
cargo test --manifest-path rust-dashboard/Cargo.toml
node rust-dashboard/static/detail.test.cjs
bash deploy-cloud.test.sh
```

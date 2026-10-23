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

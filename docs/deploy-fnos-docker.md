# fnOS Docker 部署指南（Rust 版）

本文档描述如何通过 fnOS 网页管理界面部署 Rust 版 Token Meme Monitor。

## 架构

```
┌─────────────── fnOS NAS ────────────────┐
│                                          │
│  ┌───────── tmm 容器 (~15MB) ─────────┐ │
│  │  Worker: 轮询 API → 计算信号       │ │
│  │  Dashboard: HTTP 界面 + JSON API   │ │
│  │  SQLite: WAL 模式读写              │ │
│  └───────────────┬────────────────────┘ │
│                  ▼                       │
│  /vol2/.../data/monitor.db               │
│                  ▲                       │
│           clash 容器（出站代理）          │
└──────────────┬───────────────────────────┘
               ▼ 端口 8501
         浏览器 / fnOS 反向代理
```

## 与 Python 版对比

| | Python 版 | Rust 版 |
| --- | --- | --- |
| 容器数 | 2 (worker + dashboard) | **1** |
| 总内存 | ~490MB | **~15MB** |
| 镜像大小 | ~400MB | **~25MB** |
| 响应速度 | 200-800ms | **<1ms** |
| 启动时间 | 5-15s | **<100ms** |
| 依赖 | Python + pip + streamlit + pandas + web3 | **零运行时依赖** |

## 部署步骤

### 1. 配置 Docker 镜像加速（仅需一次）

fnOS 管理界面 → Docker → 设置 → 镜像加速，添加国内加速地址。

### 2. 确认 Clash 代理

在 `.env` 中设置（替换成实际地址）：
```
HTTP_PROXY=http://NAS内网IP:7890
HTTPS_PROXY=http://NAS内网IP:7890
```

### 3. 上传项目文件

将以下文件/目录上传到 NAS 项目路径：
```
token-meme-monitor/
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .env（含代理配置）
└── rust-dashboard/
    ├── Cargo.toml
    ├── Cargo.lock
    ├── src/
    └── static/
```

### 4. 创建 Docker 项目

fnOS → Docker → 项目 → 创建项目：
- 路径：`/vol2/1000/docker/docker-ubuntu/run_app/token-meme-monitor`
- 来源：本地 docker-compose.yml

### 5. 验证

构建完成后，容器列表应显示 `tmm` 在运行。

浏览器打开 `http://NAS内网IP:8501` 看到监控面板即部署成功。

## 外网访问

fnOS → 反向代理 → 添加规则：
- 外部：飞牛公网地址
- 内部：`http://127.0.0.1:8501`

## 内存预算

```
4GB NAS
├── fnOS 系统 + Docker daemon     ~1.8GB
├── Clash 容器                    ~200MB
├── tmm 容器 (Rust 全栈)           ~15MB
└── 剩余 ~2.0GB                   极其宽裕
```

## 数据迁移

Rust 版使用与 Python 版相同的 SQLite schema。迁移时：
1. 保留 `data/` 目录不动
2. 用新 Docker 镜像启动
3. 历史数据自动可用

## 常见问题

### 构建时 Rust 编译很慢

Rust 首次编译需要下载 crates 并编译，预计 5-15 分钟（取决于网络）。后续增量构建会快很多。

### worker 不出数据

检查 `.env` 中 `HTTP_PROXY` 是否指向可达的 Clash 地址。查看容器日志确认 API 请求是否成功。

### dashboard 打不开

确认端口映射 `8501:8501`，且 `DASHBOARD_PORT=8501` 环境变量已设置。

# Depth Liquidity Priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让页面前 50 个代币的主要流动性池优先刷新，并限制后台并发。

**Architecture:** `dashboard.rs` 从已有概览选出各代币累计 95% 流动性的池子，剩余池子轮转。后台用 `JoinSet` 最多等待两个刷新任务，继续复用现有缓存和按需请求路径。

**Tech Stack:** Rust、Tokio、现有 SQLite/HTTP 服务。

**Spec:** `docs/superpowers/specs/2026-10-23-depth-liquidity-priority-design.md`

## Global Constraints

- 只在本地服务验证，不部署云端。
- 不增加依赖；失败仅重试失败池，缓存未到期不重刷。
- 页面按需请求保留一个深度执行位。

## Review Focus

- 同代币的 PancakeSwap 与 Uniswap 池都贡献主要流动性时均被选中。
- 小池不能饿死，轮转游标跨批次前进。
- 无效价格或流动性不能占用昂贵的 RPC 查询。
- 页面请求与后台同时到来时仍有一个执行位。
- 失败池的退避不能拖住同批其他池子。

---

### Task 1: 按流动性分层候选

**Files:** `rust-dashboard/src/dashboard.rs`

**Interfaces:** `depth_warm_candidates(rows: &[Value]) -> (Vec<String>, Vec<String>)`，返回优先池与轮转池。

- [ ] 写测试：两个主要流动性池都入优先队列；小池入轮转队列；无效价格和零流动性不入队列；只包含页面前 50 个代币。
- [ ] 运行 `cargo test depth_warm_candidates`，确认因功能缺失失败。
- [ ] 使用页面列表相同的排序选代币，每币按流动性降序取到累计至少 95%。
- [ ] 运行定向测试和 `cargo test`，确认通过。

### Task 2: 有界并发刷新

**Files:** `rust-dashboard/src/dashboard.rs`

**Interfaces:** `warm_depth` 继续调用 `token_depth`；后台最多两个未完成池子，总深度槽位为三个。

- [ ] 写测试：任务执行上限为两个，且等待后可继续处理；现有缓存未到期测试仍适用。
- [ ] 运行定向测试，确认失败。
- [ ] 使用优先池 240 秒、轮转池 900 秒到期时间，并用 `JoinSet` 最多并行两个；增加一位总深度槽位。
- [ ] 运行定向测试及 `cargo test`，确认通过。
- [ ] 本地编译、重启服务，比较批次耗时、重要池的过期数与 RPC 错误。

### Task 3: 备用 BSC RPC 与限流切换

**Files:** `rust-dashboard/src/depth.rs`

**Interfaces:** 每个 `calculate` 尝试独立的 RPC 节点与区块；现有深度结果 JSON 结构不变。

- [ ] 写测试：轮询选择可用节点，429 后短暂跳过；普通合约 revert 不标记节点故障。
- [ ] 运行定向测试，确认失败。
- [ ] 复用现有 reqwest client；配置已验证的官方列表端点，按池轮询，遇到可重试错误时冷却并从下一节点重算整池。
- [ ] 运行定向测试及 `cargo test`，确认通过。
- [ ] 本地编译、重启，检查深度刷新耗时、过期主池数量和限流日志。

# 可信候选展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让交易者在名单和详情中分清活跃度、主池行情风险，以及每项数据的时间和覆盖范围。

**Architecture:** 复用现有数据库字段与缓存结果，只给轻量列表和详情增加必要字段；页面按原有合并池逻辑展示，不改变评分或后台任务。

**Tech Stack:** Rust/Axum/SQLite，单文件 HTML/JavaScript，Rust 单元测试与 Node assert 测试。

**Spec:** `docs/superpowers/specs/2026-10-23-trusted-candidate-display-design.md`

## Global Constraints

- 保留当前工作区大量未提交的迁移改动，不整体暂存或提交。
- 不新增依赖、外部请求、数据库结构或评分规则。
- 所有时间均来自实际数据；未知时间显示未知，不能使用页面请求时刻替代。

## Review Focus

- 评分池和流动性主池不同，风险提示必须属于主池。
- 本地缓存名单和服务器名单均使用行情快照时间。
- 零笔交易与请求失败、部分池失败、沿用旧值明确区分。
- 时间缺失或格式异常不显示为刚更新。
- 窄屏中增加的文案不挤压列表和指标。

---

### Task 1: API 暴露时间与风险

**Files:** `rust-dashboard/src/db.rs`, `rust-dashboard/src/dashboard.rs`

**Interfaces:** `/api/tokens[].observed_at` 为该候选评分池快照时间；`/api/token/{token}/summary[].signal_risk_flags` 为同一池信号风险标记。

- [x] 在现有 overview 测试添加两个字段的断言并运行目标测试，确认先失败。
- [x] SQL 读取 `s.risk_flags`，列表保留评分池的 `observed_at`，详情白名单保留风险字段。
- [x] 目标测试与 `cargo test` 通过。

### Task 2: 列表与主池风险提示

**Files:** `rust-dashboard/static/index.html`, `rust-dashboard/static/detail.test.cjs`

**Interfaces:** 列表和详情显示“活跃度分”；名单提示取候选快照最早时间；详情风险取 `mergeTokens` 的主池字段。

- [x] 添加真实快照时间、主池风险和异池评分的断言，确认 Node 测试先失败。
- [x] 实现文案与风险标签映射，兼容风险缺失与时间无效。
- [x] Node 测试通过。

### Task 3: 指标状态与样本信息

**Files:** `rust-dashboard/static/index.html`, `rust-dashboard/static/detail.test.cjs`

**Interfaces:** 深度、大额交易显示池数、计算时间与旧值状态；转账显示实际计算时间；OI 保留其采样时间。

- [x] 添加部分成功、旧值、零笔和失败区分的断言，确认 Node 测试先失败。
- [x] 去掉固定刷新承诺，按 API 结果显示真实时间与覆盖。
- [x] Node 测试、Rust 测试、格式检查及本地 HTTP 接口通过。浏览器运行面在本环境不可用，未完成实际渲染检查。

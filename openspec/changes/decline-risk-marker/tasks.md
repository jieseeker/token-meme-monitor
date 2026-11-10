# Decline Risk Marker Implementation Plan

> 执行方式为 Superpowers executing-plans，单人实现与最终独立审查。当前功能分支保护已有未跟踪目录。

**Goal**
为 ON 类急跌和大幅回撤代币提供可解释、自动更新的风险标记。

**Architecture**
复用列表与详情行情。列表接口新增两个涨跌字段，前端共用 priceDeclineRisk(token) 输出命中原因。展示依据优先使用 tokenData 中同地址列表行情。

**Tech Stack**
Rust、现有浏览器 JavaScript、Node assert、Cargo test。

**Spec**
同目录 design.md。

## Global Constraints

保留既有买点规则、列表范围、颜色体系和观察记录。无新增依赖。

## Review Focus

缺失与非有限数值、未来与过期时间、短时反弹、阈值恢复、列表与多池详情一致性。

## Task 1 — 接口与风险标识

- [x] 在 dashboard.rs 的列表接口测试中断言两个涨跌字段可用，运行定向 Cargo 测试并确认失败。
- [x] 在 detail.test.cjs 中通过真实渲染函数覆盖风险币标记、短时反弹保留、恢复清除、旧缓存和时间异常，运行 Node 检查并确认失败。
- [x] list_overview 返回新增字段。priceDeclineRisk(token) 对有效新鲜行情按 24h≤−20% 或 1h≤−10% 生成原因。列表标记和详情提示共用判断，详情渲染缓存包含风险结果。
- [x] 运行 cargo test、Node 详情回归和 diff 检查，检查全部输出。
- [x] 使用真实云端数据在本地预览检查 ON、其他命中币、未命中币及桌面/窄屏，进行最终独立代码审查。

## Release verification

提交、合并推送并通过现有发布脚本部署。核对云端提交、HTML、列表接口新增字段、8 项服务及 ON 实际标识。发布结果由交付记录报告。

## Execution ledger

规则和展示设计由代理按用户完整交付授权决定，当前阈值为初始启发式。已有未跟踪 monitoring 目录保持独立。

定向 Rust 检查先因字段缺失失败，Node 检查先因缺少可见标记失败。实现后全部 160 项 Rust 检查及 Node 详情回归通过。Impeccable layout 检查无命中。本地预览使用云端真实行情，临时代理从 overview 补齐新接口字段供 UI 验证；接口字段另由真实 Rust 列表函数测试覆盖。ON、AKE 命中，STABLE 未命中，1280、390、320 像素均无横向溢出或控制台错误。

最终独立审查发现刷新失败时旧标记不会按时间清除。补充假时钟与挂起请求回归，先复现失败，再在共享 refresh 入口同步更新已有名单和详情，使网络响应之前即可清除过期标记。

最终修复后 Node 回归、全部 160 项 Rust 检查及 diff/布局检查通过。该修复保留初始加载提示，共用已有列表与详情渲染入口，更新时间判定在网络请求发出之前执行。

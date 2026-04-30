# Agent Instructions

## OpenSpec 与 Superpowers 路由规则

### 定位·

- OpenSpec 管长期项目记忆。`proposal`、`spec`、`design`、`tasks` 留下完整变更档案，可追溯、可归档、可同步 delta spec。
- Superpowers 管开发执行纪律。TDD 铁律、subagent 隔离、code review 门控、系统性调试和验证流程用于保证代码质量。

### 路由规则

| 场景 | 推荐 | 理由 |
| --- | --- | --- |
| 存量逻辑优化/修改 | OpenSpec | `explore` 摸清现状，`spec` 追溯变更，不强推 TDD。 |
| 新功能/新逻辑开发 | Superpowers | `brainstorm` 澄清需求，TDD 保证质量，subagent 隔离执行。 |
| 存量 bug 修复 | OpenSpec explore 或 Superpowers debug | 简单 bug 用 Superpowers；复杂 bug 先 OpenSpec explore。 |
| 大规模重构 | OpenSpec | 影响面大，需 `proposal` + `design` 评估风险。 |
| 小改动（几行修 bug/改配置） | 都不走 | 直接改，不强行套流程。 |

判断模糊时看变更主体：

- 新代码占比大，用 Superpowers。
- 改旧代码占比大，用 OpenSpec。

### 避坑规则

1. 小改动不强行 OpenSpec。几行 bugfix、改个配置、加个日志，直接做。
2. 有 OpenSpec 时不重复写 Superpowers 文档。两套文档并行只会散掉。
3. OpenSpec 下仍可借用 Superpowers 的执行纪律。TDD、code review、验证等纪律仍然适用，只是不生成 Superpowers 文档。

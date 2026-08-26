# Context Compression

## 定位

ContextManager 负责“按预算渲染”，`pico/compaction.py` 负责“何时压缩、
用哪些策略压缩、压缩了多少”。两者分离后，压缩的时机和方式可以独立配置、
测试和做 ablation。

## 触发时机

| trigger | 条件 | 入口 |
| --- | --- | --- |
| `budget_pressure` | 历史条数超过最近窗口且估算 prompt 超过 total_budget * threshold（默认 0.8） | AgentLoop 发请求前 |
| `step_interval` | tool_steps 达到步数间隔（默认 8） | AgentLoop 发请求前 |
| `manual` | 用户执行 `/compact` | CLI |
| 超预算兜底 | 压缩后仍超预算 | ContextManager 原有 reduction loop |

命中后先压缩、再重建 prompt，模型这一轮就能看到折叠后的摘要。

## 压缩方式

默认策略是 running summary：把最近窗口（默认 6 条）之外的历史条目折叠成
最多 3 行摘要，摘要缓存在 `session["compaction"]`，按 `summarized_through`
索引增量补算，不每轮全量重建。摘要条目保留用户请求、工具名与路径、
run_shell 的 exit_code 和首行输出、plan 更新等结论性信息。

重复 read 折叠、文件摘要复用、工具结果截断仍由 ContextManager 负责，
最后的 section 尾裁仍是兜底，行为与之前兼容。

## 设计约束

- 非破坏性：压缩只影响 prompt 视图，`session["history"]` 始终完整，
  run artifacts 的 trace 不受影响。
- token 估算：`TokenEstimator` 按 ASCII 约 4 字符/token、CJK 约
  1 字符/token 估算，并用 provider 返回的 `input_tokens` 校准缩放
  （缩放钳制在 0.5x-2x），避免单次异常数据带偏。
- feature flag `compaction`（默认开）关闭时不做任何压缩，也不渲染摘要。

## 可观测性

每次压缩写 `context_compacted` trace：trigger、strategies、entries_folded、
summary_chars、估算前后 tokens、recent_window。`report.json` 的
`compaction` 字段聚合触发次数、折叠条数、摘要行数与字符数，供
compaction on/off 和阈值 ablation 使用。

## 测试映射

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_compaction.py` | 估算与校准、状态归一化、摘要构建与增量、策略触发、决策指标 |
| `tests/test_context_manager.py` | 摘要渲染、窗口外条目跳过、metadata |
| `tests/test_agent_loop.py` | step_interval / budget_pressure / manual / 关闭开关、非破坏性 |

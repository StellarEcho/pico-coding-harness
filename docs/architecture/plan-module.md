# Plan Module

## 定位

Plan 是 agent 的“当前任务工作区”：一个用户请求对应一份不超过 6 步的短期计划。
计划不是长时记忆，也不替代 checkpoint；它解决的是多步任务里“模型每轮都重新
推断自己在做什么”的决策损耗。

## 运行时数据流

```text
用户请求
  -> AgentLoop.run()
     -> ensure_plan_for_request()      # 新请求重置，同一请求保留
     -> 每轮 ContextManager.build()
        -> plan section（relevant_memory 与 history 之间）
  -> 模型调用 update_plan 工具
     -> PlanManager.apply()            # 纯内存状态变更
     -> session_store.save()           # 随 session 持久化
  -> checkpoint.create_checkpoint()
     -> next_step = plan.next_pending() # resume 时直接续跑
```

## 设计约束

- 计划状态是普通 dict，`normalize_plan_state()` 是唯一入口，旧 session
  和未来 schema 变化都通过它收敛，不破坏已保存会话。
- `update_plan` 是纯内存工具（`risky=False`），不触碰文件系统，
  但仍消耗一次模型轮次，默认会计入 `tool_steps`。
- plan section 参与 ContextManager 的预算与压缩：`reduction_order` 中
  plan 排在 history 之后、memory 之前，优先于长期记忆被压缩。
- feature flag `plan` 关闭时工具从白名单移除（而不是被调用后忽略），
  与 delegate 按 depth 隐藏是同一策略。

## 与其他模块的衔接点

### 上下文压缩（compaction）

plan section 已经有独立的 `raw_chars / rendered_chars / status_counts`
metadata，压缩策略可以把它当作普通 section 处理，也可以特殊化：
计划压缩时优先折叠 note、其次裁剪步骤文本，最后才删步骤。

### Memory / Skills 可插拔

`PlanManager.metrics()` 输出结构化指标（step_count、status_counts、
next_pending），后续 skills 可以读取计划状态来决定注入哪份 prompt 片段；
工具回调模式（`ToolContext.plan_update`）也是未来“纯内存工具”
（例如 memory/skill 操作）的参考样板。

### 评测与回归

- `report.json` 已经包含 `plan` 指标，plan on/off ablation 可以直接聚合。
- benchmark 任务可以声明 `allowed_tools: ["update_plan", ...]`，
  脚本化输出即可确定性验证计划生命周期。

## 测试映射

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_planner.py` | 状态归一化、init 解析、状态迁移、渲染、metrics、往返 |
| `tests/test_tools.py` | 注册、校验、回调执行 |
| `tests/test_context_manager.py` | section 顺序、双渲染路径、预算压缩 |
| `tests/test_agent_loop.py` | 工具接线、prompt 可见性、checkpoint 复用、resume、feature 开关 |
| `tests/test_checkpoint.py` | next_step 复用、plan progress 渲染 |

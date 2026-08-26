# Memory Backends & Skills

## 定位

这一系列改动把“可插拔”落地为三层：

- **MemoryBackend 协议**：runtime 只面向协议编程，默认实现是现有
  `LayeredMemory`（关键词召回），后续 vector / vault 等 backend
  通过注册表接入，不改上层。
- **Skill 注册表**：声明式 manifest（内置 + 工作区
  `.pico/skills/*.json`），安全加载、校验、列举，并把 prompt 片段、
  工具白名单、memory hooks 真正接进 runtime。

## M1：协议与注册表

## MemoryBackend 协议

`pico/features/memory_backends.py`：

- `store(note)`：写入一条记忆（字符串或 dict），返回规范化 note；
- `retrieve(query, limit)`：按相关性召回；
- `delete(key)`：按文本精确删除，返回条数；
- `snapshot()`：可持久化的完整状态；
- `render_text()`：渲染给模型看的视图；
- `backend_id` / `durable`：标识与是否支持长期记忆。

`LayeredMemory` 已补上这些方法（行为不变），注册表
`create_memory_backend("keyword", ...)` 是唯一创建入口，未知名称
直接抛错，不做静默回退。`Pico(memory_backend="keyword")` 可指定。

## Skill 注册表

`pico/features/skills.py`：

- manifest 字段：`skill_id / version / description / prompt_fragment /
  tools / memory_hooks / enabled`；
- 校验：必填字段、id 格式、工具名必须存在于 legal 白名单、prompt 片段
  长度上限（2000 字符）且过 secret 形状检测；
- 加载：内置集合 + 工作区 `.pico/skills/*.json`；单个文件损坏只记录
  `load_errors`，不阻塞 agent 启动；
- `SkillRegistry` 提供 `all / enabled / set_enabled / prompt_fragments /
  tool_names / memory_hooks / render / summary`；
- CLI `/skills` 打印加载数、启用状态、工具与 hooks；`report.json` 的
  `skills` 字段聚合 loaded / enabled / ids / load_errors。

## M1 边界

feature flag `skills` 已加入默认配置，供后续开关。

## M2：混合检索评分与观测

`retrieval_candidates` 从“元组排序”升级为显式打分：

- tag 精确命中 +10；
- 关键词重叠 +2/个（上限 5 个）；
- 新鲜度档位 +0~3（7 天 / 30 天 / 365 天）；
- 平局按时间戳与 note_index 决胜。

新增 `retrieval_candidates_with_metadata`（`LayeredMemory.retrieval_with_metadata`），
每个候选带 `score` 和 `components`。ContextManager 把
`selected_scores / selected_components` 写进 prompt metadata，
trace/report 因此可以回答“这轮为什么召回这几条”。

## M3：技能能力点与 durable 工具化

### 技能三能力

- **prompt_fragment**：ContextManager 新增 `skills` section（plan 与
  history 之间），enabled skill 的片段按预算渲染，参与压缩（优先级
  高于 plan）。
- **tools**：skill 声明的工具名自动并入 `allowed_tools` 联合集合；
  manifest 加载时已保证工具名在 legal 白名单内。
- **memory_hooks**：`after_tool` / `plan_updated` / `context_compacted`
  三个事件；runtime 分发并写 `skill_hook_triggered` trace，默认 handler
  把高价值事件沉淀为 process note。

### durable 记忆工具

新增 `memory_read` / `memory_update` 两个工具（risky=False，feature
flag `memory` 关闭时隐藏）：

- `memory_read`：召回工作记忆 + durable 记忆；
- `memory_update`：`action=add|delete`，操作 4 个固定 durable topic，
  落盘 `.pico/memory/` 的 Markdown 文件。

`LayeredMemory` 增加 `add_durable / remove_durable / durable_topics`，
`DurableMemoryStore` 增加 `remove_topic_note`。

## 测试映射

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_memory_backends.py` | 协议一致性、store/retrieve/delete/snapshot、durable、注册表与未知名称 |
| `tests/test_skills.py` | manifest 校验、错误收集、启用过滤、能力枚举、runtime 加载与 report |
| `tests/test_memory.py` | 混合评分组件、durable add/remove |
| `tests/test_tools.py` | memory 工具注册、校验、回调 |
| `tests/test_context_manager.py` | skills section 渲染与预算、检索分数元数据 |
| `tests/test_agent_loop.py` | memory 工具集成、feature 开关、三个 skill hook |

# Memory Backends & Skills（M1）

## 定位

M1 把两块“可插拔”的接口骨架立起来：

- **MemoryBackend 协议**：runtime 只面向协议编程，默认实现是现有
  `LayeredMemory`（关键词召回），后续 vector / vault 等 backend
  通过注册表接入，不改上层。
- **Skill 注册表**：声明式 manifest（内置 + 工作区
  `.pico/skills/*.json`），M1 只负责安全加载、校验和列举，
  真正的 prompt 注入 / 工具合并 / 事件订阅在 M3 接入。

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

prompt 片段注入、skill 工具合并、memory hooks 事件订阅属于 M3，
M1 只保证这些能力被安全地校验和枚举。feature flag `skills` 已加入
默认配置，供后续开关。

## 测试映射

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_memory_backends.py` | 协议一致性、store/retrieve/delete/snapshot、durable、注册表与未知名称 |
| `tests/test_skills.py` | manifest 校验、错误收集、启用过滤、能力枚举、runtime 加载与 report |

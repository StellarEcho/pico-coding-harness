"""Memory backend 协议与注册表。

M1 阶段的目标是把“接口”和“实现”分开：runtime 只面向
``MemoryBackend`` 协议编程，默认实现是现有的 ``LayeredMemory``
（关键词召回），后续可以注册 vector / vault 等新 backend 而不改上层。

协议方法刻意保持小：

- ``store(note)``：写入一条记忆（字符串或 dict），返回规范化后的 note；
- ``retrieve(query, limit)``：按相关性召回，返回 note dict 列表；
- ``delete(key)``：删除匹配的记忆，返回删除条数；
- ``snapshot()``：返回可持久化的完整状态；
- ``render_text()``：渲染给模型看的紧凑视图。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .memory import LayeredMemory


@runtime_checkable
class MemoryBackend(Protocol):
    backend_id: str
    durable: bool

    def store(self, note) -> dict: ...

    def retrieve(self, query, limit=3) -> list: ...

    def delete(self, key) -> int: ...

    def snapshot(self) -> dict: ...

    def render_text(self) -> str: ...


MEMORY_BACKENDS = {
    "keyword": LayeredMemory,
}


def available_memory_backends():
    return sorted(MEMORY_BACKENDS)


def create_memory_backend(name, workspace_root=None, state=None):
    """按名称创建 memory backend；未知名称直接抛错，避免静默回退掩盖配置错误。"""
    normalized = str(name or "keyword").strip().lower()
    backend_cls = MEMORY_BACKENDS.get(normalized)
    if backend_cls is None:
        choices = ", ".join(available_memory_backends())
        raise ValueError(f"unknown memory backend: {normalized}. expected one of: {choices}")
    return backend_cls(state, workspace_root=workspace_root)

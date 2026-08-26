"""Narrow context passed from runtime into tool functions."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class ToolContext:
    root: Path
    path_resolver: Callable[[str], Path]
    shell_env_provider: Callable[[], dict]
    depth: int
    max_depth: int
    spawn_delegate: Callable[[dict], str]
    # 计划工具的回调。由 runtime 注入，这样 tools.py 不需要反向依赖 runtime。
    # 后续其他“纯内存”工具（例如 memory/skill 操作）可以按同样方式扩展。
    plan_update: Callable[[dict], str] | None = None

    def path(self, raw_path):
        return self.path_resolver(str(raw_path))

    def shell_env(self):
        return self.shell_env_provider()

"""Skill 注册表骨架（M1）。

M1 只做“加载 + 校验 + 列举”：skill 是声明式的 manifest，来源是内置集合
和工作区 ``.pico/skills/*.json``。manifest 里可以声明 prompt 片段、
工具名和 memory hooks，但真正注入 prompt / 合并工具 / 订阅事件属于 M3，
这一层只保证它们被安全地校验和枚举。

安全约束从第一步就立住：

- 工具名必须存在于现有 legal 白名单，不能让 skill 绕过 ToolExecutor 护栏；
- prompt 片段有长度上限，且做 secret 形状检测，避免技能把敏感文本带进 prompt；
- 单个损坏的 skill 文件不会让 agent 启动失败，错误会进入 ``load_errors``。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..security import SECRET_SHAPED_TEXT_PATTERN
from ..tools import legal_tool_names

SKILLS_DIR_NAME = "skills"
SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
REQUIRED_SKILL_FIELDS = ("skill_id", "version", "description")
MAX_PROMPT_FRAGMENT_CHARS = 2000
MAX_SKILL_TOOLS = 8


@dataclass(frozen=True)
class Skill:
    skill_id: str
    version: str
    description: str
    prompt_fragment: str = ""
    tools: tuple = ()
    memory_hooks: tuple = ()
    enabled: bool = True
    source: str = ""

    def to_dict(self):
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "description": self.description,
            "prompt_fragment": self.prompt_fragment,
            "tools": list(self.tools),
            "memory_hooks": list(self.memory_hooks),
            "enabled": self.enabled,
            "source": self.source,
        }


def validate_skill(payload, source=""):
    """校验 manifest，返回 Skill；不合法直接抛 ValueError。"""
    payload = payload or {}
    errors = []
    for field in REQUIRED_SKILL_FIELDS:
        if not str(payload.get(field, "") or "").strip():
            errors.append(f"{field} is required")

    skill_id = str(payload.get("skill_id", "") or "").strip()
    if skill_id and not SKILL_ID_PATTERN.match(skill_id):
        errors.append("skill_id must match [a-z0-9][a-z0-9_-]*")

    fragment = str(payload.get("prompt_fragment", "") or "")
    if len(fragment) > MAX_PROMPT_FRAGMENT_CHARS:
        errors.append(f"prompt_fragment exceeds {MAX_PROMPT_FRAGMENT_CHARS} chars")
    if fragment and SECRET_SHAPED_TEXT_PATTERN.search(fragment):
        errors.append("prompt_fragment contains secret-shaped text")

    raw_tools = payload.get("tools", []) or []
    if not isinstance(raw_tools, list):
        errors.append("tools must be a list")
        raw_tools = []
    tools = tuple(str(name).strip() for name in raw_tools if str(name).strip())
    if len(tools) > MAX_SKILL_TOOLS:
        errors.append(f"tools exceeds {MAX_SKILL_TOOLS} entries")
    unknown = [name for name in tools if name not in legal_tool_names()]
    if unknown:
        errors.append(f"tools reference unknown names: {', '.join(sorted(unknown))}")

    raw_hooks = payload.get("memory_hooks", []) or []
    if not isinstance(raw_hooks, list):
        errors.append("memory_hooks must be a list")
        raw_hooks = []
    memory_hooks = tuple(str(name).strip() for name in raw_hooks if str(name).strip())

    if errors:
        location = f" ({source})" if source else ""
        raise ValueError(f"invalid skill manifest{location}: {'; '.join(errors)}")

    return Skill(
        skill_id=skill_id,
        version=str(payload.get("version", "") or "").strip(),
        description=str(payload.get("description", "") or "").strip(),
        prompt_fragment=fragment,
        tools=tools,
        memory_hooks=memory_hooks,
        enabled=bool(payload.get("enabled", True)),
        source=source,
    )


def discover_skill_files(workspace_root):
    if not workspace_root:
        return []
    skills_dir = Path(workspace_root) / ".pico" / SKILLS_DIR_NAME
    if not skills_dir.is_dir():
        return []
    return sorted(path for path in skills_dir.glob("*.json") if path.is_file())


def load_skill_file(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_skill(payload, source=str(path))


BUILTIN_SKILLS = ()


class SkillRegistry:
    def __init__(self, skills=(), load_errors=None):
        self._skills = {skill.skill_id: skill for skill in skills}
        self.load_errors = list(load_errors or [])

    @classmethod
    def load(cls, workspace_root=None):
        skills = list(BUILTIN_SKILLS)
        errors = []
        for path in discover_skill_files(workspace_root):
            try:
                skills.append(load_skill_file(path))
            except (ValueError, json.JSONDecodeError) as exc:
                errors.append(str(exc))
        return cls(skills=skills, load_errors=errors)

    def all(self):
        return [self._skills[skill_id] for skill_id in sorted(self._skills)]

    def get(self, skill_id):
        return self._skills.get(str(skill_id))

    def enabled(self):
        return [skill for skill in self.all() if skill.enabled]

    def set_enabled(self, skill_id, enabled):
        skill = self._skills.get(str(skill_id))
        if skill is None:
            return False
        self._skills[str(skill_id)] = Skill(
            skill_id=skill.skill_id,
            version=skill.version,
            description=skill.description,
            prompt_fragment=skill.prompt_fragment,
            tools=skill.tools,
            memory_hooks=skill.memory_hooks,
            enabled=bool(enabled),
            source=skill.source,
        )
        return True

    def prompt_fragments(self):
        return [skill.prompt_fragment for skill in self.enabled() if skill.prompt_fragment.strip()]

    def tool_names(self):
        names = set()
        for skill in self.enabled():
            names.update(skill.tools)
        return sorted(names)

    def memory_hooks(self):
        hooks = set()
        for skill in self.enabled():
            hooks.update(skill.memory_hooks)
        return sorted(hooks)

    def hooks_for(self, event):
        return sorted(
            skill.skill_id
            for skill in self.enabled()
            if str(event) in skill.memory_hooks
        )

    def render(self):
        lines = [f"Skills: {len(self.all())} loaded, {len(self.enabled())} enabled"]
        for skill in self.all():
            status = "enabled" if skill.enabled else "disabled"
            tools = ", ".join(skill.tools) or "-"
            hooks = ", ".join(skill.memory_hooks) or "-"
            lines.append(f"- {skill.skill_id} v{skill.version} [{status}] {skill.description}")
            lines.append(f"  tools: {tools}")
            lines.append(f"  hooks: {hooks}")
        for error in self.load_errors:
            lines.append(f"- load error: {error}")
        return "\n".join(lines)

    def summary(self):
        return {
            "loaded": len(self.all()),
            "enabled": len(self.enabled()),
            "ids": [skill.skill_id for skill in self.all()],
            "enabled_ids": [skill.skill_id for skill in self.enabled()],
            "tool_names": self.tool_names(),
            "load_errors": list(self.load_errors),
        }

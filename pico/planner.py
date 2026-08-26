"""短期任务计划模块。

计划是 agent 的“当前任务工作区”：一个用户请求对应一份不超过
``PLAN_STEP_LIMIT`` 步的计划，状态保存在 session 里，随会话持久化。
模型通过 ``update_plan`` 工具创建和推进计划，ContextManager 每轮把
计划渲染进 prompt，checkpoint 复用 ``next_pending()`` 作为恢复时的下一步。

这个模块刻意保持小、纯、可测：

- 状态只用一个普通 dict，任何时刻都能 ``to_dict()`` 落盘；
- ``normalize_plan_state`` 负责兼容旧 session 和未来 schema 变化，
  后续加字段时只改归一化函数，不破坏已保存的会话；
- ``metrics()`` 输出结构化的步骤状态计数，供 report/trace 和后续的
  feature ablation（plan on/off）直接消费。
"""

import re

from .workspace import now

PLAN_STEP_LIMIT = 6
PLAN_TEXT_LIMIT = 120
PLAN_TITLE_LIMIT = 120

STEP_STATUSES = ("pending", "in_progress", "done", "blocked", "skipped")
STEP_STATUS_MARKERS = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "done": "[x]",
    "blocked": "[!]",
    "skipped": "[-]",
}

PLAN_ACTIONS = ("init", "update")

DEFAULT_PLACEHOLDER_STEPS = (
    "Understand the request",
    "Investigate the workspace",
    "Implement the change",
    "Verify with tests",
)

# 行首去除编号/圆点/破折号时使用，避免 lstrip 误吞“3-D rendering”这类内容。
_STEP_PREFIX_PATTERN = re.compile(r"^\s*(?:\d+[.)]?\s*|[-*•]\s*)")


def default_plan_state():
    return {
        "request": "",
        "title": "",
        "steps": [],
        "updated_at": "",
    }


def normalize_plan_state(state):
    """把任意来源的 plan 状态归一化成当前 schema。

    旧 session 可能没有 plan、步骤缺 id/status/note，或状态字符串写错，
    这里统一补齐并把 id 重排为连续 1..N，保证运行时只面对一种形状。
    """
    if state is None or not isinstance(state, dict):
        state = default_plan_state()

    request = str(state.get("request", "") or "").strip()
    title = _tail_clip(str(state.get("title", "") or "").strip(), PLAN_TITLE_LIMIT)
    updated_at = str(state.get("updated_at", "") or "").strip()

    steps = []
    for raw_step in state.get("steps", []) or []:
        if isinstance(raw_step, dict):
            text = str(raw_step.get("text", "") or "").strip()
            status = str(raw_step.get("status", "pending") or "pending").strip()
            note = str(raw_step.get("note", "") or "").strip()
        else:
            text = str(raw_step or "").strip()
            status = "pending"
            note = ""
        text = _tail_clip(text, PLAN_TEXT_LIMIT)
        if not text:
            continue
        if status not in STEP_STATUSES:
            status = "pending"
        steps.append({"id": len(steps) + 1, "text": text, "status": status, "note": _tail_clip(note, PLAN_TEXT_LIMIT)})
        if len(steps) >= PLAN_STEP_LIMIT:
            break

    return {
        "request": request,
        "title": title,
        "steps": steps,
        "updated_at": updated_at,
    }


def _tail_clip(text, limit):
    text = str(text)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


class PlanManager:
    def __init__(self, state=None):
        self.state = normalize_plan_state(state)

    def to_dict(self):
        self.state = normalize_plan_state(self.state)
        return {
            "request": self.state["request"],
            "title": self.state["title"],
            "steps": [dict(step) for step in self.state["steps"]],
            "updated_at": self.state["updated_at"],
        }

    def reset(self, request="", title=""):
        self.state = default_plan_state()
        self.state["request"] = str(request or "").strip()
        self.state["title"] = _tail_clip(str(title or "").strip(), PLAN_TITLE_LIMIT)
        self.state["updated_at"] = now()
        return self

    def placeholder(self, request, title=None):
        """模型没有主动建计划时，由 runtime 兜底生成占位计划。

        占位步骤是通用的“理解 -> 调查 -> 实现 -> 验证”，模型看到后
        可以用 action=init 覆盖成更具体的计划，或继续推进这些步骤。
        """
        self.state["request"] = str(request or "").strip()
        self.state["title"] = _tail_clip(str(title or request or "Task").strip(), PLAN_TITLE_LIMIT)
        self.state["steps"] = [
            {"id": index + 1, "text": text, "status": "pending", "note": ""}
            for index, text in enumerate(DEFAULT_PLACEHOLDER_STEPS)
        ]
        self.state["updated_at"] = now()
        return self

    def apply(self, args):
        """处理 ``update_plan`` 工具的 init / update 两种动作，返回渲染文本。"""
        args = dict(args or {})
        action = str(args.get("action", "") or "").strip()
        if action not in PLAN_ACTIONS:
            raise ValueError(f"plan action must be one of: {', '.join(PLAN_ACTIONS)}")
        if action == "init":
            title = str(args.get("title", "") or "").strip()
            plan_text = str(args.get("plan", "") or "").strip()
            if not title:
                raise ValueError("plan init requires a non-empty title")
            if not plan_text:
                raise ValueError("plan init requires a non-empty plan")
            return self._init_from_text(title, plan_text)

        try:
            step_id = int(args.get("step_id", 0) or 0)
        except (TypeError, ValueError):
            raise ValueError("plan update requires an integer step_id") from None
        status = str(args.get("status", "") or "").strip()
        note = str(args.get("note", "") or "").strip()
        return self._update_step(step_id, status, note)

    def _init_from_text(self, title, plan_text):
        steps = []
        for line in plan_text.splitlines():
            text = _STEP_PREFIX_PATTERN.sub("", line.strip())
            text = _tail_clip(text, PLAN_TEXT_LIMIT)
            if not text:
                continue
            steps.append({"id": len(steps) + 1, "text": text, "status": "pending", "note": ""})
            if len(steps) >= PLAN_STEP_LIMIT:
                break
        if not steps:
            raise ValueError("plan init requires at least one step")
        self.state["title"] = _tail_clip(title.strip(), PLAN_TITLE_LIMIT)
        self.state["steps"] = steps
        self.state["updated_at"] = now()
        return self.render()

    def _update_step(self, step_id, status, note=""):
        if not self.state["steps"]:
            raise ValueError("plan has no steps; call update_plan with action=init first")
        if step_id < 1 or step_id > len(self.state["steps"]):
            raise ValueError(f"step_id must be in [1, {len(self.state['steps'])}]")
        if status not in STEP_STATUSES:
            raise ValueError(f"plan step status must be one of: {', '.join(STEP_STATUSES)}")
        step = self.state["steps"][step_id - 1]
        step["status"] = status
        if note:
            step["note"] = _tail_clip(note, PLAN_TEXT_LIMIT)
        self.state["updated_at"] = now()
        return self.render()

    def next_pending(self):
        """返回第一个待推进步骤（pending 或 in_progress），没有则返回 None。"""
        for step in self.state["steps"]:
            if step["status"] in {"pending", "in_progress"}:
                return {"id": step["id"], "text": step["text"]}
        return None

    def render(self, budget=None):
        state = normalize_plan_state(self.state)
        if not state["steps"]:
            return "Plan:\n- none"
        lines = [f"Plan: {state['title'] or '(untitled)'}"]
        for step in state["steps"]:
            marker = STEP_STATUS_MARKERS.get(step["status"], "[ ]")
            line = f"- {marker} {step['id']}. {step['text']}"
            if step.get("note"):
                line += f" -- {step['note']}"
            lines.append(line)
        text = "\n".join(lines)
        if budget is None:
            return text
        return _tail_clip(text, int(budget))

    def metrics(self):
        """供 report / trace / ablation 使用的结构化指标。"""
        state = normalize_plan_state(self.state)
        status_counts = {}
        for step in state["steps"]:
            status_counts[step["status"]] = status_counts.get(step["status"], 0) + 1
        return {
            "title": state["title"],
            "step_count": len(state["steps"]),
            "status_counts": status_counts,
            "next_pending": self.next_pending(),
            "updated_at": state["updated_at"],
        }

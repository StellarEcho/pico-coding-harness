"""上下文压缩策略层。

ContextManager 负责“按预算渲染”，这个模块负责“何时压缩、用哪些策略压缩、
压缩了多少”。两者分离后，压缩的时机和方式都可以独立配置、测试和做 ablation。

设计要点：

- 非破坏性：压缩只影响送进模型的视图，``session["history"]`` 始终保留完整
  事件流，run artifacts 里的 trace 也不受影响。
- 运行摘要缓存：``session["compaction"]`` 保存折叠后的摘要条目和已摘要到的
  历史索引，新事件到达时只增量补算，不每轮全量重建。
- 分层策略：默认先做 running summary，重复 read 折叠等已有逻辑继续留在
  ContextManager，最后的 section 尾裁仍是兜底。
- 可观测：每次压缩返回 ``CompactionDecision``，runtime 写入
  ``context_compacted`` trace，report 聚合触发次数和折叠条数。
"""

import json
import math
import re
from dataclasses import dataclass

from .workspace import now

DEFAULT_TOTAL_BUDGET = 12000
DEFAULT_THRESHOLD = 0.8
DEFAULT_STEP_INTERVAL = 8
DEFAULT_RECENT_WINDOW = 6
MAX_SUMMARY_LINES = 3
SUMMARY_LINE_LIMIT = 120

# 估算参数：ASCII 约 4 字符/token，CJK 等非 ASCII 按 1 字符/token。
ASCII_CHARS_PER_TOKEN = 4.0
ASCII_CHARS_PER_TOKEN_BOUNDS = (2.0, 8.0)
CALIBRATION_SCALE_BOUNDS = (0.5, 2.0)


def _tail_clip(text, limit):
    text = str(text)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


class TokenEstimator:
    """确定性 token 估算器，支持用 provider usage 元数据校准缩放。"""

    def __init__(self):
        self._scale = 1.0

    def estimate(self, text):
        text = str(text)
        ascii_chars = sum(1 for char in text if ord(char) < 0x80)
        non_ascii_chars = len(text) - ascii_chars
        tokens = ascii_chars / ASCII_CHARS_PER_TOKEN + non_ascii_chars
        return max(1, math.ceil(tokens * self._scale))

    def calibrate(self, input_chars, input_tokens):
        """用上一次请求的实际 usage 校准 chars/token 缩放。"""
        try:
            input_chars = int(input_chars)
            input_tokens = int(input_tokens)
        except (TypeError, ValueError):
            return False
        if input_chars <= 0 or input_tokens <= 0:
            return False
        observed_rate = input_chars / input_tokens
        observed_rate = min(max(observed_rate, ASCII_CHARS_PER_TOKEN_BOUNDS[0]), ASCII_CHARS_PER_TOKEN_BOUNDS[1])
        scale = observed_rate / ASCII_CHARS_PER_TOKEN
        self._scale = min(max(scale, CALIBRATION_SCALE_BOUNDS[0]), CALIBRATION_SCALE_BOUNDS[1])
        return True


def default_compaction_state():
    return {
        "summary_entries": [],
        "summarized_through": 0,
        "stats": {"triggers": {}, "entries_folded": 0, "last_trigger": ""},
        "updated_at": "",
    }


def normalize_compaction_state(state):
    if state is None or not isinstance(state, dict):
        state = default_compaction_state()
    entries = []
    for raw in state.get("summary_entries", []) or []:
        line = _tail_clip(str(raw or "").strip(), SUMMARY_LINE_LIMIT)
        if line:
            entries.append(line)
    try:
        summarized_through = max(0, int(state.get("summarized_through", 0) or 0))
    except (TypeError, ValueError):
        summarized_through = 0
    stats = state.get("stats", {}) or {}
    if not isinstance(stats, dict):
        stats = {}
    triggers = stats.get("triggers", {}) or {}
    if not isinstance(triggers, dict):
        triggers = {}
    try:
        entries_folded = max(0, int(stats.get("entries_folded", 0) or 0))
    except (TypeError, ValueError):
        entries_folded = 0
    return {
        "summary_entries": entries[-MAX_SUMMARY_LINES:],
        "summarized_through": summarized_through,
        "stats": {
            "triggers": {str(key): int(value) for key, value in triggers.items()},
            "entries_folded": entries_folded,
            "last_trigger": str(stats.get("last_trigger", "") or ""),
        },
        "updated_at": str(state.get("updated_at", "") or ""),
    }


def _raw_items_text(history):
    lines = []
    for item in history:
        if item.get("role") == "tool":
            lines.append(f"[tool:{item.get('name', '')}] {json.dumps(item.get('args', {}), sort_keys=True)}")
            lines.append(str(item.get("content", "")))
        else:
            lines.append(f"[{item.get('role', '')}] {item.get('content', '')}")
    return "\n".join(lines)


def _first_content_line(content):
    for line in str(content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("exit_code:") or stripped in {"stdout:", "stderr:"}:
            continue
        return _tail_clip(stripped, 60)
    return ""


def _item_summary(item):
    role = str(item.get("role", "") or "").strip()
    content = str(item.get("content", "") or "")
    if role == "tool":
        name = str(item.get("name", "") or "")
        args = item.get("args") or {}
        if name == "run_shell":
            command = str(args.get("command", "") or "")
            exit_code = ""
            match = re.search(r"exit_code:\s*(-?\d+)", content)
            if match:
                exit_code = f" exit={match.group(1)}"
            return _tail_clip(f"run_shell {command}{exit_code} {_first_content_line(content)}".strip(), SUMMARY_LINE_LIMIT)
        if name in {"read_file", "write_file", "patch_file"}:
            return _tail_clip(f"{name} {args.get('path', '')}", SUMMARY_LINE_LIMIT)
        if name == "update_plan":
            action = str(args.get("action", "") or "")
            title = str(args.get("title", "") or "")
            suffix = f" {title}" if title else ""
            return _tail_clip(f"update_plan {action}{suffix}", SUMMARY_LINE_LIMIT)
        if name == "delegate":
            return _tail_clip(f"delegate {args.get('task', '')}", SUMMARY_LINE_LIMIT)
        return _tail_clip(f"{name} {json.dumps(args, sort_keys=True, ensure_ascii=True)}", SUMMARY_LINE_LIMIT)
    return _tail_clip(f"{role}: {content}", SUMMARY_LINE_LIMIT)


class RunningSummaryBuilder:
    """把窗口外历史折叠成最多 3 行摘要，支持按索引增量扩展。"""

    @staticmethod
    def extend(entries, history, start, end):
        entries = list(entries or [])
        start = max(0, int(start or 0))
        end = max(start, min(int(end or 0), len(history)))
        for index in range(start, end):
            line = _item_summary(history[index])
            if line:
                entries.append(line)
        return entries[-MAX_SUMMARY_LINES:]


@dataclass
class CompactionDecision:
    trigger: str
    strategies: list
    entries_folded: int
    total_summary_lines: int
    summary_chars: int
    history_raw_before_chars: int
    history_raw_after_chars: int
    estimated_before_tokens: int
    estimated_after_tokens: int
    recent_window: int

    def to_dict(self):
        return {
            "trigger": self.trigger,
            "strategies": list(self.strategies),
            "entries_folded": self.entries_folded,
            "total_summary_lines": self.total_summary_lines,
            "summary_chars": self.summary_chars,
            "history_raw_before_chars": self.history_raw_before_chars,
            "history_raw_after_chars": self.history_raw_after_chars,
            "estimated_before_tokens": self.estimated_before_tokens,
            "estimated_after_tokens": self.estimated_after_tokens,
            "recent_window": self.recent_window,
        }


class CompactionPolicy:
    def __init__(
        self,
        estimator=None,
        threshold=DEFAULT_THRESHOLD,
        step_interval=DEFAULT_STEP_INTERVAL,
        recent_window=DEFAULT_RECENT_WINDOW,
    ):
        self.estimator = estimator or TokenEstimator()
        self.threshold = float(threshold)
        self.step_interval = max(1, int(step_interval))
        self.recent_window = max(1, int(recent_window))

    def evaluate(self, prompt_metrics, tool_steps=0, manual=False, history_len=0):
        """返回触发原因（budget_pressure / step_interval / manual），未触发返回 None。"""
        if manual:
            return "manual"
        history_len = max(0, int(history_len or 0))
        if prompt_metrics:
            prompt_chars = int(prompt_metrics.get("prompt_chars", 0) or 0)
            budget = int(prompt_metrics.get("prompt_budget_chars", DEFAULT_TOTAL_BUDGET) or DEFAULT_TOTAL_BUDGET)
            if history_len > self.recent_window and prompt_chars > budget * self.threshold:
                return "budget_pressure"
        tool_steps = max(0, int(tool_steps or 0))
        if tool_steps and tool_steps % self.step_interval == 0:
            return "step_interval"
        return None

    def compact(self, history, state, trigger):
        """执行压缩，返回 (new_state, decision)。不改动传入的 history。"""
        history = list(history or [])
        state = normalize_compaction_state(state)
        cutoff = max(0, len(history) - self.recent_window)
        start = min(int(state.get("summarized_through", 0) or 0), cutoff)
        folded_now = max(0, cutoff - start)
        entries = RunningSummaryBuilder.extend(state.get("summary_entries", []), history, start, cutoff)
        summary_text = "\n".join(entries)

        before_raw = _raw_items_text(history)
        after_raw = summary_text
        after_items = history[cutoff:]
        if after_items:
            after_raw = (summary_text + "\n" if summary_text else "") + _raw_items_text(after_items)

        stats = dict(state.get("stats", {}) or {})
        triggers = dict(stats.get("triggers", {}) or {})
        trigger = str(trigger or "unknown")
        triggers[trigger] = int(triggers.get(trigger, 0) or 0) + 1
        stats = {
            "triggers": triggers,
            "entries_folded": int(stats.get("entries_folded", 0) or 0) + folded_now,
            "last_trigger": trigger,
        }
        new_state = {
            "summary_entries": entries,
            "summarized_through": cutoff,
            "stats": stats,
            "updated_at": now(),
        }
        decision = CompactionDecision(
            trigger=trigger,
            strategies=["running_summary"],
            entries_folded=folded_now,
            total_summary_lines=len(entries),
            summary_chars=len(summary_text),
            history_raw_before_chars=len(before_raw),
            history_raw_after_chars=len(after_raw),
            estimated_before_tokens=self.estimator.estimate(before_raw),
            estimated_after_tokens=self.estimator.estimate(after_raw),
            recent_window=self.recent_window,
        )
        return new_state, decision

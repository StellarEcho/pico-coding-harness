# Agent Harness v1 Overview

Agent Harness v1 is Pico's current runtime shape: a local control loop around a model, repository context, constrained tools, task state, memory, and auditable run artifacts.

## Runtime Flow

1. Build workspace context and runtime prefix.
2. Record the user request in session history.
3. Reset or retain the task plan for the request.
4. Create task state for the run.
5. Build bounded prompt context.
6. Request the model response.
7. Parse the response into a tool call, retry notice, or final answer.
8. Execute tools through runtime policy.
9. Write task state, trace events, checkpoints, and report artifacts.

## State Artifacts

- `task_state.json` records attempts, tool steps, status, stop reason, and final answer.
- `trace.jsonl` records the event timeline for prompt, model, tool, checkpoint, and finish phases.
- `report.json` records the review summary, prompt metadata, durable memory changes, and execution metadata.

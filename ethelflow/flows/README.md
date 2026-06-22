# ethelflow/flows

## Overview

A **flow** is an application-level orchestration graph that composes *agents* (microservices) into a higher-level capability (e.g., ingest → chunk → embed → store; retrieve → template → reason; multi-step tool use; human-in-the-loop flows).

In this repository, flows are implemented using **LangGraph** (`StateGraph`). A flow is deployed as part of the **ethelflow** API service and invoked via `POST /flow` (the flow runner loads a flow module and calls its `run(...)` generator).

Most flows in this directory are **debug/test** flows, but they illustrate the standard patterns you should follow when writing new flows.

---

## What a flow is good for

Use a flow when you need to:

- **Chain multiple agents** with explicit state passing (retrieval + reasoning, ingest pipelines, etc.).
- **Branch or fan-out** (e.g., parallel tool execution) and then join results.
- **Enforce consistent contracts** (input validation, JSON-friendly state, routing).
- **Support streaming** (typically only for the final model output).
- **Support interruption/resume** (human-in-the-loop via LangGraph `interrupt`, requiring a checkpointer).

A flow should *not* implement heavy business logic that belongs inside an agent; instead, it should keep orchestration logic clean and rely on the agents' APIs for actual work.

---

## Flow interface contract

Every flow module exposes:

```python
async def run(
    thread_id: uuid.UUID,
    context=None,
    stream: bool = False,
    checkpointer=None,
    command=None,
):
    ...
    yield ...
```

### Inputs

- `thread_id` (UUID): unique run/thread identifier. Used for LangGraph `configurable.thread_id`.
- `context` (dict): primary input payload for the flow.
- `stream` (bool): whether the caller requested streaming output.
- `checkpointer`: required for flows that use `interrupt`/resume patterns.
- `command`: used to resume a paused/interrupted flow.

### Output shape

A flow is an async generator:

- **Non-streaming** (`stream=False`): yield exactly **one** JSON-serializable object (usually a dict).
- **Streaming** (`stream=True`): yield **strings** (chunks) to the client.  
  Avoid yielding complex event dicts unless your `/flow` handler is explicitly built for that format.

> Practical rule: in `stream=True`, only stream the *final* answer text; keep intermediate steps non-streaming.

---

## Flow state: the “TypedDict state machine” pattern

Flows typically define a `TypedDict` state:

- Inputs (copied from `context`)
- Internal/transient fields produced by nodes
- Final outputs

Guidelines:

- Keep state **JSON-friendly**:
  - Use UUIDs as **strings** when passing between nodes that serialize state.
  - If a downstream adapter requires UUID objects, convert at the adapter boundary.
- Fail fast:
  - Validate required fields early (tenant, prompt, document_ids, etc.).
  - Prefer clear `ValueError(...)` messages to avoid silent empty outputs.
- Keep names consistent with adapters:
  - Adapter parameters like `*_key` define which state keys are read/written.
  - When you rename a state key, update adapter wiring.

---

## How flows interact with the system

Flows interact with:

1. **Agents** (microservices) via node adapters (HTTP calls).
2. **Model catalog routing** (indirectly) through agents:
   - Embedding and reasoning agents route by `tenant`, `space`, and/or `inference_class`.
3. **Storage** (indirectly) through agents:
   - DB tables are managed by storage/search agents (store_text/chunks/vectors, search/retrieve).
4. **/flow runner** in `ethelflow`:
   - The `/flow` endpoint runs the generator and returns either a single JSON object or a streaming response.

Important routing note:

- Do **not** assume `FlowRequest.tenant` automatically propagates into `context`.
- **Always include `tenant` inside `context`** and treat it as a required state key for agent calls.

---

## What a “typical” flow looks like

A minimal, systematic structure:

1. **Parse + validate** context → initial state
2. **Build graph** with `StateGraph(StateType)`
3. **Create nodes**:
   - pure Python nodes (small transforms/validation)
   - agent adapters (HTTP nodes)
4. **Wire edges** (entry point → nodes → finish point)
5. **Compile** with `checkpointer` if needed
6. **Run**:
   - non-stream: `await app.ainvoke(...)`
   - stream: `app.astream(...)` or `app.astream_events(...)`, but only yield final strings

Skeleton:

```python
workflow = StateGraph(MyState)

workflow.add_node("step1", step1_fn)
workflow.add_node("agent_call", some_agent_node(...))
workflow.add_edge("step1", "agent_call")

workflow.set_entry_point("step1")
workflow.set_finish_point("agent_call")

app = workflow.compile(checkpointer=checkpointer)
config = {"configurable": {"thread_id": str(thread_id)}}

if stream:
    async for chunk in app.astream(...):
        yield chunk  # usually strings only
else:
    yield await app.ainvoke(...)
```

---

## Input and output requirements

### Required inputs (recommended conventions)

Most flows should require at least:

- `tenant`: non-empty string
- Any flow-specific keys (e.g., `prompt`, `document_id(s)`, etc.)

For retrieval/RAG-like flows (common pattern):

- `document_ids`: list of UUID strings (or UUID objects)
- `prompt`: non-empty string
- Optional:
  - `embedding_space`: override tenant default embedding space
  - `extractor`: e.g. `"file_to_text"`
  - `method`: chunking method label (must match what is stored)
  - `top_k`: integer

### Recommended outputs

Return a single dict in non-stream mode, with:
- `answer` (if the flow performs reasoning)
- helpful debug fields in dev flows (e.g., `chunks`, `final_prompt`, `search_vectors_response`)
- avoid returning raw LangGraph event objects

---

## Streaming: what works reliably

Streaming is the most error-prone part of flows.

### Safe default

- **Do not stream intermediate steps.**
- Compute retrieval/template/etc. non-streaming.
- Stream only the final reasoning output.

Two-phase pattern (recommended):

1. `ainvoke` the graph to produce `final_prompt`
2. Call the reasoning agent with `stream=True` and yield **only text chunks**

This avoids:
- mixed event types in the stream
- partial results that break clients
- non-deterministic intermediate node streaming behavior

### If you must use `astream_events`

Only yield content chunks you care about (strings), and filter out:
- `None` sentinels (end markers)
- chunks from earlier reasoning calls in the same flow
- non-text events

---

## Checkpointing, interrupts, and resume

If you use `langgraph.types.interrupt(...)`, you must:

- compile with a **checkpointer**
- support `command` to resume

Pattern:

- if `command is None`: start fresh from `context`
- else: run `ainvoke(command, ...)`

This is shown in the quiz-like flow pattern (prepare → interrupt → resume → grade).

---

## Common pitfalls and guardrails

- **Missing tenant in context** → routing failures / empty results / 500s  
  Fix: always set `state["tenant"] = context["tenant"]` early.
- **Key mismatches with adapters** → runtime errors  
  Example: `search_vectors_node` expects `query_vector_key`, so pass `query_vector_key="query_embedding"` if your state uses `query_embedding`.
- **UUID typing issues**:
  - Keep state JSON-friendly (UUIDs as strings)
  - Convert to UUID objects only inside adapters/services that require them
- **Chunking/extractor label mismatch**:
  Retrieval requires the same `(extractor, method)` labels used during ingest.  
  If these don’t match, you’ll get *valid but empty* retrieval results.

---

## Writing a new flow: checklist

1. **Name**: create `my_flow.py` with `async def run(...)`.
2. **State**: define a `TypedDict` for inputs + internal fields + outputs.
3. **Validate**: fail fast for required inputs (`tenant`, etc.).
4. **Compose**: use node adapters rather than reimplementing agent logic.
5. **Routing**: always pass `tenant`; optionally `embedding_space` / `reasoning_effort`.
6. **Streaming**: stream only the final answer; keep prep steps non-streaming.
7. **Debugging**:
   - include optional debug keys in non-stream outputs (`chunks`, `final_prompt`)
   - write a small script in `ethelflow/debug/` to call `/flow` with known inputs.

---

## Developer workflow tips

- Start by copying an existing flow that is close to your goal (e.g., retrieval-only vs. ingest vs. reasoning).
- Make state keys explicit and consistent with adapter `*_key` parameters.
- Prefer small Python nodes for:
  - normalization (history formatting, field construction)
  - validation
  - reshaping agent responses into downstream-ready keys

---

## Troubleshooting

If a flow “runs” but returns empty/weak output:

- Verify `tenant` is present in `context` and non-empty.
- Verify retrieval labels: `extractor` and `method` match what was stored.
- Check that embeddings exist for the expected embedding space (catalog route + DB table).
- Temporarily return debug fields in non-stream mode (`final_prompt`, `chunk_ids`, etc.).
- Use the debug scripts in `ethelflow/debug/` to isolate whether the issue is:
  - flow wiring
  - an agent service
  - data availability (stored texts/chunks/vectors)


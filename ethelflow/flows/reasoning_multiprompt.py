import os
import uuid
from typing import Optional, TypedDict

from langgraph.graph import StateGraph

from ethelflow.agents.reasoning.node_adapter import reasoning_node

FLOW_NAME = os.path.splitext(os.path.basename(__file__))[0]


class ReasoningTestState(TypedDict, total=False):
    tenant: str
    prompt_1: str
    prompt_2: str
    reasoning_effort: Optional[str]
    stream: bool
    response_1: str
    response_2: str


async def run(
    thread_id: uuid.UUID,
    context=None,
    stream=False,
    checkpointer=None,
    command=None,
):
    if not context or not isinstance(context, dict):
        raise ValueError("Missing or invalid context dictionary")

    prompt_1 = context.get("prompt_1")
    if not prompt_1:
        raise ValueError("Missing 'prompt_1' in context")

    prompt_2 = context.get("prompt_2")
    if not prompt_2:
        raise ValueError("Missing 'prompt_2' in context")

    reasoning_effort = context.get("reasoning_effort")

    tenant = context.get("tenant")
    if not tenant:
        raise ValueError("Missing 'tenant' in context")

    initial_state: ReasoningTestState = {
        "tenant": tenant,
        "prompt_1": prompt_1,
        "prompt_2": prompt_2,
        "reasoning_effort": reasoning_effort,
        "stream": stream,
    }

    workflow = StateGraph(ReasoningTestState)

    workflow.add_node(
        "reasoning_1",
        reasoning_node(
            prompt_key="prompt_1",
            tenant_key="tenant",
            reasoning_effort_key="reasoning_effort",
            stream_key="stream",
            output_key="response_1",
        ),
    )

    @workflow.add_node
    def prepare_second_prompt(state: ReasoningTestState) -> ReasoningTestState:
        state["prompt_2"] = (state.get("response_1") or "") + f"\n\n\n{state['prompt_2']}"
        return state

    workflow.add_node(
        "reasoning_2",
        reasoning_node(
            prompt_key="prompt_2",
            tenant_key="tenant",
            reasoning_effort_key="reasoning_effort",
            stream_key="stream",
            output_key="response_2",
        ),
    )

    workflow.set_entry_point("reasoning_1")
    workflow.add_edge("reasoning_1", "prepare_second_prompt")
    workflow.add_edge("prepare_second_prompt", "reasoning_2")
    workflow.set_finish_point("reasoning_2")

    app = workflow.compile(checkpointer=checkpointer)
    config = {
        "metadata": {"flow": FLOW_NAME},
        "configurable": {"thread_id": str(thread_id)},
    }

    if stream:
        reasoning_1_done = False
        reasoning_2_done = False
        async for event in app.astream_events(initial_state, config=config, version="v2"):
            if (
                event.get("event") == "on_chain_stream"
                and "chunk" in event.get("data", {})
                and "response_1" in event["data"]["chunk"]
            ):
                chunk = event["data"]["chunk"]["response_1"]
                if reasoning_1_done or chunk is None:
                    reasoning_1_done = True
                    continue
                yield chunk

            if (
                event.get("event") == "on_chain_stream"
                and "chunk" in event.get("data", {})
                and "response_2" in event["data"]["chunk"]
            ):
                chunk = event["data"]["chunk"]["response_2"]
                if reasoning_2_done or chunk is None:
                    reasoning_2_done = True
                    continue
                yield chunk
    else:
        yield await app.ainvoke(initial_state, config=config)


import uuid
from typing import Any, Dict, TypedDict

from langgraph.graph import StateGraph

from ethelflow.agents.executor.models import ExecutionResult
from ethelflow.agents.executor.node_adapter import executor_node
from ethelflow.agents.reasoning.node_adapter import reasoning_node


class MultiMathState(TypedDict, total=False):
    tenant: str
    expression: str
    maxima_script: str
    python_script: str
    r_script: str
    maxima_type: str
    python_type: str
    r_type: str

    maxima_image: str
    python_image: str
    r_image: str

    maxima_results: Dict[str, Any]
    python_results: Dict[str, Any]
    r_results: Dict[str, Any]

    prompt: str

    reasoning_result: str
    reasoning_effort: str
    stream: bool


async def run(
    thread_id: uuid.UUID,
    context=None,
    stream=False,
    checkpointer=None,
    command=None,
):
    if not context or context.get("expression") is None:
        raise ValueError("Missing 'expression' in context")

    state: MultiMathState = {
        "expression": context.get("expression"),
        "tenant" : context.get("tenant"),
        "reasoning_effort": context.get("reasoning_effort", None),
        "maxima_image": "maxima-executor:latest",
        "python_image": "python:3.12-slim",
        "r_image": "r-executor:4.5.2",
        "maxima_type": "maxima",
        "python_type": "python",
        "r_type": "r",
        "stream": stream,
    }

    flow = StateGraph(MultiMathState)

    def normalise(st):
        expr = st.get("expression", "") or ""
        yield {
            "maxima_script": f"display2d:false$\nshowtime:false$\nprint({expr})$\n",
            "python_script": f"print({expr.replace('^', '**')})\n",
            "r_script": f"print({expr})\n",
        }

    flow.add_node("norm", normalise)

    flow.add_node(
        "python",
        executor_node(
            image_key="python_image",
            type_key="python_type",
            code_key="python_script",
            output_key="python_results",
        ),
    )

    flow.add_node(
        "maxima",
        executor_node(
            image_key="maxima_image",
            type_key="maxima_type",
            code_key="maxima_script",
            output_key="maxima_results",
        ),
    )

    flow.add_node(
        "r",
        executor_node(
            image_key="r_image",
            type_key="r_type",
            code_key="r_script",
            output_key="r_results",
        ),
    )

    def build_prompt(st: MultiMathState) -> MultiMathState:
        maxima_result: ExecutionResult = ExecutionResult.model_validate(st.get("maxima_results"))
        python_result: ExecutionResult = ExecutionResult.model_validate(st.get("python_results"))
        r_result: ExecutionResult = ExecutionResult.model_validate(st.get("r_results"))

        prompt = (
            "Here are outputs from math processors. "
            "Do they agree, why or why not? Ignore warning messages from the interpreters\n\n"
            f"Maxima: {maxima_result.stdout}\n"
            f"Python: {python_result.stdout}\n"
            f"R: {r_result.stdout}\n"
        )
        st["prompt"] = prompt
        return st

    flow.add_node("prompt", build_prompt)

    flow.set_entry_point("norm")
    flow.add_edge("norm", "python")
    flow.add_edge("norm", "maxima")
    flow.add_edge("norm", "r")
    flow.add_edge("python", "prompt")
    flow.add_edge("maxima", "prompt")
    flow.add_edge("r", "prompt")

    flow.add_node(
        "compare",
        reasoning_node(
            prompt_key="prompt",
            tenant_key="tenant",
            reasoning_effort_key="reasoning_effort",
            stream_key="stream",
            output_key="reasoning_result",
        ),
    )

    flow.add_edge("prompt", "compare")
    flow.set_finish_point("compare")

    app = flow.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": str(thread_id)}}

    if stream:
        async for item in app.astream_events(state, config=config, version="v2"):
            if item.get("event") == "on_chain_stream":
                chunk = item["data"]["chunk"].get("reasoning_result")
                if chunk is not None:
                    yield chunk
    else:
        yield await app.ainvoke(state, config=config)


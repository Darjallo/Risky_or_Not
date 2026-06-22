import logging
import os
import uuid
from typing import Optional, TypedDict

from langgraph.graph import StateGraph
from langgraph.pregel import Pregel
from langgraph.types import Command, interrupt

from ethelflow.agents.reasoning.node_adapter import reasoning_node

FLOW_NAME = os.path.splitext(os.path.basename(__file__))[0]
logger = logging.getLogger("uvicorn.error")


class QuizState(TypedDict, total=False):
    # routing
    tenant: str
    inference_class: str
    deployment: Optional[str]          # optional override; routing decides otherwise
    reasoning_effort: Optional[str]
    stream: bool

    # quiz content
    topic: str
    topic_prompt: str
    feedback_prompt: str
    question: str
    answer: str
    feedback: str


async def run(
    thread_id: uuid.UUID,
    context=None,
    stream=False,
    command: Command = None,
    checkpointer=None,
):
    if command is not None and checkpointer is None:
        raise ValueError("Checkpointer must be provided when resuming a flow with a command")

    workflow = StateGraph(QuizState)

    # ── start state (only when starting fresh) ────────────────────────────────
    if command is None:
        if not context or not isinstance(context, dict):
            raise ValueError("Missing or invalid context dictionary")

        topic = context.get("topic")
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("Missing or invalid 'topic' in context")

        tenant = context.get("tenant")
        if not isinstance(tenant, str) or not tenant.strip():
            raise ValueError("Missing or invalid 'tenant' in context")

        inference_class = context.get("inference_class") or "reasoning"
        if not isinstance(inference_class, str) or not inference_class.strip():
            raise ValueError("Missing or invalid 'inference_class' in context")

        initial_state: QuizState = {
            "topic": topic.strip(),
            "tenant": tenant.strip(),
            "inference_class": inference_class.strip(),
            "deployment": context.get("deployment"),  # optional override
            "reasoning_effort": context.get("reasoning_effort"),
            "stream": bool(stream),
        }

    # ── nodes ────────────────────────────────────────────────────────────────
    @workflow.add_node
    def prepare_topic_prompt(state: QuizState) -> QuizState:
        state["topic_prompt"] = (
            "You are a strict quiz master.\n\n"
            f"Topic: {state['topic']}\n\n"
            "Task:\n"
            "1) Provide a concise but accurate explanation of the topic.\n"
            "2) Ask ONE challenging, unambiguous question.\n\n"
            "Format exactly:\n"
            "Explanation: ...\n\n"
            "Question: ...\n\n"
            "Do NOT include the answer."
        )
        return state

    workflow.add_node(
        "prepare_quiz",
        reasoning_node(
            tenant_key="tenant",
            prompt_key="topic_prompt",
            reasoning_effort_key="reasoning_effort",
            stream_key=None,  # don't stream this node; easier for interrupt parsing
            output_key="question",
        ),
    )

    @workflow.add_node
    def human_answer(state: QuizState) -> QuizState:
        # interrupt value is the question text shown to the user
        ans = interrupt(state["question"])

        # Guardrail: if resume didn't deliver a real answer, fail loudly
        if not isinstance(ans, str) or not ans.strip():
            raise ValueError("Quiz resume delivered empty/invalid answer (did the client send the correct interrupt id?)")

        state["answer"] = ans.strip()
        return state

    @workflow.add_node
    def prepare_feedback_prompt(state: QuizState) -> QuizState:
        # Another guardrail, just in case
        if not isinstance(state.get("answer"), str) or not state["answer"].strip():
            raise ValueError("Missing/empty 'answer' in state before feedback")

        state["feedback_prompt"] = (
            "You are a strict quiz master grading a single response.\n\n"
            "Here is the question you asked:\n"
            "-----\n"
            f"{state['question']}\n"
            "-----\n\n"
            "Here is the user's answer:\n"
            "-----\n"
            f"{state['answer']}\n"
            "-----\n\n"
            "Now:\n"
            "1) Provide the correct answer.\n"
            "2) Decide correctness. If the user answer is missing, evasive, or unrelated -> Incorrect.\n"
            "3) Provide constructive feedback.\n\n"
            "Format exactly:\n"
            "Correct Answer: ...\n\n"
            "Correctness: Correct|Incorrect\n\n"
            "Feedback: ...\n"
        )
        return state

    workflow.add_node(
        "feedback",
        reasoning_node(
            tenant_key="tenant",
            prompt_key="feedback_prompt",
            reasoning_effort_key="reasoning_effort",
            stream_key=None,
            output_key="feedback",
        ),
    )

    # ── edges ────────────────────────────────────────────────────────────────
    workflow.set_entry_point("prepare_topic_prompt")
    workflow.add_edge("prepare_topic_prompt", "prepare_quiz")
    workflow.add_edge("prepare_quiz", "human_answer")
    workflow.add_edge("human_answer", "prepare_feedback_prompt")
    workflow.add_edge("prepare_feedback_prompt", "feedback")
    workflow.set_finish_point("feedback")

    app: Pregel = workflow.compile(checkpointer=checkpointer)
    config = {
        "metadata": {"flow": FLOW_NAME},
        "configurable": {"thread_id": str(thread_id)},
    }

    input_state: QuizState | Command = command if command is not None else initial_state

    if command is not None:
        logger.info(f"Resuming flow {FLOW_NAME} for run_id: {thread_id}, with command: {command}")

    if stream:
        async for event in app.astream_events(input_state, config=config, version="v2"):
            yield str(event)
    else:
        yield await app.ainvoke(input_state, config=config)


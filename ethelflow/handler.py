from types import ModuleType
from fastapi.responses import StreamingResponse
from langgraph.types import Command, Checkpointer
import uuid


async def handler(
    mod: ModuleType,
    context: dict,
    stream: bool,
    checkpointer: Checkpointer | None = None,
    command: Command | None = None,
    thread_id: uuid.UUID = uuid.uuid4(),
):
    if stream:

        async def stream():
            async for update in mod.run(
                thread_id=thread_id,
                context=context,
                command=command,
                stream=True,
                checkpointer=checkpointer,
            ):
                yield update

        return StreamingResponse(stream(), media_type="application/json")
    else:
        gen = mod.run(
            thread_id=thread_id,
            context=context,
            command=command,
            stream=False,
            checkpointer=checkpointer,
        )

        first = await anext(gen, {})

        return first

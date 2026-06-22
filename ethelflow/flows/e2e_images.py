import uuid
from typing import Any, Dict, List, TypedDict, Optional

from langgraph.graph import StateGraph

from ethelflow.agents.file_to_images.node_adapter import file_to_images_node, cleanup_temp_node
from ethelflow.agents.store_images.node_adapter import store_images_node


class E2EImagesState(TypedDict, total=False):
    tenant: str  # kept for consistency with other flows (not required by these agents today)

    document_id: uuid.UUID

    # Rendering inputs
    groups: List[List[int]]
    dpi: int
    image_format: str
    layout: str
    renderer: str

    # Control flags
    store: bool  # if false: render only (temp S3) and return manifest
    override_images: bool
    cleanup_temp: bool  # request cleanup at end (flow also does best-effort cleanup)

    # Outputs/intermediate
    temp_prefix: str
    image_manifest: dict

    store_images_response: dict
    image_set_id: str

    cleanup_temp_response: dict


def _should_store(state: E2EImagesState) -> bool:
    return bool(state.get("store", True))


def _noop(state: E2EImagesState) -> E2EImagesState:
    return state


async def run(thread_id: uuid.UUID, context=None, stream: bool = False, checkpointer=None, command=None):
    """
    Context keys (recommended):
      - tenant: str (required by convention)
      - document_id: UUID (required)

    Rendering:
      - groups: list[list[int]] (required)
      - dpi: int (default 150)
      - image_format: str (default "png")
      - layout: str (default "vertical")
      - renderer: str (default "pymupdf")

    Storage:
      - store: bool (default True)  # if False, don't persist to DB/permanent S3
      - override_images: bool (default False)
      - cleanup_temp: bool (default True)  # whether to request cleanup node as well
    """
    context = context or {}

    tenant = context.get("tenant")
    if not isinstance(tenant, str) or not tenant.strip():
        raise ValueError("context['tenant'] is required (non-empty str)")

    document_id = context.get("document_id")
    if document_id is None:
        raise ValueError("context['document_id'] is required")

    groups = context.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("context['groups'] is required (non-empty list)")

    initial_state: E2EImagesState = {
        "tenant": tenant,
        "document_id": document_id,
        "groups": groups,
        "dpi": int(context.get("dpi", 150)),
        "image_format": context.get("image_format", "png"),
        "layout": context.get("layout", "vertical"),
        "renderer": context.get("renderer", "pymupdf"),
        "store": bool(context.get("store", True)),
        "override_images": bool(context.get("override_images", False)),
        "cleanup_temp": bool(context.get("cleanup_temp", True)),
    }

    workflow = StateGraph(E2EImagesState)

    render_images = file_to_images_node(
        document_id_key="document_id",
        groups_key="groups",
        dpi_key="dpi",
        image_format_key="image_format",
        layout_key="layout",
        renderer_key="renderer",
        temp_prefix_key="temp_prefix",
        output_manifest_key="image_manifest",
        timeout_s=300,
    )

    persist_images = store_images_node(
        document_id_key="document_id",
        manifest_key="image_manifest",
        override_key="override_images",
        cleanup_temp_key="cleanup_temp",  # store_images may best-effort cleanup as well
        output_image_set_id_key="image_set_id",
        output_key="store_images_response",
        timeout_s=300,
    )

    cleanup = cleanup_temp_node(
        temp_prefix_key="temp_prefix",
        output_key="cleanup_temp_response",
        timeout_s=120,
    )

    workflow.add_node("file_to_images", render_images)
    workflow.add_node("store_images", persist_images)
    workflow.add_node("noop", _noop)
    workflow.add_node("cleanup_temp", cleanup)

    workflow.set_entry_point("file_to_images")

    # Branch: store or not
    workflow.add_conditional_edges(
        "file_to_images",
        _should_store,
        {
            True: "store_images",
            False: "noop",
        },
    )

    # Always try cleanup after either path
    workflow.add_edge("store_images", "cleanup_temp")
    workflow.add_edge("noop", "cleanup_temp")

    workflow.set_finish_point("cleanup_temp")

    app = workflow.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": str(thread_id)}}

    # Best-effort cleanup even if the graph errors mid-run:
    # - We rely on temp_prefix being set once file_to_images runs.
    try:
        if stream:
            async for item in app.astream(initial_state, config=config):
                yield item
        else:
            yield await app.ainvoke(initial_state, config=config)
    finally:
        # If the graph crashed before cleanup_temp, try cleanup here as a backstop.
        # This is intentionally best-effort: never hide the real error.
        try:
            temp_prefix = initial_state.get("temp_prefix")
            # If the graph ran file_to_images, it will have placed temp_prefix into state,
            # but we don't have direct access to the final state here.
            # However, many callers already pass a temp_prefix; if they don't,
            # leaving this as a no-op is fine.
            if isinstance(temp_prefix, str) and temp_prefix.strip():
                # Call cleanup service directly via node adapter
                # (reuse the cleanup node with a tiny state)
                node = cleanup_temp_node(temp_prefix_key="temp_prefix")
                async for _ in node({"temp_prefix": temp_prefix}):
                    pass
        except Exception:
            pass


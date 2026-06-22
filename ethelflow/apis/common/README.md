# ethelflow/apis/common

This package holds **shared FastAPI dependencies** used by multiple API routers in EthelFlow (e.g., `chatapi`, `admin`, future APIs).

Today it contains a single module: `deps.py`.

## Do we still need this?

Yes—unless you want to inline these dependencies in every router.

`apis/common/deps.py` centralizes:

- Access to the **LangGraph Postgres checkpointer** (from `app.state.checkpointer`)
- Construction of the **PodStore** backed by Postgres (via a request-scoped SQLAlchemy session)

Keeping these in one place avoids duplication and makes it harder for APIs to accidentally diverge in how they obtain the checkpointer / PodStore.

If you ever remove the checkpointer or PodStore concept entirely, *then* this package could go away.

## What's inside

### `deps.py`

```py
async def get_checkpointer(request: Request) -> AsyncPostgresSaver
```

- Reads `request.app.state.checkpointer`
- Raises a clear error if it hasn’t been initialized
- Used by routers that need to pass a checkpointer into `ethelflow.handler(...)`

```py
async def get_pod_store(session: AsyncSession = Depends(get_session)) -> PodStore
```

- Uses `ethelflow.data.db_utils.get_session` to get a request-scoped SQLAlchemy session
- Returns a `PostgresPodStore(session=...)` (implements the `PodStore` interface)
- Used by routers that want Pod-based storage (conversation pods, environment pods, etc.)

## Contract / expectations

### App state

Something in the app startup must set:

- `app.state.checkpointer` to an initialized `AsyncPostgresSaver`

If that is missing, calls into endpoints that depend on `get_checkpointer()` will fail fast with:

- `ValueError("Checkpointer is not initialized")`

### Database session lifecycle

`get_pod_store()` depends on `get_session()` to provide an `AsyncSession`.

That function is responsible for:

- creating the session
- committing / rolling back appropriately
- closing the session

(So `apis/common` stays intentionally thin and focused.)

## How routers use it

Typical usage in a router:

```py
from fastapi import Depends
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from ethelflow.apis.common.deps import get_checkpointer, get_pod_store
from ethelflow.data.pods import PodStore

@router.post("/something")
async def endpoint(
    pod_store: PodStore = Depends(get_pod_store),
    checkpointer: AsyncPostgresSaver = Depends(get_checkpointer),
):
    ...
```

## Why this pattern

- **Consistency:** every API gets the same PodStore implementation & checkpointer wiring
- **Testability:** easy to override dependencies in FastAPI tests
- **Separation of concerns:** routers focus on HTTP logic; shared wiring lives here

## Future extensions

As other APIs appear, this is a reasonable place to add common dependencies like:

- tenant resolution (headers + request metadata)
- auth/authz hooks (SpiceDB integration later)
- request correlation IDs / tracing helpers
- common error mappers

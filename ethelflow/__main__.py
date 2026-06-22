import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool


from ethelflow.assets.s3 import s3_manager
from ethelflow.routes.assets import router as assets_router
from ethelflow.routes.flows import router as flows_router
from ethelflow.settings.postgres_settings import postgres_settings

# API-related stuff - environment admin:

from ethelflow.apis.admin.router import router as admin_router

# Add more APIs below:

from ethelflow.apis.chatapi.router import router as chatapi_router

from pathlib import Path
from fastapi.responses import HTMLResponse
import markdown

# from alembic.config import Config
# from alembic import command

logger = logging.getLogger("uvicorn.error")


async def init_checkpointer():
    async with AsyncPostgresSaver.from_conn_string(
        postgres_settings.db_url
    ) as checkpointer:
        await checkpointer.setup()
        logger.info("LangGraph checkpointer setup done")

    pool = AsyncConnectionPool(conninfo=postgres_settings.db_url, open=False)
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    app.state.checkpointer = checkpointer
    app.state.checkpointer_pool = pool


async def teardown_checkpointer():
    pool: AsyncConnectionPool = app.state.checkpointer_pool
    await pool.close()
    logger.info("LangGraph checkpointer PG pool closed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_checkpointer()
    await s3_manager.init()
    yield

    await s3_manager.close()
    await teardown_checkpointer()


app = FastAPI(lifespan=lifespan,
              openapi_tags=[{
                  "name": "Docs",
                  "description": "Upload and manage documents stored in S3 and Postgres."
              },
              {
                  "name": "Flows",
                  "description": """
Typical usage pattern:
1. Start a flow using `/flow/start` to obtain a `run_id`
2. Continue the flow with `/flow/{run_id}/continue` when it requires input
3. Query `/flow/{run_id}/status` or `/flow/{run_id}/history` at any time
4. Attach to a running flow using `/flow/{run_id}/attach` for live updates
""",
              }])


BASE_DIR = Path(__file__).resolve().parent
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def readme():
    readme_path = BASE_DIR / "README.md"
    with open(readme_path, "r", encoding="utf-8") as f:
        md = f.read()
    return markdown.markdown(md, extensions=["fenced_code"])


app.include_router(assets_router)
app.include_router(flows_router)

# API environment admin:

app.include_router(admin_router)

# Any APIs go here:

app.include_router(chatapi_router)

if __name__ == "__main__":
    import uvicorn

    # TODO: run alembic migrations programmatically on startup
    # alembic_cfg = Config()
    # alembic_cfg.set_main_option("script_location", "alembic")
    # alembic_cfg.set_main_option("sqlalchemy.url", postgres_settings.url)
    # command.upgrade(alembic_cfg, "head")

    uvicorn.run(app, host="0.0.0.0", port=8080)

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ethelflow.settings.postgres_settings import postgres_settings

AsyncSessionLocal: sessionmaker[AsyncSession] = sessionmaker(
    create_async_engine(
        postgres_settings.async_url,
        pool_size=20,
        max_overflow=20,
    ),
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_session_ctx():
    async with AsyncSessionLocal() as session:
        yield session


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

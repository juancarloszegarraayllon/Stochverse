import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        print("--- COLUMNS ---")
        result = await conn.execute(text(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema='sp' AND table_name='review_queue' "
            "ORDER BY ordinal_position"
        ))
        for row in result:
            print(row)
        print()
        print("--- INDEXES ---")
        result = await conn.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='sp' AND tablename='review_queue'"
        ))
        for row in result:
            print(row)
    await engine.dispose()

asyncio.run(main())
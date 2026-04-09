"""Create pgvector extension in the test database."""

import asyncio
import asyncpg


async def setup():
    conn = await asyncpg.connect("postgresql://nmem:nmem@localhost:5433/nmem")
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    print("pgvector extension ready")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(setup())

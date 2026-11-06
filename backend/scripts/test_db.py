
import asyncio

from app.db.session import engine
from sqlalchemy import text


async def main():
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT current_database(), current_user, version()")
            )

            row = result.fetchone()

            print("\nSUCCESS: Neon PostgreSQL connected!")
            print("Database:", row[0])
            print("User:", row[1])
            print("Version:", row[2].splitlines()[0])

    except Exception as exc:
        print("\nDATABASE CONNECTION FAILED")
        print(type(exc).__name__, str(exc))

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

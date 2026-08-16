import asyncio

from hermes_chatgpt_web.translation.database import get_db


async def migrate():
    db = await get_db()
    try:
        print("Starting migration...")

        try:
            await db.execute("ALTER TABLE translation_jobs ADD COLUMN source_text_raw TEXT DEFAULT ''")
            print("Added source_text_raw")
        except Exception as e:
            print(f"Error adding source_text_raw: {e}")

        try:
            await db.execute("ALTER TABLE translation_jobs ADD COLUMN source_text_cleaned TEXT DEFAULT ''")
            print("Added source_text_cleaned")
        except Exception as e:
            print(f"Error adding source_text_cleaned: {e}")

        try:
            await db.execute("ALTER TABLE translation_jobs ADD COLUMN raw_response TEXT DEFAULT ''")
            print("Added raw_response")
        except Exception as e:
            print(f"Error adding raw_response: {e}")

        try:
            await db.execute("ALTER TABLE translation_jobs ADD COLUMN cleaned_response TEXT DEFAULT ''")
            print("Added cleaned_response")
        except Exception as e:
            print(f"Error adding cleaned_response: {e}")

        try:
            await db.execute(
                "UPDATE translation_jobs SET source_text_raw = source_text, source_text_cleaned = source_text WHERE source_text_raw = ''"
            )
            print("Migrated source_text data")
        except Exception as e:
            print(f"Error migrating source_text: {e}")

        await db.commit()
        print("Migration complete.")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(migrate())

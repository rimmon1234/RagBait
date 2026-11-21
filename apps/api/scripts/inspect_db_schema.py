"""Inspect local database schema for M3.1 pre-implementation checks."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg

from app.core.config import settings


async def inspect_db() -> None:
    conn = await asyncpg.connect(settings.DATABASE_URL)
    try:
        # 1. Migration History
        migrations = await conn.fetch(
            "SELECT version FROM supabase_migrations.schema_migrations ORDER BY version DESC"
        )
        print("=== DATABASE MIGRATIONS ===")
        for m in migrations:
            print(f" - {m['version']}")

        # 2. Profiles columns and constraints
        profile_cols = await conn.fetch(
            """
            SELECT column_name, data_type, udt_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'profiles'
            ORDER BY ordinal_position
            """
        )
        print("\n=== PUBLIC.PROFILES COLUMNS ===")
        for c in profile_cols:
            print(
                f" - {c['column_name']}: {c['data_type']} ({c['udt_name']}), nullable={c['is_nullable']}, default={c['column_default']}"
            )

        # 3. Enums in public schema
        enums = await conn.fetch(
            """
            SELECT t.typname, e.enumlabel
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE n.nspname = 'public'
            ORDER BY t.typname, e.enumsortorder
            """
        )
        print("\n=== PUBLIC ENUMS ===")
        enum_map = {}
        for e in enums:
            enum_map.setdefault(e["typname"], []).append(e["enumlabel"])
        for name, vals in enum_map.items():
            print(f" - {name}: {vals}")

        # 4. Helper functions in public and app_private schemas
        funcs = await conn.fetch(
            """
            SELECT n.nspname, p.proname, pg_get_functiondef(p.oid) as def
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname IN ('public', 'app_private')
            ORDER BY n.nspname, p.proname
            """
        )
        print("\n=== FUNCTIONS IN public / app_private ===")
        for f in funcs:
            print(f"\n--- {f['nspname']}.{f['proname']} ---")
            print(f["def"][:300] + ("..." if len(f["def"]) > 300 else ""))

        # 5. Existing updated_at triggers
        triggers = await conn.fetch(
            """
            SELECT event_object_table, trigger_name, action_statement
            FROM information_schema.triggers
            WHERE trigger_schema = 'public'
            """
        )
        print("\n=== PUBLIC TRIGGERS ===")
        for t in triggers:
            print(f" - {t['event_object_table']}: {t['trigger_name']} -> {t['action_statement']}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(inspect_db())

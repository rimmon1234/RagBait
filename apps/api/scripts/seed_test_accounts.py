"""Provision and verify Phase 3 test accounts in local development environment.

Configures:
1. Super Admin:              rimmon.bhowmick.cse28@heritageit.edu.in (role: super_admin, status: approved)
2. Approved Contributor:     student.approved@heritageit.edu.in     (role: user,        status: approved)
3. Unapproved Student:       rimmonbhowmick@gmail.com               (role: user,        status: pending)
4. Approved Non-Contributor: student.nocreds@heritageit.edu.in      (role: user,        status: approved)
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
from supabase import Client, create_client

from app.core.config import settings

TEST_ACCOUNTS = [
    {
        "email": "rimmon.bhowmick.cse28@heritageit.edu.in",
        "display_name": "Rimmon Bhowmick (Super Admin)",
        "role": "super_admin",
        "approval_status": "approved",
        "password": "Password123!",
    },
    {
        "email": "student.approved@heritageit.edu.in",
        "display_name": "Alice Approved (Student Contributor)",
        "role": "user",
        "approval_status": "approved",
        "password": "Password123!",
    },
    {
        "email": "rimmonbhowmick@gmail.com",
        "display_name": "Rimmon Unapproved",
        "role": "user",
        "approval_status": "pending",
        "password": "Password123!",
    },
    {
        "email": "student.nocreds@heritageit.edu.in",
        "display_name": "Bob NoCreds (Approved Student)",
        "role": "user",
        "approval_status": "approved",
        "password": "Password123!",
    },
]


async def seed_accounts() -> None:
    if settings.ENVIRONMENT not in ("development", "test"):
        print(
            f"Error: Account seeding is only permitted in dev/test, not '{settings.ENVIRONMENT}'",
            file=sys.stderr,
        )
        sys.exit(1)

    print("=" * 65)
    print("RagBait Phase 3 — Test Account Provisioning & Verification")
    print("=" * 65)

    client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    conn = await asyncpg.connect(settings.DATABASE_URL)

    try:
        for acc in TEST_ACCOUNTS:
            email = acc["email"].lower()
            role = acc["role"]
            status = acc["approval_status"]
            name = acc["display_name"]
            pwd = acc["password"]

            # 1. Check or create in auth.users
            existing_user = await conn.fetchrow(
                "SELECT id FROM auth.users WHERE LOWER(email) = $1", email
            )
            if not existing_user:
                print(f"Creating Auth user for '{email}'...")
                user_res = client.auth.admin.create_user(
                    {
                        "email": email,
                        "password": pwd,
                        "email_confirm": True,
                        "user_metadata": {"display_name": name},
                    }
                )
                user_id = user_res.user.id
            else:
                user_id = str(existing_user["id"])

            # 2. Update profile in public.profiles
            await conn.execute(
                """
                INSERT INTO public.profiles (id, email, display_name, role, approval_status, updated_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (id) DO UPDATE
                SET role = $4,
                    approval_status = $5,
                    display_name = $3,
                    updated_at = NOW()
                """,
                user_id,
                email,
                name,
                role,
                status,
            )

            # 3. Verify
            prof = await conn.fetchrow(
                "SELECT id, email, display_name, role, approval_status FROM public.profiles WHERE id = $1",
                user_id,
            )
            print(
                f"OK: {prof['email']:<42} | Role: {prof['role']:<11} | Status: {prof['approval_status']:<8} | ID: {prof['id']}"
            )

        print("=" * 65)
        print("All 4 Phase 3 Test Accounts Verified!")
        print("=" * 65)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed_accounts())

"""Local development Super Admin bootstrap script.

Safely provisions or elevates a local development user to 'super_admin' with 'approved' status.
Strictly restricted to 'development' and 'test' environments.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg

from app.core.config import settings


async def bootstrap_super_admin(email: str, display_name: str | None = None) -> None:
    """Elevate an existing local auth user to super_admin and record an audit log."""
    print("=" * 60)
    print("RagBait Super Admin Bootstrap Tool")
    print(f"Environment: {settings.ENVIRONMENT}")
    print(f"Target Email: {email}")
    print("=" * 60)

    # 1. Environment Guard: Strictly refuse production or staging
    if settings.ENVIRONMENT not in ("development", "test"):
        print(
            f"SECURITY ERROR: Super admin bootstrap is strictly prohibited in '{settings.ENVIRONMENT}' environment.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Connect to database
    conn = await asyncpg.connect(settings.DATABASE_URL)
    try:
        # 3. Verify user exists in auth.users
        auth_user = await conn.fetchrow(
            "SELECT id, email FROM auth.users WHERE LOWER(email) = LOWER($1)",
            email.strip(),
        )
        if not auth_user:
            print(
                f"ERROR: No registered identity found in auth.users for email: '{email}'.\n"
                f"Please register the user through the web UI or API first before bootstrapping.",
                file=sys.stderr,
            )
            sys.exit(1)

        user_id = auth_user["id"]
        canonical_email = auth_user["email"]
        print(f"Found auth identity: {user_id} ({canonical_email})")

        # 4. Check existing profile or create if trigger didn't fire
        profile = await conn.fetchrow(
            "SELECT id, email, role, approval_status FROM public.profiles WHERE id = $1",
            user_id,
        )

        name_to_use = display_name or (email.split("@")[0].replace(".", " ").title())

        async with conn.transaction():
            if profile:
                print(
                    f"Current profile: role='{profile['role']}', status='{profile['approval_status']}'"
                )
                await conn.execute(
                    """
                    UPDATE public.profiles
                    SET role = 'super_admin',
                        approval_status = 'approved',
                        display_name = COALESCE($2, display_name),
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    user_id,
                    display_name,
                )
            else:
                print("Profile missing in public.profiles. Inserting profile...")
                await conn.execute(
                    """
                    INSERT INTO public.profiles (id, email, display_name, role, approval_status)
                    VALUES ($1, $2, $3, 'super_admin', 'approved')
                    """,
                    user_id,
                    canonical_email,
                    name_to_use,
                )

            # 5. Record immutable audit log entry in public.audit_logs
            audit_payload = json.dumps(
                {
                    "email": canonical_email,
                    "previous_role": profile["role"] if profile else None,
                    "previous_status": profile["approval_status"] if profile else None,
                    "new_role": "super_admin",
                    "new_status": "approved",
                    "environment": settings.ENVIRONMENT,
                    "bootstrapped_by": "bootstrap_super_admin.py",
                }
            )

            await conn.execute(
                """
                INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload, created_at)
                VALUES ($1, 'bootstrap_super_admin', 'profile', $1, $2::jsonb, NOW())
                """,
                user_id,
                audit_payload,
            )

        # 6. Verify updated profile
        updated = await conn.fetchrow(
            "SELECT id, email, display_name, role, approval_status, updated_at FROM public.profiles WHERE id = $1",
            user_id,
        )
        print("=" * 60)
        print("Super Admin Bootstrap SUCCESSFUL!")
        print(f"User ID:        {updated['id']}")
        print(f"Email:          {updated['email']}")
        print(f"Display Name:   {updated['display_name']}")
        print(f"Role:           {updated['role']}")
        print(f"Status:         {updated['approval_status']}")
        print(f"Updated At:     {updated['updated_at']}")
        print("Audit Log Entry: Recorded in public.audit_logs")
        print("=" * 60)

    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap a local development Super Admin.")
    parser.add_argument("--email", required=True, help="Email of the registered user to elevate")
    parser.add_argument(
        "--name", required=False, default=None, help="Optional display name override"
    )
    args = parser.parse_args()

    asyncio.run(bootstrap_super_admin(args.email, args.name))


if __name__ == "__main__":
    main()

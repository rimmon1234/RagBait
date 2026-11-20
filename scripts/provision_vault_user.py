#!/usr/bin/env python3
"""Provision or Rotate Dedicated Credential Vault Login Role.

This script executes an out-of-band operational task to create or update
an environment-specific database LOGIN user and grant it membership in the
least-privilege NOLOGIN capability role `ragbait_vault_service`.

Usage:
    python scripts/provision_vault_user.py --admin-dsn "postgresql://postgres:pass@host:port/db"
    python scripts/provision_vault_user.py --verify-only --vault-dsn "postgresql://user:pass@host:port/db"
"""

import argparse
import asyncio
import os
import secrets
import sys

import asyncpg


def generate_secure_password(length: int = 36) -> str:
    """Generate a high-entropy cryptographically secure password."""
    return secrets.token_urlsafe(length)


async def provision_vault_user(
    admin_dsn: str,
    username: str = "ragbait_vault_user",
    password: str | None = None,
) -> tuple[str, str]:
    """Create or update vault login user and grant capability role membership."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        # 1. Verify capability role exists
        has_capability_role = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ragbait_vault_service' AND NOT rolcanlogin)"
        )
        if not has_capability_role:
            raise RuntimeError(
                "Capability role 'ragbait_vault_service' does not exist as NOLOGIN. "
                "Ensure migration 20260202000000_m2_credential_vault.sql has run."
            )

        # 2. Determine password
        secure_password = password or generate_secure_password()

        # 3. Create or update user
        user_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", username
        )

        escaped_username = username.replace('"', '""')
        # Use parameterized query or safe identifier
        if not user_exists:
            await conn.execute(
                f'CREATE ROLE "{escaped_username}" WITH LOGIN PASSWORD $1 NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;',
                secure_password,
            )
            print(f"[+] Created LOGIN role '{username}'")
        else:
            await conn.execute(
                f'ALTER ROLE "{escaped_username}" WITH LOGIN PASSWORD $1 NOBYPASSRLS;',
                secure_password,
            )
            print(f"[+] Updated password for existing LOGIN role '{username}'")

        # 4. Grant capability role
        await conn.execute(f'GRANT ragbait_vault_service TO "{escaped_username}";')
        print(f"[+] Granted capability role 'ragbait_vault_service' to '{username}'")

        return username, secure_password
    finally:
        await conn.close()


async def verify_vault_user_privileges(vault_dsn: str) -> bool:
    """Verify that the provisioned user can access the vault and is denied unrelated tables."""
    conn = await asyncpg.connect(vault_dsn)
    try:
        current_user = await conn.fetchval("SELECT current_user")
        is_member = await conn.fetchval(
            "SELECT pg_has_role(current_user, 'ragbait_vault_service', 'MEMBER')"
        )
        bypass_rls = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )

        print(f"[+] Authenticated as '{current_user}'")
        print(f"[+] Member of ragbait_vault_service: {is_member}")
        print(f"[+] BYPASSRLS disabled: {not bypass_rls}")

        if not is_member or bypass_rls:
            print("[-] Privileges verification failed: invalid membership or BYPASSRLS enabled.")
            return False

        # Verify access to private.api_key_secrets
        await conn.fetchval("SELECT count(*) FROM private.api_key_secrets")
        print("[+] Access to private.api_key_secrets verified")

        # Verify access to public.api_key_metadata
        await conn.fetchval("SELECT count(*) FROM public.api_key_metadata")
        print("[+] Access to public.api_key_metadata verified")

        # Verify denied access to public.profiles
        try:
            await conn.fetchval("SELECT count(*) FROM public.profiles")
            print("[-] Security check failed: user can read public.profiles!")
            return False
        except asyncpg.InsufficientPrivilegeError:
            print("[+] Isolation verified: Access to public.profiles is denied")

        # Verify denied access to public.audit_logs
        try:
            await conn.fetchval("SELECT count(*) FROM public.audit_logs")
            print("[-] Security check failed: user can read public.audit_logs!")
            return False
        except asyncpg.InsufficientPrivilegeError:
            print("[+] Isolation verified: Access to public.audit_logs is denied")

        print("[✓] All vault security and isolation checks passed!")
        return True
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description="Provision RagBait Vault Database User")
    parser.add_argument(
        "--admin-dsn",
        default=os.getenv("ADMIN_DATABASE_URL"),
        help="Admin connection string (default: env ADMIN_DATABASE_URL)",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("VAULT_USER_NAME", "ragbait_vault_user"),
        help="Login username to provision (default: ragbait_vault_user)",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("VAULT_USER_PASSWORD"),
        help="Password (if omitted, a cryptographically secure random password is generated)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only run privilege and isolation verification checks",
    )
    parser.add_argument(
        "--vault-dsn",
        default=os.getenv("VAULT_DATABASE_URL"),
        help="Vault DSN to verify (for --verify-only)",
    )

    args = parser.parse_args()

    if args.verify_only:
        if not args.vault_dsn:
            print("[-] Error: --vault-dsn or VAULT_DATABASE_URL is required for verification.")
            sys.exit(1)
        success = asyncio.run(verify_vault_user_privileges(args.vault_dsn))
        sys.exit(0 if success else 1)

    if not args.admin_dsn:
        print("[-] Error: --admin-dsn or ADMIN_DATABASE_URL is required.")
        sys.exit(1)

    username, password = asyncio.run(
        provision_vault_user(args.admin_dsn, args.username, args.password)
    )
    print("\n" + "=" * 60)
    print("VAULT LOGIN USER PROVISIONED SUCCESSFULLY")
    print("=" * 60)
    print(f"Username: {username}")
    print(f"Password: {password}")
    print("\nNext steps:")
    print("1. Store the password in your environment secret manager (e.g. GCP Secret Manager).")
    print("2. Set VAULT_DATABASE_URL in the API service environment.")
    print("3. Never commit this password or add it to database migration scripts.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()

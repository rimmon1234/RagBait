"""Setup and verify private Supabase Storage bucket for RagBait Phase 3 Knowledge Base."""

import sys
from pathlib import Path

# Add project root to sys.path so app modules can be loaded
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supabase import Client, create_client

from app.core.config import settings


def setup_storage_bucket() -> None:
    """Initialize the private study-materials bucket using Supabase Storage API."""
    print("=" * 60)
    print("RagBait Phase 3 — Supabase Storage Initialization")
    print(f"Supabase URL: {settings.SUPABASE_URL}")
    print("=" * 60)

    client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    bucket_id = "study-materials"

    try:
        existing_buckets = client.storage.list_buckets()
        existing_ids = [b.id for b in existing_buckets]
        print(f"Existing buckets: {existing_ids}")

        if bucket_id in existing_ids:
            print(f"Bucket '{bucket_id}' already exists. Verifying configuration...")
            # Retrieve specific bucket info
            bucket = client.storage.get_bucket(bucket_id)
            print(f"Bucket '{bucket_id}' is public: {bucket.public}")
            if bucket.public:
                print("Warning: Bucket was public. Updating to private...")
                client.storage.update_bucket(
                    bucket_id,
                    options={
                        "public": False,
                        "file_size_limit": 52428800,  # 50 MB
                        "allowed_mime_types": ["application/pdf", "text/plain"],
                    },
                )
                print(f"Bucket '{bucket_id}' updated to private (max 50MB, PDF/TXT only).")
            else:
                print(f"Bucket '{bucket_id}' verified private.")
        else:
            print(f"Creating private bucket '{bucket_id}'...")
            client.storage.create_bucket(
                bucket_id,
                options={
                    "public": False,
                    "file_size_limit": 52428800,  # 50 MB
                    "allowed_mime_types": ["application/pdf", "text/plain"],
                },
            )
            print(f"Bucket '{bucket_id}' created successfully as private (max 50MB, PDF/TXT).")

        # Verify access control
        bucket = client.storage.get_bucket(bucket_id)
        assert bucket.id == bucket_id, "Bucket ID mismatch"
        assert bucket.public is False, "Security violation: Bucket must not be public"
        print(f"Verification PASSED: Bucket '{bucket_id}' is ready and secure.")

    except Exception as e:
        print(f"Storage setup failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    setup_storage_bucket()

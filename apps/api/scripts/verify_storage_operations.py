"""Verify Supabase Storage signed upload and download URLs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from supabase import Client, create_client

from app.core.config import settings


def test_signed_storage_lifecycle() -> None:
    print("Testing Supabase Storage signed lifecycle...")
    client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    bucket = "study-materials"
    test_path = "test/test_doc_01/source.txt"
    test_content = b"RagBait Knowledge Base Storage Verification Page 1\nHello World!"

    # 1. Create signed upload URL
    res = client.storage.from_(bucket).create_signed_upload_url(test_path)
    print("Signed upload response:", res)
    upload_url = res.get("signed_url") or res.get("signedUrl") or res.get("url")
    token = res.get("token")
    print(f"Generated upload URL: {upload_url}, token: {token}")

    # 2. Upload using signed URL directly via HTTP PUT / POST
    # Supabase signed upload URL standard: PUT with multipart or binary, or POST
    # In Supabase Storage, create_signed_upload_url returns a url or signed_url that accepts PUT
    headers = {"Content-Type": "text/plain"}
    if upload_url.startswith("/"):
        full_upload_url = f"{settings.SUPABASE_URL}/storage/v1{upload_url}"
    else:
        full_upload_url = upload_url

    print(f"Uploading to {full_upload_url}...")
    upload_resp = httpx.put(full_upload_url, content=test_content, headers=headers)
    print(f"Upload HTTP status: {upload_resp.status_code}")
    if upload_resp.status_code not in (200, 201):
        print(f"Upload failed: {upload_resp.text}", file=sys.stderr)
        # Try uploadToSignedUrl if python sdk has it
        sys.exit(1)

    # 3. Verify signed download URL
    dl_res = client.storage.from_(bucket).create_signed_url(test_path, expires_in=300)
    print("Signed download response:", dl_res)
    dl_url = dl_res.get("signed_url") or dl_res.get("signedUrl")
    if dl_url.startswith("/"):
        full_dl_url = f"{settings.SUPABASE_URL}/storage/v1{dl_url}"
    else:
        full_dl_url = dl_url

    dl_resp = httpx.get(full_dl_url)
    print(f"Download HTTP status: {dl_resp.status_code}, content: {dl_resp.text}")
    assert dl_resp.content == test_content, "Downloaded content did not match uploaded content!"

    # 4. Clean up test object
    rm_res = client.storage.from_(bucket).remove([test_path])
    print("Cleanup response:", rm_res)
    print("Storage lifecycle verification PASSED!")


if __name__ == "__main__":
    test_signed_storage_lifecycle()

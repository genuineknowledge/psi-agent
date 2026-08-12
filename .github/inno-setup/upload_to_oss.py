#!/usr/bin/env python3
"""Upload the Haitun Agent installer and version.txt to Aliyun OSS.

Required env:
  ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET
  ALIYUN_OSS_BUCKET, ALIYUN_OSS_ENDPOINT
  HAITUN_VERSION

Optional env:
  ALIYUN_OSS_PREFIX      default: empty (bucket root)
"""

import os
import sys

try:
    import oss2  # ty: ignore[unresolved-import]
except ImportError:
    raise SystemExit("oss2 is not installed; run: python -m pip install oss2") from None


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    access_key_id = _require_env("ALIYUN_ACCESS_KEY_ID")
    access_key_secret = _require_env("ALIYUN_ACCESS_KEY_SECRET")
    bucket_name = _require_env("ALIYUN_OSS_BUCKET")
    endpoint = _require_env("ALIYUN_OSS_ENDPOINT")
    version = _require_env("HAITUN_VERSION")

    prefix = os.environ.get("ALIYUN_OSS_PREFIX", "").strip().strip("/")
    if prefix in ("", ".", "-", "root", "ROOT"):
        prefix = ""
    installer_name = os.environ.get("HAITUN_UPDATE_INSTALLER_NAME", "HaiTun_Agent_Setup.exe").strip()
    if not installer_name:
        installer_name = "HaiTun_Agent_Setup.exe"
    installer = os.environ.get(
        "INSTALLER_PATH",
        os.path.join("installer", "HaiTun Agent Setup.exe"),
    )
    if not os.path.isfile(installer):
        raise SystemExit(f"Installer artifact not found: {installer}")

    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)

    base_name, ext = os.path.splitext(installer_name)
    stable_key = f"{prefix}/{installer_name}" if prefix else installer_name
    versioned_key = f"{prefix}/{base_name}-{version}{ext}" if prefix else f"{base_name}-{version}{ext}"
    version_key = f"{prefix}/version.txt" if prefix else "version.txt"

    exe_headers = {
        "Content-Type": "application/octet-stream",
        "Cache-Control": "public, max-age=300",
        "x-oss-object-acl": "public-read",
    }
    bucket.put_object_from_file(stable_key, installer, headers=exe_headers)
    copy_headers = {**exe_headers, "x-oss-metadata-directive": "REPLACE"}
    bucket.copy_object(bucket_name, stable_key, versioned_key, headers=copy_headers)
    bucket.put_object(
        version_key,
        version + "\n",
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "no-cache",
            "x-oss-object-acl": "public-read",
        },
    )


if __name__ == "__main__":
    sys.exit(main())

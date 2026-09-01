#!/usr/bin/env python3
"""Upload the Haitun Agent installers and two version files to Aliyun OSS.

Four packages: three Windows installers plus the macOS dmg.

Upload order is fixed: installers first, version files last, so clients never
see a new version before the matching packages are available. This matters more
now that both platforms share ``haitun-version.txt`` — writing it early would
point macOS clients at a dmg that is not up yet.
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


def _upload_file(bucket: oss2.Bucket, key: str, path: str, headers: dict[str, str]) -> None:
    if not os.path.isfile(path):
        raise SystemExit(f"File not found: {path}")
    bucket.put_object_from_file(key, path, headers=headers)


def main() -> None:
    access_key_id = _require_env("ALIYUN_ACCESS_KEY_ID")
    access_key_secret = _require_env("ALIYUN_ACCESS_KEY_SECRET")
    bucket_name = _require_env("ALIYUN_OSS_BUCKET")
    endpoint = _require_env("ALIYUN_OSS_ENDPOINT")
    haitun_version = _require_env("HAITUN_VERSION")
    msys_version = _require_env("MSYS_VERSION")
    full_path = _require_env("INSTALLER_FULL_PATH")
    app_path = _require_env("INSTALLER_APP_PATH")
    msys_path = _require_env("INSTALLER_MSYS_PATH")
    macos_path = _require_env("INSTALLER_MACOS_PATH")
    haitun_version_file = _require_env("HAITUN_VERSION_FILE")
    msys_version_file = _require_env("MSYS_VERSION_FILE")

    prefix = os.environ.get("ALIYUN_OSS_PREFIX", "").strip().strip("/")
    if prefix in ("", ".", "-", "root", "ROOT"):
        prefix = ""

    def key(name: str) -> str:
        return f"{prefix}/{name}" if prefix else name

    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)

    exe_headers = {
        "Content-Type": "application/octet-stream",
        "Cache-Control": "public, max-age=300",
        "x-oss-object-acl": "public-read",
    }
    text_headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-cache",
        "x-oss-object-acl": "public-read",
    }

    _upload_file(bucket, key("HaiTun_Agent_Setup.exe"), full_path, exe_headers)
    _upload_file(bucket, key("HaiTun_Agent_App_Setup.exe"), app_path, exe_headers)
    _upload_file(bucket, key("msys-setup.exe"), msys_path, exe_headers)
    _upload_file(bucket, key("HaiTun_Agent.dmg"), macos_path, exe_headers)

    with open(haitun_version_file, encoding="utf-8") as fh:
        haitun_text = fh.read().strip() or haitun_version
    with open(msys_version_file, encoding="utf-8") as fh:
        msys_text = fh.read().strip() or msys_version

    bucket.put_object(key("haitun-version.txt"), haitun_text + "\n", headers=text_headers)
    bucket.put_object(key("msys-version.txt"), msys_text + "\n", headers=text_headers)


if __name__ == "__main__":
    sys.exit(main())

"""Independently verify APK bytes, Android signing certificate and package.

Reads a signed APK ONLY after the published source ZIP has passed both archive
and APK SHA-256 checks. Neither metadata nor a sidecar signature text file is
accepted as proof of the APK's actual cryptographic signing identity.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HEX = re.compile(r"^[a-f0-9]{64}$")
SIGNER_LINE = re.compile(
    r"(?im)^\s*(?:Signer\s+#\d+|V\d+(?:\.\d+)?\s+Signer:)\s+"
    r"certificate\s+SHA-256\s+digest:\s*([0-9a-f]{64})\s*$"
)
COUNT = re.compile(r"(?im)^\s*Number of signers:\s*(\d+)\s*$")


class APKVerificationError(ValueError):
    pass


def android_tools():
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk:
        raise APKVerificationError("android_sdk_unavailable")
    build_root = Path(sdk) / "build-tools"
    if not build_root.is_dir():
        raise APKVerificationError("android_build_tools_unavailable")
    for folder in sorted(build_root.iterdir(), reverse=True):
        if folder.is_dir():
            signer = folder / "apksigner"
            aapt = folder / "aapt"
            if signer.is_file() and aapt.is_file():
                return str(signer), str(aapt)
    raise APKVerificationError("apk_verification_tools_unavailable")


def checked_output(args):
    try:
        result = subprocess.run(
            args, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise APKVerificationError("android_verification_command_unavailable") from e
    if result.returncode != 0:
        # Never include raw stderr; external tools can disclose private paths.
        raise APKVerificationError("android_verification_command_failed")
    return result.stdout


def verify_certificate_report(report, expected):
    count = COUNT.search(report)
    fingerprints = SIGNER_LINE.findall(report)
    if count is None or int(count.group(1)) != 1:
        raise APKVerificationError("apk_signer_count_not_one")
    if not fingerprints:
        raise APKVerificationError("apk_certificate_unparseable")
    if any(item.lower() != expected for item in fingerprints):
        raise APKVerificationError("apk_production_certificate_mismatch")


def verify_badging(report, version_name, version_code):
    package = next((line for line in report.splitlines() if line.startswith("package: ")), "")
    for field, expected in (
        ("name", "com.ziwuxilv.nativeapp"),
        ("versionName", version_name),
        ("versionCode", str(version_code)),
    ):
        if not re.search(r"(?:^|\s)" + re.escape(field) + r"='" +
                         re.escape(expected) + r"'(?:\s|$)", package):
            raise APKVerificationError("apk_package_or_version_mismatch")


def verify(apk, expected_sha, cert, version_name, version_code, *,
           tools=None, run=checked_output):
    if not apk.is_file() or not HEX.fullmatch(expected_sha) or not HEX.fullmatch(cert):
        raise APKVerificationError("apk_or_digest_invalid")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    if digest != expected_sha:
        raise APKVerificationError("apk_digest_mismatch")
    signer, aapt = tools if tools is not None else android_tools()
    report = run([signer, "verify", "--verbose", "--print-certs", str(apk)])
    verify_certificate_report(report, cert)
    badging = run([aapt, "dump", "badging", str(apk)])
    verify_badging(badging, version_name, version_code)
    return {"verified": True, "apkSha256": digest,
            "package": "com.ziwuxilv.nativeapp", "version": version_name,
            "code": version_code, "productionCertificateMatches": True}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apk", required=True, type=Path)
    p.add_argument("--sha256", required=True)
    p.add_argument("--certificate", required=True)
    p.add_argument("--version-name", required=True)
    p.add_argument("--version-code", type=int, required=True)
    args = p.parse_args()
    try:
        status = verify(args.apk, args.sha256, args.certificate,
                        args.version_name, args.version_code)
    except APKVerificationError as e:
        print("Android APK verification blocked: " + str(e))
        return 2
    print("Android APK cryptographic certificate, package, version and SHA-256 verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.verify_signed_apk import (
    APKVerificationError, verify, verify_badging, verify_certificate_report
)

CERT = "bef3f5cbb2989734f20740eddaac3e105dfecb6c63ea9275e178eedd892ec105"


class SignedAPKVerificationTests(unittest.TestCase):
    def test_old_signer_format(self):
        verify_certificate_report(
            f"Number of signers: 1\nSigner #1 certificate SHA-256 digest: {CERT}\n", CERT)

    def test_build_tools_37_format(self):
        verify_certificate_report(
            f"Number of signers: 1\nV3.0 Signer: certificate SHA-256 digest: {CERT}\n", CERT)

    def test_mixed_signer_certificates_rejected(self):
        with self.assertRaises(APKVerificationError):
            verify_certificate_report(
                f"Number of signers: 1\nSigner #1 certificate SHA-256 digest: {CERT}\n"
                f"V3.0 Signer: certificate SHA-256 digest: {'a' * 64}\n", CERT)

    def test_multiple_signers_rejected(self):
        with self.assertRaises(APKVerificationError):
            verify_certificate_report(
                f"Number of signers: 2\nSigner #1 certificate SHA-256 digest: {CERT}\n", CERT)

    def test_missing_signature_details_rejected(self):
        with self.assertRaises(APKVerificationError):
            verify_certificate_report("Number of signers: 1\n", CERT)

    def test_package_name_and_version(self):
        verify_badging("package: name='com.ziwuxilv.nativeapp' versionCode='118' "
                       "versionName='2.3.5' compileSdkVersion='37'\n", "2.3.5", 118)
        with self.assertRaises(APKVerificationError):
            verify_badging("package: name='attacker' versionCode='118' "
                           "versionName='2.3.5'\n", "2.3.5", 118)

    def test_valid_apk_with_mocked_android_tools(self):
        with tempfile.TemporaryDirectory() as d:
            apk = Path(d) / "signed.apk"
            apk.write_bytes(b"fake-signed-test-bytes")
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            def fake_run(args):
                if args[0] == "signer":
                    return (f"Number of signers: 1\n"
                            f"V3.0 Signer: certificate SHA-256 digest: {CERT}\n")
                return ("package: name='com.ziwuxilv.nativeapp' "
                        "versionCode='118' versionName='2.3.5'\n")
            output = verify(apk, digest, CERT, "2.3.5", 118,
                            tools=("signer", "aapt"), run=fake_run)
            self.assertTrue(output["verified"])

    def test_apk_digest_mismatch_stops_before_signer(self):
        with tempfile.TemporaryDirectory() as d:
            apk = Path(d) / "signed.apk"
            apk.write_bytes(b"fake")
            def must_not_run(args):
                raise AssertionError("Signer invoked before digest check")
            with self.assertRaises(APKVerificationError):
                verify(apk, "0" * 64, CERT, "2.3.5", 118,
                       tools=("signer", "aapt"), run=must_not_run)


if __name__ == "__main__":
    unittest.main()

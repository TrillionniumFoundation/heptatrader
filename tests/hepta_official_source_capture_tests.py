#!/usr/bin/env python3

import email.message
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hepta_market_official_source_extractor as extractor  # noqa: E402
import hepta_official_source_capture as capture  # noqa: E402


class Response:
    def __init__(self, url: str, content_type: str, payload: bytes,
                 status: int = 200, encoding: str = "identity") -> None:
        self._url = url
        self._status = status
        self._payload = payload
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Encoding"] = encoding

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self._status

    def geturl(self):
        return self._url

    def read(self, limit: int):
        return self._payload[:limit]


class Opener:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.request = None

    def open(self, request, timeout: int):
        self.request = request
        if timeout != capture.TIMEOUT_SECONDS:
            raise AssertionError("timeout drift")
        return self.response


class CaptureTests(unittest.TestCase):
    def test_exact_five_sources_only(self) -> None:
        self.assertEqual(len(capture.SOURCE_ORDER), 5)
        self.assertEqual(
            {(provider, url) for provider, url, _name, _type
             in capture.SOURCE_ORDER},
            set(extractor.SOURCE_RULES))
        for _provider, _url, filename, _type in capture.SOURCE_ORDER:
            self.assertEqual(Path(filename).parent, Path("."))

    def test_fetch_accepts_pinned_verified_shape(self) -> None:
        provider, url, _name, expected_type = capture.SOURCE_ORDER[0]
        opener = Opener(Response(url, expected_type, b"BEGIN:VCALENDAR\n"))
        result = capture.fetch(opener, provider, url, expected_type)
        self.assertEqual(result["payload"], b"BEGIN:VCALENDAR\n")
        self.assertEqual(opener.request.get_method(), "GET")
        self.assertEqual(opener.request.get_header("Accept-encoding"), "identity")

    def test_fetch_rejects_redirect_encoding_and_oversize(self) -> None:
        provider, url, _name, expected_type = capture.SOURCE_ORDER[0]
        bad = (
            Response(url + "?redirected", expected_type, b"x"),
            Response(url, expected_type, b"x", encoding="gzip"),
            Response(url, expected_type, b"x" * (capture.MAX_RESPONSE_BYTES + 1)),
        )
        for response in bad:
            with self.subTest(response=response), self.assertRaisesRegex(
                    capture.CaptureError, "RESPONSE_INVALID"):
                capture.fetch(Opener(response), provider, url, expected_type)

    def test_source_has_no_dynamic_url_or_credentials(self) -> None:
        source = (ROOT / "scripts/hepta_official_source_capture.py").read_text(
            encoding="utf-8")
        self.assertIn("urllib.request.ProxyHandler({})", source)
        self.assertNotIn("add_argument(\"--url", source)
        for forbidden in (
                "Authorization", "Cookie", "ibapi", "ib_insync",
                "place_order(", "cancel_order("):
            self.assertNotIn(forbidden, source)

    def test_fixed_export_is_atomically_replaceable_only_when_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "official-source-bundle.json"
            target.write_bytes(b"old\n")
            os.chmod(target, 0o440)
            capture._atomic_replace_export(
                target, b"new\n", os.getegid())
            self.assertEqual(target.read_bytes(), b"new\n")
            self.assertEqual(target.stat().st_mode & 0o777, 0o440)
            os.chmod(target, 0o640)
            with self.assertRaisesRegex(
                    capture.CaptureError, "EXPORT_REPLACE_UNSAFE"):
                capture._atomic_replace_export(
                    target, b"rejected\n", os.getegid())
            self.assertEqual(target.read_bytes(), b"new\n")
            target.unlink()
            target.symlink_to(root / "missing")
            with self.assertRaisesRegex(
                    capture.CaptureError, "EXPORT_REPLACE_UNSAFE"):
                capture._atomic_replace_export(
                    target, b"rejected\n", os.getegid())


if __name__ == "__main__":
    unittest.main()

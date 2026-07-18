"""Process-wide SSL context reuse for concurrent HTTPS on Windows."""
from __future__ import annotations

import threading
import unittest
from unittest import mock

from core import http_ssl


class TestHttpSsl(unittest.TestCase):
    def setUp(self):
        http_ssl._default_ctx = None
        http_ssl._insecure_ctx = None
        http_ssl._warmed = False

    def test_warm_up_creates_shared_contexts_once(self):
        with mock.patch.object(http_ssl.ssl, "create_default_context") as create_default:
            fake = mock.Mock(name="default_ctx")
            create_default.return_value = fake
            http_ssl.warm_up()
            http_ssl.warm_up()
            self.assertIs(http_ssl.get_default_context(), fake)
            self.assertEqual(create_default.call_count, 1)
            insecure = http_ssl.get_insecure_context()
            self.assertIs(http_ssl.get_insecure_context(), insecure)
            self.assertFalse(insecure.check_hostname)
            self.assertEqual(insecure.verify_mode, http_ssl.ssl.CERT_NONE)

    def test_concurrent_get_default_context_is_singleton(self):
        results = []

        def worker():
            results.append(http_ssl.get_default_context())

        threads = [threading.Thread(target=worker) for _ in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 32)
        self.assertTrue(all(ctx is results[0] for ctx in results))

    def test_urlopen_passes_shared_context(self):
        shared = object()
        with mock.patch.object(http_ssl, "get_default_context", return_value=shared):
            with mock.patch.object(http_ssl, "_stdlib_urlopen") as std_urlopen:
                std_urlopen.return_value = "resp"
                out = http_ssl.urlopen("https://example.invalid", timeout=3)
        self.assertEqual(out, "resp")
        kwargs = std_urlopen.call_args.kwargs
        self.assertIs(kwargs["context"], shared)
        self.assertEqual(kwargs["timeout"], 3)


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from speed_probe import sample_download


class SpeedProbeTests(unittest.TestCase):
    def test_cancellation_interrupts_stalled_tls_handshake(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen(1)
        port = listener.getsockname()[1]
        cancel = threading.Event()
        def stall():
            with listener.accept()[0] as conn:
                time.sleep(.8)
            listener.close()
        thread = threading.Thread(target=stall, daemon=True); thread.start()
        timer = threading.Timer(.05, cancel.set); timer.start()
        started = time.monotonic()
        result = sample_download('127.0.0.1', f'https://localhost:{port}/', timeout_seconds=2, cancel=cancel)
        elapsed = time.monotonic() - started
        timer.join(); thread.join(2)
        self.assertLess(elapsed, .6)
        self.assertEqual(result['download_error'], 'cancelled')
        self.assertFalse(result['download_ok'])

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cert, cls.key = root / 'cert', root / 'key'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(cls.key), '-out', str(cls.cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def serve(self, parts, tls=False, window=.1, timeout=.7, max_bytes=50000000,
              cancel=None, reset=False):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(2)
        port = listener.getsockname()[1]
        request = []
        sni = []
        context = ssl.create_default_context(cafile=str(self.cert)) if tls else None
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(self.cert, self.key)
        server.set_servername_callback(lambda sock, name, ctx: sni.append(name))
        def run():
            conn = None
            try:
                conn, _ = listener.accept()
                if tls:
                    conn = server.wrap_socket(conn, server_side=True)
                request.append(conn.recv(4096))
                for delay, chunk in parts:
                    time.sleep(delay)
                    if chunk:
                        conn.sendall(chunk)
                if reset:
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            except (OSError, ssl.SSLError):
                pass
            finally:
                if conn: conn.close()
                listener.close()
        thread = threading.Thread(target=run)
        thread.start()
        try:
            with patch.dict(os.environ, {'http_proxy': 'http://127.0.0.1:1', 'HTTPS_PROXY': 'http://127.0.0.1:1'}):
                result = sample_download('127.0.0.1', f'{"https" if tls else "http"}://localhost:{port}/file?x=1',
                                         window, timeout, max_bytes, ssl_context=context, cancel=cancel)
        finally:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        return result, request, sni

    def test_window_success_http_and_verified_tls_correct_host_sni_no_proxy(self):
        for tls in (False, True):
            result, request, sni = self.serve([(0, b'HTTP/1.1 200 OK\r\nContent-Length: 9000000\r\n\r\n'),
                                             (0, b'x' * 4096), (.25, b'x')], tls=tls)
            self.assertTrue(result['download_ok'], result)
            self.assertEqual(result['download_stop_reason'], 'window')
            self.assertGreater(result['download_mbps'], 0)
            self.assertIn(b'Host: localhost:', request[0])
            if tls: self.assertEqual(sni, ['localhost'])

    def test_clean_eof_and_byte_cap(self):
        for cap, reason in [(50000000, 'eof'), (1024, 'byte_cap')]:
            result, _, _ = self.serve([(0, b'HTTP/1.1 200 OK\r\nContent-Length: 4096\r\n\r\n' + b'x' * 4096)], max_bytes=cap)
            self.assertTrue(result['download_ok'], result)
            self.assertEqual(result['download_stop_reason'], reason)

    def test_incomplete_reset_status_redirect_zero(self):
        cases = [(b'HTTP/1.1 200 OK\r\nContent-Length: 9000\r\n\r\n' + b'x' * 2048, False, 'incomplete_body'),
                 (b'HTTP/1.1 200 OK\r\nContent-Length: 9000\r\n\r\n' + b'x' * 2048, True, 'io_error'),
                 (b'HTTP/1.1 503 Fail\r\nContent-Length: 0\r\n\r\n', False, 'http_status'),
                 (b'HTTP/1.1 302 Found\r\nLocation: http://other.invalid/\r\n\r\n', False, 'redirect'),
                 (b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n', False, 'zero_bytes')]
        for response, reset, error in cases:
            result, _, _ = self.serve([(0, response)], reset=reset)
            self.assertFalse(result['download_ok'], result)
            self.assertIsNone(result['download_mbps'])
            self.assertEqual(result['download_error'], error)

    def test_header_timeout_and_slow_first_body_are_not_window_success(self):
        for parts, window, timeout in [([(.25, b'HTTP/1.1 200 OK\r\n\r\nx')], .05, .1),
                                       ([(0, b'HTTP/1.1 200 OK\r\n\r\n'), (.25, b'x')], .05, .5)]:
            result, _, _ = self.serve(parts, window=window, timeout=timeout)
            self.assertFalse(result['download_ok'], result)
            self.assertIn(result['download_error'], ('timeout', 'zero_bytes'))

    def test_overall_timeout_after_bytes_and_cancellation_are_failures(self):
        parts = [(0, b'HTTP/1.1 200 OK\r\n\r\n' + b'x' * 2048), (.3, b'x')]
        result, _, _ = self.serve(parts, window=.5, timeout=.1)
        self.assertEqual(result['download_error'], 'timeout')
        start = time.monotonic()
        result, _, _ = self.serve(parts, window=1, timeout=2, cancel=lambda: time.monotonic() - start > .08)
        self.assertEqual(result['download_error'], 'cancelled')
        self.assertFalse(result['download_ok'])

    def test_unsafe_urls_and_unverified_tls_rejected(self):
        for url in ['file:///etc/passwd', 'https://a:b@example.com/', 'http://x/\r\nInjected:yes', 'ftp://example.com/', 'https://example.com/#fragment']:
            self.assertEqual(sample_download('127.0.0.1', url)['download_error'], 'invalid_input')

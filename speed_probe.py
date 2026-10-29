"""Bounded direct-IP HTTP(S) throughput sampling (stdlib only).

No proxy discovery, DNS lookup of the URL host, redirects, or timer threads.
TLS still verifies the URL hostname against the selected IP's certificate.
"""
import datetime
import errno
import http.client
import io
import ipaddress
import math
import select
import socket
import ssl
import time
import urllib.parse

DEFAULT_URL = 'https://speed.cloudflare.com/__down?bytes=50000000'


class _Stop(Exception):
    pass


def _cancelled(cancel):
    return cancel is not None and (cancel() if callable(cancel) else cancel.is_set())


def _target(url):
    url = url or DEFAULT_URL
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError('invalid URL')
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError('invalid URL')
    host = parsed.hostname.encode('idna').decode('ascii')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        labels = host.rstrip('.').split('.')
        if len(host) > 253 or any(not label or len(label) > 63 or label.startswith('-')
                or label.endswith('-') or not all(c.isalnum() or c == '-' for c in label)
                for label in labels):
            raise ValueError('invalid hostname')
    if '%' in host:
        raise ValueError('scoped IP not allowed')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if not 1 <= port <= 65535:
        raise ValueError('invalid port')
    path = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
    path.encode('ascii')
    authority = '[' + host + ']' if ':' in host else host
    if port != (443 if parsed.scheme == 'https' else 80):
        authority += ':' + str(port)
    return parsed.scheme, host, port, authority, path


class _Reader(io.RawIOBase):
    def __init__(self, sock, remaining):
        self.sock, self.remaining = sock, remaining

    def readable(self):
        return True

    def readinto(self, buffer):
        # Polling socket reads permit prompt cooperative cancellation without
        # invalidating a socket.makefile after a short socket timeout.
        while True:
            self.sock.settimeout(min(0.2, self.remaining()))
            try:
                return self.sock.recv_into(buffer)
            except socket.timeout:
                self.remaining()


class _StreamSocket:
    def __init__(self, sock, remaining):
        self.sock, self.remaining = sock, remaining

    def makefile(self, *args, **kwargs):
        return io.BufferedReader(_Reader(self.sock, self.remaining))


def sample_download(ip, url='', window_seconds=6, timeout_seconds=15,
                    max_bytes=50000000, *, ssl_context=None, cancel=None,
                    min_bytes=1024, clock=time.monotonic):
    """Sample one IP; partial/error/cancelled transfers never expose valid Mbps.

    The body window begins after 2xx headers, just before reading the body.
    Reaching that deadline is success with >= min_bytes; connection/header/
    overall timeouts are failures. Clean EOF or the explicit byte cap may end
    a successful sample sooner. No redirect is followed, even to the same host.
    ssl_context is a verified TLS test seam, cancel accepts Event or callable.
    """
    result = dict(download_ok=False, download_bytes=0, download_ms=0.0,
                  download_mbps=None, download_error='', download_stop_reason='',
                  method='windowed_http', source='direct_ip',
                  measured_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    raw = sock = response = None
    body_started = body_deadline = None
    started = clock()
    try:
        ip = str(ipaddress.ip_address(ip))
        scheme, host, port, authority, path = _target(url)
        window_seconds, timeout_seconds = float(window_seconds), float(timeout_seconds)
        if (not math.isfinite(window_seconds) or not math.isfinite(timeout_seconds)
                or window_seconds <= 0 or timeout_seconds <= 0
                or isinstance(max_bytes, bool) or int(max_bytes) != max_bytes
                or not 1 <= max_bytes <= 1000000000 or not 1 <= min_bytes <= max_bytes):
            raise ValueError('invalid sampling bounds')
        overall_deadline = started + timeout_seconds

        def remaining():
            if _cancelled(cancel):
                raise _Stop('cancelled')
            now = clock()
            # Overall expiry wins ties; it is never a completed body window.
            if now >= overall_deadline:
                raise _Stop('timeout')
            if body_deadline is not None and now >= body_deadline:
                raise _Stop('window')
            return min(overall_deadline, body_deadline or overall_deadline) - now

        # Nonblocking connect/handshake preserve cancellation responsiveness
        # even before headers and body sampling begin.
        raw = socket.socket(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM)
        raw.setblocking(False)
        code = raw.connect_ex((ip, port))
        if code not in (0, errno.EISCONN, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
            raise OSError(code, 'connect failed')
        while code not in (0, errno.EISCONN):
            _, writable, exceptional = select.select([], [raw], [raw], min(.2, remaining()))
            if writable or exceptional:
                code = raw.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if code:
                    raise OSError(code, 'connect failed')
        sock, raw = raw, None
        if scheme == 'https':
            context = ssl_context or ssl.create_default_context()
            if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
                raise ValueError('TLS verification required')
            sock = context.wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
            while True:
                remaining()
                try:
                    sock.do_handshake()
                    break
                except ssl.SSLWantReadError:
                    select.select([sock], [], [], min(.2, remaining()))
                except ssl.SSLWantWriteError:
                    select.select([], [sock], [], min(.2, remaining()))
        sock.settimeout(remaining())
        sock.sendall((f'GET {path} HTTP/1.1\r\nHost: {authority}\r\n'
                      'User-Agent: cfipup2dns-window/1.0\r\nAccept: */*\r\n'
                      'Accept-Encoding: identity\r\nConnection: close\r\n\r\n').encode('ascii'))
        response = http.client.HTTPResponse(_StreamSocket(sock, remaining))
        response.begin()
        if not 200 <= response.status < 300:
            raise _Stop('redirect' if 300 <= response.status < 400 else 'http_status')
        body_started = clock()
        body_deadline = body_started + window_seconds
        while result['download_bytes'] < max_bytes:
            remaining()
            chunk = response.read1(min(65536, max_bytes - result['download_bytes']))
            if not chunk:
                if response.length not in (None, 0):
                    raise _Stop('incomplete_body')
                result['download_stop_reason'] = 'eof'
                break
            result['download_bytes'] += len(chunk)
        else:
            result['download_stop_reason'] = 'byte_cap'
        remaining()  # Check cancellation/overall deadline before accepting EOF.
        result['download_ok'] = result['download_bytes'] >= min_bytes
        if not result['download_ok']:
            result['download_error'] = 'zero_bytes' if not result['download_bytes'] else 'insufficient_bytes'
    except _Stop as exc:
        reason = str(exc)
        result['download_stop_reason'] = reason
        result['download_ok'] = reason == 'window' and result['download_bytes'] >= min_bytes
        if not result['download_ok']:
            result['download_error'] = ('zero_bytes' if reason == 'window' and not result['download_bytes']
                                        else 'insufficient_bytes' if reason == 'window' else reason)
    except (socket.timeout, TimeoutError):
        result['download_error'] = result['download_stop_reason'] = 'timeout'
    except http.client.IncompleteRead:
        result['download_error'] = result['download_stop_reason'] = 'incomplete_body'
    except ssl.SSLError:
        result['download_error'] = result['download_stop_reason'] = 'tls_error'
    except (ValueError, TypeError, UnicodeError):
        result['download_error'] = result['download_stop_reason'] = 'invalid_input'
    except (OSError, http.client.HTTPException):
        result['download_error'] = result['download_stop_reason'] = 'io_error'
    finally:
        if body_started is not None:
            result['download_ms'] = max(0.0, (clock() - body_started) * 1000)
        for resource in (response, sock, raw):
            if resource is not None:
                resource.close()
    if result['download_ok'] and result['download_ms'] > 0:
        result['download_mbps'] = result['download_bytes'] * 8 / (result['download_ms'] * 1000)
    else:
        result['download_ok'] = False
    return result

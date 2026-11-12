# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Counts the requests a source receives, on the wire. The spec names the tap's address as the
endpoint and the tap forwards each request to the store unchanged, so the count covers any client
in any process and holds for moto and MinIO alike. Nothing here imports the project.
"""

# Type annotations
from __future__ import annotations
from typing import Iterator, List, NamedTuple, Tuple

# Standard libs
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

# Public interface
__all__ = ['Tap', 'Request', 'Window']

HOP_BY_HOP = frozenset({'connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer', 'upgrade',
                        'proxy-authenticate', 'proxy-authorization'})
LINE = 65537                     # the longest chunk-size or trailer line the tap reads
BLOCK = 65536                    # how much of a refused body the tap reads at a time


class Request(NamedTuple):
    """One request the source received."""

    kind: str                # 'read' | 'stat' | 'list' | 'write'
    method: str
    bucket: str
    key: str                 # '' for a request on the bucket itself
    query: str               # says what a bucket-level request is: 'list-type=2', 'delete'


def classify(method: str, target: str) -> Request:
    """
    Name a request's kind. Anything that is not a GET or a HEAD is a write, wherever its key is:
    the S3 client the engine uses deletes with a POST on the bucket and the key in the body.
    """
    parts = urlsplit(target)
    path = unquote(parts.path)
    bucket, _, key = path.lstrip('/').partition('/')
    if method == 'HEAD':
        kind = 'stat'
    elif method == 'GET':
        kind = 'read' if key else 'list'
    else:
        kind = 'write'
    return Request(kind, method, bucket, key, parts.query)


class Window:
    """The requests seen between entering a `Tap.window()` block and leaving it."""

    def __init__(self) -> None:
        self._requests: List[Request] = []
        self.closed = False

    @property
    def requests(self) -> List[Request]:
        """Every request of the window. Reading it inside the block is an error: it would read as empty."""
        if not self.closed:
            raise RuntimeError('A window is read after its block ends')
        return self._requests

    def of(self, kind: str) -> List[Tuple[str, str]]:
        """Method and key of every request of `kind`, in arrival order."""
        return [(request.method, request.key) for request in self.requests if request.kind == kind]


class Tap:
    """A forwarding HTTP server in front of `upstream` that records every request it passes on."""

    def __init__(self, upstream: str) -> None:
        self.upstream = urlsplit(upstream)
        self.requests: List[Request] = []
        self.refused: List[str] = []
        self._mutex = threading.Lock()
        self._server = ThreadingHTTPServer(('127.0.0.1', 0), self._handler())
        self.endpoint = f'http://127.0.0.1:{self._server.server_address[1]}'
        # The default half-second poll would make every stop wait that long.
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': 0.02},
                                        name='tap', daemon=True)

    def _handler(self) -> type:
        tap = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args) -> None:
                return

            def drain(self) -> None:
                """
                Read a chunked body to its last chunk and the trailer section, discarding it. A reply
                sent with request bytes still unread closes the connection under the client's writes,
                which the client may see as a broken pipe instead of the reply.
                """
                while True:
                    size = int(self.rfile.readline(LINE).split(b';', 1)[0], 16)
                    if size == 0:
                        break
                    size += 2                                # the line break after the chunk
                    while size:
                        data = self.rfile.read(min(size, BLOCK))
                        if not data:
                            raise ConnectionError('the client closed inside a chunk')
                        size -= len(data)
                while self.rfile.readline(LINE) not in (b'\r\n', b'\n', b''):
                    pass

            def forward(self) -> None:
                if 'chunked' in self.headers.get('Transfer-Encoding', '').lower():
                    # A body this tap cannot replay faithfully is refused and remembered, never guessed at,
                    # and never forwarded. It is read to its end first, so the client always sees the refusal.
                    with tap._mutex:
                        tap.refused.append(f'{self.command} {self.path}')
                    self.close_connection = True
                    try:
                        self.drain()
                    except (ValueError, OSError):
                        return                               # broken framing has no end to read to
                    self.send_error(501, 'The tap forwards bodies with a Content-Length only')
                    return
                body = self.rfile.read(int(self.headers.get('Content-Length', 0) or 0))
                with tap._mutex:
                    tap.requests.append(classify(self.command, self.path))
                upstream = HTTPConnection(tap.upstream.hostname, tap.upstream.port, timeout=30)
                try:
                    # The Host header goes through as the client sent it: a store that checks the
                    # signature checks it against the header it receives.
                    upstream.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
                    for name, value in self.headers.items():
                        if name.lower() not in HOP_BY_HOP:
                            upstream.putheader(name, value)
                    upstream.endheaders(body)
                    reply = upstream.getresponse()
                    payload = reply.read()
                    self.send_response_only(reply.status, reply.reason)
                    for name, value in reply.getheaders():
                        if name.lower() not in HOP_BY_HOP and name.lower() != 'content-length':
                            self.send_header(name, value)
                    length = reply.getheader('Content-Length') if self.command == 'HEAD' else str(len(payload))
                    if length is not None:
                        self.send_header('Content-Length', length)
                    self.end_headers()
                    if self.command != 'HEAD':
                        self.wfile.write(payload)
                finally:
                    upstream.close()

            do_GET = do_HEAD = do_PUT = do_POST = do_DELETE = do_PATCH = do_OPTIONS = forward

        return Handler

    def start(self) -> Tap:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def __enter__(self) -> Tap:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    @contextmanager
    def window(self) -> Iterator[Window]:
        """Collect the requests that arrive inside the block. Leaving it fails if the tap refused one."""
        seen = Window()
        with self._mutex:
            start, refused = len(self.requests), len(self.refused)
        try:
            yield seen
        finally:
            with self._mutex:
                seen._requests = self.requests[start:]
                seen.closed = True
                dropped = self.refused[refused:]
        if dropped:
            raise AssertionError(f'The tap refused {len(dropped)} request(s) it could not forward: {dropped}')

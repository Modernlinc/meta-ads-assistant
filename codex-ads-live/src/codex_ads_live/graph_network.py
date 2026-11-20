"""Fixed Graph HTTPS route with optional macOS system HTTP CONNECT."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import http.client
import ssl
import sys
import time
from urllib.parse import urlsplit

from .validation import fail


HOST = 'graph.facebook.com'
PORT = 443
NETWORK_FAILURE = 'HTTP_NETWORK_OR_TLS_FAILURE'
PROXY_UNAVAILABLE = 'MACOS_SYSTEM_PROXY_UNAVAILABLE'


@dataclass
class _SystemRoute:
    endpoint: tuple[str, int] | None = None


_system_route = ContextVar('graph_system_route', default=None)


@contextmanager
def graph_network_scope(use_system_proxy):
    token = _system_route.set(_SystemRoute() if use_system_proxy else None)
    try:
        yield
    finally:
        _system_route.reset(token)


def _macos_https_proxy():
    if sys.platform != 'darwin':
        fail(PROXY_UNAVAILABLE)
    try:
        from urllib.request import getproxies_macosx_sysconf, proxy_bypass_macosx_sysconf
        url = getproxies_macosx_sysconf().get('https', '')
        if not url or not url.isascii() or any(ch.isspace() or ord(ch) < 33 for ch in url):
            fail(PROXY_UNAVAILABLE)
        parts = urlsplit(url)
        if (parts.scheme != 'http' or parts.hostname is None or parts.username is not None
                or parts.password is not None or parts.path not in ('', '/')
                or parts.query or parts.fragment or proxy_bypass_macosx_sysconf(HOST)):
            fail(PROXY_UNAVAILABLE)
        port = parts.port if parts.port is not None else 80
        if port == 0:
            fail(PROXY_UNAVAILABLE)
        return parts.hostname, port
    except (ImportError, OSError, ValueError):
        fail(PROXY_UNAVAILABLE)


def graph_https_connection(timeout, context):
    route = _system_route.get()
    if route is None:
        connection = http.client.HTTPSConnection(HOST, port=PORT, timeout=timeout, context=context)
    else:
        if route.endpoint is None:
            route.endpoint = _macos_https_proxy()
        proxy_host, proxy_port = route.endpoint
        connection = http.client.HTTPSConnection(proxy_host, port=proxy_port, timeout=timeout, context=context)
        # The CONNECT request carries only the fixed destination. Request headers
        # and parameters are sent after TLS has authenticated that destination.
        connection.set_tunnel(HOST, port=PORT)
        connect = connection.connect

        def connect_with_budget():
            deadline = time.monotonic() + connection.timeout
            connect()
            if time.monotonic() >= deadline:
                raise TimeoutError

        connection.connect = connect_with_budget
    connection.set_debuglevel(0)
    return connection


def check_graph_network():
    result = dict(host=HOST, port=PORT, method='HEAD',
                  transport='MACOS_SYSTEM_HTTP_PROXY' if _system_route.get() else 'DIRECT',
                  credential_accessed=False, redirect_followed=False, http_status=None)
    connection = response = None
    try:
        connection = graph_https_connection(6, ssl.create_default_context())
        connection.request('HEAD', '/')
        response = connection.getresponse()
        return dict(result, status='META_HTTPS_REACHABLE', http_status=response.status)
    except (OSError, http.client.HTTPException):
        return dict(result, status='META_HTTPS_UNREACHABLE', error_code=NETWORK_FAILURE)
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            if connection is not None:
                connection.close()

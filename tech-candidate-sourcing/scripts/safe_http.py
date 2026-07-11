#!/usr/bin/env python3
"""Small public-network-only HTTP helper for user-influenced URLs."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests


MAX_REDIRECTS = 5
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal")


def resolved_addresses(hostname: str, port: int) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for result in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM):
        raw = result[4][0].split("%", 1)[0]
        addresses.add(ipaddress.ip_address(raw))
    return addresses


def validate_public_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only public http/https URLs are allowed.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must have a public hostname and no embedded credentials.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(BLOCKED_HOST_SUFFIXES):
        raise ValueError("Local or internal hostnames are not allowed.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = resolved_addresses(hostname, port)
    except (OSError, ValueError) as exc:
        raise ValueError(f"URL hostname could not be resolved safely: {hostname}") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("URL resolves to a non-public network address.")
    return parsed.geturl()


def validate_connected_peer(
    response: requests.Response,
    expected: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    *,
    require_global: bool = True,
) -> None:
    connection = getattr(response.raw, "_connection", None) or getattr(response.raw, "connection", None)
    sock = getattr(connection, "sock", None)
    if sock is None:
        raise ValueError("Could not verify the connected peer address.")
    peer_raw = sock.getpeername()[0].split("%", 1)[0]
    peer = ipaddress.ip_address(peer_raw)
    if (require_global and not peer.is_global) or peer not in expected:
        raise ValueError("Connected peer does not match the validated public DNS addresses.")


def expected_peer_addresses(url: str, target_addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address]) -> tuple[set[ipaddress.IPv4Address | ipaddress.IPv6Address], bool]:
    parsed = urlparse(url)
    proxies = requests.utils.get_environ_proxies(url)
    proxy_url = proxies.get(parsed.scheme or "") or proxies.get("all")
    if not proxy_url:
        return target_addresses, True
    proxy = urlparse(proxy_url)
    if proxy.scheme not in {"http", "https", "socks5", "socks5h"} or not proxy.hostname:
        raise ValueError("Configured proxy URL is invalid.")
    proxy_port = proxy.port or (443 if proxy.scheme == "https" else 80)
    return resolved_addresses(proxy.hostname, proxy_port), False


def safe_get_public(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int | float = 10,
    max_bytes: int = 5 * 1024 * 1024,
) -> requests.Response:
    current = url
    for _redirect in range(MAX_REDIRECTS + 1):
        current = validate_public_url(current)
        parsed = urlparse(current)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        target_addresses = resolved_addresses(parsed.hostname or "", port)
        expected, require_global_peer = expected_peer_addresses(current, target_addresses)
        response: requests.Response | None = None
        try:
            response = requests.get(
                current,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            validate_connected_peer(response, expected, require_global=require_global_peer)
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location", "")
                if not location:
                    raise ValueError("Redirect response is missing Location.")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"Public response exceeds {max_bytes} bytes.")
                chunks.append(chunk)
        finally:
            if response is not None:
                response.close()
        if response is None:
            raise ValueError("Public request did not return a response.")
        response._content = b"".join(chunks)  # requests uses this for .text/.content/.json().
        response._content_consumed = True
        return response
    raise ValueError(f"Too many redirects; maximum is {MAX_REDIRECTS}.")

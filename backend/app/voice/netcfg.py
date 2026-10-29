"""WebRTC reachability when `app` runs in a container (TRD 01 §6.1/§6.2, deploy/docker-compose.yml).

A browser cannot reach a container's bridge address, and aioice binds random UDP ports, so a published port range
alone is not enough. Two settings fix both halves, the way media servers do ("announced IP" / 1:1 NAT mapping):

  rtc_udp_port_min/max   host candidates bind only inside this range, so `docker run -p A-B:A-B/udp` covers them.
  rtc_announce_ips       the answer SDP gets one extra host candidate per address, same port: e.g. 127.0.0.1 for a
                         browser on the Docker host, the host's LAN address for other PCs. Private/loopback only.

Both are off by default (host networking in development and production needs neither).
"""
import asyncio
import ipaddress
import itertools
import re
import types

_installed: tuple[int, int] | None = None


class _RangeLoop:
    """Event-loop proxy for aioice: `local_addr=(ip, 0)` binds the next free port of the range instead."""

    _next = itertools.count()

    def __init__(self, loop: asyncio.AbstractEventLoop, lo: int, hi: int) -> None:
        self._loop, self._lo, self._hi = loop, lo, hi

    def __getattr__(self, name: str):
        return getattr(self._loop, name)

    async def create_datagram_endpoint(self, protocol_factory, local_addr=None, **kw):
        if not local_addr or local_addr[1] != 0:
            return await self._loop.create_datagram_endpoint(protocol_factory, local_addr=local_addr, **kw)
        size = self._hi - self._lo + 1
        start = next(self._next) % size  # spread sessions over the range
        for i in range(size):
            try:
                return await self._loop.create_datagram_endpoint(
                    protocol_factory, local_addr=(local_addr[0], self._lo + (start + i) % size), **kw)
            except OSError:
                continue
        raise OSError(f"no free UDP port in {self._lo}-{self._hi}")


def install_port_range(lo: int, hi: int) -> None:
    """Idempotent. aioice calls `asyncio.get_event_loop()` from its own module, so only its view is redirected."""
    global _installed
    if _installed == (lo, hi):
        return
    import aioice.ice

    real = asyncio
    proxy = types.ModuleType("asyncio_rtc_ports")
    proxy.__getattr__ = lambda name: getattr(real, name)  # type: ignore[method-assign]
    proxy.get_event_loop = lambda: _RangeLoop(real.get_event_loop(), lo, hi)  # type: ignore[attr-defined]
    aioice.ice.asyncio = proxy
    _installed = (lo, hi)


def validate_announce_ips(ips: list[str]) -> None:
    for ip in ips:
        addr = ipaddress.ip_address(ip)  # ValueError for names: an IP literal is required
        if addr.version != 4 or not (addr.is_private or addr.is_loopback):
            raise ValueError(f"rtc_announce_ips must be private/loopback IPv4 addresses (AC-22): {ip}")


_CAND = re.compile(r"^a=candidate:(\S+) (\d+) udp (\d+) (\d+\.\d+\.\d+\.\d+) (\d+) typ host(.*)$", re.IGNORECASE)


def announce(sdp: str, ips: list[str]) -> str:
    """Adds `typ host` candidates on the announced addresses next to each IPv4 host candidate (same port)."""
    if not ips:
        return sdp
    out = []
    for line in sdp.split("\r\n"):
        out.append(line)
        m = _CAND.match(line)
        if not m:
            continue
        foundation, comp, prio, host, port, rest = m.groups()
        for n, ip in enumerate(ips):
            if ip != host:
                # Foundations are at most 32 characters (RFC 8445); aioice already uses all 32.
                fnd = f"{foundation[:29]}a{n:02d}"
                out.append(f"a=candidate:{fnd} {comp} udp {int(prio) - 1 - n} {ip} {port} typ host{rest}")
    return "\r\n".join(out)

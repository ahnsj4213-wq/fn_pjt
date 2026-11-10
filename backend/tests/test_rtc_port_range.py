"""netcfg.install_port_range: ICE host candidates bind inside the published UDP range, whichever loop accessor aioice
uses (get_event_loop up to 0.10, get_running_loop from 0.11; the api image had 0.12.1 while dev had 0.10.2)."""
import asyncio

import aioice.ice
import pytest

from app.voice import netcfg

LO, HI = 41_700, 41_704


@pytest.fixture
def port_range():
    saved = aioice.ice.asyncio, netcfg._installed
    netcfg._installed = None
    netcfg.install_port_range(LO, HI)
    yield
    aioice.ice.asyncio, netcfg._installed = saved


async def test_both_loop_accessors_are_redirected(port_range):
    assert isinstance(aioice.ice.asyncio.get_running_loop(), netcfg._RangeLoop)
    assert isinstance(aioice.ice.asyncio.get_event_loop(), netcfg._RangeLoop)
    assert aioice.ice.asyncio.sleep is asyncio.sleep  # everything else is the real module


async def test_host_candidates_bind_inside_the_range(port_range):
    conn = aioice.Connection(ice_controlling=False)
    try:
        await conn.gather_candidates()
        ports = {c.port for c in conn.local_candidates if c.type == "host"}
        assert ports and all(LO <= p <= HI for p in ports), ports
    finally:
        await conn.close()

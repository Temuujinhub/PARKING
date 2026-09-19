"""Do not reboot a camera for credentials, unsupported URLs or header timeouts."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services import barrier, camera_health as health


@pytest.fixture
def harness(monkeypatch):
    ticks = count()
    monkeypatch.setattr(health, 'time', SimpleNamespace(monotonic=lambda: next(ticks) / 100))

    @asynccontextmanager
    async def unlocked(*args):
        yield

    monkeypatch.setattr(barrier, '_rpc_lock', unlocked)
    monkeypatch.setattr(barrier, 'wait_rpc_gap', AsyncMock())
    done = MagicMock()
    monkeypatch.setattr(barrier, 'note_rpc_done', done)

    async def run(handler, last_seen=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monkeypatch.setattr(barrier, 'camera_client', lambda ip: client)
            result = await health._classify('203.0.113.10', 'Test', ('test', 'test'), 1, last_seen)
        done.assert_called_with('203.0.113.10')
        return result
    return run


@pytest.mark.parametrize('status', [200, 401, 403, 404, 429, 500, 503])
def test_fast_wrong_http_status_never_marks_live_camera_hung(harness, status):
    result = asyncio.run(harness(lambda request: httpx.Response(status, content=b'no JPEG'), datetime.utcnow()))
    assert result['verdict'] == 'busy'
    assert result['failure_statuses'] == {str(status): 3}
    assert not result['fast_400_only']


@pytest.mark.parametrize('error', [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
def test_fast_transport_error_is_not_snapshot_400(harness, error):
    def fail(request):
        raise error('synthetic transport failure', request=request)
    result = asyncio.run(harness(fail, datetime.utcnow()))
    assert result['verdict'] == 'busy'
    assert result['transport_errors'] == 3
    assert not result['fast_400_only']


def test_mixed_bad_statuses_do_not_trigger_reboot(harness):
    replies = iter([400, 401, 400])
    result = asyncio.run(harness(lambda r: httpx.Response(next(replies)), datetime.utcnow()))
    assert result['verdict'] == 'busy'
    assert result['failure_statuses'] == {'400': 2, '401': 1}


def test_observed_fast_400_signature_with_recent_heartbeat_is_retained(harness):
    result = asyncio.run(harness(lambda r: httpx.Response(400), datetime.utcnow()))
    assert result['verdict'] == 'hung'
    assert result['fast_400_only']


@pytest.mark.parametrize('last_seen', [None, datetime(2020, 1, 1), datetime(2099, 1, 1)])
def test_header_read_timeout_does_not_prove_event_stream_alive(harness, last_seen):
    def respond(request):
        if request.url.path.endswith('eventManager.cgi'):
            raise httpx.ReadTimeout('No response headers', request=request)
        return httpx.Response(400)
    result = asyncio.run(harness(respond, last_seen))
    assert result['verdict'] == 'unreachable'
    assert result['fast_400_only']


def test_actual_event_200_allows_observed_signature(harness):
    result = asyncio.run(harness(lambda r: httpx.Response(
        200 if r.url.path.endswith('eventManager.cgi') else 400)))
    assert result['verdict'] == 'hung'


def test_jpeg_success_overrides_an_unsupported_alternate_url(harness):
    replies = iter([httpx.Response(404), httpx.Response(200, content=b'\xff\xd8jpeg')])
    result = asyncio.run(harness(lambda r: next(replies)))
    assert result['verdict'] == 'healthy' and result['ok'] == 1


def test_slow_400_among_fast_400s_requires_diagnosis(harness, monkeypatch):
    ticks = iter([0, .01, 1, 1.3, 2, 2.01])
    monkeypatch.setattr(health, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))
    result = asyncio.run(harness(lambda r: httpx.Response(400), datetime.utcnow()))
    assert result['verdict'] == 'busy' and not result['fast_400_only']


def test_automatic_runner_does_not_reboot_camera_with_credential_failure(harness, monkeypatch):
    cam = SimpleNamespace(id='camera', site_id='site', name='Camera',
                          ip_address='203.0.113.10', last_seen=datetime.utcnow())
    db = MagicMock()
    db.query.return_value.join.return_value.filter.return_value.all.return_value = [cam]
    monkeypatch.setattr(health, 'SessionLocal', lambda: db)
    monkeypatch.setattr(health, 'get_state', lambda *args: {})
    monkeypatch.setattr(health, 'camera_credentials', lambda cam: ('test', 'test'))
    reboot = AsyncMock()
    monkeypatch.setattr(health, 'reboot_camera', reboot)
    monkeypatch.setattr(barrier, 'close_camera_clients', AsyncMock())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as client:
            monkeypatch.setattr(barrier, 'camera_client', lambda ip: client)
            return await health._run_all(False, {'samples': 1, 'auto_reboot': True})
    result = asyncio.run(run())
    assert result['rebooted'] == [] and result['hung'] == []
    reboot.assert_not_awaited()
    db.add.assert_not_called()

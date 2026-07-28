"""set_screen — олон мөрийг Custom=жагсаалтаар илгээх (Monnis 2026-07-28 туршилт).

Firmware нь Custom доторх мөр таслалыг үл ойшоож бүгдийг 1-р мөрөнд урсгадаг;
Custom-ыг жагсаалтаар өгвөл мөр тус бүр LogicScreens-ийн 1/2/3-р мөрөнд гардаг
нь LED дээр нүдээр батлагдсан. Жагсаалт дэмжихгүй хуучин firmware-д нэг мөрийн
fallback ажиллана.
"""
import pytest

from app.services.barrier import DahuaRpc


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _CapturingRpc(DahuaRpc):
    def __init__(self, reject_list=False):
        super().__init__(None, "192.0.2.1", "u", "p")
        self.calls = []
        self.reject_list = reject_list

    async def _call(self, method, params=None, url="/RPC2", obj=None):
        self.calls.append((method, params))
        if self.reject_list and isinstance((params or {}).get("Custom"), list):
            return {"result": False}
        return {"result": True}


@pytest.mark.anyio
async def test_multiline_sent_as_list():
    rpc = _CapturingRpc()
    await rpc.set_screen("1234УБА\n2ts 05min\nTulbur: 3500")
    assert len(rpc.calls) == 1
    method, params = rpc.calls[0]
    assert method == "trafficParking.setScreenDisplay"
    assert params["Custom"] == ["1234УБА", "2ts 05min", "Tulbur: 3500"]


@pytest.mark.anyio
async def test_single_line_stays_string():
    rpc = _CapturingRpc()
    await rpc.set_screen("Sain yavaarai!")
    assert rpc.calls[0][1]["Custom"] == "Sain yavaarai!"


@pytest.mark.anyio
async def test_pipe_separator_becomes_list():
    rpc = _CapturingRpc()
    await rpc.set_screen("9999АБВ|Tavtai morilno uu")
    assert rpc.calls[0][1]["Custom"] == ["9999АБВ", "Tavtai morilno uu"]


@pytest.mark.anyio
async def test_old_firmware_falls_back_to_joined_string():
    rpc = _CapturingRpc(reject_list=True)
    await rpc.set_screen("1234УБА\nTulbur: 3500")
    assert len(rpc.calls) == 2, "жагсаалт татгалзвал нэг мөрөөр дахин илгээнэ"
    assert isinstance(rpc.calls[1][1]["Custom"], str)
    assert rpc.calls[1][1]["Custom"] == "1234УБА\nTulbur: 3500"

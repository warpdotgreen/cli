"""Height-monitor pings. No live chain: node, Ethereum RPC, and HTTP are faked."""

from __future__ import annotations

import asyncio
import logging

import pytest

from commands.monitoring import (
    ETH_INTERVAL_SECONDS,
    XCH_INTERVAL_SECONDS,
    HeightMonitor,
    default_http_get,
    monitors_for_config,
    start_height_monitors,
)


class _Peak:
    def __init__(self, height: int):
        self.height = height


class _Node:
    def __init__(self, heights: list[int]):
        self.heights = list(heights)
        self.closed = 0

    async def get_blockchain_state(self):
        return {"peak": _Peak(self.heights.pop(0))}

    def close(self):
        self.closed += 1

    async def await_closed(self):
        return None


class _Response:
    def __init__(self, payload=None, status: int = 200):
        self.status = status
        self._payload = {} if payload is None else payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def read(self):
        return b""

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _Session:
    posts: list[tuple[str, dict]] = []
    gets: list[str] = []
    eth_results: list[str] = []
    get_error: BaseException | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None, **kwargs):
        _Session.posts.append((url, json))
        result = _Session.eth_results.pop(0)
        return _Response({"result": result})

    def get(self, url, **kwargs):
        if _Session.get_error is not None:
            raise _Session.get_error
        _Session.gets.append(url)
        return _Response()


def _reset_session():
    _Session.posts = []
    _Session.gets = []
    _Session.eth_results = []
    _Session.get_error = None


def test_missing_or_empty_monitoring_url_leaves_chain_off():
    missing = monitors_for_config({})
    assert [(m.chain, m.url, m.interval) for m in missing] == [
        ("xch", None, XCH_INTERVAL_SECONDS),
        ("eth", None, ETH_INTERVAL_SECONDS),
    ]

    empty = monitors_for_config(
        {
            "xch": {"monitoring_url": ""},
            "eth": {"monitoring_url": "   "},
            "bse": {"monitoring_url": "https://hc.example/bse"},
        }
    )
    assert [m.chain for m in empty] == ["xch", "eth"]
    assert [m.url for m in empty] == [None, None]


def test_monitoring_url_does_not_use_get_config_item(monkeypatch):
    def boom(path):
        raise AssertionError(f"get_config_item called with {path}")

    monkeypatch.setattr("commands.config.get_config_item", boom)
    monitors = monitors_for_config(
        {
            "xch": {"monitoring_url": "https://hc.example/chia"},
            "eth": {
                "monitoring_url": "https://hc.example/eth",
                "rpc_url": "http://127.0.0.1:8545",
            },
        }
    )
    assert monitors[0].url == "https://hc.example/chia"
    assert monitors[1].url == "https://hc.example/eth"


@pytest.mark.asyncio
async def test_chia_changed_height_pings_once_and_first_height_pings():
    node = _Node([100, 100, 101])

    async def open_node():
        return node

    pings: list[str] = []

    async def http_get(url: str) -> None:
        pings.append(url)

    monitor = monitors_for_config(
        {"xch": {"monitoring_url": "https://hc.example/chia"}},
        open_chia_node=open_node,
    )[0]

    await monitor.tick(http_get)
    await monitor.tick(http_get)
    await monitor.tick(http_get)

    assert pings == [
        "https://hc.example/chia?status=up&ping=&msg=100",
        "https://hc.example/chia?status=up&ping=&msg=101",
    ]
    assert node.closed == 3


@pytest.mark.asyncio
async def test_eth_changed_height_pings_once_and_first_height_pings(monkeypatch):
    _reset_session()
    _Session.eth_results = ["0x64", "0x64", "0x65"]
    monkeypatch.setattr("commands.monitoring.aiohttp.ClientSession", _Session)

    eth = monitors_for_config(
        {
            "eth": {
                "monitoring_url": "https://hc.example/eth",
                "rpc_url": "http://127.0.0.1:8545",
            },
            "bse": {
                "monitoring_url": "https://hc.example/bse",
                "rpc_url": "http://127.0.0.1:8546",
            },
        }
    )[1]
    assert eth.chain == "eth"
    assert eth.interval == ETH_INTERVAL_SECONDS

    await eth.tick(default_http_get)
    await eth.tick(default_http_get)
    await eth.tick(default_http_get)

    assert _Session.gets == [
        "https://hc.example/eth?status=up&ping=&msg=100",
        "https://hc.example/eth?status=up&ping=&msg=101",
    ]
    assert [url for url, _payload in _Session.posts] == ["http://127.0.0.1:8545"] * 3
    assert all(payload["method"] == "eth_blockNumber" for _url, payload in _Session.posts)
    assert all(payload["params"] == [] for _url, payload in _Session.posts)
    assert all("bse" not in url and "8546" not in url for url in _Session.gets)


@pytest.mark.asyncio
async def test_no_monitoring_url_sends_no_ping():
    pings: list[str] = []

    async def http_get(url: str) -> None:
        pings.append(url)

    async def read_height():
        raise AssertionError("height should not be read when the chain is off")

    for chain, interval in (("xch", XCH_INTERVAL_SECONDS), ("eth", ETH_INTERVAL_SECONDS)):
        monitor = HeightMonitor(chain, None, interval, read_height)
        await monitor.tick(http_get)
        await monitor.run(http_get=http_get)

    for monitor in monitors_for_config(
        {"xch": {"monitoring_url": ""}, "eth": {}, "bse": {"monitoring_url": "https://hc.example/bse"}}
    ):
        await monitor.tick(http_get)
        await monitor.run(http_get=http_get)

    assert pings == []


@pytest.mark.asyncio
async def test_base_is_not_pinged():
    node = _Node([4])
    posts: list[str] = []
    pings: list[str] = []

    async def open_node():
        return node

    async def eth_rpc_post(url: str, payload: dict) -> dict:
        posts.append(url)
        assert payload["method"] == "eth_blockNumber"
        return {"result": "0xa"}

    async def http_get(url: str) -> None:
        pings.append(url)

    monitors = monitors_for_config(
        {
            "xch": {"monitoring_url": "https://hc.example/chia"},
            "eth": {
                "monitoring_url": "https://hc.example/eth",
                "rpc_url": "http://127.0.0.1:8545",
            },
            "bse": {
                "monitoring_url": "https://hc.example/bse",
                "rpc_url": "http://127.0.0.1:8546",
            },
        },
        open_chia_node=open_node,
        eth_rpc_post=eth_rpc_post,
    )
    assert [m.chain for m in monitors] == ["xch", "eth"]

    for monitor in monitors:
        await monitor.tick(http_get)

    assert pings == [
        "https://hc.example/chia?status=up&ping=&msg=4",
        "https://hc.example/eth?status=up&ping=&msg=10",
    ]
    assert posts == ["http://127.0.0.1:8545"]
    assert all("bse" not in url and "8546" not in url for url in pings + posts)


@pytest.mark.asyncio
async def test_non_positive_height_does_not_ping():
    node = _Node([0, 8])
    pings: list[str] = []

    async def open_node():
        return node

    async def http_get(url: str) -> None:
        pings.append(url)

    monitor = monitors_for_config(
        {"xch": {"monitoring_url": "https://hc.example/chia"}},
        open_chia_node=open_node,
    )[0]
    await monitor.tick(http_get)
    await monitor.tick(http_get)
    assert pings == ["https://hc.example/chia?status=up&ping=&msg=8"]


@pytest.mark.asyncio
async def test_failed_ping_is_logged_and_does_not_stop(caplog):
    async def read_height():
        return 7

    async def http_get(url: str) -> None:
        raise OSError("connection refused")

    monitor = HeightMonitor("eth", "https://hc.example/eth", ETH_INTERVAL_SECONDS, read_height)
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        raise asyncio.CancelledError

    with caplog.at_level(logging.ERROR):
        with pytest.raises(asyncio.CancelledError):
            await monitor.run(http_get=http_get, sleep=fake_sleep)

    assert slept == [ETH_INTERVAL_SECONDS]
    assert any("ping failed" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_monitor_intervals_use_a_fake_clock():
    heights = {"xch": 3, "eth": 9}
    pings: list[str] = []

    async def http_get(url: str) -> None:
        pings.append(url)

    slept: list[tuple[str, float]] = []

    async def sleep_for(chain: str):
        async def fake_sleep(seconds: float) -> None:
            slept.append((chain, seconds))
            raise asyncio.CancelledError

        return fake_sleep

    for chain, interval, url in (
        ("xch", XCH_INTERVAL_SECONDS, "https://hc.example/chia"),
        ("eth", ETH_INTERVAL_SECONDS, "https://hc.example/eth"),
    ):
        async def read_height(chain=chain):
            return heights[chain]

        monitor = HeightMonitor(chain, url, interval, read_height)
        with pytest.raises(asyncio.CancelledError):
            await monitor.run(http_get=http_get, sleep=await sleep_for(chain))

    assert slept == [("xch", 120), ("eth", 60)]
    assert pings == [
        "https://hc.example/chia?status=up&ping=&msg=3",
        "https://hc.example/eth?status=up&ping=&msg=9",
    ]


@pytest.mark.asyncio
async def test_start_height_monitors_missing_keys_does_not_ping(monkeypatch):
    async def fail_node(*args, **kwargs):
        raise AssertionError("node contacted")

    monkeypatch.setattr("commands.cli_wrappers.get_node_client", fail_node)

    class BoomSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError("http session opened")

    monkeypatch.setattr("commands.monitoring.aiohttp.ClientSession", BoomSession)

    tasks = start_height_monitors(
        asyncio.get_running_loop(),
        {"bse": {"monitoring_url": "https://hc.example/bse", "rpc_url": "http://127.0.0.1:8546"}},
    )
    assert len(tasks) == 2
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

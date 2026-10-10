"""Chia and Ethereum height pings for `cli listen`.

Matches monitor-chia.sh (every 120s) and monitor-eth.sh (every 60s):
GET {monitoring_url}?status=up&ping=&msg={height} when the height is a
positive integer and different from the last pinged height. The previous
height starts empty, so the first valid height pings. Base is not monitored.
A missing or empty monitoring_url leaves that chain off.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import aiohttp

XCH_INTERVAL_SECONDS = 120
ETH_INTERVAL_SECONDS = 60

OpenNode = Callable[[], Awaitable[object]]
HttpGet = Callable[[str], Awaitable[None]]
RpcPost = Callable[[str, dict], Awaitable[dict]]


def monitoring_ping_url(url: str, height: int) -> str:
    return f"{url}?status=up&ping=&msg={height}"


def positive_height(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value <= 0:
        return None
    return int(value)


def _config_string(cfg: dict, section: str, key: str) -> Optional[str]:
    block = cfg.get(section)
    if not isinstance(block, dict):
        return None
    raw = block.get(key)
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value or None


def monitoring_url_from_config(cfg: dict, chain: str) -> Optional[str]:
    return _config_string(cfg, chain, "monitoring_url")


async def _close_node(node: object) -> None:
    try:
        node.close()
        await node.await_closed()
    except Exception:
        logging.error("xch height monitor: error closing full node", exc_info=True)


async def read_chia_peak_height(open_node: Optional[OpenNode] = None) -> int:
    """Peak height via get_blockchain_state, same read as ChiaFollower."""
    if open_node is None:
        from commands.cli_wrappers import get_node_client

        async def default_open_node() -> object:
            return await get_node_client("xch")

        opener: OpenNode = default_open_node
    else:
        opener = open_node

    node = await opener()
    if node is None:
        raise RuntimeError("Could not connect to xch full node")
    try:
        return (await node.get_blockchain_state())["peak"].height
    finally:
        await _close_node(node)


async def _post_json(url: str, payload: dict) -> dict:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as response:
            response.raise_for_status()
            data = await response.json()
    if not isinstance(data, dict):
        raise RuntimeError("eth_blockNumber response was not an object")
    return data


async def read_eth_block_number(
    rpc_url: str,
    post_json: Optional[RpcPost] = None,
) -> int:
    if not rpc_url:
        raise RuntimeError("eth.rpc_url is not configured")
    post = post_json or _post_json
    data = await post(
        rpc_url,
        {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
    )
    result = data.get("result", "0")
    return int(result, 16)


async def default_http_get(url: str) -> None:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            await response.read()
            if response.status >= 400:
                raise RuntimeError(f"monitoring ping HTTP {response.status}")


class HeightMonitor:
    def __init__(
        self,
        chain: str,
        url: Optional[str],
        interval: int,
        read_height: Callable[[], Awaitable[object]],
    ):
        self.chain = chain
        self.url = url.strip() if isinstance(url, str) else ""
        self.url = self.url or None
        self.interval = interval
        self.read_height = read_height
        self.last_height: Optional[int] = None

    async def tick(self, http_get: HttpGet) -> None:
        """One check, without sleeping. Safe to call from tests."""
        if self.url is None:
            return
        try:
            height = positive_height(await self.read_height())
        except Exception:
            logging.warning(
                f"{self.chain} height monitor: could not retrieve a valid height",
                exc_info=True,
            )
            return
        if height is None:
            logging.warning(f"{self.chain} height monitor: could not retrieve a valid height")
            return
        if height == self.last_height:
            return
        try:
            logging.info(f"{self.chain} height monitor: sending ping for height {height}")
            await http_get(monitoring_ping_url(self.url, height))
        except Exception:
            logging.error(
                f"{self.chain} height monitor: ping failed for height {height}",
                exc_info=True,
            )
        self.last_height = height

    async def run(
        self,
        http_get: Optional[HttpGet] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        if self.url is None:
            return
        http_get = default_http_get if http_get is None else http_get
        sleep = asyncio.sleep if sleep is None else sleep
        logging.info(f"Starting {self.chain} height monitor")
        while True:
            await self.tick(http_get)
            await sleep(self.interval)


def monitors_for_config(
    cfg: Optional[dict] = None,
    *,
    open_chia_node: Optional[OpenNode] = None,
    eth_rpc_post: Optional[RpcPost] = None,
) -> list[HeightMonitor]:
    """XCH and ETH monitors only. Ignores bse even if that section has a URL."""
    if not isinstance(cfg, dict):
        cfg = {}
    rpc_url = _config_string(cfg, "eth", "rpc_url") or ""

    async def read_chia() -> int:
        return await read_chia_peak_height(open_chia_node)

    async def read_eth() -> int:
        return await read_eth_block_number(rpc_url, eth_rpc_post)

    return [
        HeightMonitor(
            "xch",
            monitoring_url_from_config(cfg, "xch"),
            XCH_INTERVAL_SECONDS,
            read_chia,
        ),
        HeightMonitor(
            "eth",
            monitoring_url_from_config(cfg, "eth"),
            ETH_INTERVAL_SECONDS,
            read_eth,
        ),
    ]


def start_height_monitors(loop, cfg: Optional[dict] = None) -> list[asyncio.Task]:
    if cfg is None:
        from commands.config import config as cfg
    tasks = []
    for monitor in monitors_for_config(cfg):
        tasks.append(loop.create_task(monitor.run()))
    return tasks

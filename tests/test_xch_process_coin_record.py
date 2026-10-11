"""Bridging-coin parent fetch stops when the child coin reorgs. No full node."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.sized_bytes import bytes32

from commands.followers.xch_follower import ChiaFollower


def _follower() -> ChiaFollower:
    follower = ChiaFollower.__new__(ChiaFollower)
    follower.chain = "xch"
    follower.chain_id = b"xch"
    return follower


def _coins():
    parent = Coin(bytes32(b"\x01" * 32), bytes32(b"\x02" * 32), 1)
    bridging = Coin(parent.name(), bytes32(b"\xaa" * 32), 5)
    coin_record = SimpleNamespace(coin=bridging, confirmed_block_index=10)
    return parent, bridging, coin_record


@pytest.mark.parametrize("case", ["gone", "height"])
def test_disappeared_child_stops_parent_fetch(case: str):
    asyncio.run(_disappeared_child_stops_parent_fetch(case))


async def _disappeared_child_stops_parent_fetch(case: str):
    parent, bridging, coin_record = _coins()
    follower = _follower()
    child_id = bridging.name()
    parent_id = parent.name()

    class Node:
        def __init__(self):
            self.calls: list[bytes] = []
            self.child_seen = 0

        async def get_coin_record_by_name(self, name):
            self.calls.append(name)
            if name == child_id:
                self.child_seen += 1
                if self.child_seen == 1:
                    return coin_record
                if case == "height":
                    return SimpleNamespace(
                        coin=bridging,
                        confirmed_block_index=coin_record.confirmed_block_index + 1,
                    )
                return None
            return None

        async def get_puzzle_and_solution(self, name, height):
            raise AssertionError("parent spend fetched without a parent record")

    node = Node()

    async def fast_sleep(_seconds):
        return None

    with patch("commands.followers.xch_follower.asyncio.sleep", fast_sleep):
        result = await asyncio.wait_for(
            follower.processCoinRecord(None, node, coin_record),
            timeout=2,
        )

    assert result is True
    assert node.calls[0] == child_id
    assert node.child_seen >= 2
    assert node.calls.count(parent_id) == 1
    assert node.calls[-1] == child_id


class _StopListener(Exception):
    pass


def _query():
    class Query:
        def __init__(self):
            self.ordered = False

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            self.ordered = True
            return self

        def first(self):
            if self.ordered:
                return (1,)
            return None

    class Db:
        def query(self, *args, **kwargs):
            return Query()

    return Db()


def _later_coin():
    parent = Coin(bytes32(b"\x03" * 32), bytes32(b"\x04" * 32), 1)
    bridging = Coin(parent.name(), bytes32(b"\xbb" * 32), 6)
    return bridging, SimpleNamespace(coin=bridging, confirmed_block_index=20)


@pytest.mark.parametrize("case", ["gone", "height"])
def test_listener_reorg_signal_drops_stale_batch(case: str):
    asyncio.run(_listener_reorg_signal_drops_stale_batch(case))


async def _listener_reorg_signal_drops_stale_batch(case: str):
    _parent, bridging, coin_record = _coins()
    later, later_record = _later_coin()
    follower = _follower()
    follower.per_message_toll = 1
    follower.sign_min_height = 0
    follower.control_config = None
    follower.getDb = _query

    class Node:
        def __init__(self):
            self.batch_fetches = 0
            self.child_seen = 0
            self.later_seen = 0
            self.sleeps: list[int] = []

        async def get_coin_records_by_puzzle_hash(self, puzzle_hash, include_spent_coins=False, start_height=None):
            self.batch_fetches += 1
            if self.batch_fetches == 1:
                return [later_record, coin_record]
            raise _StopListener()

        async def get_blockchain_state(self):
            return {"peak": SimpleNamespace(height=10_000)}

        async def get_coin_record_by_name(self, name):
            if name == bridging.name():
                self.child_seen += 1
                if self.child_seen <= 2:
                    return coin_record
                if case == "height":
                    return SimpleNamespace(
                        coin=bridging,
                        confirmed_block_index=coin_record.confirmed_block_index + 1,
                    )
                return None
            if name == later.name():
                self.later_seen += 1
                return later_record
            return None

        async def get_puzzle_and_solution(self, name, height):
            raise AssertionError("parent spend fetched without a parent record")

    node = Node()

    async def get_node(log: bool = True):
        return node

    follower.getNode = get_node

    async def fast_sleep(seconds):
        node.sleeps.append(seconds)

    async def allow_start(_config):
        return True

    def stop_exit(code=0):
        raise _StopListener(code)

    with (
        patch("commands.followers.xch_follower.asyncio.sleep", fast_sleep),
        patch("commands.followers.xch_follower.control_allows_start", allow_start),
        patch("commands.followers.xch_follower.sys.exit", stop_exit),
    ):
        with pytest.raises(_StopListener):
            await asyncio.wait_for(follower.messageListener(), timeout=2)

    assert node.batch_fetches == 2
    assert node.child_seen >= 3
    assert node.later_seen == 0
    assert 5 in node.sleeps
    assert 30 not in node.sleeps


def test_listener_normal_return_skips_coin_and_waits():
    asyncio.run(_listener_normal_return_skips_coin_and_waits())


async def _listener_normal_return_skips_coin_and_waits():
    _parent, bridging, coin_record = _coins()
    follower = _follower()
    follower.per_message_toll = 1
    follower.sign_min_height = 0
    follower.control_config = None
    follower.getDb = _query
    processed: list[bytes] = []
    sleeps: list[int] = []
    fetches = 0

    class Node:
        async def get_coin_records_by_puzzle_hash(self, puzzle_hash, include_spent_coins=False, start_height=None):
            nonlocal fetches
            fetches += 1
            if fetches > 2:
                raise _StopListener()
            return [coin_record]

        async def get_blockchain_state(self):
            return {"peak": SimpleNamespace(height=10_000)}

        async def get_coin_record_by_name(self, name):
            return coin_record

    async def get_node(log: bool = True):
        return Node()

    async def process(_db, _node, record):
        processed.append(record.coin.name())
        return None

    async def fast_sleep(seconds):
        sleeps.append(seconds)

    async def allow_start(_config):
        return True

    def stop_exit(code=0):
        raise _StopListener(code)

    follower.getNode = get_node
    follower.processCoinRecord = process

    with (
        patch("commands.followers.xch_follower.asyncio.sleep", fast_sleep),
        patch("commands.followers.xch_follower.control_allows_start", allow_start),
        patch("commands.followers.xch_follower.sys.exit", stop_exit),
    ):
        with pytest.raises(_StopListener):
            await asyncio.wait_for(follower.messageListener(), timeout=2)

    assert processed == [bridging.name(), bridging.name()]
    assert sleeps == [30, 30]

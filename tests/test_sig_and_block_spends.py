"""decode_signature and block-spend loading. No full node, no network."""

from __future__ import annotations

import pytest

from commands.followers.sig import MessageBroadcaster, decode_signature, encode_signature
from commands.models import Message, setup_database
from commands.policy_runtime import load_block_spends


def test_decode_signature_round_trip_with_coin_id():
    nonce = bytes(range(32))
    coin_id = bytes(range(32, 64))
    sig = bytes(range(96))
    encoded = encode_signature(b"eth", b"xch", nonce, coin_id, sig)

    origin, dest, got_nonce, got_coin, got_sig = decode_signature(encoded)

    assert origin == b"eth"
    assert dest == b"xch"
    assert got_nonce == nonce
    assert got_coin == coin_id
    assert got_sig == sig


def test_decode_signature_round_trip_without_coin_id():
    nonce = b"\xab" * 32
    sig = b"\xcd" * 96
    encoded = encode_signature(b"xch", b"eth", nonce, None, sig)
    assert encoded.split("-")[1] == ""

    origin, dest, got_nonce, got_coin, got_sig = decode_signature(encoded)

    assert origin == b"xch"
    assert dest == b"eth"
    assert got_nonce == nonce
    assert got_coin is None
    assert got_sig == sig


def test_clear_message_sig_resets_eth_bound_row(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'msgs.db'}"

    def open_db():
        return setup_database(db_url)

    monkeypatch.setattr("commands.followers.sig.setup_database", open_db)

    nonce = b"\x11" * 32
    encoded = encode_signature(b"xch", b"bse", nonce, None, b"\x22" * 96)
    db = open_db()
    db.add(Message(
        nonce=nonce,
        source_chain=b"xch",
        source=b"\x00" * 32,
        destination_chain=b"bse",
        destination=b"\x33" * 32,
        contents=b"",
        block_number=1,
        sig=encoded.encode(),
    ))
    db.commit()
    db.close()

    broadcaster = MessageBroadcaster.__new__(MessageBroadcaster)
    broadcaster._clear_message_sig(encoded)

    db = open_db()
    msg = db.query(Message).one()
    assert msg.sig == b""
    db.close()


class _Record:
    def __init__(self, header_hash: bytes):
        self.header_hash = header_hash


class _Coin:
    def __init__(self, name: bytes):
        self._name = name

    def name(self) -> bytes:
        return self._name


class _Removal:
    def __init__(self, name: bytes, height: int):
        self.coin = _Coin(name)
        self.spent_block_index = height


class _Node:
    def __init__(self, spends: dict, block_spends=None):
        self.spends = spends
        self.block_spends = block_spends
        self.header = b"\xab" * 32

    async def get_block_record_by_height(self, height: int):
        return _Record(self.header)

    async def get_block_spends(self, header_hash: bytes):
        return self.block_spends

    async def get_additions_and_removals(self, header_hash: bytes):
        removals = [_Removal(name, 7) for name in self.spends]
        return [], removals

    async def get_puzzle_and_solution(self, name: bytes, height: int):
        return self.spends[name]


@pytest.mark.asyncio
async def test_load_block_spends_returns_none_when_any_spend_is_missing():
    present = object()
    node = _Node({b"\x01" * 32: present, b"\x02" * 32: None})

    assert await load_block_spends(node, 7) is None


@pytest.mark.asyncio
async def test_load_block_spends_returns_complete_fallback():
    first, second = object(), object()
    node = _Node({b"\x01" * 32: first, b"\x02" * 32: second})

    spends = await load_block_spends(node, 7)

    assert spends == [first, second]


@pytest.mark.asyncio
async def test_load_block_spends_prefers_block_spends_rpc():
    direct = [object()]
    node = _Node({b"\x01" * 32: None}, block_spends=direct)

    assert await load_block_spends(node, 7) == direct

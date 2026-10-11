"""Pure spend-policy tests: no full node, no network."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import List, Optional, Tuple
from unittest.mock import patch

import pytest
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.serialized_program import SerializedProgram
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.wallet.cat_wallet.cat_utils import (
    CAT_MOD,
    SpendableCAT,
    construct_cat_puzzle,
    unsigned_spend_bundle_for_spendable_cats,
)
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.trading.offer import OFFER_MOD, OFFER_MOD_HASH

from commands import spend_policy
from commands.policy_runtime import evaluate_chia_message_policy
from commands.spend_policy import (
    BridgeRoutes,
    WrappedCatArtifact,
    WrappedCatImmutable,
    chia_row_matches_verified,
    compute_bridge_tip,
    decode_message_sent_fields,
    evm_address_from_bytes32,
    evm_log_matches_row,
    left_pad_32,
    load_wrapped_cat_artifact_bytecode,
    load_wrapped_cat_build_info,
    stamp_wrapped_cat_immutables,
    VerifiedChiaMessage,
    signed_destination_address,
    validate_chia_cat_wrap,
    validate_chia_erc20_unwrap,
    validate_evm_cat_unwrap,
    validate_evm_erc20_wrap,
    wrapped_cat_immutable_values_match,
)
from drivers.portal import BRIDGING_PUZZLE_HASH
from drivers.utils import raw_hash
from drivers.wrapped_assets import (
    get_burn_inner_puzzle_solution,
    get_cat_burn_inner_puzzle,
    get_cat_burner_puzzle,
    get_cat_burner_puzzle_solution,
    get_cat_minter_puzzle,
    get_wrapped_tail,
)
from drivers.wrapped_cats import (
    get_locker_puzzle,
    get_locker_solution,
    get_p2_controller_puzzle_hash_inner_puzzle_hash,
    get_unlocker_puzzle,
)

PORTAL = bytes32(b"\x01" * 32)
ETH_BRIDGE = bytes.fromhex("22" * 20)
ETH_PORTAL = bytes.fromhex("11" * 20)
MILLIETH = bytes.fromhex("33" * 20)
BSE_BRIDGE = bytes.fromhex("66" * 20)
WRAPPED_CAT_ADDR = bytes.fromhex("99" * 20)
ASSET_A = left_pad_32(bytes.fromhex("aa" * 20))
ASSET_B = left_pad_32(bytes.fromhex("cc" * 20))
RECEIVER = bytes.fromhex("bb" * 20)
DEST_CHAIN = b"eth"
CAT_AMOUNT = 10_000
BURNER_AMOUNT = 1
BRIDGING_TOLL = 10**9
ASSET_AMOUNT = 1_337_000
CHANGE_AMOUNT = 10_000
WRAP_TIP_BPS = 30
WRAP_MOJO_TO_TOKEN_RATIO = 10**12

Q_TAIL = Program.from_bytes(bytes.fromhex("ff0180"))


def _routes() -> BridgeRoutes:
    return BridgeRoutes.from_config(
        {
            "xch": {"portal_launcher_id": PORTAL.hex()},
            "eth": {
                "portal_address": "0x" + ETH_PORTAL.hex(),
                "erc20_bridge_address": "0x" + ETH_BRIDGE.hex(),
                "millieth_address": "0x" + MILLIETH.hex(),
                "portal_impl_codehash": "0x" + "44" * 32,
            },
            "bse": {
                "portal_address": "0x" + "55" * 20,
                "erc20_bridge_address": "0x" + BSE_BRIDGE.hex(),
                "millieth_address": "0x" + "77" * 20,
                "portal_impl_codehash": "0x" + "88" * 32,
            },
        }
    )


def _coin_spend(coin: Coin, puzzle: Program, solution: Program) -> CoinSpend:
    return CoinSpend(
        coin,
        SerializedProgram.from_bytes(bytes(puzzle)),
        SerializedProgram.from_bytes(bytes(solution)),
    )


def _build_erc20_burn(
    *,
    asset: bytes = ASSET_A,
    memo_asset: Optional[bytes] = None,
    tail: Optional[Program] = None,
    tail_hash_for_burner: Optional[bytes32] = None,
    dest_chain: bytes = DEST_CHAIN,
    dest_addr: bytes = ETH_BRIDGE,
    include_cat: bool = True,
    burner_puzzle: Optional[Program] = None,
) -> Tuple[Coin, CoinSpend, Optional[CoinSpend]]:
    """Build burner + CAT melt CoinSpends and the bridging coin they create."""
    memo_asset = asset if memo_asset is None else memo_asset
    burner_puzzle = burner_puzzle or get_cat_burner_puzzle(dest_chain, dest_addr)
    burner_parent = bytes32(b"\x02" * 32)
    burner_coin = Coin(burner_parent, burner_puzzle.get_tree_hash(), BURNER_AMOUNT)

    if tail is None:
        tail = get_wrapped_tail(PORTAL, dest_chain, dest_addr, asset)
    tail_hash = tail.get_tree_hash()
    if tail_hash_for_burner is None:
        tail_hash_for_burner = (
            get_wrapped_tail(PORTAL, dest_chain, dest_addr, memo_asset).get_tree_hash()
            if memo_asset != asset
            else tail_hash
        )

    inner = get_cat_burn_inner_puzzle(
        dest_chain, dest_addr, asset, RECEIVER, BURNER_AMOUNT
    )
    cat_puzzle = construct_cat_puzzle(CAT_MOD, tail_hash, inner, CAT_MOD_HASH)
    cat_parent = bytes32(b"\x03" * 32)
    cat_coin = Coin(cat_parent, cat_puzzle.get_tree_hash(), CAT_AMOUNT)

    cat_spend: Optional[CoinSpend] = None
    if include_cat:
        inner_sol = get_burn_inner_puzzle_solution(
            burner_parent, cat_coin.name(), tail
        )
        spendable = SpendableCAT(
            cat_coin,
            tail_hash,
            inner,
            inner_sol,
            lineage_proof=LineageProof(
                bytes32(b"\x04" * 32), bytes32(b"\x05" * 32), CAT_AMOUNT
            ),
            extra_delta=-CAT_AMOUNT,
            limitations_program_reveal=tail,
            limitations_solution=Program.to(
                (
                    raw_hash([b"\x01", RECEIVER]),
                    raw_hash([b"\x01", bytes([BURNER_AMOUNT])]),
                )
            ),
        )
        cat_spend = unsigned_spend_bundle_for_spendable_cats(
            CAT_MOD, [spendable]
        ).coin_spends[0]

    burner_sol = get_cat_burner_puzzle_solution(
        cat_parent,
        tail_hash_for_burner,
        CAT_AMOUNT,
        memo_asset,
        RECEIVER,
        burner_coin,
    )
    burner_spend = _coin_spend(burner_coin, burner_puzzle, burner_sol)
    bridging = Coin(burner_coin.name(), BRIDGING_PUZZLE_HASH, BURNER_AMOUNT)
    return bridging, burner_spend, cat_spend


def _vault_inner_ph(asset_id: Optional[bytes32]) -> bytes32:
    return get_p2_controller_puzzle_hash_inner_puzzle_hash(
        get_unlocker_puzzle(DEST_CHAIN, WRAPPED_CAT_ADDR, PORTAL, asset_id).get_tree_hash()
    ).get_tree_hash()


def _build_lock(
    *,
    asset_id: Optional[bytes32],
    with_change: bool = False,
    omit_settlement: bool = False,
    empty_settlement_payments: bool = False,
    spend_vault: bool = False,
) -> Tuple[Coin, CoinSpend, List[CoinSpend]]:
    toll = BRIDGING_TOLL
    locker_puzzle = get_locker_puzzle(DEST_CHAIN, WRAPPED_CAT_ADDR, PORTAL, asset_id)
    locker_parent = bytes32(b"\x10" * 32)
    locker_coin = Coin(locker_parent, locker_puzzle.get_tree_hash(), toll)
    locker_sol = get_locker_solution(
        toll, locker_coin.name(), ASSET_AMOUNT, RECEIVER
    )
    locker_spend = _coin_spend(locker_coin, locker_puzzle, locker_sol)
    bridging = Coin(locker_coin.name(), BRIDGING_PUZZLE_HASH, toll)

    vault_inner_ph = _vault_inner_ph(asset_id)
    other: List[CoinSpend] = []
    if omit_settlement:
        return bridging, locker_spend, other

    offer_parent = bytes32(b"\x11" * 32)
    if empty_settlement_payments:
        payments: list = []
    elif with_change and asset_id is not None:
        change_ph = bytes32(b"\xcd" * 32)
        payments = [
            [locker_coin.name(), [vault_inner_ph, ASSET_AMOUNT]],
            [bytes32(b"\xee" * 32), [change_ph, CHANGE_AMOUNT]],
        ]
    else:
        payments = [[locker_coin.name(), [vault_inner_ph, ASSET_AMOUNT]]]

    if asset_id is None:
        offer_coin = Coin(
            offer_parent,
            OFFER_MOD_HASH,
            ASSET_AMOUNT if payments else 1,
        )
        settlement = _coin_spend(offer_coin, OFFER_MOD, Program.to(payments))
    else:
        total = ASSET_AMOUNT + (CHANGE_AMOUNT if with_change else 0)
        if empty_settlement_payments:
            total = max(total, 1)
        cat_offer_puzzle = construct_cat_puzzle(
            CAT_MOD, asset_id, OFFER_MOD, CAT_MOD_HASH
        )
        cat_offer_coin = Coin(
            offer_parent, cat_offer_puzzle.get_tree_hash(), total
        )
        spendable = SpendableCAT(
            cat_offer_coin,
            asset_id,
            OFFER_MOD,
            Program.to(payments),
            lineage_proof=LineageProof(
                bytes32(b"\x04" * 32), OFFER_MOD_HASH, total
            ),
        )
        settlement = unsigned_spend_bundle_for_spendable_cats(
            CAT_MOD, [spendable]
        ).coin_spends[0]
    other.append(settlement)

    if spend_vault and payments:
        if asset_id is None:
            vault_ph = vault_inner_ph
            vault_puzzle: Program = get_p2_controller_puzzle_hash_inner_puzzle_hash(
                get_unlocker_puzzle(
                    DEST_CHAIN, WRAPPED_CAT_ADDR, PORTAL, asset_id
                ).get_tree_hash()
            )
        else:
            vault_inner = get_p2_controller_puzzle_hash_inner_puzzle_hash(
                get_unlocker_puzzle(
                    DEST_CHAIN, WRAPPED_CAT_ADDR, PORTAL, asset_id
                ).get_tree_hash()
            )
            vault_puzzle = construct_cat_puzzle(
                CAT_MOD, asset_id, vault_inner, CAT_MOD_HASH
            )
            vault_ph = vault_puzzle.get_tree_hash()
        vault_coin = Coin(settlement.coin.name(), vault_ph, ASSET_AMOUNT)
        # Spend the just-created vault in the same list; policy only needs the create.
        vault_spend = _coin_spend(
            vault_coin, vault_puzzle, Program.to([])
        )
        other.append(vault_spend)

    return bridging, locker_spend, other


def _wrap_stamp_kwargs():
    return dict(
        portal=ETH_PORTAL,
        other_chain=b"xch",
        tip=WRAP_TIP_BPS,
        mojo_to_token_ratio=WRAP_MOJO_TO_TOKEN_RATIO,
    )


def _wrap_views(locker_ph: bytes32):
    try:
        artifact = load_wrapped_cat_build_info()
    except FileNotFoundError:
        return None
    code = stamp_wrapped_cat_immutables(
        artifact.bytecode,
        artifact.immutables,
        **_wrap_stamp_kwargs(),
    )
    return dict(
        wrapped_cat_runtime_code=code,
        view_portal=left_pad_32(ETH_PORTAL),
        view_other_chain=b"xch",
        view_locker_puzzle_hash=locker_ph,
        view_tip=WRAP_TIP_BPS,
        view_mojo_to_token_ratio=WRAP_MOJO_TO_TOKEN_RATIO,
    )


def _synthetic_wrapped_cat_artifact(
    *,
    portal_width: int = 32,
    extra: Optional[WrappedCatImmutable] = None,
) -> WrappedCatArtifact:
    """Standalone bytecode and per-id spans, used when build-info is absent."""
    portal_spans = ((4, portal_width), (4 + portal_width + 1, portal_width))
    immutables = [
        WrappedCatImmutable("1", "portal", portal_spans, "WrappedCAT"),
        WrappedCatImmutable("2", "tip", ((80, 32),), "WrappedCAT"),
        WrappedCatImmutable("3", "mojoToTokenRatio", ((120, 32),), "WrappedCAT"),
        WrappedCatImmutable("4", "otherChain", ((160, 32),), "WrappedCAT"),
    ]
    if extra is not None:
        immutables.append(extra)
    length = 240
    code = bytearray(b"\x5b" * length)
    for immutable in immutables:
        for start, span_length in immutable.spans:
            code[start:start + span_length] = b"\x00" * span_length
    immutables.sort(key=lambda item: item.ast_id)
    return WrappedCatArtifact(bytes(code), tuple(immutables), ("WrappedCAT",))


def _corrupt_portal_span(code: bytes, artifact: WrappedCatArtifact) -> bytes:
    portal = next(item for item in artifact.immutables if item.name == "portal")
    assert len(portal.spans) >= 2
    getter_start, getter_length = portal.spans[0]
    other_start, other_length = portal.spans[1]
    forged = bytearray(code)
    getter = bytes(forged[getter_start:getter_start + getter_length])
    attacker = b"\x00" * (getter_length - 20) + bytes.fromhex("44" * 20)
    assert getter != attacker
    forged[other_start:other_start + other_length] = attacker
    assert bytes(forged[getter_start:getter_start + getter_length]) == getter
    return bytes(forged)


# ---------------------------------------------------------------------------
# 1. Chia ERC-20 unwrap (burn)
# ---------------------------------------------------------------------------


class TestChiaErc20Unwrap:
    def test_honest_full_melt_accepts(self):
        bridging, burner, cat = _build_erc20_burn()
        result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        assert result.kind == "accept"
        assert result.reason == "chia erc20 unwrap"
        verified = result.verified
        assert verified is not None
        assert verified.source == bytes(burner.coin.puzzle_hash)
        assert verified.destination_chain == DEST_CHAIN
        assert signed_destination_address(verified.destination) == ETH_BRIDGE
        assert verified.contents == (
            ASSET_A,
            left_pad_32(RECEIVER),
            CAT_AMOUNT.to_bytes(32, "big"),
        )
        assert chia_row_matches_verified(
            source=verified.source,
            destination_chain=verified.destination_chain,
            destination=verified.destination,
            contents=verified.contents,
            verified=verified,
        )

    def test_row_asset_amount_or_receiver_must_match_memo(self):
        bridging, burner, cat = _build_erc20_burn()
        result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        verified = result.verified
        assert verified is not None
        asset, receiver, amount = verified.contents
        other_asset = ASSET_B
        other_receiver = left_pad_32(bytes.fromhex("ee" * 20))
        other_amount = (CAT_AMOUNT + 1).to_bytes(32, "big")
        base = dict(
            source=verified.source,
            destination_chain=verified.destination_chain,
            destination=left_pad_32(verified.destination),
            verified=verified,
        )
        assert chia_row_matches_verified(contents=verified.contents, **base)
        assert not chia_row_matches_verified(
            contents=(other_asset, receiver, amount), **base
        )
        assert not chia_row_matches_verified(
            contents=(asset, other_receiver, amount), **base
        )
        assert not chia_row_matches_verified(
            contents=(asset, receiver, other_amount), **base
        )

    def test_source_must_be_exact_puzzle_hash(self):
        bridging, burner, cat = _build_erc20_burn()
        result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        verified = result.verified
        assert verified is not None
        source = b"\x00" + verified.source[1:]
        verified = VerifiedChiaMessage(
            source=source,
            destination_chain=verified.destination_chain,
            destination=verified.destination,
            contents=verified.contents,
        )
        fields = dict(
            destination_chain=verified.destination_chain,
            destination=verified.destination,
            contents=verified.contents,
            verified=verified,
        )
        assert chia_row_matches_verified(source=source, **fields)
        assert not chia_row_matches_verified(source=source[1:], **fields)
        assert not chia_row_matches_verified(source=source + b"\x00", **fields)

    def test_q_tail_rejects(self):
        bridging, burner, cat = _build_erc20_burn(tail=Q_TAIL)
        result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        assert result.kind == "reject"
        assert result.reason == "tail_hash_hash mismatch"
        assert result.verified is None

    def test_tail_token_a_memo_token_b_rejects(self):
        bridging, burner, cat = _build_erc20_burn(
            asset=ASSET_A, memo_asset=ASSET_B
        )
        result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        assert result.kind == "reject"

    def test_memo_destination_chain_swapped_rejects(self):
        bridging, burner, cat = _build_erc20_burn()
        real = spend_policy._extract_bridging_memo(
            Program.from_bytes(bytes(burner.puzzle_reveal)),
            Program.from_bytes(bytes(burner.solution)),
            burner.coin.amount,
        )
        assert real is not None

        def _swapped(*_a, **_k):
            _chain, dest, contents = real
            return b"bse", dest, contents

        with patch.object(spend_policy, "_extract_bridging_memo", _swapped):
            result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        assert result.kind == "reject"
        assert "memo destination" in result.reason

    def test_partial_melt_extra_delta_rejects(self):
        bridging, burner, cat = _build_erc20_burn()
        assert cat is not None
        sol_args = list(Program.from_bytes(bytes(cat.solution)).as_iter())
        sol_args[6] = Program.to(0)  # not -amount
        cat = CoinSpend(
            cat.coin,
            cat.puzzle_reveal,
            SerializedProgram.from_bytes(bytes(Program.to(sol_args))),
        )
        result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        assert result.kind == "reject"
        assert "extra_delta" in result.reason

    def test_missing_cat_spend_rejects(self):
        bridging, burner, _ = _build_erc20_burn(include_cat=False)
        result = validate_chia_erc20_unwrap(bridging, burner, None, _routes())
        assert result.kind == "reject"
        assert result.reason == "missing CAT spend"

    def test_parent_not_burner_rejects(self):
        _, _, cat = _build_erc20_burn()
        fake = CoinSpend(
            Coin(bytes32(b"\x02" * 32), Program.to(1).get_tree_hash(), BURNER_AMOUNT),
            SerializedProgram.from_bytes(bytes(Program.to(1))),
            SerializedProgram.from_bytes(bytes(Program.to([]))),
        )
        bridging = Coin(fake.coin.name(), BRIDGING_PUZZLE_HASH, fake.coin.amount)
        result = validate_chia_erc20_unwrap(bridging, fake, cat, _routes())
        assert result.kind == "reject"
        assert "CAT_BURNER_MOD" in result.reason or "parent" in result.reason


class _MissingCoinNode:
    async def get_coin_record_by_name(self, _name):
        return None


async def _evaluate_unloaded_burn(burner_kwargs: dict):
    bridging, burner, _ = _build_erc20_burn(**burner_kwargs)
    return await evaluate_chia_message_policy(
        bridging_coin=bridging,
        parent_spend=burner,
        routes=_routes(),
        node=_MissingCoinNode(),
        block_spends=None,
        web3_for_chain=lambda _chain: None,
        evm_block_number=None,
    )


class TestUnloadedBurnerCatSpend:
    @pytest.mark.asyncio
    async def test_q_tail_missing_coin_rejects_tail_hash_hash_mismatch(self):
        result = await _evaluate_unloaded_burn({"tail": Q_TAIL})
        assert result.kind == "reject"
        assert result.reason == "tail_hash_hash mismatch"

    @pytest.mark.asyncio
    async def test_other_tail_missing_coin_rejects_tail_hash_hash_mismatch(self):
        result = await _evaluate_unloaded_burn({"tail": Program.to(1)})
        assert result.kind == "reject"
        assert result.reason == "tail_hash_hash mismatch"

    @pytest.mark.asyncio
    async def test_honest_wrapped_tail_missing_coin_retries(self):
        result = await _evaluate_unloaded_burn({})
        assert result.kind == "retry"
        assert result.reason == "could not load CAT spend for burner"


# ---------------------------------------------------------------------------
# 2. Chia CAT / XCH wrap (lock)
# ---------------------------------------------------------------------------


class TestChiaCatWrap:
    def test_honest_cat_lock_with_change_accepts(self):
        views = _wrap_views(bytes32(b"\x00" * 32))
        if views is None:
            pytest.skip("WrappedCAT artifacts missing")
        asset_id = bytes32(b"\xab" * 32)
        bridging, locker, other = _build_lock(asset_id=asset_id, with_change=True)
        views["view_locker_puzzle_hash"] = locker.coin.puzzle_hash
        result = validate_chia_cat_wrap(
            bridging, locker, other, _routes(), **views
        )
        assert result.kind == "accept"
        verified = result.verified
        assert verified is not None
        assert verified.source == bytes(locker.coin.puzzle_hash)
        assert verified.destination_chain == DEST_CHAIN
        assert signed_destination_address(verified.destination) == WRAPPED_CAT_ADDR
        assert verified.contents == (
            left_pad_32(RECEIVER),
            ASSET_AMOUNT.to_bytes(32, "big"),
        )

    def test_honest_xch_lock_accepts(self):
        views = _wrap_views(bytes32(b"\x00" * 32))
        if views is None:
            pytest.skip("WrappedCAT artifacts missing")
        bridging, locker, other = _build_lock(asset_id=None)
        views["view_locker_puzzle_hash"] = locker.coin.puzzle_hash
        result = validate_chia_cat_wrap(
            bridging, locker, other, _routes(), **views
        )
        assert result.kind == "accept"

    def test_missing_vault_settlement_rejects(self):
        views = _wrap_views(bytes32(b"\x00" * 32))
        if views is None:
            # Structural path still rejects before / without bytecode when views missing → retry
            bridging, locker, other = _build_lock(
                asset_id=None, omit_settlement=True
            )
            result = validate_chia_cat_wrap(
                bridging,
                locker,
                other,
                _routes(),
                wrapped_cat_runtime_code=None,
                view_portal=None,
                view_other_chain=None,
                view_locker_puzzle_hash=None,
            )
            assert result.kind == "retry"
            return
        bridging, locker, other = _build_lock(asset_id=None, omit_settlement=True)
        views["view_locker_puzzle_hash"] = locker.coin.puzzle_hash
        result = validate_chia_cat_wrap(
            bridging, locker, other, _routes(), **views
        )
        assert result.kind == "reject"
        assert "settlement" in result.reason

    def test_announcement_without_vault_create_rejects(self):
        views = _wrap_views(bytes32(b"\x00" * 32))
        if views is None:
            pytest.skip("WrappedCAT artifacts missing")
        bridging, locker, other = _build_lock(
            asset_id=None, empty_settlement_payments=True
        )
        views["view_locker_puzzle_hash"] = locker.coin.puzzle_hash
        result = validate_chia_cat_wrap(
            bridging, locker, other, _routes(), **views
        )
        assert result.kind == "reject"
        assert "settlement" in result.reason

    def test_vault_spent_in_same_list_still_accepts(self):
        views = _wrap_views(bytes32(b"\x00" * 32))
        if views is None:
            pytest.skip("WrappedCAT artifacts missing")
        bridging, locker, other = _build_lock(asset_id=None, spend_vault=True)
        views["view_locker_puzzle_hash"] = locker.coin.puzzle_hash
        result = validate_chia_cat_wrap(
            bridging, locker, other, _routes(), **views
        )
        assert result.kind == "accept"

    def test_parent_not_locker_rejects(self):
        fake = CoinSpend(
            Coin(bytes32(b"\x10" * 32), Program.to(1).get_tree_hash(), BRIDGING_TOLL),
            SerializedProgram.from_bytes(bytes(Program.to(1))),
            SerializedProgram.from_bytes(bytes(Program.to([]))),
        )
        bridging = Coin(fake.coin.name(), BRIDGING_PUZZLE_HASH, BRIDGING_TOLL)
        result = validate_chia_cat_wrap(
            bridging,
            fake,
            [],
            _routes(),
            wrapped_cat_runtime_code=b"\x00",
            view_portal=left_pad_32(ETH_PORTAL),
            view_other_chain=b"xch",
            view_locker_puzzle_hash=fake.coin.puzzle_hash,
        )
        assert result.kind == "reject"
        assert "LOCKER_MOD" in result.reason or "parent" in result.reason

    def test_artifact_load_error_names_the_exception(self):
        with patch(
            "commands.spend_policy.match_wrapped_cat_bytecode",
            side_effect=FileNotFoundError("no WrappedCAT in artifacts/build-info"),
        ):
            result = validate_evm_cat_unwrap(
                routes=_routes(),
                source_chain=b"eth",
                source=left_pad_32(WRAPPED_CAT_ADDR),
                destination=b"\x24" * 32,
                destination_chain=b"xch",
                contents=[left_pad_32(RECEIVER), (1).to_bytes(32, "big")],
                tx_status=1,
                tip_bps=30,
                mojo_to_token_ratio=1,
                gross_mojo_amount=1,
                sender_balance_diff=1,
                portal_balance_diff=1,
                view_portal=left_pad_32(ETH_PORTAL),
                view_other_chain=b"xch",
                unlocker_puzzle_hash=b"\x24" * 32,
                wrapped_cat_runtime_code=b"\x60\x00",
            )
        assert result.kind == "retry"
        assert "could not load WrappedCAT artifact for mask compare" in result.reason
        assert "FileNotFoundError" in result.reason
        assert "no WrappedCAT in artifacts/build-info" in result.reason

    def test_load_wrapped_cat_build_info_from_other_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        artifact = load_wrapped_cat_build_info()
        assert isinstance(artifact, WrappedCatArtifact)
        assert artifact.bytecode
        assert artifact.immutables

    def test_bytecode_mismatch_rejects_when_artifacts_exist(self):
        try:
            code = load_wrapped_cat_artifact_bytecode()
        except FileNotFoundError:
            pytest.skip("WrappedCAT artifacts missing")
        bridging, locker, other = _build_lock(asset_id=None)
        result = validate_chia_cat_wrap(
            bridging,
            locker,
            other,
            _routes(),
            wrapped_cat_runtime_code=code + b"\xff",
            view_portal=left_pad_32(ETH_PORTAL),
            view_other_chain=b"xch",
            view_locker_puzzle_hash=locker.coin.puzzle_hash,
        )
        assert result.kind == "reject"
        assert "bytecode" in result.reason

    def test_empty_runtime_rejects_without_views(self):
        try:
            load_wrapped_cat_artifact_bytecode()
        except FileNotFoundError:
            pytest.skip("WrappedCAT artifacts missing")
        bridging, locker, other = _build_lock(asset_id=None)
        result = validate_chia_cat_wrap(
            bridging,
            locker,
            other,
            _routes(),
            wrapped_cat_runtime_code=b"",
            view_portal=None,
            view_other_chain=None,
            view_locker_puzzle_hash=None,
        )
        assert result.kind == "reject"
        assert "bytecode" in result.reason

    def test_unloaded_runtime_still_retries(self):
        bridging, locker, other = _build_lock(asset_id=None)
        result = validate_chia_cat_wrap(
            bridging,
            locker,
            other,
            _routes(),
            wrapped_cat_runtime_code=None,
            view_portal=None,
            view_other_chain=None,
            view_locker_puzzle_hash=None,
        )
        assert result.kind == "retry"
        assert "WrappedCAT" in result.reason

    def test_divergent_portal_span_rejects_while_getter_stays(self):
        bridging, locker, other = _build_lock(asset_id=None)
        views = _wrap_views(locker.coin.puzzle_hash)
        if views is None:
            artifact = _synthetic_wrapped_cat_artifact()
            code = stamp_wrapped_cat_immutables(
                artifact.bytecode, artifact.immutables, **_wrap_stamp_kwargs()
            )
            views = dict(
                wrapped_cat_runtime_code=code,
                view_portal=left_pad_32(ETH_PORTAL),
                view_other_chain=b"xch",
                view_locker_puzzle_hash=locker.coin.puzzle_hash,
                view_tip=WRAP_TIP_BPS,
                view_mojo_to_token_ratio=WRAP_MOJO_TO_TOKEN_RATIO,
            )
            patcher = patch.object(
                spend_policy, "load_wrapped_cat_build_info", return_value=artifact
            )
        else:
            artifact = load_wrapped_cat_build_info()
            code = views["wrapped_cat_runtime_code"]
            patcher = None
        forged = _corrupt_portal_span(code, artifact)
        forged_views = dict(views)
        forged_views["wrapped_cat_runtime_code"] = forged
        forged_views["view_portal"] = left_pad_32(ETH_PORTAL)

        def run():
            assert wrapped_cat_immutable_values_match(code, **_wrap_stamp_kwargs())
            result = validate_chia_cat_wrap(
                bridging, locker, other, _routes(), **forged_views
            )
            assert result.kind == "reject"
            assert result.reason == "WrappedCAT immutable occurrence mismatch"

        if patcher is None:
            run()
        else:
            with patcher:
                run()


class TestWrappedCatImmutableOccurrences:
    def test_unknown_name_is_not_masked_away(self):
        extra = WrappedCatImmutable("9", "mintAuthority", ((200, 32),), "WrappedCAT")
        artifact = _synthetic_wrapped_cat_artifact(extra=extra)
        mutated = bytearray(artifact.bytecode)
        mutated[200:232] = b"\x11" * 32
        with patch.object(spend_policy, "load_wrapped_cat_build_info", return_value=artifact):
            assert spend_policy.match_wrapped_cat_bytecode(artifact.bytecode) is False
            assert spend_policy.match_wrapped_cat_bytecode(bytes(mutated)) is False

    def test_unexpected_width_is_not_masked_away(self):
        artifact = _synthetic_wrapped_cat_artifact(portal_width=20)
        mutated = bytearray(artifact.bytecode)
        start, length = next(
            item for item in artifact.immutables if item.name == "portal"
        ).spans[1]
        mutated[start:start + length] = b"\x22" * length
        with patch.object(spend_policy, "load_wrapped_cat_build_info", return_value=artifact):
            assert spend_policy.match_wrapped_cat_bytecode(artifact.bytecode) is False
            assert spend_policy.match_wrapped_cat_bytecode(bytes(mutated)) is False

    def test_evm_unwrap_rejects_divergent_portal_span(self):
        artifact = _synthetic_wrapped_cat_artifact()
        code = stamp_wrapped_cat_immutables(
            artifact.bytecode, artifact.immutables, **_wrap_stamp_kwargs()
        )
        forged = _corrupt_portal_span(code, artifact)
        unlocker = b"\x24" * 32
        with patch.object(spend_policy, "load_wrapped_cat_build_info", return_value=artifact):
            result = validate_evm_cat_unwrap(
                routes=_routes(),
                source_chain=b"eth",
                source=left_pad_32(WRAPPED_CAT_ADDR),
                destination=unlocker,
                destination_chain=b"xch",
                contents=[left_pad_32(RECEIVER), (100).to_bytes(32, "big")],
                tx_status=1,
                tip_bps=WRAP_TIP_BPS,
                mojo_to_token_ratio=WRAP_MOJO_TO_TOKEN_RATIO,
                gross_mojo_amount=10_000,
                sender_balance_diff=0,
                portal_balance_diff=0,
                view_portal=left_pad_32(ETH_PORTAL),
                view_other_chain=b"xch",
                unlocker_puzzle_hash=unlocker,
                wrapped_cat_runtime_code=forged,
            )
        assert result.kind == "reject"
        assert result.reason == "WrappedCAT immutable occurrence mismatch"


# ---------------------------------------------------------------------------
# 3. EVM ERC-20 wrap tip / balance math
# ---------------------------------------------------------------------------


class TestEvmErc20Wrap:
    def _dest(self) -> bytes32:
        return get_cat_minter_puzzle(PORTAL, b"eth", ETH_BRIDGE).get_tree_hash()

    def _base_kwargs(self, gross: int = 10_000, tip_bps: int = 30, decimals: int = 18):
        tip = compute_bridge_tip(gross, tip_bps)
        net = gross - tip
        factor = 10 ** (decimals - 3)
        asset = ASSET_A
        return dict(
            routes=_routes(),
            source_chain=b"eth",
            source=left_pad_32(ETH_BRIDGE),
            destination=self._dest(),
            destination_chain=b"xch",
            contents=[asset, left_pad_32(RECEIVER), net.to_bytes(32, "big")],
            tx_status=1,
            tip_bps=tip_bps,
            decimals=decimals,
            gross_mojo_amount=gross,
            bridge_token_balance_diff=net * factor,
            balance_diff_asset=asset,
        )

    def test_tip_math_and_matching_diff_accepts(self):
        # tip = amount * bps / 10000; if 0 then tip is 1; message amount == net
        assert compute_bridge_tip(10_000, 30) == 30
        assert compute_bridge_tip(1, 30) == 1
        result = validate_evm_erc20_wrap(**self._base_kwargs())
        assert result.kind == "accept"

    def test_decimals_below_3_rejects(self):
        kwargs = self._base_kwargs(decimals=2)
        # recompute factor for contents net still ok; validator rejects decimals
        result = validate_evm_erc20_wrap(**kwargs)
        assert result.kind == "reject"
        assert "decimals" in result.reason

    def test_tip_bps_out_of_range_rejects(self):
        for bad in (0, 1001):
            kwargs = self._base_kwargs(tip_bps=30)
            kwargs["tip_bps"] = bad
            result = validate_evm_erc20_wrap(**kwargs)
            assert result.kind == "reject"
            assert "tip_bps" in result.reason

    def test_balance_diff_none_retries(self):
        kwargs = self._base_kwargs()
        kwargs["bridge_token_balance_diff"] = None
        result = validate_evm_erc20_wrap(**kwargs)
        assert result.kind == "retry"

    def test_balance_diff_asset_mismatch_rejects(self):
        kwargs = self._base_kwargs()
        kwargs["balance_diff_asset"] = ASSET_B
        result = validate_evm_erc20_wrap(**kwargs)
        assert result.kind == "reject"
        assert "balance diff asset" in result.reason

    def test_message_amount_must_equal_net(self):
        kwargs = self._base_kwargs(gross=10_000, tip_bps=30)
        wrong_net = 10_000  # not net after tip
        kwargs["contents"] = [
            ASSET_A,
            left_pad_32(RECEIVER),
            wrong_net.to_bytes(32, "big"),
        ]
        result = validate_evm_erc20_wrap(**kwargs)
        assert result.kind == "reject"
        assert "message amount" in result.reason

    def test_ether_amount_after_toll_divisible(self):
        gross = 1000
        tip_bps = 30
        tip = compute_bridge_tip(gross, tip_bps)
        net = gross - tip
        decimals = 18
        factor = 10 ** (decimals - 3)
        ratio = 10**12
        toll = 10**15
        amount_after_toll = gross * ratio * factor
        msg_value = amount_after_toll + toll
        contents = [
            left_pad_32(MILLIETH),
            left_pad_32(RECEIVER),
            net.to_bytes(32, "big"),
        ]
        ok = validate_evm_erc20_wrap(
            routes=_routes(),
            source_chain=b"eth",
            source=left_pad_32(ETH_BRIDGE),
            destination=self._dest(),
            destination_chain=b"xch",
            contents=contents,
            tx_status=1,
            tip_bps=tip_bps,
            decimals=decimals,
            gross_mojo_amount=gross,
            bridge_token_balance_diff=net * factor,
            balance_diff_asset=left_pad_32(MILLIETH),
            is_bridge_ether=True,
            msg_value=msg_value,
            message_toll=toll,
            weth_to_eth_ratio=ratio,
        )
        assert ok.kind == "accept"

        bad = validate_evm_erc20_wrap(
            routes=_routes(),
            source_chain=b"eth",
            source=left_pad_32(ETH_BRIDGE),
            destination=self._dest(),
            destination_chain=b"xch",
            contents=contents,
            tx_status=1,
            tip_bps=tip_bps,
            decimals=decimals,
            gross_mojo_amount=gross,
            bridge_token_balance_diff=net * factor,
            balance_diff_asset=left_pad_32(MILLIETH),
            is_bridge_ether=True,
            msg_value=msg_value + 1,
            message_toll=toll,
            weth_to_eth_ratio=ratio,
        )
        assert bad.kind == "reject"
        assert "wethToEthRatio" in bad.reason


class _ViewCall:
    def __init__(self, value):
        self._value = value

    async def call(self, block_identifier=None):
        return self._value


class _ViewFunctions:
    def __init__(self, values):
        self._values = values

    def __getattr__(self, name):
        value = self._values[name]

        def method(*_args, **_kwargs):
            return _ViewCall(value)

        return method


class _ViewContract:
    def __init__(self, values):
        self.functions = _ViewFunctions(values)


class _ViewEth:
    def __init__(self, values):
        self._values = values

    def contract(self, address, abi):
        return _ViewContract(self._values)


class _ViewWeb3:
    def __init__(self, values):
        self.eth = _ViewEth(values)


class _CalldataTx:
    def __init__(self, data: bytes, value: int = 0, sender: str = "0x" + "44" * 20):
        self.input = data
        self.value = value
        self._sender = sender

    def __getitem__(self, key):
        if key == "from":
            return self._sender
        raise KeyError(key)


class TestSkippedBalanceDiff:
    def test_wrap_accepts_without_diff_and_still_rejects_amount_mismatch(self):
        kwargs = TestEvmErc20Wrap()._base_kwargs()
        kwargs["bridge_token_balance_diff"] = None
        kwargs["verify_balance_diff"] = False
        result = validate_evm_erc20_wrap(**kwargs)
        assert result.kind == "accept"

        kwargs["contents"] = [
            ASSET_A,
            left_pad_32(RECEIVER),
            (10_000).to_bytes(32, "big"),
        ]
        result = validate_evm_erc20_wrap(**kwargs)
        assert result.kind == "reject"
        assert "message amount" in result.reason

    def test_unwrap_accepts_without_diffs_and_still_rejects_amount_mismatch(self):
        unlocker = b"\x24" * 32
        gross = 10_000
        tip = compute_bridge_tip(gross, WRAP_TIP_BPS)
        net = gross - tip
        kwargs = dict(
            routes=_routes(),
            source_chain=b"eth",
            source=left_pad_32(WRAPPED_CAT_ADDR),
            destination=unlocker,
            destination_chain=b"xch",
            contents=[left_pad_32(RECEIVER), net.to_bytes(32, "big")],
            tx_status=1,
            tip_bps=WRAP_TIP_BPS,
            mojo_to_token_ratio=WRAP_MOJO_TO_TOKEN_RATIO,
            gross_mojo_amount=gross,
            sender_balance_diff=None,
            portal_balance_diff=None,
            view_portal=left_pad_32(ETH_PORTAL),
            view_other_chain=b"xch",
            unlocker_puzzle_hash=unlocker,
            wrapped_cat_runtime_code=b"\x60\x00",
            verify_balance_diff=False,
        )
        with patch("commands.spend_policy.match_wrapped_cat_bytecode", return_value=True), patch(
            "commands.spend_policy.wrapped_cat_immutable_values_match", return_value=True
        ):
            ok = validate_evm_cat_unwrap(**kwargs)
            assert ok.kind == "accept"

            bad_kwargs = dict(kwargs)
            bad_kwargs["contents"] = [left_pad_32(RECEIVER), gross.to_bytes(32, "big")]
            bad = validate_evm_cat_unwrap(**bad_kwargs)
            assert bad.kind == "reject"
            assert "net amount" in bad.reason

            default_kwargs = dict(kwargs)
            default_kwargs["verify_balance_diff"] = True
            default = validate_evm_cat_unwrap(**default_kwargs)
            assert default.kind == "retry"
            assert default.reason == "missing balance diffs"

    @pytest.mark.asyncio
    async def test_wrap_evaluate_skips_fetch_and_default_still_fetches(self):
        from commands.policy_runtime import _evaluate_evm_erc20_wrap

        gross = 10_000
        net = gross - compute_bridge_tip(gross, 30)
        asset = bytes.fromhex("aa" * 20)
        calldata = (
            bytes.fromhex("cdb50da7")
            + left_pad_32(asset)
            + left_pad_32(RECEIVER)
            + gross.to_bytes(32, "big")
        )
        web3 = _ViewWeb3(
            {"tip": 30, "wethToEthRatio": 1, "messageToll": 0, "decimals": 18}
        )
        contents = [left_pad_32(asset), left_pad_32(RECEIVER), net.to_bytes(32, "big")]
        calls = []

        async def fetch(*_args, **_kwargs):
            calls.append(1)
            return None

        common = dict(
            web3=web3,
            routes=_routes(),
            route=_routes().for_chain(b"eth"),
            source_chain=b"eth",
            source=left_pad_32(ETH_BRIDGE),
            destination=get_cat_minter_puzzle(PORTAL, b"eth", ETH_BRIDGE).get_tree_hash(),
            destination_chain=b"xch",
            contents=contents,
            block_number=1,
            tx=_CalldataTx(calldata),
            tx_hash="0x" + "11" * 32,
            tx_status=1,
        )
        with patch("commands.policy_runtime.fetch_token_balance_diff", new=fetch):
            skipped = await _evaluate_evm_erc20_wrap(**common, verify_balance_diff=False)
            assert calls == []
            assert skipped.kind == "accept"

            default = await _evaluate_evm_erc20_wrap(**common)
            assert calls == [1]
            assert default.kind == "retry"
            assert default.reason == "missing bridge balance diff"

    @pytest.mark.asyncio
    async def test_unwrap_evaluate_skips_fetch_and_default_still_fetches(self):
        from commands.policy_runtime import _evaluate_evm_cat_unwrap

        unlocker = b"\x24" * 32
        gross = 10_000
        net = gross - compute_bridge_tip(gross, WRAP_TIP_BPS)
        calldata = (
            bytes.fromhex("415d9361")
            + left_pad_32(RECEIVER)
            + gross.to_bytes(32, "big")
        )
        contents = [left_pad_32(RECEIVER), net.to_bytes(32, "big")]
        calls = []

        async def fetch(*_args, **_kwargs):
            calls.append(1)
            return None

        async def views(*_args, **_kwargs):
            return (
                b"\x60\x00",
                left_pad_32(ETH_PORTAL),
                b"xch",
                b"\x00" * 32,
                unlocker,
                WRAP_TIP_BPS,
                WRAP_MOJO_TO_TOKEN_RATIO,
            )

        common = dict(
            web3=object(),
            routes=_routes(),
            source_chain=b"eth",
            source=left_pad_32(WRAPPED_CAT_ADDR),
            destination=unlocker,
            destination_chain=b"xch",
            contents=contents,
            block_number=1,
            tx=_CalldataTx(calldata),
            tx_hash="0x" + "11" * 32,
            tx_status=1,
        )
        with patch("commands.policy_runtime.fetch_token_balance_diff", new=fetch), patch(
            "commands.policy_runtime.read_wrapped_cat_views", new=views
        ), patch(
            "commands.policy_runtime.match_wrapped_cat_bytecode", return_value=True
        ), patch(
            "commands.spend_policy.match_wrapped_cat_bytecode", return_value=True
        ), patch(
            "commands.spend_policy.wrapped_cat_immutable_values_match", return_value=True
        ):
            skipped = await _evaluate_evm_cat_unwrap(**common, verify_balance_diff=False)
            assert calls == []
            assert skipped.kind == "accept"

            default = await _evaluate_evm_cat_unwrap(**common)
            assert calls == [1, 1]
            assert default.kind == "retry"
            assert default.reason == "missing balance diffs"

    @pytest.mark.asyncio
    async def test_bundled_unwrap_uses_inner_bridge_back_and_burn_sender(self):
        from commands.policy_runtime import _TRANSFER_TOPIC, _evaluate_evm_cat_unwrap

        unlocker = b"\x24" * 32
        smart = bytes.fromhex("ab" * 20)
        gross = 1_000_000_000
        tip = compute_bridge_tip(gross, WRAP_TIP_BPS)
        net = gross - tip
        prefix = bytes.fromhex("1fad948c") + b"\x11" * 80
        calldata = (
            prefix
            + bytes.fromhex("415d9361")
            + left_pad_32(RECEIVER)
            + gross.to_bytes(32, "big")
        )
        burn = gross * WRAP_MOJO_TO_TOKEN_RATIO
        tx_hash = "0x" + "11" * 32
        web3 = _LogsWeb3(
            [
                {
                    "address": "0x" + WRAPPED_CAT_ADDR.hex(),
                    "topics": [
                        _TRANSFER_TOPIC,
                        _padded_topic(smart),
                        _padded_topic(b"\x00" * 20),
                    ],
                    "data": _uint_data(burn),
                    "transactionHash": tx_hash,
                }
            ]
        )
        holders = []

        async def fetch(_web3, _tx_hash, _token, holder, _block):
            holders.append(bytes(holder)[-20:])
            if bytes(holder)[-20:] == smart:
                return -burn
            return tip * WRAP_MOJO_TO_TOKEN_RATIO

        with _unwrap_patches(fetch, unlocker):
            result = await _evaluate_evm_cat_unwrap(
                **_unwrap_call(
                    web3,
                    calldata,
                    [left_pad_32(RECEIVER), net.to_bytes(32, "big")],
                    unlocker,
                    tx_hash,
                    "0x" + "44" * 20,
                )
            )
        assert web3.eth.calls == 1
        assert holders[0] == smart
        assert result.kind == "accept"

    @pytest.mark.asyncio
    async def test_ambiguous_embedded_bridge_back_retries(self):
        from commands.policy_runtime import _evaluate_evm_cat_unwrap

        unlocker = b"\x24" * 32
        receiver = left_pad_32(RECEIVER)
        first = bytes.fromhex("415d9361") + receiver + (1_000_000_000).to_bytes(32, "big")
        second = bytes.fromhex("415d9361") + receiver + (2_000_000_000).to_bytes(32, "big")
        calldata = bytes.fromhex("1fad948c") + first + second
        calls = []

        async def fetch(*_args, **_kwargs):
            calls.append(1)
            return None

        with _unwrap_patches(fetch, unlocker):
            result = await _evaluate_evm_cat_unwrap(
                **_unwrap_call(
                    _LogsWeb3([]),
                    calldata,
                    [receiver, (997_000_000).to_bytes(32, "big")],
                    unlocker,
                    "0x" + "11" * 32,
                    "0x" + "44" * 20,
                )
            )
        assert calls == []
        assert result.kind == "retry"
        assert result.reason == "ambiguous bridgeBack amount"

    @pytest.mark.asyncio
    async def test_direct_bridge_back_uses_tx_from_without_burn_log(self):
        from commands.policy_runtime import _evaluate_evm_cat_unwrap

        unlocker = b"\x24" * 32
        gross = 10_000
        tip = compute_bridge_tip(gross, WRAP_TIP_BPS)
        net = gross - tip
        sender = bytes.fromhex("44" * 20)
        calldata = (
            bytes.fromhex("415d9361")
            + left_pad_32(RECEIVER)
            + gross.to_bytes(32, "big")
        )
        web3 = _LogsWeb3(AssertionError("burn log"))
        holders = []

        async def fetch(_web3, _tx_hash, _token, holder, _block):
            holders.append(bytes(holder)[-20:])
            if len(holders) == 1:
                return -(gross * WRAP_MOJO_TO_TOKEN_RATIO)
            return tip * WRAP_MOJO_TO_TOKEN_RATIO

        with _unwrap_patches(fetch, unlocker):
            result = await _evaluate_evm_cat_unwrap(
                **_unwrap_call(
                    web3,
                    calldata,
                    [left_pad_32(RECEIVER), net.to_bytes(32, "big")],
                    unlocker,
                    "0x" + "11" * 32,
                    "0x" + sender.hex(),
                )
            )
        assert web3.eth.calls == 0
        assert holders[0] == sender
        assert result.kind == "accept"

    @pytest.mark.asyncio
    async def test_failed_bundled_unwrap_rejects_without_burn_lookup(self):
        from commands.policy_runtime import _evaluate_evm_cat_unwrap

        unlocker = b"\x24" * 32
        gross = 1_000_000_000
        net = gross - compute_bridge_tip(gross, WRAP_TIP_BPS)
        calldata = (
            bytes.fromhex("1fad948c")
            + bytes.fromhex("415d9361")
            + left_pad_32(RECEIVER)
            + gross.to_bytes(32, "big")
        )
        web3 = _LogsWeb3(AssertionError("burn log"))
        calls = []

        async def fetch(*_args, **_kwargs):
            calls.append(1)
            return None

        kwargs = _unwrap_call(
            web3,
            calldata,
            [left_pad_32(RECEIVER), net.to_bytes(32, "big")],
            unlocker,
            "0x" + "11" * 32,
            "0x" + "44" * 20,
        )
        kwargs["tx_status"] = 0
        with _unwrap_patches(fetch, unlocker):
            result = await _evaluate_evm_cat_unwrap(**kwargs)
        assert web3.eth.calls == 0
        assert calls == []
        assert result.kind == "reject"
        assert result.reason == "tx status not success"

    @pytest.mark.asyncio
    async def test_direct_bridge_back_receiver_mismatch_rejects(self):
        from commands.policy_runtime import _evaluate_evm_cat_unwrap

        unlocker = b"\x24" * 32
        gross = 10_000
        net = gross - compute_bridge_tip(gross, WRAP_TIP_BPS)
        other = bytes.fromhex("cc" * 20)
        calldata = (
            bytes.fromhex("415d9361")
            + left_pad_32(other)
            + gross.to_bytes(32, "big")
        )
        calls = []

        async def fetch(*_args, **_kwargs):
            calls.append(1)
            return None

        with _unwrap_patches(fetch, unlocker):
            result = await _evaluate_evm_cat_unwrap(
                **_unwrap_call(
                    _LogsWeb3([]),
                    calldata,
                    [left_pad_32(RECEIVER), net.to_bytes(32, "big")],
                    unlocker,
                    "0x" + "11" * 32,
                    "0x" + "44" * 20,
                )
            )
        assert calls == []
        assert result.kind == "reject"
        assert result.reason == "bridgeBack receiver mismatch"

    @pytest.mark.asyncio
    async def test_embedded_bridge_to_chia_decodes_gross(self):
        from commands.policy_runtime import _evaluate_evm_erc20_wrap

        gross = 10_000
        net = gross - compute_bridge_tip(gross, 30)
        asset = bytes.fromhex("aa" * 20)
        prefix = bytes.fromhex("1fad948c") + b"\x22" * 40
        calldata = (
            prefix
            + bytes.fromhex("cdb50da7")
            + left_pad_32(asset)
            + left_pad_32(RECEIVER)
            + gross.to_bytes(32, "big")
        )
        web3 = _ViewWeb3(
            {"tip": 30, "wethToEthRatio": 1, "messageToll": 0, "decimals": 18}
        )
        contents = [left_pad_32(asset), left_pad_32(RECEIVER), net.to_bytes(32, "big")]
        calls = []

        async def fetch(*_args, **_kwargs):
            calls.append(1)
            return None

        with patch("commands.policy_runtime.fetch_token_balance_diff", new=fetch):
            result = await _evaluate_evm_erc20_wrap(
                web3=web3,
                routes=_routes(),
                route=_routes().for_chain(b"eth"),
                source_chain=b"eth",
                source=left_pad_32(ETH_BRIDGE),
                destination=get_cat_minter_puzzle(PORTAL, b"eth", ETH_BRIDGE).get_tree_hash(),
                destination_chain=b"xch",
                contents=contents,
                block_number=1,
                tx=_CalldataTx(calldata),
                tx_hash="0x" + "11" * 32,
                tx_status=1,
                verify_balance_diff=False,
            )
        assert calls == []
        assert result.kind == "accept"

    @pytest.mark.asyncio
    async def test_failed_wrap_rejects_before_balance_lookup(self):
        from commands.policy_runtime import _evaluate_evm_erc20_wrap

        asset = bytes.fromhex("aa" * 20)
        web3 = _ViewWeb3(
            {"tip": 30, "wethToEthRatio": 1, "messageToll": 0, "decimals": 18}
        )
        calls = []

        async def fetch(*_args, **_kwargs):
            calls.append(1)
            return None

        with patch("commands.policy_runtime.fetch_token_balance_diff", new=fetch):
            result = await _evaluate_evm_erc20_wrap(
                web3=web3,
                routes=_routes(),
                route=_routes().for_chain(b"eth"),
                source_chain=b"eth",
                source=left_pad_32(ETH_BRIDGE),
                destination=get_cat_minter_puzzle(PORTAL, b"eth", ETH_BRIDGE).get_tree_hash(),
                destination_chain=b"xch",
                contents=[
                    left_pad_32(asset),
                    left_pad_32(RECEIVER),
                    (1).to_bytes(32, "big"),
                ],
                block_number=1,
                tx=_CalldataTx(b""),
                tx_hash="0x" + "11" * 32,
                tx_status=0,
            )
        assert calls == []
        assert result.kind == "reject"
        assert result.reason == "tx status not success"


def _unwrap_call(web3, calldata, contents, unlocker, tx_hash, sender):
    return dict(
        web3=web3,
        routes=_routes(),
        source_chain=b"eth",
        source=left_pad_32(WRAPPED_CAT_ADDR),
        destination=unlocker,
        destination_chain=b"xch",
        contents=contents,
        block_number=1,
        tx=_CalldataTx(calldata, sender=sender),
        tx_hash=tx_hash,
        tx_status=1,
    )


@contextmanager
def _unwrap_patches(fetch, unlocker):
    async def views(*_args, **_kwargs):
        return (
            b"\x60\x00",
            left_pad_32(ETH_PORTAL),
            b"xch",
            b"\x00" * 32,
            unlocker,
            WRAP_TIP_BPS,
            WRAP_MOJO_TO_TOKEN_RATIO,
        )

    with patch("commands.policy_runtime.fetch_token_balance_diff", new=fetch), patch(
        "commands.policy_runtime.read_wrapped_cat_views", new=views
    ), patch(
        "commands.policy_runtime.match_wrapped_cat_bytecode", return_value=True
    ), patch(
        "commands.spend_policy.match_wrapped_cat_bytecode", return_value=True
    ), patch(
        "commands.spend_policy.wrapped_cat_immutable_values_match", return_value=True
    ):
        yield


class _LogsEth:
    def __init__(self, logs):
        self.logs = logs
        self.calls = 0

    async def get_logs(self, _filt):
        self.calls += 1
        if isinstance(self.logs, Exception):
            raise self.logs
        return self.logs


class _LogsWeb3:
    def __init__(self, logs):
        self.eth = _LogsEth(logs)


# ---------------------------------------------------------------------------
# 4. evm_address_from_bytes32
# ---------------------------------------------------------------------------


class TestEvmAddressFromBytes32:
    def test_left_padded_source_normalizes_to_20_bytes(self):
        source = bytes.fromhex("000bA7E6824AA002033Df7BEDf8bAB72Fc6465e9")
        assert len(source) == 20
        padded = left_pad_32(source)
        assert len(padded) == 32
        out = evm_address_from_bytes32(padded)
        assert len(out) == 20
        assert out == bytes.fromhex("000ba7e6824aa002033df7bedf8bab72fc6465e9")
        assert out != out[-19:]  # not truncated to 19 bytes

    def test_nonzero_prefix_rejects(self):
        addr20 = bytes.fromhex("000bA7E6824AA002033Df7BEDf8bAB72Fc6465e9")
        bad = b"\x01" + b"\x00" * 11 + addr20
        assert len(bad) == 32
        with pytest.raises(ValueError, match="non-zero prefix"):
            evm_address_from_bytes32(bad)


class TestEvmLogFieldMatch:
    def test_identical_log_matches_and_receiver_mismatch_does_not(self):
        nonce = b"\x11" * 32
        source = ETH_BRIDGE
        row_source = b"\x00" * (64 - len(source)) + source
        destination = b"\x42" * 32
        receiver = left_pad_32(RECEIVER)
        amount = (1000).to_bytes(32, "big")
        contents = (ASSET_A, receiver, amount)
        event = {
            "args": {
                "nonce": "0x" + nonce.hex(),
                "source": "0x" + source.hex(),
                "destination_chain": b"xch",
                "destination": "0x" + destination.hex(),
                "contents": ["0x" + word.hex() for word in contents],
            }
        }
        decoded = decode_message_sent_fields(event)
        assert decoded is not None
        log_nonce, log_source, log_chain, log_dest, log_contents = decoded
        kwargs = dict(
            nonce=nonce,
            source=row_source,
            destination_chain=b"xch",
            destination=destination,
            contents=contents,
            log_nonce=log_nonce,
            log_source=log_source,
            log_destination_chain=log_chain,
            log_destination=log_dest,
            log_contents=log_contents,
        )
        assert evm_log_matches_row(**kwargs)
        other_receiver = left_pad_32(bytes.fromhex("ee" * 20))
        assert not evm_log_matches_row(
            **{**kwargs, "log_contents": (ASSET_A, other_receiver, amount)}
        )


class _PortalEth:
    def __init__(self):
        self.storage_address = None
        self.code_address = None

    async def get_storage_at(self, portal, _slot, block_identifier=None):
        from hexbytes import HexBytes

        self.storage_address = portal
        impl = bytes.fromhex("5d89db6cc9295c8e802d96c521c8209353ce3215")
        return HexBytes(b"\x00" * 12 + impl)

    async def get_code(self, address, block_identifier=None):
        from hexbytes import HexBytes

        self.code_address = address
        return HexBytes(b"\x60\x00")


class _PortalWeb3:
    def __init__(self):
        self.eth = _PortalEth()


class TestStorageKey:
    def test_single_prefix_hex_quantity(self):
        import re

        from hexbytes import HexBytes
        from web3 import Web3

        from commands.policy_runtime import _storage_key

        holder = HexBytes(bytes.fromhex("22" * 20))
        digest = Web3.keccak(left_pad_32(bytes(holder)) + left_pad_32((3).to_bytes(32, "big")))
        assert isinstance(digest, HexBytes)
        assert digest.hex().startswith("0x")
        key = _storage_key(holder, 3)
        assert re.fullmatch(r"0x[0-9a-fA-F]{64}", key)
        assert "0x0x" not in key
        assert Web3.to_bytes(hexstr=key) == bytes(digest)


class TestAccountKeyVariants:
    def test_hexbytes_address_is_single_prefixed(self):
        from hexbytes import HexBytes
        from web3 import Web3

        from commands.policy_runtime import _account_key_variants

        raw = bytes.fromhex("5d89db6cc9295c8e802d96c521c8209353ce3215")
        address = HexBytes(b"\x00" * 12 + raw)
        assert address.hex().startswith("0x")
        variants = _account_key_variants(address)
        checksum = Web3.to_checksum_address(raw)
        assert checksum in variants
        assert checksum.lower() in variants
        assert raw.hex() in variants
        assert all("0x0x" not in variant.lower() for variant in variants)


class TestPortalViewHex:
    def test_hexbytes_portal_decodes_without_fromhex_on_prefixed_hex(self):
        from hexbytes import HexBytes

        from commands.policy_runtime import _decode_hex_or_bytes

        raw = bytes.fromhex("ab" * 20)
        portal = HexBytes(b"\x00" * 12 + raw)
        assert portal.hex().startswith("0x")
        with pytest.raises(ValueError):
            bytes.fromhex(portal.hex())
        assert _decode_hex_or_bytes(portal) == bytes(portal)
        assert _decode_hex_or_bytes(portal.hex()) == bytes(portal)
        assert left_pad_32(_decode_hex_or_bytes(portal)) == bytes(portal)


class TestPortalImplCodehash:
    @pytest.mark.asyncio
    async def test_impl_address_is_not_double_prefixed(self):
        from hexbytes import HexBytes
        from web3 import Web3

        from commands.policy_runtime import portal_impl_codehash

        web3 = _PortalWeb3()
        portal = HexBytes(bytes.fromhex("11" * 20))
        assert isinstance(portal, HexBytes)
        assert portal.hex().startswith("0x")
        codehash = await portal_impl_codehash(web3, portal, 1)
        storage_address = web3.eth.storage_address
        impl_address = web3.eth.code_address
        assert storage_address == Web3.to_checksum_address(bytes(portal))
        assert impl_address == Web3.to_checksum_address(
            bytes.fromhex("5d89db6cc9295c8e802d96c521c8209353ce3215")
        )
        assert not storage_address.lower().startswith("0x0x")
        assert not impl_address.lower().startswith("0x0x")
        assert codehash is not None
        assert not codehash.lower().startswith("0x0x")


class _RuntimeEth:
    def __init__(self, code: bytes, *, fail: bool = False):
        self._code = code
        self._fail = fail

    async def get_code(self, address, block_identifier=None):
        if self._fail:
            raise RuntimeError("code read failed")
        return self._code


class _RuntimeWeb3:
    def __init__(self, code: bytes, *, fail: bool = False):
        self.eth = _RuntimeEth(code, fail=fail)


class TestUnsupportedUnwrapSource:
    async def _evaluate(self, web3):
        from commands.policy_runtime import _evaluate_evm_cat_unwrap

        async def _balance_diff(*_args, **_kwargs):
            raise AssertionError("fetch_token_balance_diff should not run")

        with patch("commands.policy_runtime.fetch_token_balance_diff", new=_balance_diff):
            return await _evaluate_evm_cat_unwrap(
                web3=web3,
                routes=_routes(),
                source_chain=b"eth",
                source=left_pad_32(WRAPPED_CAT_ADDR),
                destination=b"\x24" * 32,
                destination_chain=b"xch",
                contents=[left_pad_32(RECEIVER), (1).to_bytes(32, "big")],
                block_number=1,
                tx=object(),
                tx_hash="0x" + "11" * 32,
                tx_status=1,
            )

    @pytest.mark.asyncio
    async def test_eoa_and_nonmatching_runtime_reject_without_balance_diff(self):
        eoa = await self._evaluate(_RuntimeWeb3(b""))
        assert eoa.kind == "reject"
        assert eoa.reason == "WrappedCAT bytecode mismatch after immutable mask"

        other = await self._evaluate(_RuntimeWeb3(b"\x60\x00"))
        assert other.kind == "reject"
        assert other.reason == "WrappedCAT bytecode mismatch after immutable mask"

    @pytest.mark.asyncio
    async def test_code_read_failure_retries_without_balance_diff(self):
        result = await self._evaluate(_RuntimeWeb3(b"", fail=True))
        assert result.kind == "retry"
        assert result.reason == "missing WrappedCAT runtime code"

    @pytest.mark.asyncio
    async def test_uncomparable_runtime_retries_but_empty_code_still_rejects(self):
        with patch(
            "commands.policy_runtime.match_wrapped_cat_bytecode",
            side_effect=OSError("WrappedCAT artifact missing"),
        ):
            empty = await self._evaluate(_RuntimeWeb3(b""))
            other = await self._evaluate(_RuntimeWeb3(b"\x60\x00"))
        assert empty.kind == "reject"
        assert other.kind == "retry"
        assert other.reason == "missing WrappedCAT runtime code"


class TestChiaWrapEmptyCode:
    async def _evaluate(self, web3):
        bridging, locker, other = _build_lock(asset_id=None)
        return await evaluate_chia_message_policy(
            bridging_coin=bridging,
            parent_spend=locker,
            routes=_routes(),
            node=None,
            block_spends=other,
            web3_for_chain=lambda _chain: web3,
            evm_block_number=1,
        )

    @pytest.mark.asyncio
    async def test_empty_code_rejects_when_artifact_loader_raises(self):
        with patch(
            "commands.spend_policy.load_wrapped_cat_build_info",
            side_effect=OSError("WrappedCAT artifact missing"),
        ):
            empty = await self._evaluate(_RuntimeWeb3(b""))
            other = await self._evaluate(_RuntimeWeb3(b"\x60\x00"))
        assert empty.kind == "reject"
        assert empty.reason == "WrappedCAT bytecode mismatch after immutable mask"
        assert other.kind == "retry"


def _padded_topic(addr: bytes) -> str:
    return "0x" + addr.rjust(32, b"\x00").hex()


def _uint_data(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


class _TraceProvider:
    def __init__(self, responses):
        self.responses = responses
        self.methods = []

    async def make_request(self, method, params):
        self.methods.append(method)
        response = self.responses.get(method)
        if response is None:
            return {
                "error": {
                    "code": -32601,
                    "message": "The method does not exist/is not available",
                }
            }
        if isinstance(response, Exception):
            raise response
        return response


class _BalanceCall:
    def __init__(self, balance):
        self._balance = balance

    async def call(self, block_identifier=None):
        return self._balance


class _BalanceContract:
    def __init__(self, balance):
        self._balance = balance
        self.functions = self

    def balanceOf(self, _holder):
        return _BalanceCall(self._balance)


class _BalanceEth:
    def __init__(self, balance, slots, logs, provider):
        self._balance = balance
        self.slots = slots
        self.logs = logs
        self.provider = provider
        self.log_calls = 0

    def contract(self, address, abi):
        return _BalanceContract(self._balance)

    async def get_storage_at(self, token, key, block_identifier=None):
        value = self.slots.get((block_identifier, str(key).lower()), 0)
        return value.to_bytes(32, "big")

    async def get_logs(self, filt):
        self.log_calls += 1
        if isinstance(self.logs, Exception):
            raise self.logs
        return self.logs


class _BalanceWeb3:
    def __init__(self, eth):
        self.eth = eth
        self.provider = eth.provider


class TestLogBalanceDiff:
    TOKEN = bytes.fromhex("11" * 20)
    HOLDER = bytes.fromhex("22" * 20)
    OTHER = bytes.fromhex("33" * 20)
    TX_A = "aa" * 32
    TX_B = "bb" * 32
    BLOCK = 10

    def _transfer(self, tx_hash: str, sender: bytes, receiver: bytes, value: int):
        from commands.policy_runtime import _TRANSFER_TOPIC

        return {
            "address": "0x" + self.TOKEN.hex(),
            "topics": [
                _TRANSFER_TOPIC,
                _padded_topic(sender),
                _padded_topic(receiver),
            ],
            "data": _uint_data(value),
            "transactionHash": "0x" + tx_hash,
        }

    def _slot(self, block: int, value: int, slot: int = 0):
        from commands.policy_runtime import _storage_key

        return {(block, _storage_key(self.HOLDER, slot).lower()): value}

    def _web3(self, balance, slots, logs, responses=None):
        provider = _TraceProvider(responses or {})
        eth = _BalanceEth(balance, slots, logs, provider)
        return _BalanceWeb3(eth)

    def _trace(self, pre: int, post: int):
        from commands.policy_runtime import _storage_key

        slot = _storage_key(self.HOLDER, 0)
        account = "0x" + self.TOKEN.hex()
        return {
            "pre": {account: {"storage": {slot: hex(pre)}}},
            "post": {account: {"storage": {slot: hex(post)}}},
        }

    async def _fetch(self, web3, tx_hash: str):
        from commands.policy_runtime import fetch_token_balance_diff

        return await fetch_token_balance_diff(
            web3, "0x" + tx_hash, self.TOKEN, self.HOLDER, self.BLOCK
        )

    @pytest.mark.asyncio
    async def test_two_transfers_attribute_per_transaction(self):
        logs = [
            self._transfer(self.TX_A, self.OTHER, self.HOLDER, 50),
            self._transfer(self.TX_B, self.OTHER, self.HOLDER, 70),
        ]
        slots = {**self._slot(self.BLOCK, 120), **self._slot(self.BLOCK - 1, 0)}
        web3 = self._web3(120, slots, logs)
        assert await self._fetch(web3, self.TX_A) == 50
        assert await self._fetch(web3, self.TX_B) == 70

    @pytest.mark.asyncio
    async def test_log_sum_must_match_storage_delta(self):
        logs = [self._transfer(self.TX_A, self.OTHER, self.HOLDER, 50)]
        slots = {**self._slot(self.BLOCK, 120), **self._slot(self.BLOCK - 1, 0)}
        web3 = self._web3(120, slots, logs)
        assert await self._fetch(web3, self.TX_A) is None

    @pytest.mark.asyncio
    async def test_unchanged_zero_balance_uses_cancelling_log_net(self):
        logs = [
            self._transfer(self.TX_A, self.OTHER, self.HOLDER, 4),
            self._transfer(self.TX_B, self.HOLDER, self.OTHER, 4),
        ]
        web3 = self._web3(0, {}, logs)
        assert await self._fetch(web3, self.TX_A) == 4
        assert await self._fetch(web3, self.TX_B) == -4

    @pytest.mark.asyncio
    async def test_missing_tracers_fall_through_and_log_once(self, caplog):
        from commands import policy_runtime

        policy_runtime._missing_tracer_methods.clear()
        logs = [self._transfer(self.TX_A, self.OTHER, self.HOLDER, 50)]
        slots = {**self._slot(self.BLOCK, 50), **self._slot(self.BLOCK - 1, 0)}
        web3 = self._web3(50, slots, logs)
        with caplog.at_level(logging.INFO):
            assert await self._fetch(web3, self.TX_A) == 50
            assert await self._fetch(web3, self.TX_A) == 50
        missing = [
            record
            for record in caplog.records
            if record.message.endswith("is not available on this RPC")
        ]
        assert [record.message for record in missing] == [
            "debug_traceTransaction is not available on this RPC",
            "trace_replayTransaction is not available on this RPC",
        ]
        assert all(record.exc_info is None for record in missing)
        assert web3.provider.methods == [
            "debug_traceTransaction",
            "trace_replayTransaction",
        ]

    @pytest.mark.asyncio
    async def test_http_405_is_remembered_per_endpoint(self, caplog):
        from aiohttp import ClientResponseError
        from commands import policy_runtime

        policy_runtime._missing_tracer_methods.clear()
        refused = ClientResponseError(
            type("Req", (), {"real_url": "https://rpc.example/base"})(),
            (),
            status=405,
            message="Method Not Allowed",
        )
        logs = [self._transfer(self.TX_A, self.OTHER, self.HOLDER, 50)]
        slots = {**self._slot(self.BLOCK, 50), **self._slot(self.BLOCK - 1, 0)}
        base = self._web3(
            50,
            slots,
            logs,
            {
                "debug_traceTransaction": refused,
                "trace_replayTransaction": refused,
            },
        )
        base.provider.endpoint_uri = "https://rpc.example/base"
        eth = self._web3(
            50,
            slots,
            logs,
            {
                "debug_traceTransaction": refused,
                "trace_replayTransaction": refused,
            },
        )
        eth.provider.endpoint_uri = "https://rpc.example/eth"
        with caplog.at_level(logging.INFO):
            assert await self._fetch(base, self.TX_A) == 50
            assert await self._fetch(base, self.TX_A) == 50
            assert await self._fetch(eth, self.TX_A) == 50
        missing = [
            record
            for record in caplog.records
            if record.message.endswith("is not available on this RPC")
        ]
        assert [record.message for record in missing] == [
            "debug_traceTransaction is not available on this RPC",
            "trace_replayTransaction is not available on this RPC",
            "debug_traceTransaction is not available on this RPC",
            "trace_replayTransaction is not available on this RPC",
        ]
        assert all(record.exc_info is None for record in missing)
        assert base.provider.methods == [
            "debug_traceTransaction",
            "trace_replayTransaction",
        ]
        assert eth.provider.methods == [
            "debug_traceTransaction",
            "trace_replayTransaction",
        ]

    @pytest.mark.asyncio
    async def test_other_tracer_errors_keep_a_traceback(self, caplog):
        from commands import policy_runtime

        policy_runtime._missing_tracer_methods.clear()
        logs = [self._transfer(self.TX_A, self.OTHER, self.HOLDER, 50)]
        slots = {**self._slot(self.BLOCK, 50), **self._slot(self.BLOCK - 1, 0)}
        web3 = self._web3(
            50,
            slots,
            logs,
            {"debug_traceTransaction": {"error": {"code": -32000, "message": "boom"}}},
        )
        with caplog.at_level(logging.INFO):
            assert await self._fetch(web3, self.TX_A) == 50
            assert await self._fetch(web3, self.TX_A) == 50
        failed = [
            record
            for record in caplog.records
            if record.message == "debug_traceTransaction failed"
        ]
        assert len(failed) == 2
        assert all(record.exc_info is not None for record in failed)
        assert web3.provider.methods == [
            "debug_traceTransaction",
            "trace_replayTransaction",
            "debug_traceTransaction",
        ]

    @pytest.mark.asyncio
    async def test_trace_result_skips_logs(self):
        slots = self._slot(self.BLOCK, 25)
        web3 = self._web3(
            25,
            slots,
            AssertionError("logs should not be read"),
            {"debug_traceTransaction": {"result": self._trace(10, 25)}},
        )
        assert await self._fetch(web3, self.TX_A) == 15
        assert web3.eth.log_calls == 0
        assert "trace_replayTransaction" not in web3.provider.methods

    @pytest.mark.asyncio
    async def test_openzeppelin_v5_balance_slot_matches_logs(self):
        from commands.policy_runtime import _OZ_ERC20_BALANCES_SLOT

        logs = [self._transfer(self.TX_A, self.OTHER, self.HOLDER, 1_000_000_000_000_000)]
        slots = {
            **self._slot(self.BLOCK, 20_040_000_000_000_000_000, _OZ_ERC20_BALANCES_SLOT),
            **self._slot(self.BLOCK - 1, 20_039_000_000_000_000_000, _OZ_ERC20_BALANCES_SLOT),
        }
        web3 = self._web3(20_040_000_000_000_000_000, slots, logs)
        assert await self._fetch(web3, self.TX_A) == 1_000_000_000_000_000

    @pytest.mark.asyncio
    async def test_trace_without_token_does_not_use_logs(self):
        logs = [self._transfer(self.TX_A, self.OTHER, self.HOLDER, 50)]
        slots = {**self._slot(self.BLOCK, 50), **self._slot(self.BLOCK - 1, 0)}
        web3 = self._web3(
            50,
            slots,
            logs,
            {"debug_traceTransaction": {"result": {"pre": {}, "post": {}}}},
        )
        assert await self._fetch(web3, self.TX_A) is None
        assert web3.eth.log_calls == 0

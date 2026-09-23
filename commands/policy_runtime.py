from __future__ import annotations

import logging
import time
from typing import List, Optional, Sequence, Tuple

from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.wallet.cat_wallet.cat_utils import CAT_MOD, construct_cat_puzzle
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH
from web3 import AsyncWeb3, Web3

from commands.control_channel import (
    ControlConfig,
    decide_control,
    fetch_control_events,
)
from commands.spend_policy import (
    REJECTED_SIG,
    BridgeRoutes,
    PolicyResult,
    compare_portal_impl,
    evm_address_from_bytes32,
    left_pad_32,
    validate_chia_cat_wrap,
    validate_chia_erc20_unwrap,
    validate_evm_cat_unwrap,
    validate_evm_erc20_wrap,
)
from drivers.wrapped_assets import (
    CAT_BURNER_MOD,
    get_cat_burn_inner_puzzle,
    get_wrapped_tail,
)
from drivers.wrapped_cats import LOCKER_MOD

EIP1967_IMPL_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)

BRIDGE_TO_CHIA_SEL = bytes.fromhex("cdb50da7")
BRIDGE_ETHER_SEL = bytes.fromhex("9ec911b0")
BRIDGE_PERMIT_SEL = bytes.fromhex("77c3ebbb")
BRIDGE_BACK_SEL = bytes.fromhex("415d9361")

ERC20_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

BRIDGE_ABI = [
    {
        "inputs": [],
        "name": "tip",
        "outputs": [{"type": "uint16"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "wethToEthRatio",
        "outputs": [{"type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "iweth",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

WRAPPED_CAT_ABI = [
    {
        "inputs": [],
        "name": "portal",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "otherChain",
        "outputs": [{"type": "bytes3"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "lockerPuzzleHash",
        "outputs": [{"type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "unlockerPuzzleHash",
        "outputs": [{"type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "tip",
        "outputs": [{"type": "uint16"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "mojoToTokenRatio",
        "outputs": [{"type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
]

PORTAL_ABI = [
    {
        "inputs": [],
        "name": "messageToll",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def _policy_message_id(message) -> str:
    return f"{message.source_chain.decode()}-{message.nonce.hex()}"


def apply_policy_result(message, result: PolicyResult) -> bool:
    message_id = _policy_message_id(message)
    if result.kind == "reject":
        logging.warning(f"REJECTED {message_id}: {result.reason}")
        message.sig = REJECTED_SIG
        return False
    if result.kind == "retry":
        logging.info(f"RETRY {message_id}: {result.reason}")
        return False
    if result.kind == "hold":
        logging.warning(f"HOLD {message_id}: {result.reason}")
        return False
    return True


_last_control_command: str | None = None


def _note_control_command(command: str) -> None:
    global _last_control_command
    if command == _last_control_command:
        return
    if command == "stop":
        logging.warning("NOSTR STOP")
    elif command == "start" and _last_control_command == "stop":
        logging.warning("NOSTR START: resume after stop")
    _last_control_command = command


async def control_allows_start(control_config: ControlConfig) -> bool:
    global _last_control_command
    try:
        events = await fetch_control_events(
            control_config.relays,
            control_config.stop_authors,
            control_config.start_authors,
        )
        command = decide_control(
            events,
            int(time.time()),
            control_config.stop_authors,
            control_config.start_authors,
        )
        _note_control_command(command)
        return command == "start"
    except Exception:
        if _last_control_command != "stop":
            logging.warning("NOSTR STOP: control relay fetch failed", exc_info=True)
            _last_control_command = "stop"
        return False


def _checksum(addr: bytes) -> str:
    return Web3.to_checksum_address("0x" + evm_address_from_bytes32(left_pad_32(addr)).hex())


def _as_bytes3(value) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, str):
        raw = bytes.fromhex(value.replace("0x", "")) if value.startswith("0x") else value.encode()
    else:
        raw = bytes(value)
    if len(raw) >= 3:
        return raw[:3]
    return raw + b"\x00" * (3 - len(raw))


def _as_bytes32(value) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, str):
        raw = bytes.fromhex(value.replace("0x", ""))
    elif isinstance(value, int):
        raw = value.to_bytes(32, "big")
    else:
        raw = bytes(value)
    return left_pad_32(raw)


async def portal_impl_codehash(
    web3: AsyncWeb3, portal_address: bytes, block_number: int
) -> Optional[str]:
    try:
        portal = _checksum(portal_address)
        storage = await web3.eth.get_storage_at(
            portal, EIP1967_IMPL_SLOT, block_identifier=block_number
        )
        impl = storage[-20:]
        if impl == b"\x00" * 20:
            return None
        code = await web3.eth.get_code(
            Web3.to_checksum_address("0x" + impl.hex()), block_identifier=block_number
        )
        return Web3.keccak(code).hex()
    except Exception:
        logging.error("Failed to read portal impl codehash", exc_info=True)
        return None


async def check_portal_impl(
    web3: AsyncWeb3, route, block_number: int
) -> PolicyResult:
    actual = await portal_impl_codehash(web3, route.portal_address, block_number)
    if actual is None:
        return PolicyResult("retry", "could not read portal impl codehash")
    return compare_portal_impl(actual, route.portal_impl_codehash)


def _storage_key(holder: bytes, slot: int) -> str:
    return "0x" + Web3.keccak(left_pad_32(holder) + left_pad_32(slot.to_bytes(32, "big"))).hex()


def _parse_uint(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (bytes, bytearray)):
        return int.from_bytes(value, "big")
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    if isinstance(value, dict):
        if "to" in value:
            return _parse_uint(value["to"])
        if "+" in value:
            return _parse_uint(value["+"])
        if "*" in value and isinstance(value["*"], dict) and "to" in value["*"]:
            return _parse_uint(value["*"]["to"])
    return None


def _parse_uint_from(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, dict):
        if "from" in value:
            return _parse_uint(value["from"])
        if "-" in value:
            return None
        if "*" in value and isinstance(value["*"], dict) and "from" in value["*"]:
            return _parse_uint(value["*"]["from"])
        if "+" in value:
            return 0
    return _parse_uint(value)


def _storage_diff_map(account_diff: dict) -> dict:
    if not account_diff:
        return {}
    storage = account_diff.get("storage") or account_diff.get("Storage") or {}
    return storage


def _find_storage_entry(storage_diff: dict, holder: bytes, slot: int):
    key_alt = _storage_key(holder, slot).lower()
    for k, v in storage_diff.items():
        if str(k).lower() == key_alt:
            return v
    return None


def _slot_delta_from_entry(entry) -> Optional[int]:
    if entry is None:
        return 0
    if isinstance(entry, dict) and (
        "from" in entry or "to" in entry or "*" in entry or "+" in entry or "-" in entry
    ):
        pre = _parse_uint_from(entry)
        post = _parse_uint(entry)
        if pre is None or post is None:
            return None
        return post - pre
    return None


async def _find_balance_mapping_slot(
    web3: AsyncWeb3,
    token: bytes,
    holder: bytes,
    block_number: int,
    storage_diff: dict,
) -> Optional[int]:
    token_cs = _checksum(token)
    holder_cs = _checksum(holder)
    try:
        contract = web3.eth.contract(address=token_cs, abi=ERC20_ABI)
        balance = int(
            await contract.functions.balanceOf(holder_cs).call(
                block_identifier=block_number
            )
        )
    except Exception:
        logging.info("balanceOf failed during balance-slot identification", exc_info=True)
        return None

    matching: List[int] = []
    for slot in range(0, 64):
        key = _storage_key(holder, slot)
        try:
            raw = await web3.eth.get_storage_at(
                token_cs, key, block_identifier=block_number
            )
        except Exception:
            continue
        stored = int.from_bytes(bytes(raw), "big")
        if stored == balance:
            matching.append(slot)

    if balance == 0:
        matching = [
            slot
            for slot in matching
            if _find_storage_entry(storage_diff, holder, slot) is not None
        ]

    if len(matching) == 1:
        return matching[0]
    return None


def _account_key_variants(address: bytes) -> List[str]:
    addr = evm_address_from_bytes32(left_pad_32(address)).hex()
    return [
        "0x" + addr.lower(),
        Web3.to_checksum_address("0x" + addr),
        addr.lower(),
    ]


def _find_account_diff(state_diff: dict, address: bytes) -> Optional[dict]:
    variants = {v.lower() for v in _account_key_variants(address)}
    for key, value in state_diff.items():
        if str(key).lower() in variants:
            return value
    return None


async def _extract_token_balance_diff(
    web3: AsyncWeb3,
    state_diff: dict,
    token: bytes,
    holder: bytes,
    block_number: int,
) -> Optional[int]:
    account = _find_account_diff(state_diff, token)
    if account is None:
        return None
    storage = _storage_diff_map(account)
    if not storage:
        return None
    slot = await _find_balance_mapping_slot(
        web3, token, holder, block_number, storage
    )
    if slot is None:
        return None
    return _slot_delta_from_entry(_find_storage_entry(storage, holder, slot))


def _normalize_trace_state_diff(result) -> Optional[dict]:
    if result is None:
        return None
    if isinstance(result, dict):
        if "stateDiff" in result:
            return result["stateDiff"]
        if "pre" in result and "post" in result:
            merged = {}
            pre = result["pre"] or {}
            post = result["post"] or {}
            addrs = set(pre.keys()) | set(post.keys())
            for addr in addrs:
                pre_acc = pre.get(addr) or {}
                post_acc = post.get(addr) or {}
                pre_storage = pre_acc.get("storage") or {}
                post_storage = post_acc.get("storage") or {}
                storage = {}
                for slot in set(pre_storage.keys()) | set(post_storage.keys()):
                    storage[slot] = {
                        "from": pre_storage.get(slot, "0x0"),
                        "to": post_storage.get(slot, "0x0"),
                    }
                merged[addr] = {"storage": storage}
            return merged
        if any(isinstance(v, dict) and ("storage" in v or "balance" in v) for v in result.values()):
            return result
    return None


async def _rpc(web3: AsyncWeb3, method: str, params: list):
    provider = web3.provider
    if hasattr(provider, "make_request"):
        response = await provider.make_request(method, params)
        if isinstance(response, dict) and "result" in response:
            return response["result"]
        if isinstance(response, dict) and "error" in response:
            raise RuntimeError(response["error"])
        return response
    raise RuntimeError("web3 provider does not support make_request")


async def fetch_token_balance_diff(
    web3: AsyncWeb3,
    tx_hash: str,
    token: bytes,
    holder: bytes,
    block_number: int,
) -> Optional[int]:
    tx_hash_hex = tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash

    try:
        result = await _rpc(
            web3,
            "debug_traceTransaction",
            [tx_hash_hex, {"tracer": "prestateTracer", "tracerConfig": {"diffMode": True}}],
        )
        state_diff = _normalize_trace_state_diff(result)
        if state_diff is not None:
            diff = await _extract_token_balance_diff(
                web3, state_diff, token, holder, block_number
            )
            if diff is not None:
                return diff
    except Exception:
        logging.info("debug_traceTransaction prestateTracer failed", exc_info=True)

    try:
        result = await _rpc(
            web3,
            "trace_replayTransaction",
            [tx_hash_hex, ["stateDiff"]],
        )
        state_diff = _normalize_trace_state_diff(result)
        if state_diff is not None:
            diff = await _extract_token_balance_diff(
                web3, state_diff, token, holder, block_number
            )
            if diff is not None:
                return diff
    except Exception:
        logging.info("trace_replayTransaction stateDiff failed", exc_info=True)

    return None


async def read_wrapped_cat_views(
    web3: AsyncWeb3, address: bytes, block_number: int
) -> Tuple[Optional[bytes], Optional[bytes], Optional[bytes], Optional[bytes], Optional[bytes], Optional[int], Optional[int]]:
    try:
        contract = web3.eth.contract(address=_checksum(address), abi=WRAPPED_CAT_ABI)
        portal = await contract.functions.portal().call(block_identifier=block_number)
        other_chain = await contract.functions.otherChain().call(block_identifier=block_number)
        locker = await contract.functions.lockerPuzzleHash().call(block_identifier=block_number)
        unlocker = await contract.functions.unlockerPuzzleHash().call(block_identifier=block_number)
        tip = await contract.functions.tip().call(block_identifier=block_number)
        ratio = await contract.functions.mojoToTokenRatio().call(block_identifier=block_number)
        code = await web3.eth.get_code(_checksum(address), block_identifier=block_number)
        return (
            bytes(code),
            left_pad_32(bytes.fromhex(portal[2:] if isinstance(portal, str) else portal.hex())),
            _as_bytes3(other_chain),
            _as_bytes32(locker),
            _as_bytes32(unlocker),
            int(tip),
            int(ratio),
        )
    except Exception:
        logging.error("Failed to read WrappedCAT views", exc_info=True)
        return None, None, None, None, None, None, None


def _decode_uint256(data: bytes, position: int) -> int:
    return int.from_bytes(data[position:position + 32], "big")


def _decode_address(data: bytes, position: int) -> bytes:
    return data[position + 12:position + 32]


async def evaluate_chia_message_policy(
    *,
    bridging_coin: Coin,
    parent_spend: CoinSpend,
    routes: BridgeRoutes,
    node,
    block_spends: Optional[Sequence[CoinSpend]],
    web3_for_chain,
    evm_block_number: Optional[int],
) -> PolicyResult:
    try:
        puzzle = Program.from_bytes(bytes(parent_spend.puzzle_reveal))
        mod, _ = puzzle.uncurry()
    except Exception as exc:
        return PolicyResult("reject", f"parent puzzle parse error: {exc}")

    if mod == CAT_BURNER_MOD:
        cat_spend = await _fetch_burner_cat_spend(node, parent_spend, routes)
        if cat_spend is None:
            return PolicyResult("retry", "could not load CAT spend for burner")
        return validate_chia_erc20_unwrap(bridging_coin, parent_spend, cat_spend, routes)

    if mod == LOCKER_MOD:
        if block_spends is None:
            return PolicyResult("retry", "missing block spends for locker")
        try:
            curry_args = list(puzzle.uncurry()[1].as_iter())
            dest_chain = curry_args[0].as_atom()
            dest_addr = curry_args[1].as_atom()
        except Exception:
            return PolicyResult("reject", "locker curry parse error")

        web3 = web3_for_chain(dest_chain)
        if web3 is None:
            return PolicyResult("retry", "no web3 for locker destination chain")
        if evm_block_number is None:
            try:
                evm_block_number = await web3.eth.block_number
            except Exception:
                return PolicyResult("retry", "could not read destination block number")

        code, view_portal, view_other, view_locker, _, __, ___ = await read_wrapped_cat_views(
            web3, dest_addr, evm_block_number
        )
        return validate_chia_cat_wrap(
            bridging_coin,
            parent_spend,
            block_spends,
            routes,
            wrapped_cat_runtime_code=code,
            view_portal=view_portal,
            view_other_chain=view_other,
            view_locker_puzzle_hash=view_locker,
        )

    return PolicyResult("reject", "parent is neither burner nor locker")


async def _fetch_burner_cat_spend(node, burner_spend: CoinSpend, routes: BridgeRoutes) -> Optional[CoinSpend]:
    try:
        puzzle = Program.from_bytes(bytes(burner_spend.puzzle_reveal))
        solution = Program.from_bytes(bytes(burner_spend.solution))
        mod, curried = puzzle.uncurry()
        if mod != CAT_BURNER_MOD:
            return None
        curry_args = list(curried.as_iter())
        dest_chain = curry_args[3].as_atom()
        dest_addr = curry_args[4].as_atom()
        sol_args = list(solution.as_iter())
        cat_parent_info = sol_args[0].as_atom()
        cat_amount = sol_args[2].as_int()
        memo_asset = sol_args[3].as_atom()
        memo_receiver = sol_args[4].as_atom()

        wrapped_tail = get_wrapped_tail(
            bytes32(routes.portal_launcher_id),
            dest_chain,
            dest_addr,
            left_pad_32(memo_asset),
        )
        expected_inner = get_cat_burn_inner_puzzle(
            dest_chain,
            dest_addr,
            left_pad_32(memo_asset),
            memo_receiver,
            burner_spend.coin.amount,
        )
        expected_cat_puzzle = construct_cat_puzzle(
            CAT_MOD,
            wrapped_tail.get_tree_hash(),
            expected_inner,
            CAT_MOD_HASH,
        )
        expected_cat_coin = Coin(cat_parent_info, expected_cat_puzzle.get_tree_hash(), cat_amount)
        cat_id = expected_cat_coin.name()

        coin_record = await node.get_coin_record_by_name(cat_id)
        if coin_record is None or coin_record.spent_block_index == 0:
            return None
        spend = await node.get_puzzle_and_solution(cat_id, coin_record.spent_block_index)
        return spend
    except Exception:
        logging.error("Failed to fetch burner CAT spend", exc_info=True)
        return None


async def evaluate_evm_message_policy(
    *,
    web3: AsyncWeb3,
    routes: BridgeRoutes,
    source_chain: bytes,
    source: bytes,
    destination: bytes,
    destination_chain: bytes,
    contents: Sequence[bytes],
    block_number: int,
    tx_hash: str,
) -> PolicyResult:
    if len(source) > 32:
        source = source[-32:]
    if len(destination) > 32:
        destination = destination[-32:]

    route = routes.for_chain(source_chain)
    if route is None:
        return PolicyResult("reject", "unknown source chain")

    impl_result = await check_portal_impl(web3, route, block_number)
    if impl_result.kind != "accept":
        return impl_result

    try:
        tx = await web3.eth.get_transaction(tx_hash)
        receipt = await web3.eth.get_transaction_receipt(tx_hash)
        tx_status = int(receipt["status"])
    except Exception:
        return PolicyResult("retry", "could not load tx/receipt")

    source_addr = evm_address_from_bytes32(left_pad_32(source))
    bridge_addr = evm_address_from_bytes32(left_pad_32(route.erc20_bridge_address))

    if source_addr == bridge_addr:
        return await _evaluate_evm_erc20_wrap(
            web3=web3,
            routes=routes,
            route=route,
            source_chain=source_chain,
            source=source,
            destination=destination,
            destination_chain=destination_chain,
            contents=contents,
            block_number=block_number,
            tx=tx,
            tx_hash=tx_hash,
            tx_status=tx_status,
        )

    return await _evaluate_evm_cat_unwrap(
        web3=web3,
        routes=routes,
        source_chain=source_chain,
        source=source,
        destination=destination,
        destination_chain=destination_chain,
        contents=contents,
        block_number=block_number,
        tx=tx,
        tx_hash=tx_hash,
        tx_status=tx_status,
    )


async def _evaluate_evm_erc20_wrap(
    *,
    web3: AsyncWeb3,
    routes: BridgeRoutes,
    route,
    source_chain: bytes,
    source: bytes,
    destination: bytes,
    destination_chain: bytes,
    contents: Sequence[bytes],
    block_number: int,
    tx,
    tx_hash: str,
    tx_status: int,
) -> PolicyResult:
    try:
        bridge = web3.eth.contract(address=_checksum(route.erc20_bridge_address), abi=BRIDGE_ABI)
        tip_bps = await bridge.functions.tip().call(block_identifier=block_number)
        weth_to_eth_ratio = await bridge.functions.wethToEthRatio().call(
            block_identifier=block_number
        )
        portal = web3.eth.contract(address=_checksum(route.portal_address), abi=PORTAL_ABI)
        message_toll = await portal.functions.messageToll().call(block_identifier=block_number)
    except Exception:
        return PolicyResult("retry", "could not read bridge/portal views")

    raw_input = bytes(tx.input)
    selector = raw_input[:4] if len(raw_input) >= 4 else b""
    msg_value = int(tx.value)

    is_bridge_ether = selector == BRIDGE_ETHER_SEL
    gross_mojo_amount: Optional[int] = None
    asset_for_decimals: Optional[bytes] = None

    try:
        if selector == BRIDGE_TO_CHIA_SEL:
            asset_for_decimals = _decode_address(raw_input, 4)
            gross_mojo_amount = _decode_uint256(raw_input, 4 + 32 + 32)
        elif selector == BRIDGE_PERMIT_SEL:
            asset_for_decimals = _decode_address(raw_input, 4)
            gross_mojo_amount = _decode_uint256(raw_input, 4 + 32 + 32)
        elif selector == BRIDGE_ETHER_SEL:
            asset_for_decimals = evm_address_from_bytes32(left_pad_32(route.millieth_address))
            if msg_value < message_toll:
                return PolicyResult("reject", "msg.value < message toll")
            amount_after_toll = msg_value - message_toll
            token = web3.eth.contract(
                address=_checksum(route.millieth_address), abi=ERC20_ABI
            )
            decimals = await token.functions.decimals().call(block_identifier=block_number)
            factor = 10 ** (int(decimals) - 3)
            if weth_to_eth_ratio <= 0 or amount_after_toll % weth_to_eth_ratio != 0:
                return PolicyResult("reject", "amountAfterToll not divisible by wethToEthRatio")
            gross_mojo_amount = amount_after_toll // weth_to_eth_ratio // factor
        else:
            if len(contents) == 3:
                asset_for_decimals = evm_address_from_bytes32(left_pad_32(contents[0]))
            gross_mojo_amount = None
    except Exception:
        return PolicyResult("retry", "could not decode bridge calldata")

    decimals: Optional[int] = None
    if asset_for_decimals is not None:
        try:
            token = web3.eth.contract(address=_checksum(asset_for_decimals), abi=ERC20_ABI)
            decimals = int(await token.functions.decimals().call(block_identifier=block_number))
        except Exception:
            return PolicyResult("retry", "could not read token decimals")

    balance_diff_asset = left_pad_32(contents[0]) if contents else None
    bridge_token_balance_diff = None
    if balance_diff_asset is not None:
        bridge_token_balance_diff = await fetch_token_balance_diff(
            web3,
            tx_hash,
            balance_diff_asset,
            route.erc20_bridge_address,
            block_number,
        )
        if bridge_token_balance_diff is None:
            return PolicyResult("retry", "missing bridge balance diff")

    return validate_evm_erc20_wrap(
        routes=routes,
        source_chain=source_chain,
        source=source,
        destination=destination,
        destination_chain=destination_chain,
        contents=contents,
        tx_status=tx_status,
        tip_bps=int(tip_bps) if tip_bps is not None else None,
        decimals=decimals,
        gross_mojo_amount=gross_mojo_amount,
        bridge_token_balance_diff=bridge_token_balance_diff,
        balance_diff_asset=balance_diff_asset,
        is_bridge_ether=is_bridge_ether,
        msg_value=msg_value if is_bridge_ether else None,
        message_toll=int(message_toll) if is_bridge_ether else None,
        weth_to_eth_ratio=int(weth_to_eth_ratio) if is_bridge_ether else None,
    )


async def _evaluate_evm_cat_unwrap(
    *,
    web3: AsyncWeb3,
    routes: BridgeRoutes,
    source_chain: bytes,
    source: bytes,
    destination: bytes,
    destination_chain: bytes,
    contents: Sequence[bytes],
    block_number: int,
    tx,
    tx_hash: str,
    tx_status: int,
) -> PolicyResult:
    code, view_portal, view_other, _, unlocker, tip_bps, mojo_to_token_ratio = await read_wrapped_cat_views(
        web3, source, block_number
    )
    if code is None:
        return PolicyResult("retry", "missing WrappedCAT runtime code")

    raw_input = bytes(tx.input)
    gross_mojo_amount: Optional[int] = None
    selector = raw_input[:4] if len(raw_input) >= 4 else b""
    if selector == BRIDGE_BACK_SEL and len(raw_input) >= 4 + 64:
        gross_mojo_amount = _decode_uint256(raw_input, 4 + 32)

    tx_from = tx["from"]
    if isinstance(tx_from, str):
        sender = bytes.fromhex(tx_from[2:] if tx_from.startswith("0x") else tx_from)
    else:
        sender = bytes(tx_from)
    route = routes.for_chain(source_chain)
    if route is None:
        return PolicyResult("reject", "unknown source chain")

    sender_balance_diff = await fetch_token_balance_diff(
        web3, tx_hash, source, sender, block_number
    )
    portal_balance_diff = await fetch_token_balance_diff(
        web3, tx_hash, source, route.portal_address, block_number
    )
    if sender_balance_diff is None or portal_balance_diff is None:
        return PolicyResult("retry", "missing balance diffs")

    return validate_evm_cat_unwrap(
        routes=routes,
        source_chain=source_chain,
        source=source,
        destination=destination,
        destination_chain=destination_chain,
        contents=contents,
        tx_status=tx_status,
        tip_bps=tip_bps,
        mojo_to_token_ratio=mojo_to_token_ratio,
        gross_mojo_amount=gross_mojo_amount,
        sender_balance_diff=sender_balance_diff,
        portal_balance_diff=portal_balance_diff,
        view_portal=view_portal,
        view_other_chain=view_other,
        unlocker_puzzle_hash=unlocker,
        wrapped_cat_runtime_code=code,
        asset_id_known=False,
    )


async def load_block_spends(node, height: int) -> Optional[List[CoinSpend]]:
    try:
        record = await node.get_block_record_by_height(height)
        if record is None:
            return None
        spends = await node.get_block_spends(record.header_hash)
        if spends is not None:
            return spends
    except Exception:
        logging.info("get_block_spends failed; trying additions_and_removals", exc_info=True)

    try:
        record = await node.get_block_record_by_height(height)
        if record is None:
            return None
        additions, removals = await node.get_additions_and_removals(record.header_hash)
        spends: List[CoinSpend] = []
        for coin_record in removals:
            spend = await node.get_puzzle_and_solution(
                coin_record.coin.name(), coin_record.spent_block_index
            )
            # A missing spend is an incomplete load. Returning the partial list
            # makes CAT-wrap policy reject the message permanently.
            if spend is None:
                logging.info(
                    "block spend missing for coin %s at height %s; will retry",
                    coin_record.coin.name().hex(),
                    height,
                )
                return None
            spends.append(spend)
        return spends
    except Exception:
        logging.error("Failed to load block spends", exc_info=True)
        return None

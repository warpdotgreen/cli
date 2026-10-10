from __future__ import annotations

import binascii
import json
import logging
import time
from pathlib import Path
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
    ControlEvent,
    _command_and_id,
    decide_control,
    fetch_control_events,
    future_stop_author_notes,
    normalize_hex,
)
from commands.spend_policy import (
    REJECTED_SIG,
    BridgeRoutes,
    PolicyResult,
    compare_portal_impl,
    evm_address_from_bytes32,
    left_pad_32,
    match_wrapped_cat_bytecode,
    validate_chia_cat_wrap,
    validate_chia_erc20_unwrap,
    validate_evm_cat_unwrap,
    validate_evm_erc20_wrap,
)
from drivers.utils import raw_hash
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


SIGNER_BATCH_CAP = 8
SIGNER_RETRY_INITIAL_SECONDS = 5
SIGNER_RETRY_MAX_SECONDS = 5 * 60


def signer_retry_delay_seconds(attempt: int) -> int:
    """Exponential delay for a 1-based retry attempt, capped at five minutes."""
    delay = SIGNER_RETRY_INITIAL_SECONDS * (2 ** (attempt - 1))
    if delay > SIGNER_RETRY_MAX_SECONDS:
        return SIGNER_RETRY_MAX_SECONDS
    return delay


class SignerRetryDeadlines:
    """In-memory message id → next attempt. A process restart starts over."""

    def __init__(self) -> None:
        self._next_attempt: dict[str, float] = {}
        self._attempts: dict[str, int] = {}

    def due_batch(self, messages, now: float, cap: int = SIGNER_BATCH_CAP):
        def rank(message):
            message_id = _policy_message_id(message)
            return (self._next_attempt.get(message_id, 0.0), message_id)

        due = [
            message
            for message in messages
            if self._next_attempt.get(_policy_message_id(message), 0.0) <= now
        ]
        due.sort(key=rank)
        return due[:cap]

    def note_retry(self, message, now: float) -> None:
        message_id = _policy_message_id(message)
        attempt = self._attempts.get(message_id, 0) + 1
        self._attempts[message_id] = attempt
        self._next_attempt[message_id] = now + signer_retry_delay_seconds(attempt)

    def note_finished(self, message) -> None:
        message_id = _policy_message_id(message)
        self._next_attempt.pop(message_id, None)
        self._attempts.pop(message_id, None)


def _signature_still_open(message) -> bool:
    sig = message.sig
    return sig is None or sig == b""


async def sign_due_unsigned_messages(
    messages,
    deadlines: SignerRetryDeadlines,
    sign_one,
    now: float | None = None,
):
    """Sign one bounded batch of unsigned rows whose retry deadline has elapsed.

    A row that stays unsigned is a retry: its next attempt is remembered here,
    not in the database. A reject writes REJECTED_SIG and drops out of the
    unsigned query, so it is not scheduled again.
    """
    if now is None:
        now = time.monotonic()
    batch = deadlines.due_batch(messages, now)
    for message in batch:
        await sign_one(message)
        if _signature_still_open(message):
            deadlines.note_retry(message, now)
        else:
            deadlines.note_finished(message)
    return batch


_last_control_command: str | None = None

# App folder, same working directory as config.json and data.db (/app in Docker).
STOPS_FILE = Path("stops")
_MISSING_STOPS_WARNING = (
    "WARNING: Could not find the 'stops' file in the app's folder; please create an empty one"
)
_seen_stops: dict[str, ControlEvent] = {}
_missing_stops_warned = False


class StopsFileError(Exception):
    """The stops file exists but is not a JSON object of stops."""


def _note_control_command(command: str) -> None:
    global _last_control_command
    if command == _last_control_command:
        return
    if command == "stop":
        logging.warning("NOSTR STOP")
    elif command == "start" and _last_control_command == "stop":
        logging.warning("NOSTR START: resume after stop")
    _last_control_command = command


def _is_remembered_stop(event: ControlEvent, stop_authors: set[str]) -> bool:
    """A stop note from a stop author, including one dated in the future."""
    parsed = _command_and_id(event.content)
    if parsed is None:
        return False
    command, _note_id = parsed
    return command == "stop" and normalize_hex(event.author) in stop_authors


def _stops_from_json(data: object) -> dict[str, ControlEvent]:
    if not isinstance(data, dict):
        raise StopsFileError("stops file is not a JSON object of stops")
    loaded: dict[str, ControlEvent] = {}
    for event_id, record in data.items():
        if not isinstance(event_id, str) or not isinstance(record, dict):
            raise StopsFileError("stops file is not a JSON object of stops")
        author = record.get("author")
        content = record.get("content")
        created_at = record.get("created_at")
        if (
            not isinstance(author, str)
            or not isinstance(content, str)
            or isinstance(created_at, bool)
            or not isinstance(created_at, int)
        ):
            raise StopsFileError("stops file is not a JSON object of stops")
        parsed = _command_and_id(content)
        if parsed is None or parsed[0] != "stop":
            raise StopsFileError("stops file is not a JSON object of stops")
        loaded[event_id] = ControlEvent(
            id=event_id,
            author=author,
            content=content,
            created_at=created_at,
        )
    return loaded


def _read_stops_file(path: Path) -> dict[str, ControlEvent] | None:
    """Load stops. None means the file is missing. An empty file is {}."""
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise StopsFileError("stops file is not a JSON object of stops") from e
    if raw.strip() == "":
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise StopsFileError("stops file is not a JSON object of stops") from e
    return _stops_from_json(data)


def _write_stops_file(path: Path, stops: dict[str, ControlEvent]) -> None:
    """Overwrite the existing file in place so a bind-mounted inode stays put."""
    payload = {
        event_id: {
            "author": event.author,
            "content": event.content,
            "created_at": event.created_at,
        }
        for event_id, event in stops.items()
    }
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with path.open("r+") as handle:
        handle.seek(0)
        handle.write(body)
        handle.truncate()


def _absorb_fetch_stops(
    events: Sequence[ControlEvent], stop_authors: set[str]
) -> None:
    for event in events:
        if _is_remembered_stop(event, stop_authors):
            _seen_stops[event.id] = event


def _remember_stops(
    events: Sequence[ControlEvent], stop_authors: set[str]
) -> dict[str, ControlEvent]:
    """Merge the stops file with stops in this fetch.

    A missing file logs the warning and leaves new stops in this process only.
    A file that is not a JSON object of stops raises StopsFileError after the
    stops in this fetch are remembered in memory. Those are not written back.
    """
    global _missing_stops_warned
    path = STOPS_FILE
    try:
        disk = _read_stops_file(path)
    except StopsFileError:
        _absorb_fetch_stops(events, stop_authors)
        raise
    if disk is None:
        if not _missing_stops_warned:
            logging.warning(_MISSING_STOPS_WARNING)
            _missing_stops_warned = True
        disk = {}
        file_missing = True
    else:
        _missing_stops_warned = False
        file_missing = False

    for event in disk.values():
        _seen_stops.setdefault(event.id, event)
    _absorb_fetch_stops(events, stop_authors)

    if not file_missing and set(_seen_stops) - set(disk):
        try:
            _write_stops_file(path, _seen_stops)
        except OSError:
            logging.error("failed to write stops file", exc_info=True)
            raise
    return dict(_seen_stops)


def _with_remembered_stops(
    events: Sequence[ControlEvent], remembered: dict[str, ControlEvent]
) -> List[ControlEvent]:
    present = {event.id for event in events}
    extra = [event for event_id, event in remembered.items() if event_id not in present]
    return list(events) + extra


async def control_allows_start(control_config: ControlConfig) -> bool:
    global _last_control_command
    try:
        events = await fetch_control_events(
            control_config.relays,
            control_config.stop_authors,
            control_config.start_authors,
        )
    except Exception:
        if _last_control_command != "stop":
            logging.warning("NOSTR STOP: control relay fetch failed", exc_info=True)
            _last_control_command = "stop"
        return False

    stop_authors = {normalize_hex(author) for author in control_config.stop_authors}
    try:
        remembered = _remember_stops(events, stop_authors)
    except StopsFileError:
        logging.error("stops file is not a JSON object of stops", exc_info=True)
        _note_control_command("stop")
        return False
    except OSError:
        _note_control_command("stop")
        return False

    now_unix = int(time.time())
    combined = _with_remembered_stops(events, remembered)
    for ev in future_stop_author_notes(
        combined, now_unix, control_config.stop_authors
    ):
        logging.warning(
            f"ignoring future-dated stop-author note {ev.content!r} "
            f"created_at={ev.created_at} until the clock catches up"
        )
    command = decide_control(
        combined,
        now_unix,
        control_config.stop_authors,
        control_config.start_authors,
    )
    _note_control_command(command)
    return command == "start"


def _checksum(addr: bytes) -> str:
    # evm_address_from_bytes32 returns a 20-byte HexBytes unchanged. Copy to
    # plain bytes so to_checksum_address encodes with binascii, one 0x prefix.
    raw = bytes(evm_address_from_bytes32(left_pad_32(addr)))
    return Web3.to_checksum_address(raw)


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


def _decode_hex_or_bytes(value) -> bytes:
    if isinstance(value, str):
        text = value[2:] if value[:2].lower() == "0x" else value
        return bytes.fromhex(text)
    return bytes(value)


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
        if bytes(impl) == b"\x00" * 20:
            return None
        code = await web3.eth.get_code(
            _checksum(impl), block_identifier=block_number
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
    digest = Web3.keccak(left_pad_32(holder) + left_pad_32(slot.to_bytes(32, "big")))
    return "0x" + binascii.hexlify(bytes(digest)).decode("ascii")


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
    raw = bytes(evm_address_from_bytes32(left_pad_32(address)))
    bare = binascii.hexlify(raw).decode("ascii")
    return [
        "0x" + bare,
        Web3.to_checksum_address(raw),
        bare,
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


def _keccak_topic(signature: str) -> str:
    digest = bytes(Web3.keccak(text=signature))
    return "0x" + binascii.hexlify(digest).decode("ascii")


_TRANSFER_TOPIC = _keccak_topic("Transfer(address,address,uint256)")
_missing_tracer_methods: set[str] = set()


def _rpc_error_code(exc: BaseException) -> Optional[int]:
    if not exc.args:
        return None
    err = exc.args[0]
    if isinstance(err, dict):
        code = err.get("code")
        if isinstance(code, int):
            return code
    return None


def _log_tracer_failure(method: str, exc: BaseException) -> None:
    if _rpc_error_code(exc) == -32601:
        if method not in _missing_tracer_methods:
            _missing_tracer_methods.add(method)
            logging.info("%s is not available on this RPC", method)
        return
    logging.info("%s failed", method, exc_info=True)


def _as_hex_bytes(value) -> Optional[bytes]:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        text = value[2:] if value[:2].lower() == "0x" else value
        try:
            return bytes.fromhex(text)
        except ValueError:
            return None
    return None


def _norm_tx_hash(value) -> Optional[str]:
    raw = _as_hex_bytes(value)
    if raw is None:
        return None
    return binascii.hexlify(raw).decode("ascii")


def _topic_address(topic) -> Optional[bytes]:
    raw = _as_hex_bytes(topic)
    if raw is None or len(raw) != 32:
        return None
    return raw[-20:]


def _log_field(log, name: str):
    if isinstance(log, dict):
        return log.get(name)
    return getattr(log, name, None)


def _holder_log_nets(
    logs, token: bytes, holder: bytes
) -> Optional[dict]:
    """Net Transfer amount per tx hash for holder.

    Logs that are not a 3-topic Transfer plus a 32-byte amount are ignored.
    A standard Transfer that cannot be tied to a transaction hash fails the
    whole sum: dropping it would under-count the block.
    """
    try:
        holder_addr = evm_address_from_bytes32(left_pad_32(holder))
        token_addr = evm_address_from_bytes32(left_pad_32(token))
    except ValueError:
        return None
    transfer_topic = bytes.fromhex(_TRANSFER_TOPIC[2:])
    nets: dict = {}
    for log in logs:
        address = _topic_address(_log_field(log, "address"))
        if address is None:
            raw_address = _as_hex_bytes(_log_field(log, "address"))
            if raw_address is None or len(raw_address) != 20:
                return None
            address = raw_address
        if address != token_addr:
            continue
        topics = _log_field(log, "topics") or []
        try:
            topic_list = list(topics)
        except TypeError:
            return None
        if len(topic_list) != 3:
            continue
        topic0 = _as_hex_bytes(topic_list[0])
        if topic0 != transfer_topic:
            continue
        data = _as_hex_bytes(_log_field(log, "data"))
        sender = _topic_address(topic_list[1])
        receiver = _topic_address(topic_list[2])
        if data is None or len(data) != 32 or sender is None or receiver is None:
            continue
        tx_key = _norm_tx_hash(_log_field(log, "transactionHash"))
        if tx_key is None:
            return None
        value = int.from_bytes(data, "big")
        delta = 0
        if sender == holder_addr:
            delta -= value
        if receiver == holder_addr:
            delta += value
        if delta != 0:
            nets[tx_key] = nets.get(tx_key, 0) + delta
    return nets


async def _read_slot_uint(
    web3: AsyncWeb3, token_cs: str, holder: bytes, slot: int, block_number: int
) -> Optional[int]:
    try:
        raw = await web3.eth.get_storage_at(
            token_cs, _storage_key(holder, slot), block_identifier=block_number
        )
        return int.from_bytes(bytes(raw), "big")
    except Exception:
        logging.info("get_storage_at failed during log balance diff", exc_info=True)
        return None


async def _balance_storage_delta(
    web3: AsyncWeb3, token: bytes, holder: bytes, block_number: int
) -> Optional[int]:
    """Change in the balanceOf slot from the parent block to this block.

    A zero balance whose mapped slots are unchanged has delta 0. An ambiguous
    slot returns None so the caller retries instead of guessing.
    """
    if block_number < 1:
        return None
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
        logging.info("balanceOf failed during log balance diff", exc_info=True)
        return None

    matching: List[int] = []
    for slot in range(0, 64):
        stored = await _read_slot_uint(web3, token_cs, holder, slot, block_number)
        if stored is None:
            return None
        if stored == balance:
            matching.append(slot)

    if balance != 0:
        if len(matching) != 1:
            return None
        pre = await _read_slot_uint(
            web3, token_cs, holder, matching[0], block_number - 1
        )
        if pre is None:
            return None
        return balance - pre

    if not matching:
        return None
    changed: List[int] = []
    for slot in matching:
        pre = await _read_slot_uint(web3, token_cs, holder, slot, block_number - 1)
        if pre is None:
            return None
        if pre != 0:
            changed.append(0 - pre)
    if len(changed) > 1:
        return None
    if len(changed) == 1:
        return changed[0]
    return 0


async def _balance_diff_from_logs(
    web3: AsyncWeb3,
    tx_hash: str,
    token: bytes,
    holder: bytes,
    block_number: int,
) -> Optional[int]:
    storage_delta = await _balance_storage_delta(web3, token, holder, block_number)
    if storage_delta is None:
        return None
    try:
        logs = await web3.eth.get_logs(
            {
                "fromBlock": block_number,
                "toBlock": block_number,
                "address": _checksum(token),
                "topics": [_TRANSFER_TOPIC],
            }
        )
        log_list = list(logs)
    except Exception:
        logging.info("eth_getLogs failed during log balance diff", exc_info=True)
        return None

    nets = _holder_log_nets(log_list, token, holder)
    if nets is None:
        return None
    if sum(nets.values()) != storage_delta:
        logging.info(
            "token balance logs do not match storage slot change (logs=%s storage=%s)",
            sum(nets.values()),
            storage_delta,
        )
        return None
    tx_key = _norm_tx_hash(tx_hash)
    if tx_key is None:
        return None
    return nets.get(tx_key, 0)


async def _diff_from_trace_method(
    web3: AsyncWeb3,
    method: str,
    params: list,
    token: bytes,
    holder: bytes,
    block_number: int,
) -> Optional[Tuple[Optional[int]]]:
    """(diff,) when this method returned a state diff, else None.

    The diff inside the tuple may be None. That still means a tracer
    answered, so the log fallback must not replace it.
    """
    try:
        result = await _rpc(web3, method, params)
    except Exception as exc:
        _log_tracer_failure(method, exc)
        return None
    state_diff = _normalize_trace_state_diff(result)
    if state_diff is None:
        return None
    diff = await _extract_token_balance_diff(
        web3, state_diff, token, holder, block_number
    )
    return (diff,)


async def fetch_token_balance_diff(
    web3: AsyncWeb3,
    tx_hash: str,
    token: bytes,
    holder: bytes,
    block_number: int,
) -> Optional[int]:
    tx_hash_hex = tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash
    trace_calls = (
        (
            "debug_traceTransaction",
            [tx_hash_hex, {"tracer": "prestateTracer", "tracerConfig": {"diffMode": True}}],
        ),
        (
            "trace_replayTransaction",
            [tx_hash_hex, ["stateDiff"]],
        ),
    )
    saw_state_diff = False
    for method, params in trace_calls:
        traced = await _diff_from_trace_method(
            web3, method, params, token, holder, block_number
        )
        if traced is None:
            continue
        saw_state_diff = True
        if traced[0] is not None:
            return traced[0]
    if saw_state_diff:
        return None
    return await _balance_diff_from_logs(
        web3, tx_hash_hex, token, holder, block_number
    )


class RpcRateLimited(Exception):
    """The RPC refused the call because the endpoint is rate limited.

    The message is fixed so logs do not include the request URL.
    """


def is_rpc_rate_limited(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(current, "status", None)
        if status == 429:
            return True
        message = getattr(current, "message", None)
        if isinstance(message, str) and "too many requests" in message.lower():
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return False


def _raise_if_rate_limited(exc: BaseException) -> None:
    if is_rpc_rate_limited(exc):
        body = getattr(exc, "body", "") or ""
        if not isinstance(body, str):
            body = ""
        body = body.replace("\n", " ").strip()
        if len(body) > 300:
            body = body[:300]
        message = "rpc rate limited" if not body else f"rpc rate limited: {body}"
        raise RpcRateLimited(message) from None


def _bytecode_is_not_wrapped_cat(code: bytes) -> bool:
    """True when code is present and cannot be the current WrappedCAT.

    An artifact-load failure returns False so the caller retries instead of
    rejecting a contract it could not compare.
    """
    try:
        return not match_wrapped_cat_bytecode(code)
    except Exception:
        return False


async def read_wrapped_cat_views(
    web3: AsyncWeb3, address: bytes, block_number: int
) -> Tuple[Optional[bytes], Optional[bytes], Optional[bytes], Optional[bytes], Optional[bytes], Optional[int], Optional[int]]:
    try:
        code = bytes(await web3.eth.get_code(_checksum(address), block_identifier=block_number))
    except Exception as exc:
        _raise_if_rate_limited(exc)
        logging.error("Failed to read WrappedCAT code", exc_info=True)
        return None, None, None, None, None, None, None

    # Empty code and structurally non-matching runtime are a definite reject.
    # Empty is checked first: an artifact-load failure makes
    # _bytecode_is_not_wrapped_cat return False, and that must not turn an
    # EOA into a retry. A non-empty runtime we could not compare falls through
    # so the caller retries. A masked match is not identity: validate_chia_cat_wrap
    # and validate_evm_cat_unwrap still check every immutable occurrence against
    # the route portal, xch, and the tip and ratio those callers pass.
    if len(code) == 0 or _bytecode_is_not_wrapped_cat(code):
        return code, None, None, None, None, None, None

    try:
        contract = web3.eth.contract(address=_checksum(address), abi=WRAPPED_CAT_ABI)
        portal = await contract.functions.portal().call(block_identifier=block_number)
        other_chain = await contract.functions.otherChain().call(block_identifier=block_number)
        locker = await contract.functions.lockerPuzzleHash().call(block_identifier=block_number)
        unlocker = await contract.functions.unlockerPuzzleHash().call(block_identifier=block_number)
        tip = await contract.functions.tip().call(block_identifier=block_number)
        ratio = await contract.functions.mojoToTokenRatio().call(block_identifier=block_number)
        return (
            code,
            left_pad_32(_decode_hex_or_bytes(portal)),
            _as_bytes3(other_chain),
            _as_bytes32(locker),
            _as_bytes32(unlocker),
            int(tip),
            int(ratio),
        )
    except RpcRateLimited:
        raise
    except Exception as exc:
        _raise_if_rate_limited(exc)
        logging.error("Failed to read WrappedCAT views", exc_info=True)
        return None, None, None, None, None, None, None


def _decode_uint256(data: bytes, position: int) -> int:
    return int.from_bytes(data[position:position + 32], "big")


def _decode_address(data: bytes, position: int) -> bytes:
    return data[position + 12:position + 32]


def _burner_tail_hash_hash_mismatch(burner_spend: CoinSpend, routes: BridgeRoutes) -> bool:
    """True only when the burner solution parses and tail_hash_hash is wrong.

    A match, or a solution that cannot be parsed, returns False so the caller
    can keep retrying the CAT coin lookup.
    """
    try:
        puzzle = Program.from_bytes(bytes(burner_spend.puzzle_reveal))
        solution = Program.from_bytes(bytes(burner_spend.solution))
        _, curried = puzzle.uncurry()
        curry_args = list(curried.as_iter())
        dest_chain = curry_args[3].as_atom()
        dest_addr = curry_args[4].as_atom()
        sol_args = list(solution.as_iter())
        tail_hash_hash = sol_args[1].as_atom()
        memo_asset = sol_args[3].as_atom()
        if (
            dest_chain is None
            or dest_addr is None
            or tail_hash_hash is None
            or memo_asset is None
        ):
            return False
        wrapped_tail = get_wrapped_tail(
            bytes32(routes.portal_launcher_id),
            dest_chain,
            dest_addr,
            left_pad_32(memo_asset),
        )
        expected = raw_hash([b"\x01", wrapped_tail.get_tree_hash()])
        return tail_hash_hash != expected
    except Exception:
        return False


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
            if _burner_tail_hash_hash_mismatch(parent_spend, routes):
                return PolicyResult("reject", "tail_hash_hash mismatch")
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
            except Exception as exc:
                if is_rpc_rate_limited(exc):
                    return PolicyResult("retry", "rpc rate limited")
                return PolicyResult("retry", "could not read destination block number")

        try:
            code, view_portal, view_other, view_locker, _, view_tip, view_ratio = await read_wrapped_cat_views(
                web3, dest_addr, evm_block_number
            )
        except RpcRateLimited:
            return PolicyResult("retry", "rpc rate limited")
        # get_code succeeded with an empty runtime. Reject before the mask
        # compare: an artifact-load failure must not turn this into a retry.
        # A non-empty runtime we could not compare still falls through.
        if code is not None and len(code) == 0:
            return PolicyResult("reject", "WrappedCAT bytecode mismatch after immutable mask")
        return validate_chia_cat_wrap(
            bridging_coin,
            parent_spend,
            block_spends,
            routes,
            wrapped_cat_runtime_code=code,
            view_portal=view_portal,
            view_other_chain=view_other,
            view_locker_puzzle_hash=view_locker,
            view_tip=view_tip,
            view_mojo_to_token_ratio=view_ratio,
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
    verify_balance_diff: bool = True,
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
            verify_balance_diff=verify_balance_diff,
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
        verify_balance_diff=verify_balance_diff,
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
    verify_balance_diff: bool = True,
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
    if verify_balance_diff and balance_diff_asset is not None:
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
        verify_balance_diff=verify_balance_diff,
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
    verify_balance_diff: bool = True,
) -> PolicyResult:
    try:
        code, view_portal, view_other, _, unlocker, tip_bps, mojo_to_token_ratio = await read_wrapped_cat_views(
            web3, source, block_number
        )
    except RpcRateLimited:
        return PolicyResult("retry", "rpc rate limited")
    if code is None:
        return PolicyResult("retry", "missing WrappedCAT runtime code")
    # get_code succeeded. Empty or non-WrappedCAT is final: do not trace the
    # transaction or read balances. Artifact-load failure returns False from
    # _bytecode_is_not_wrapped_cat, so a non-empty runtime we could not compare
    # still falls through to retry.
    if len(code) == 0 or _bytecode_is_not_wrapped_cat(code):
        return PolicyResult("reject", "WrappedCAT bytecode mismatch after immutable mask")

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

    sender_balance_diff = None
    portal_balance_diff = None
    if verify_balance_diff:
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
        verify_balance_diff=verify_balance_diff,
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

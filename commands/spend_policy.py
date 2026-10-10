from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Sequence, Tuple, Union

from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import INFINITE_COST, Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.types.condition_opcodes import ConditionOpcode
from chia.wallet.cat_wallet.cat_utils import CAT_MOD, CAT_MOD_HASH_HASH, construct_cat_puzzle
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH
from chia.wallet.trading.offer import OFFER_MOD, OFFER_MOD_HASH
from chia.wallet.util.curry_and_treehash import calculate_hash_of_quoted_mod_hash, curry_and_treehash

from drivers.portal import BRIDGING_PUZZLE_HASH
from drivers.utils import raw_hash
from drivers.wrapped_assets import (
    BURN_INNER_PUZZLE_MOD,
    BURN_INNER_PUZZLE_MOD_HASH,
    CAT_BURNER_MOD,
    get_cat_burn_inner_puzzle,
    get_cat_burner_puzzle,
    get_cat_minter_puzzle,
    get_wrapped_tail,
)
from drivers.wrapped_cats import (
    LOCKER_MOD,
    get_locker_puzzle,
    get_p2_controller_puzzle_hash_inner_puzzle_hash,
    get_unlocker_puzzle,
)

REJECTED_SIG = b"rejected"

PolicyKind = Literal["accept", "reject", "retry", "hold"]


@dataclass(frozen=True)
class VerifiedChiaMessage:
    source: bytes
    destination_chain: bytes
    destination: bytes
    contents: Tuple[bytes, ...]


@dataclass(frozen=True)
class PolicyResult:
    kind: PolicyKind
    reason: str
    verified: Optional[VerifiedChiaMessage] = None


@dataclass(frozen=True)
class EvmRoute:
    chain_id: bytes
    portal_address: bytes
    erc20_bridge_address: bytes
    millieth_address: bytes
    portal_impl_codehash: str


@dataclass(frozen=True)
class BridgeRoutes:
    portal_launcher_id: bytes
    eth: EvmRoute
    bse: EvmRoute

    def for_chain(self, chain_id: bytes) -> Optional[EvmRoute]:
        if chain_id == self.eth.chain_id:
            return self.eth
        if chain_id == self.bse.chain_id:
            return self.bse
        return None

    def pairs(self) -> Tuple[EvmRoute, EvmRoute]:
        return (self.eth, self.bse)

    @staticmethod
    def from_config(config: dict) -> BridgeRoutes:
        xch = config["xch"]
        return BridgeRoutes(
            portal_launcher_id=_parse_hex_bytes(xch["portal_launcher_id"]),
            eth=_parse_evm_route(config, b"eth"),
            bse=_parse_evm_route(config, b"bse"),
        )


def evm_address_from_bytes32(value: bytes) -> bytes:
    if len(value) == 20:
        return value
    if len(value) == 32:
        if value[:12] != b"\x00" * 12:
            raise ValueError("bytes32 address has non-zero prefix")
        return value[-20:]
    raise ValueError("address must be 20 bytes or left-padded 32 bytes")


def left_pad_32(value: bytes) -> bytes:
    if len(value) > 32:
        raise ValueError("value longer than 32 bytes")
    if len(value) < 32:
        return b"\x00" * (32 - len(value)) + value
    return value


def signed_destination_address(destination: bytes) -> bytes:
    """Last 20 bytes, the address the Base signer puts in typed data."""
    raw = destination.hex()
    if len(raw) < 40:
        raw = "0" * (40 - len(raw)) + raw
    else:
        raw = raw[-40:]
    return bytes.fromhex(raw)


def _canonical_contents(words: Sequence[bytes]) -> Optional[Tuple[bytes, ...]]:
    canonical = []
    for word in words:
        if len(word) > 32:
            return None
        canonical.append(left_pad_32(word))
    return tuple(canonical)


def _verified_chia_message(
    *,
    source: bytes,
    destination_chain: bytes,
    destination: bytes,
    contents: Sequence[bytes],
) -> Optional[VerifiedChiaMessage]:
    canonical = _canonical_contents(contents)
    if canonical is None or len(source) != 32:
        return None
    return VerifiedChiaMessage(
        source=source,
        destination_chain=destination_chain,
        destination=destination,
        contents=canonical,
    )


def chia_row_matches_verified(
    *,
    source: bytes,
    destination_chain: bytes,
    destination: bytes,
    contents: Sequence[bytes],
    verified: VerifiedChiaMessage,
) -> bool:
    if source != verified.source:
        return False
    if destination_chain != verified.destination_chain:
        return False
    if signed_destination_address(destination) != signed_destination_address(verified.destination):
        return False
    if len(contents) != len(verified.contents):
        return False
    return tuple(contents) == verified.contents


def normalize_signed_evm_source(source: bytes) -> Optional[bytes]:
    """20-byte address, using the same padding rules as the Chia signer."""
    if len(source) > 32:
        dropped = source[:-32]
        if dropped != b"\x00" * len(dropped):
            return None
        source = source[-32:]
    try:
        return evm_address_from_bytes32(source)
    except ValueError:
        return None


def _event_atom(value) -> bytes:
    if isinstance(value, str):
        if value.startswith(("0x", "0X")):
            return bytes.fromhex(value[2:])
        return value.encode()
    return bytes(value)


def decode_message_sent_fields(
    event,
) -> Optional[Tuple[bytes, bytes, bytes, bytes, Tuple[bytes, ...]]]:
    """nonce, source, destination_chain, destination, contents from a MessageSent log."""
    try:
        args = event["args"]
        source = _event_atom(args["source"])
        nonce = _event_atom(args["nonce"])
        destination_chain = _event_atom(args["destination_chain"])
        destination = _event_atom(args["destination"])
        contents = tuple(_event_atom(word) for word in args["contents"])
    except Exception:
        return None
    return nonce, source, destination_chain, destination, contents


def evm_log_matches_row(
    *,
    nonce: bytes,
    source: bytes,
    destination_chain: bytes,
    destination: bytes,
    contents: Sequence[bytes],
    log_nonce: bytes,
    log_source: bytes,
    log_destination_chain: bytes,
    log_destination: bytes,
    log_contents: Sequence[bytes],
) -> bool:
    if bytes(nonce) != bytes(log_nonce):
        return False
    row_source = normalize_signed_evm_source(bytes(source))
    event_source = normalize_signed_evm_source(bytes(log_source))
    if row_source is None or event_source is None or row_source != event_source:
        return False
    if bytes(destination_chain) != bytes(log_destination_chain):
        return False
    if bytes(destination) != bytes(log_destination):
        return False
    return tuple(bytes(word) for word in contents) == tuple(bytes(word) for word in log_contents)


def compare_portal_impl(actual_codehash: str, expected_codehash: str) -> PolicyResult:
    if _norm_hex(actual_codehash) != _norm_hex(expected_codehash):
        return PolicyResult("hold", "portal impl codehash mismatch")
    return PolicyResult("accept", "portal impl matches")


# solc 0.8 inlines every immutable as a PUSH32 word. bytesN is left-aligned in
# that word; addresses and integers are right-aligned. A different width is not
# masked, because the value check would not know which bytes are the value.
_IMMUTABLE_WORD = 32
_WRAPPED_CAT_IMMUTABLES = ("portal", "tip", "mojoToTokenRatio", "otherChain")


@dataclass(frozen=True)
class WrappedCatImmutable:
    """One AST id's occurrences. Spans are not flattened across ids."""

    ast_id: str
    name: str
    spans: Tuple[Tuple[int, int], ...]
    contract: str


@dataclass(frozen=True)
class WrappedCatArtifact:
    bytecode: bytes
    immutables: Tuple[WrappedCatImmutable, ...]
    base_contracts: Tuple[str, ...]


def mask_immutable_bytecode(code: bytes, spans: Sequence[Tuple[int, int]]) -> bytes:
    buf = bytearray(code)
    for start, length in spans:
        end = start + length
        if start < 0 or length < 0 or end > len(buf):
            raise ValueError("immutable span out of range")
        buf[start:end] = b"\x00" * length
    return bytes(buf)


def _walk_immutable_ast(node, contract_id, contracts, variables, wrapped_bases) -> None:
    if isinstance(node, dict):
        next_contract = contract_id
        node_type = node.get("nodeType")
        if node_type == "ContractDefinition":
            next_contract = node.get("id")
            contracts[next_contract] = str(node.get("name") or "")
            if node.get("name") == "WrappedCAT":
                wrapped_bases.extend(node.get("linearizedBaseContracts") or [])
        elif (
            node_type == "VariableDeclaration"
            and node.get("stateVariable")
            and node.get("mutability") == "immutable"
        ):
            variables[str(node.get("id"))] = (next_contract, str(node.get("name") or ""))
        for value in node.values():
            _walk_immutable_ast(value, next_contract, contracts, variables, wrapped_bases)
    elif isinstance(node, list):
        for value in node:
            _walk_immutable_ast(value, contract_id, contracts, variables, wrapped_bases)


def _wrapped_cat_from_build_info(data: dict) -> Optional[WrappedCatArtifact]:
    try:
        contract = data["output"]["contracts"]["contracts/WrappedCAT.sol"]["WrappedCAT"]
    except KeyError:
        return None
    deployed = contract["evm"]["deployedBytecode"]
    bytecode_hex = deployed.get("object")
    if bytecode_hex is None:
        return None
    bytecode = bytes.fromhex(str(bytecode_hex).replace("0x", ""))
    refs = deployed.get("immutableReferences") or {}
    if not isinstance(refs, dict):
        return None

    contracts: Dict[object, str] = {}
    variables: Dict[str, Tuple[object, str]] = {}
    wrapped_bases: List[object] = []
    for source in data.get("output", {}).get("sources", {}).values():
        ast = source.get("ast") if isinstance(source, dict) else None
        if ast is not None:
            _walk_immutable_ast(ast, None, contracts, variables, wrapped_bases)
    if not wrapped_bases:
        return None
    base_names: List[str] = []
    for base_id in wrapped_bases:
        base_name = contracts.get(base_id)
        if not base_name:
            return None
        base_names.append(base_name)

    immutables: List[WrappedCatImmutable] = []
    for ast_id, entries in refs.items():
        if not isinstance(entries, list):
            return None
        contract_id, var_name = variables.get(str(ast_id), (None, ""))
        contract_name = contracts.get(contract_id, "") if contract_id is not None else ""
        try:
            spans = tuple(
                sorted((int(entry["start"]), int(entry["length"])) for entry in entries)
            )
        except (KeyError, TypeError, ValueError):
            return None
        immutables.append(
            WrappedCatImmutable(str(ast_id), var_name, spans, contract_name)
        )
    immutables.sort(key=lambda item: item.ast_id)
    return WrappedCatArtifact(bytecode, tuple(immutables), tuple(base_names))


def load_wrapped_cat_build_info() -> WrappedCatArtifact:
    build_info_dir = Path(__file__).resolve().parents[1] / "artifacts" / "build-info"
    matches = build_info_dir.glob("*.json")
    found: Optional[WrappedCatArtifact] = None
    for path in matches:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.loads(handle.read())
        extracted = _wrapped_cat_from_build_info(data)
        if extracted is None:
            continue
        if found is None:
            found = extracted
        elif found != extracted:
            raise ValueError("WrappedCAT build-info artifacts disagree")
    if found is None:
        raise FileNotFoundError("no WrappedCAT in artifacts/build-info")
    return found


def load_wrapped_cat_artifact_bytecode() -> bytes:
    return load_wrapped_cat_build_info().bytecode


def load_wrapped_cat_immutable_spans() -> Tuple[WrappedCatImmutable, ...]:
    """Per-AST-id immutable occurrences. Spans of different ids stay grouped."""
    return load_wrapped_cat_build_info().immutables


def _span_is_word(span: Tuple[int, int], code_len: int) -> bool:
    start, length = span
    return length == _IMMUTABLE_WORD and 0 <= start and start + length <= code_len


def _classify_wrapped_cat_immutables(
    artifact: WrappedCatArtifact,
) -> Optional[Tuple[List[Tuple[int, int]], List[WrappedCatImmutable], List[WrappedCatImmutable]]]:
    """Maskable spans, WrappedCAT value immutables, and inherited immutables.

    An unknown name or a width other than the compiler's 32-byte word fails the
    classification. Those spans are left out of the mask list.
    """
    bases = set(artifact.base_contracts)
    mask_spans: List[Tuple[int, int]] = []
    values: List[WrappedCatImmutable] = []
    inherited: List[WrappedCatImmutable] = []
    found = set()
    for immutable in artifact.immutables:
        if not immutable.spans or not immutable.name or immutable.contract not in bases:
            return None
        if any(not _span_is_word(span, len(artifact.bytecode)) for span in immutable.spans):
            return None
        if immutable.contract == "WrappedCAT":
            if immutable.name not in _WRAPPED_CAT_IMMUTABLES:
                return None
            values.append(immutable)
            found.add(immutable.name)
        else:
            inherited.append(immutable)
        mask_spans.extend(immutable.spans)
    if found != set(_WRAPPED_CAT_IMMUTABLES):
        return None
    return mask_spans, values, inherited


def _uniform_span_bytes(code: bytes, spans: Sequence[Tuple[int, int]]) -> Optional[bytes]:
    if not spans:
        return None
    first: Optional[bytes] = None
    for start, length in spans:
        end = start + length
        if start < 0 or length < 0 or end > len(code):
            return None
        chunk = code[start:end]
        if first is None:
            first = chunk
        elif chunk != first:
            return None
    return first


def _portal_word(portal: bytes) -> bytes:
    if len(portal) == 20:
        address = portal
    else:
        address = evm_address_from_bytes32(portal)
    if len(address) != 20:
        raise ValueError("portal")
    return b"\x00" * 12 + address


def _tip_word(tip: int) -> bytes:
    if isinstance(tip, bool) or not isinstance(tip, int) or tip < 1 or tip > 1000:
        raise ValueError("tip")
    return tip.to_bytes(_IMMUTABLE_WORD, "big")


def _ratio_word(mojo_to_token_ratio: int) -> bytes:
    if (
        isinstance(mojo_to_token_ratio, bool)
        or not isinstance(mojo_to_token_ratio, int)
        or mojo_to_token_ratio < 0
        or mojo_to_token_ratio >= 2**64
    ):
        raise ValueError("mojoToTokenRatio")
    return mojo_to_token_ratio.to_bytes(_IMMUTABLE_WORD, "big")


def _other_chain_word(other_chain: bytes) -> bytes:
    if bytes(other_chain) != b"xch":
        raise ValueError("otherChain")
    # bytes3 is left-aligned in the PUSH32 word.
    return b"xch" + b"\x00" * (_IMMUTABLE_WORD - 3)


def _wrapped_cat_value_words(
    *,
    portal: bytes,
    other_chain: bytes,
    tip: int,
    mojo_to_token_ratio: int,
) -> Dict[str, bytes]:
    return {
        "portal": _portal_word(portal),
        "otherChain": _other_chain_word(other_chain),
        "tip": _tip_word(tip),
        "mojoToTokenRatio": _ratio_word(mojo_to_token_ratio),
    }


def stamp_wrapped_cat_immutables(
    runtime: bytes,
    immutables: Sequence[WrappedCatImmutable],
    *,
    portal: bytes,
    other_chain: bytes,
    tip: int,
    mojo_to_token_ratio: int,
) -> bytes:
    """Write portal, xch, tip, and ratio into every span of those ids."""
    words = _wrapped_cat_value_words(
        portal=portal,
        other_chain=other_chain,
        tip=tip,
        mojo_to_token_ratio=mojo_to_token_ratio,
    )
    buf = bytearray(runtime)
    for immutable in immutables:
        word = words.get(immutable.name)
        if word is None or immutable.contract != "WrappedCAT":
            continue
        for start, length in immutable.spans:
            if length != len(word):
                raise ValueError("unexpected immutable width")
            end = start + length
            if start < 0 or end > len(buf):
                raise ValueError("immutable span out of range")
            buf[start:end] = word
    return bytes(buf)


def match_wrapped_cat_bytecode(runtime_code: bytes) -> bool:
    """Masked structural compare. Does not accept immutable values.

    Unknown names and unexpected widths are not masked; they fail the compare.
    """
    artifact = load_wrapped_cat_build_info()
    classified = _classify_wrapped_cat_immutables(artifact)
    if classified is None or len(runtime_code) != len(artifact.bytecode):
        return False
    mask_spans, _, _ = classified
    return mask_immutable_bytecode(runtime_code, mask_spans) == mask_immutable_bytecode(
        artifact.bytecode, mask_spans
    )


def wrapped_cat_immutable_values_match(
    runtime_code: bytes,
    *,
    portal: bytes,
    other_chain: bytes,
    tip: int,
    mojo_to_token_ratio: int,
) -> bool:
    """Require each AST id to be one value, and WrappedCAT's four to match.

    ``tip`` and ``mojo_to_token_ratio`` are the integers fee math uses. Inherited
    immutables must be uniform across their spans but are not pinned: EIP-712
    caches depend on the token name, chain id, and deploy address.
    """
    artifact = load_wrapped_cat_build_info()
    classified = _classify_wrapped_cat_immutables(artifact)
    if classified is None or len(runtime_code) != len(artifact.bytecode):
        return False
    _, values, inherited = classified
    try:
        expected = _wrapped_cat_value_words(
            portal=portal,
            other_chain=other_chain,
            tip=tip,
            mojo_to_token_ratio=mojo_to_token_ratio,
        )
    except ValueError:
        return False
    seen = set()
    for immutable in values:
        word = _uniform_span_bytes(runtime_code, immutable.spans)
        if word is None or word != expected[immutable.name]:
            return False
        seen.add(immutable.name)
    if seen != set(expected):
        return False
    for immutable in inherited:
        if _uniform_span_bytes(runtime_code, immutable.spans) is None:
            return False
    return True


def compute_bridge_tip(amount: int, tip_bps: int) -> int:
    if tip_bps < 1 or tip_bps > 1000:
        raise ValueError("tip_bps out of range")
    tip = (amount * tip_bps) // 10000
    if tip == 0:
        tip = 1
    return tip


def validate_chia_erc20_unwrap(
    bridging_coin: Coin,
    burner_spend: CoinSpend,
    cat_spend: Optional[CoinSpend],
    routes: BridgeRoutes,
) -> PolicyResult:
    if cat_spend is None:
        return PolicyResult("reject", "missing CAT spend")

    try:
        burner_puzzle = _as_program(burner_spend.puzzle_reveal)
        burner_solution = _as_program(burner_spend.solution)
        mod, curried = burner_puzzle.uncurry()
        if mod != CAT_BURNER_MOD:
            return PolicyResult("reject", "parent is not CAT_BURNER_MOD")
        curry_args = _program_list(curried)
        if len(curry_args) != 5:
            return PolicyResult("reject", "burner curry arity")
        cat_mod_hash, burn_inner_mod_hash, bridging_ph, dest_chain, dest_addr = (
            _as_atom(curry_args[0]),
            _as_atom(curry_args[1]),
            _as_atom(curry_args[2]),
            _as_atom(curry_args[3]),
            _as_atom(curry_args[4]),
        )
        if cat_mod_hash != CAT_MOD_HASH:
            return PolicyResult("reject", "burner CAT_MOD_HASH mismatch")
        if burn_inner_mod_hash != BURN_INNER_PUZZLE_MOD_HASH:
            return PolicyResult("reject", "burner BURN_INNER_PUZZLE_MOD_HASH mismatch")
        if bridging_ph != BRIDGING_PUZZLE_HASH:
            return PolicyResult("reject", "burner BRIDGING_PUZZLE_HASH mismatch")

        route = _route_for_burner_destination(routes, dest_chain, dest_addr)
        if route is None:
            return PolicyResult("reject", "burner destination is not a configured route")

        expected_burner = get_cat_burner_puzzle(dest_chain, dest_addr)
        if burner_puzzle.get_tree_hash() != expected_burner.get_tree_hash():
            return PolicyResult("reject", "burner puzzle hash mismatch")
        if burner_spend.coin.puzzle_hash != burner_puzzle.get_tree_hash():
            return PolicyResult("reject", "burner coin puzzle hash mismatch")

        sol_args = _program_list(burner_solution)
        if len(sol_args) != 8:
            return PolicyResult("reject", "burner solution arity")
        cat_parent_info = _as_atom(sol_args[0])
        tail_hash_hash = _as_atom(sol_args[1])
        cat_amount = sol_args[2].as_int()
        memo_asset = _as_atom(sol_args[3])
        memo_receiver = _as_atom(sol_args[4])
        my_amount = sol_args[5].as_int()
        my_puzzle_hash = _as_atom(sol_args[6])
        my_coin_id = _as_atom(sol_args[7])

        if my_amount != burner_spend.coin.amount:
            return PolicyResult("reject", "burner amount mismatch")
        if my_puzzle_hash != burner_spend.coin.puzzle_hash:
            return PolicyResult("reject", "burner solution puzzle hash mismatch")
        if my_coin_id != burner_spend.coin.name():
            return PolicyResult("reject", "burner solution coin id mismatch")

        memo = _extract_bridging_memo(burner_puzzle, burner_solution, burner_spend.coin.amount)
        if memo is None:
            return PolicyResult("reject", "burner missing bridging CREATE_COIN memo")

        memo_chain, memo_dest, memo_contents = memo
        if memo_chain != dest_chain or left_pad_32(memo_dest) != left_pad_32(dest_addr):
            return PolicyResult("reject", "memo destination does not match burner curry")
        if evm_address_from_bytes32(left_pad_32(memo_dest)) != evm_address_from_bytes32(
            left_pad_32(route.erc20_bridge_address)
        ):
            return PolicyResult("reject", "memo destination is not the route bridge")
        if len(memo_contents) != 3:
            return PolicyResult("reject", "unwrap memo must be exactly 3 words")
        if left_pad_32(memo_contents[0]) != left_pad_32(memo_asset):
            return PolicyResult("reject", "memo asset mismatch")
        if left_pad_32(memo_contents[1]) != left_pad_32(memo_receiver):
            return PolicyResult("reject", "memo receiver mismatch")
        if _as_int_word(memo_contents[2]) != cat_amount:
            return PolicyResult("reject", "memo amount mismatch")

        if bridging_coin.parent_coin_info != burner_spend.coin.name():
            return PolicyResult("reject", "bridging coin parent is not burner")
        if bridging_coin.puzzle_hash != BRIDGING_PUZZLE_HASH:
            return PolicyResult("reject", "bridging coin puzzle hash")
        if bridging_coin.amount != burner_spend.coin.amount:
            return PolicyResult("reject", "bridging coin amount")

        asset = left_pad_32(memo_asset)
        wrapped_tail = get_wrapped_tail(
            bytes32(routes.portal_launcher_id),
            dest_chain,
            dest_addr,
            asset,
        )
        expected_tail_hash_hash = raw_hash([b"\x01", wrapped_tail.get_tree_hash()])
        if tail_hash_hash != expected_tail_hash_hash:
            return PolicyResult("reject", "tail_hash_hash mismatch")

        expected_inner = get_cat_burn_inner_puzzle(
            dest_chain,
            dest_addr,
            asset,
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
        if cat_spend.coin != expected_cat_coin:
            return PolicyResult("reject", "CAT coin does not match burner reconstruction")

        cat_puzzle = _as_program(cat_spend.puzzle_reveal)
        cat_solution = _as_program(cat_spend.solution)
        cat_mod, cat_args_p = cat_puzzle.uncurry()
        if cat_mod != CAT_MOD:
            return PolicyResult("reject", "CAT spend is not CAT_MOD")
        cat_args = _program_list(cat_args_p)
        if len(cat_args) != 3:
            return PolicyResult("reject", "CAT curry arity")
        if _as_atom(cat_args[0]) != CAT_MOD_HASH:
            return PolicyResult("reject", "CAT inner CAT_MOD_HASH mismatch")
        if _as_atom(cat_args[1]) != wrapped_tail.get_tree_hash():
            return PolicyResult("reject", "CAT tail hash mismatch")
        inner_puzzle = cat_args[2]
        if inner_puzzle.get_tree_hash() != expected_inner.get_tree_hash():
            return PolicyResult("reject", "CAT burn-inner mismatch")

        inner_mod_1, inner_args_1_p = inner_puzzle.uncurry()
        inner_args_1 = _program_list(inner_args_1_p)
        if len(inner_args_1) != 2:
            return PolicyResult("reject", "burn-inner second curry arity")
        if left_pad_32(_as_atom(inner_args_1[0])) != left_pad_32(memo_receiver):
            return PolicyResult("reject", "burn-inner receiver mismatch")
        if inner_args_1[1].as_int() != burner_spend.coin.amount:
            return PolicyResult("reject", "burn-inner fee mismatch")
        inner_mod_0, inner_args_0_p = inner_mod_1.uncurry()
        if inner_mod_0 != BURN_INNER_PUZZLE_MOD:
            return PolicyResult("reject", "inner is not BURN_INNER_PUZZLE_MOD")
        inner_args_0 = _program_list(inner_args_0_p)
        if len(inner_args_0) != 2:
            return PolicyResult("reject", "burn-inner first curry arity")
        if _as_atom(inner_args_0[0]) != burner_spend.coin.puzzle_hash:
            return PolicyResult("reject", "burn-inner burner puzzle hash mismatch")
        if left_pad_32(_as_atom(inner_args_0[1])) != asset:
            return PolicyResult("reject", "burn-inner token mismatch")

        cat_sol_args = _program_list(cat_solution)
        if len(cat_sol_args) < 7:
            return PolicyResult("reject", "CAT solution arity")
        inner_solution = cat_sol_args[0]
        extra_delta = cat_sol_args[6].as_int()
        inner_sol_args = _program_list(inner_solution)
        if len(inner_sol_args) != 3:
            return PolicyResult("reject", "burn-inner solution arity")
        tail_reveal = inner_sol_args[2]
        if bytes(tail_reveal) != bytes(wrapped_tail):
            return PolicyResult("reject", "tail reveal is not reconstructed wrapped_tail")

        if cat_spend.coin.amount != cat_amount:
            return PolicyResult("reject", "CAT amount does not match message amount")
        if extra_delta != -cat_amount:
            return PolicyResult("reject", "CAT extra_delta is not full melt")

        try:
            _, inner_conds = inner_puzzle.run_with_cost(INFINITE_COST, inner_solution)
        except Exception:
            return PolicyResult("reject", "burn-inner spend failed")
        if not _is_full_melt(inner_conds):
            return PolicyResult("reject", "CAT spend is not a full melt")

        try:
            _, burner_conds = burner_puzzle.run_with_cost(INFINITE_COST, burner_solution)
        except Exception:
            return PolicyResult("reject", "burner spend failed")
        if not _announcements_tie(burner_conds, inner_conds, burner_spend.coin.name(), cat_spend.coin.name()):
            return PolicyResult("reject", "burner/CAT announcements do not tie")

        verified = _verified_chia_message(
            source=bytes(burner_spend.coin.puzzle_hash),
            destination_chain=memo_chain,
            destination=memo_dest,
            contents=memo_contents,
        )
        if verified is None:
            return PolicyResult("reject", "memo content longer than 32 bytes")
        return PolicyResult("accept", "chia erc20 unwrap", verified)
    except Exception as exc:
        return PolicyResult("reject", f"chia erc20 unwrap parse error: {exc}")


def validate_chia_cat_wrap(
    bridging_coin: Coin,
    locker_spend: CoinSpend,
    other_spends: Sequence[CoinSpend],
    routes: BridgeRoutes,
    *,
    wrapped_cat_runtime_code: Optional[bytes],
    view_portal: Optional[bytes],
    view_other_chain: Optional[bytes],
    view_locker_puzzle_hash: Optional[bytes],
    view_tip: Optional[int] = None,
    view_mojo_to_token_ratio: Optional[int] = None,
) -> PolicyResult:
    try:
        locker_puzzle = _as_program(locker_spend.puzzle_reveal)
        locker_solution = _as_program(locker_spend.solution)
        mod, curried = locker_puzzle.uncurry()
        if mod != LOCKER_MOD:
            return PolicyResult("reject", "parent is not LOCKER_MOD")
        curry_args = _program_list(curried)
        if len(curry_args) != 7:
            return PolicyResult("reject", "locker curry arity")

        dest_chain = _as_atom(curry_args[0])
        dest_addr = _as_atom(curry_args[1])
        cat_mod_hash = _as_atom(curry_args[2])
        offer_mod_hash = _as_atom(curry_args[3])
        bridging_ph = _as_atom(curry_args[4])
        vault_inner_ph = _as_atom(curry_args[5])
        asset_id_atom = _as_atom(curry_args[6])

        if cat_mod_hash != CAT_MOD_HASH:
            return PolicyResult("reject", "locker CAT_MOD_HASH mismatch")
        if offer_mod_hash != OFFER_MOD_HASH:
            return PolicyResult("reject", "locker OFFER_MOD_HASH mismatch")
        if bridging_ph != BRIDGING_PUZZLE_HASH:
            return PolicyResult("reject", "locker BRIDGING_PUZZLE_HASH mismatch")

        route = routes.for_chain(dest_chain)
        if route is None:
            return PolicyResult("reject", "locker destination chain is not configured")

        asset_id: Optional[bytes32]
        if asset_id_atom == b"":
            asset_id = None
        else:
            if len(asset_id_atom) != 32:
                return PolicyResult("reject", "locker asset_id length")
            asset_id = bytes32(asset_id_atom)

        expected_locker = get_locker_puzzle(
            dest_chain,
            dest_addr,
            bytes32(routes.portal_launcher_id),
            asset_id,
        )
        if locker_puzzle.get_tree_hash() != expected_locker.get_tree_hash():
            return PolicyResult("reject", "locker puzzle hash mismatch")
        if locker_spend.coin.puzzle_hash != locker_puzzle.get_tree_hash():
            return PolicyResult("reject", "locker coin puzzle hash mismatch")
        if vault_inner_ph != _expected_vault_inner_ph(
            dest_chain, dest_addr, routes.portal_launcher_id, asset_id
        ):
            return PolicyResult("reject", "locker vault inner mismatch")

        sol_args = _program_list(locker_solution)
        if len(sol_args) != 4:
            return PolicyResult("reject", "locker solution arity")
        my_amount = sol_args[0].as_int()
        my_id = _as_atom(sol_args[1])
        asset_amount = sol_args[2].as_int()
        receiver = _as_atom(sol_args[3])

        if my_amount != locker_spend.coin.amount:
            return PolicyResult("reject", "locker amount mismatch")
        if my_id != locker_spend.coin.name():
            return PolicyResult("reject", "locker coin id mismatch")

        memo = _extract_bridging_memo(locker_puzzle, locker_solution, locker_spend.coin.amount)
        if memo is None:
            return PolicyResult("reject", "locker missing bridging CREATE_COIN memo")
        memo_chain, memo_dest, memo_contents = memo
        if memo_chain != dest_chain or left_pad_32(memo_dest) != left_pad_32(dest_addr):
            return PolicyResult("reject", "memo destination does not match locker curry")
        if len(memo_contents) != 2:
            return PolicyResult("reject", "wrap memo must be exactly (receiver, asset_amount)")
        if left_pad_32(memo_contents[0]) != left_pad_32(receiver):
            return PolicyResult("reject", "memo receiver mismatch")
        if _as_int_word(memo_contents[1]) != asset_amount:
            return PolicyResult("reject", "memo asset_amount mismatch")

        if bridging_coin.parent_coin_info != locker_spend.coin.name():
            return PolicyResult("reject", "bridging coin parent is not locker")
        if bridging_coin.puzzle_hash != BRIDGING_PUZZLE_HASH:
            return PolicyResult("reject", "bridging coin puzzle hash")
        if bridging_coin.amount != locker_spend.coin.amount:
            return PolicyResult("reject", "bridging coin amount")

        if wrapped_cat_runtime_code is None:
            return PolicyResult("retry", "missing WrappedCAT bytecode or views")

        try:
            if not match_wrapped_cat_bytecode(wrapped_cat_runtime_code):
                return PolicyResult("reject", "WrappedCAT bytecode mismatch after immutable mask")
        except Exception:
            return PolicyResult("retry", "could not load WrappedCAT artifact for mask compare")

        if view_tip is None or view_mojo_to_token_ratio is None:
            return PolicyResult("retry", "missing WrappedCAT bytecode or views")
        try:
            if not wrapped_cat_immutable_values_match(
                wrapped_cat_runtime_code,
                portal=route.portal_address,
                other_chain=b"xch",
                tip=view_tip,
                mojo_to_token_ratio=view_mojo_to_token_ratio,
            ):
                return PolicyResult("reject", "WrappedCAT immutable occurrence mismatch")
        except Exception:
            return PolicyResult("retry", "could not load WrappedCAT artifact for mask compare")

        if (
            view_portal is None
            or view_other_chain is None
            or view_locker_puzzle_hash is None
        ):
            return PolicyResult("retry", "missing WrappedCAT bytecode or views")

        if evm_address_from_bytes32(left_pad_32(view_portal)) != evm_address_from_bytes32(
            left_pad_32(route.portal_address)
        ):
            return PolicyResult("reject", "WrappedCAT portal() does not match route portal")
        if view_other_chain != b"xch":
            return PolicyResult("reject", "WrappedCAT otherChain() is not xch")
        if view_locker_puzzle_hash != locker_spend.coin.puzzle_hash:
            return PolicyResult("reject", "WrappedCAT lockerPuzzleHash() mismatch")

        vault_ph = _vault_puzzle_hash(asset_id, vault_inner_ph)
        settlement = _find_settlement_spend(
            other_spends,
            asset_id,
            locker_spend.coin.name(),
            vault_inner_ph,
            vault_ph,
            asset_amount,
        )
        if settlement is None:
            return PolicyResult("reject", "settlement offer spend not found")

        verified = _verified_chia_message(
            source=bytes(locker_spend.coin.puzzle_hash),
            destination_chain=memo_chain,
            destination=memo_dest,
            contents=memo_contents,
        )
        if verified is None:
            return PolicyResult("reject", "memo content longer than 32 bytes")
        return PolicyResult("accept", "chia cat/xch wrap", verified)
    except Exception as exc:
        return PolicyResult("reject", f"chia cat wrap parse error: {exc}")


def validate_evm_erc20_wrap(
    *,
    routes: BridgeRoutes,
    source_chain: bytes,
    source: bytes,
    destination: bytes,
    destination_chain: bytes,
    contents: Sequence[bytes],
    tx_status: Optional[int],
    tip_bps: Optional[int],
    decimals: Optional[int],
    gross_mojo_amount: Optional[int],
    bridge_token_balance_diff: Optional[int],
    balance_diff_asset: Optional[bytes],
    is_bridge_ether: bool = False,
    msg_value: Optional[int] = None,
    message_toll: Optional[int] = None,
    weth_to_eth_ratio: Optional[int] = None,
) -> PolicyResult:
    route = routes.for_chain(source_chain)
    if route is None:
        return PolicyResult("reject", "unknown source chain")
    if destination_chain != b"xch":
        return PolicyResult("reject", "destination_chain is not xch")
    try:
        if evm_address_from_bytes32(source) != evm_address_from_bytes32(
            left_pad_32(route.erc20_bridge_address)
        ):
            return PolicyResult("reject", "source is not pinned erc20 bridge")
    except ValueError:
        return PolicyResult("reject", "source is not pinned erc20 bridge")

    expected_dest = get_cat_minter_puzzle(
        bytes32(routes.portal_launcher_id),
        source_chain,
        evm_address_from_bytes32(route.erc20_bridge_address),
    ).get_tree_hash()
    if destination != expected_dest:
        return PolicyResult("reject", "destination is not cat-minter puzzle hash")

    if len(contents) != 3:
        return PolicyResult("reject", "erc20 wrap contents must be 3 words")

    if tx_status is None:
        return PolicyResult("retry", "missing tx status")
    if tx_status != 1:
        return PolicyResult("reject", "tx status not success")

    if tip_bps is None or decimals is None or gross_mojo_amount is None:
        return PolicyResult("retry", "missing tip/decimals/gross amount")
    if tip_bps < 1 or tip_bps > 1000:
        return PolicyResult("reject", "tip_bps out of range")
    if decimals < 3:
        return PolicyResult("reject", "decimals < 3")
    if gross_mojo_amount <= 0:
        return PolicyResult("reject", "gross amount must be positive")

    tip = compute_bridge_tip(gross_mojo_amount, tip_bps)
    if gross_mojo_amount <= tip:
        return PolicyResult("reject", "amount <= tip")
    net = gross_mojo_amount - tip
    if _as_int_word(contents[2]) != net:
        return PolicyResult("reject", "message amount != net")

    factor = 10 ** (decimals - 3)
    asset = left_pad_32(contents[0])

    if bridge_token_balance_diff is None:
        return PolicyResult("retry", "missing bridge balance diff")
    if balance_diff_asset is None:
        return PolicyResult("retry", "missing balance diff asset")
    if evm_address_from_bytes32(balance_diff_asset) != evm_address_from_bytes32(contents[0]):
        return PolicyResult("reject", "balance diff asset mismatch")

    if is_bridge_ether:
        if msg_value is None or message_toll is None or weth_to_eth_ratio is None:
            return PolicyResult("retry", "missing ether-bridge inputs")
        if msg_value < message_toll:
            return PolicyResult("reject", "msg.value < message toll")
        amount_after_toll = msg_value - message_toll
        if weth_to_eth_ratio <= 0 or amount_after_toll % weth_to_eth_ratio != 0:
            return PolicyResult("reject", "amountAfterToll not divisible by wethToEthRatio")
        mojos = amount_after_toll // weth_to_eth_ratio // factor
        if mojos != gross_mojo_amount:
            return PolicyResult("reject", "ether bridge mojo amount mismatch")
        if evm_address_from_bytes32(asset) != evm_address_from_bytes32(
            left_pad_32(route.millieth_address)
        ):
            return PolicyResult("reject", "ether bridge asset is not milliETH/WETH")
        expected_diff = (gross_mojo_amount - tip) * factor
        if bridge_token_balance_diff != expected_diff:
            return PolicyResult("reject", "milliETH bridge balance diff mismatch")
    else:
        expected_diff = (gross_mojo_amount - tip) * factor
        if bridge_token_balance_diff != expected_diff:
            return PolicyResult("reject", "bridge token balance diff mismatch")

    return PolicyResult("accept", "evm erc20 wrap")


def validate_evm_cat_unwrap(
    *,
    routes: BridgeRoutes,
    source_chain: bytes,
    source: bytes,
    destination: bytes,
    destination_chain: bytes,
    contents: Sequence[bytes],
    tx_status: Optional[int],
    tip_bps: Optional[int],
    mojo_to_token_ratio: Optional[int],
    gross_mojo_amount: Optional[int],
    sender_balance_diff: Optional[int],
    portal_balance_diff: Optional[int],
    view_portal: Optional[bytes],
    view_other_chain: Optional[bytes],
    unlocker_puzzle_hash: Optional[bytes],
    wrapped_cat_runtime_code: Optional[bytes] = None,
    asset_id: Optional[bytes] = None,
    asset_id_known: bool = False,
) -> PolicyResult:
    route = routes.for_chain(source_chain)
    if route is None:
        return PolicyResult("reject", "unknown source chain")
    if destination_chain != b"xch":
        return PolicyResult("reject", "destination_chain is not xch")

    if tx_status is None:
        return PolicyResult("retry", "missing tx status")
    if tx_status != 1:
        return PolicyResult("reject", "tx status not success")

    if wrapped_cat_runtime_code is None:
        return PolicyResult("retry", "missing WrappedCAT runtime code")
    try:
        if not match_wrapped_cat_bytecode(wrapped_cat_runtime_code):
            return PolicyResult("reject", "WrappedCAT bytecode mismatch after immutable mask")
    except Exception:
        return PolicyResult("retry", "could not load WrappedCAT artifact for mask compare")

    if tip_bps is not None and mojo_to_token_ratio is not None:
        if isinstance(tip_bps, bool) or not isinstance(tip_bps, int) or tip_bps < 1 or tip_bps > 1000:
            return PolicyResult("reject", "tip_bps out of range")
        if (
            isinstance(mojo_to_token_ratio, bool)
            or not isinstance(mojo_to_token_ratio, int)
            or mojo_to_token_ratio <= 0
        ):
            return PolicyResult("reject", "invalid ratio or gross amount")
        try:
            if not wrapped_cat_immutable_values_match(
                wrapped_cat_runtime_code,
                portal=route.portal_address,
                other_chain=b"xch",
                tip=tip_bps,
                mojo_to_token_ratio=mojo_to_token_ratio,
            ):
                return PolicyResult("reject", "WrappedCAT immutable occurrence mismatch")
        except Exception:
            return PolicyResult("retry", "could not load WrappedCAT artifact for mask compare")

    if view_portal is None or view_other_chain is None or unlocker_puzzle_hash is None:
        return PolicyResult("retry", "missing WrappedCAT views")
    if evm_address_from_bytes32(left_pad_32(view_portal)) != evm_address_from_bytes32(
        left_pad_32(route.portal_address)
    ):
        return PolicyResult("reject", "WrappedCAT portal() does not match route portal")
    if view_other_chain != b"xch":
        return PolicyResult("reject", "WrappedCAT otherChain() is not xch")
    if destination != unlocker_puzzle_hash:
        return PolicyResult("reject", "destination != unlockerPuzzleHash()")

    if asset_id_known:
        driver_asset_id: Optional[bytes32]
        if asset_id is None or asset_id == b"":
            driver_asset_id = None
        else:
            driver_asset_id = bytes32(left_pad_32(asset_id))
        expected_unlocker = get_unlocker_puzzle(
            source_chain,
            evm_address_from_bytes32(left_pad_32(source)),
            bytes32(routes.portal_launcher_id),
            driver_asset_id,
        ).get_tree_hash()
        if expected_unlocker != unlocker_puzzle_hash:
            return PolicyResult("reject", "unlockerPuzzleHash inconsistent with drivers")

    if len(contents) != 2:
        return PolicyResult("reject", "cat unwrap contents must be (receiver, net)")

    if tip_bps is None or mojo_to_token_ratio is None or gross_mojo_amount is None:
        return PolicyResult("retry", "missing tip/ratio/gross amount")
    if tip_bps < 1 or tip_bps > 1000:
        return PolicyResult("reject", "tip_bps out of range")
    if mojo_to_token_ratio <= 0 or gross_mojo_amount <= 0:
        return PolicyResult("reject", "invalid ratio or gross amount")

    tip = compute_bridge_tip(gross_mojo_amount, tip_bps)
    if gross_mojo_amount <= tip:
        return PolicyResult("reject", "amount <= tip")
    net = gross_mojo_amount - tip
    if _as_int_word(contents[1]) != net:
        return PolicyResult("reject", "message net amount mismatch")

    if sender_balance_diff is None or portal_balance_diff is None:
        return PolicyResult("retry", "missing balance diffs")

    expected_sender_diff = -(gross_mojo_amount * mojo_to_token_ratio)
    expected_portal_diff = tip * mojo_to_token_ratio
    if sender_balance_diff != expected_sender_diff:
        return PolicyResult("reject", "sender balance diff mismatch")
    if portal_balance_diff != expected_portal_diff:
        return PolicyResult("reject", "portal balance diff mismatch")

    return PolicyResult("accept", "evm cat unwrap")


def _parse_evm_route(config: dict, chain_id: bytes) -> EvmRoute:
    key = chain_id.decode()
    chain_cfg = config[key]
    millieth = chain_cfg.get("millieth_address", chain_cfg.get("weth_address"))
    if millieth is None:
        raise KeyError(f"{key}.millieth_address")
    return EvmRoute(
        chain_id=chain_id,
        portal_address=_parse_hex_bytes(chain_cfg["portal_address"]),
        erc20_bridge_address=_parse_hex_bytes(chain_cfg["erc20_bridge_address"]),
        millieth_address=_parse_hex_bytes(millieth),
        portal_impl_codehash=str(chain_cfg["portal_impl_codehash"]),
    )


def _parse_hex_bytes(value: Union[str, bytes]) -> bytes:
    if isinstance(value, bytes):
        return value
    return bytes.fromhex(value.replace("0x", "").replace("0X", ""))


def _norm_hex(value: str) -> str:
    return value.lower().replace("0x", "")


def _as_program(value) -> Program:
    if isinstance(value, Program):
        return value
    return Program.from_bytes(bytes(value))


def _program_list(program: Program) -> List[Program]:
    return list(program.as_iter())


def _as_atom(program: Program) -> bytes:
    atom = program.as_atom()
    if atom is None:
        raise ValueError("expected atom")
    return atom


def _as_int_word(value: bytes) -> int:
    return int.from_bytes(left_pad_32(value), "big")


def _route_for_burner_destination(
    routes: BridgeRoutes, dest_chain: bytes, dest_addr: bytes
) -> Optional[EvmRoute]:
    route = routes.for_chain(dest_chain)
    if route is None:
        return None
    try:
        if evm_address_from_bytes32(left_pad_32(dest_addr)) != evm_address_from_bytes32(
            left_pad_32(route.erc20_bridge_address)
        ):
            return None
    except ValueError:
        return None
    return route


def _extract_bridging_memo(
    puzzle: Program, solution: Program, expected_amount: int
) -> Optional[Tuple[bytes, bytes, List[bytes]]]:
    try:
        _, conditions = puzzle.run_with_cost(INFINITE_COST, solution)
    except Exception:
        return None
    create_coin = bytes(ConditionOpcode.CREATE_COIN)
    found: Optional[Tuple[bytes, bytes, Tuple[bytes, ...]]] = None
    for condition in conditions.as_iter():
        items = _program_list(condition)
        if not items or _as_atom(items[0]) != create_coin:
            continue
        if len(items) < 4:
            continue
        created_ph = _as_atom(items[1])
        if created_ph != BRIDGING_PUZZLE_HASH:
            continue
        if items[2].as_int() != expected_amount:
            continue
        memo = items[3]
        memo_items = _program_list(memo)
        if len(memo_items) < 2:
            return None
        chain = _as_atom(memo_items[0])
        dest = _as_atom(memo_items[1])
        contents = tuple(_as_atom(x) for x in memo_items[2:])
        candidate = (chain, dest, contents)
        if found is not None and found != candidate:
            return None
        found = candidate
    if found is None:
        return None
    chain, dest, contents = found
    return chain, dest, list(contents)


def _is_full_melt(conditions: Program) -> bool:
    saw_melt = False
    create_coin = bytes(ConditionOpcode.CREATE_COIN)
    for condition in conditions.as_iter():
        items = _program_list(condition)
        if not items or _as_atom(items[0]) != create_coin:
            continue
        if len(items) < 3:
            return False
        amount = items[2].as_int()
        if amount == -113:
            saw_melt = True
            continue
        if amount > 0:
            return False
    return saw_melt


def _announcements_tie(
    burner_conds: Program,
    cat_inner_conds: Program,
    burner_id: bytes,
    cat_id: bytes,
) -> bool:
    return (
        _has_announcement(burner_conds, ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, cat_id)
        and _has_announcement(
            burner_conds, ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT, raw_hash([cat_id, burner_id])
        )
        and _has_announcement(cat_inner_conds, ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, burner_id)
        and _has_announcement(
            cat_inner_conds, ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT, raw_hash([burner_id, cat_id])
        )
    )


def _has_announcement(conditions: Program, opcode: ConditionOpcode, payload: bytes) -> bool:
    op = bytes(opcode)
    for condition in conditions.as_iter():
        items = _program_list(condition)
        if not items or _as_atom(items[0]) != op:
            continue
        if len(items) < 2:
            continue
        if _as_atom(items[1]) == payload:
            return True
    return False


def _expected_vault_inner_ph(
    dest_chain: bytes,
    dest_addr: bytes,
    portal_launcher_id: bytes,
    asset_id: Optional[bytes32],
) -> bytes:
    unlocker = get_unlocker_puzzle(
        dest_chain,
        dest_addr,
        bytes32(portal_launcher_id),
        asset_id,
    )
    return get_p2_controller_puzzle_hash_inner_puzzle_hash(unlocker.get_tree_hash()).get_tree_hash()


_QUOTED_CAT_MOD_HASH = calculate_hash_of_quoted_mod_hash(CAT_MOD_HASH)


def _vault_puzzle_hash(asset_id: Optional[bytes32], vault_inner_ph: bytes) -> bytes:
    if asset_id is None:
        return vault_inner_ph
    return curry_and_treehash(
        _QUOTED_CAT_MOD_HASH,
        CAT_MOD_HASH_HASH,
        Program.to(asset_id).get_tree_hash(),
        bytes32(vault_inner_ph),
    )


def _find_settlement_spend(
    spends: Sequence[CoinSpend],
    asset_id: Optional[bytes32],
    locker_coin_id: bytes,
    vault_inner_ph: bytes,
    vault_ph: bytes,
    asset_amount: int,
) -> Optional[CoinSpend]:
    for spend in spends:
        try:
            puzzle = _as_program(spend.puzzle_reveal)
            if spend.coin.puzzle_hash != puzzle.get_tree_hash():
                continue
            if asset_id is None:
                if not (puzzle == OFFER_MOD or puzzle.get_tree_hash() == OFFER_MOD_HASH):
                    continue
            else:
                mod, args_p = puzzle.uncurry()
                if mod != CAT_MOD:
                    continue
                args = _program_list(args_p)
                if len(args) != 3:
                    continue
                if _as_atom(args[0]) != CAT_MOD_HASH:
                    continue
                if _as_atom(args[1]) != asset_id:
                    continue
                inner = args[2]
                if not (inner == OFFER_MOD or inner.get_tree_hash() == OFFER_MOD_HASH):
                    continue
            if not _settlement_has_notarized_payment(
                spend, locker_coin_id, vault_inner_ph, asset_amount
            ):
                continue
            if not _settlement_creates_vault(
                spend, asset_id, vault_inner_ph, vault_ph, asset_amount
            ):
                continue
            return spend
        except Exception:
            continue
    return None


def _settlement_has_notarized_payment(
    settlement: CoinSpend,
    locker_coin_id: bytes,
    vault_inner_ph: bytes,
    asset_amount: int,
) -> bool:
    solution = _as_program(settlement.solution)
    # CAT settlement: solution[0] is inner OFFER solution; plain OFFER: whole solution
    puzzle = _as_program(settlement.puzzle_reveal)
    mod, _ = puzzle.uncurry()
    offer_solution = solution
    if mod == CAT_MOD:
        sol_args = _program_list(solution)
        if not sol_args:
            return False
        offer_solution = sol_args[0]
    for payment in _program_list(offer_solution):
        parts = _program_list(payment)
        if len(parts) < 2:
            continue
        nonce = _as_atom(parts[0])
        if nonce != locker_coin_id:
            continue
        for notarized in parts[1:]:
            np = _program_list(notarized)
            if len(np) < 2:
                continue
            if _as_atom(np[0]) == vault_inner_ph and np[1].as_int() == asset_amount:
                return True
    return False


def _settlement_creates_vault(
    settlement: CoinSpend,
    asset_id: Optional[bytes32],
    vault_inner_ph: bytes,
    vault_ph: bytes,
    asset_amount: int,
) -> bool:
    puzzle = _as_program(settlement.puzzle_reveal)
    solution = _as_program(settlement.solution)
    create_coin = bytes(ConditionOpcode.CREATE_COIN)

    if asset_id is None:
        try:
            _, conditions = puzzle.run_with_cost(INFINITE_COST, solution)
        except Exception:
            return False
        for condition in conditions.as_iter():
            items = _program_list(condition)
            if not items or _as_atom(items[0]) != create_coin:
                continue
            if len(items) < 3:
                continue
            if _as_atom(items[1]) == vault_ph and items[2].as_int() == asset_amount:
                return True
        return False

    # CAT(asset_id, OFFER_MOD): OFFER creates the inner vault ph; CAT wraps it.
    sol_args = _program_list(solution)
    if not sol_args:
        return False
    try:
        _, conditions = OFFER_MOD.run_with_cost(INFINITE_COST, sol_args[0])
    except Exception:
        return False
    for condition in conditions.as_iter():
        items = _program_list(condition)
        if not items or _as_atom(items[0]) != create_coin:
            continue
        if len(items) < 3:
            continue
        if _as_atom(items[1]) == vault_inner_ph and items[2].as_int() == asset_amount:
            return True
    return False

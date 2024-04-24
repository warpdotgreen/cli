from drivers.utils import load_clvm_hex, raw_hash
from drivers.portal import get_message_coin_puzzle_1st_curry, BRIDGING_PUZZLE_HASH
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH
from chia.wallet.trading.offer import OFFER_MOD_HASH
from typing import List, Tuple

LOCKER_MOD = load_clvm_hex("puzzles/wrapped_cats/locker.clsp")

UNLOCKER_MOD = load_clvm_hex("puzzles/wrapped_cats/unlocker.clsp")

P2_CONTROLLER_PUZZLE_HASH_MOD = load_clvm_hex("puzzles/wrapped_cats/p2_controller_puzzle_hash.clsp")
P2_CONTROLLER_PUZZLE_HASH_MOD_HASH = P2_CONTROLLER_PUZZLE_HASH_MOD.get_tree_hash()


def get_p2_controller_puzzle_hash_inner_puzzle_hash(
    controller_puzzle_hash: bytes32
) -> Program:
  return P2_CONTROLLER_PUZZLE_HASH_MOD.curry(
    controller_puzzle_hash
  )


def get_unlocker_puzzle(
    message_source_chain: bytes,
    message_source: bytes,
    portal_receiver_launcher_id: bytes32,
    asset_id: bytes32 | None
) -> Program:
  return UNLOCKER_MOD.curry(
    CAT_MOD_HASH,
    P2_CONTROLLER_PUZZLE_HASH_MOD_HASH,
    get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id).get_tree_hash(),
    message_source_chain,
    raw_hash([b"\x01", message_source]),
    [] if asset_id is None else []
  )


def get_locker_puzzle(
    message_destination_chain: bytes,
    message_destination: bytes,
    portal_receiver_launcher_id: bytes32,
    asset_id: bytes32 | None
) -> Program:
  return LOCKER_MOD.curry(
    message_destination_chain,
    message_destination,
    CAT_MOD_HASH,
    OFFER_MOD_HASH,
    BRIDGING_PUZZLE_HASH,
    get_p2_controller_puzzle_hash_inner_puzzle_hash(
      get_unlocker_puzzle(
        message_destination_chain,
        message_destination,
        portal_receiver_launcher_id,
        asset_id
      ).get_tree_hash()
    ).get_tree_hash(),
    [] if asset_id is None else asset_id
  )


def get_p2_controller_puzzle_hash_inner_solution(
    my_id: bytes32,
    controller_parent_info: bytes32,
    controller_amount: int,
    delegated_puzzle: Program,
    delegated_solution: Program
) -> Program:
  return Program.to([
    my_id,
    controller_parent_info,
    controller_amount,
    delegated_puzzle,
    delegated_solution
  ])


def get_unlocker_solution(
    message_coin_parent_id: bytes32,
    message_nonce_hash: bytes32,
    receiver: bytes32,
    asset_amount_b32: bytes32,
    my_puzzle_hash: bytes32,
    my_id: bytes32,
    locked_coin_proofs: List[Tuple[bytes32, int]]
) -> Program:
  return Program.to([
    message_coin_parent_id,
    message_nonce_hash,
    receiver,
    asset_amount_b32,
    my_puzzle_hash,
    my_id,
    Program.to(locked_coin_proofs)
  ])


def get_locker_solution(
    my_amount: int,
    my_id: bytes32,
    asset_amount: int,
    receiver: bytes
) -> Program:
  return Program.to([
    my_amount,
    my_id,
    asset_amount,
    receiver
  ])

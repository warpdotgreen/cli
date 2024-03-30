from drivers.utils import load_clvm_hex, raw_hash
from drivers.portal import get_message_coin_puzzle_1st_curry
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH
from chia.wallet.trading.offer import OFFER_MOD_HASH

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
    asset_id: bytes32
) -> Program:
  return UNLOCKER_MOD.curry(
    CAT_MOD_HASH,
    P2_CONTROLLER_PUZZLE_HASH_MOD_HASH,
    get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id).get_tree_hash(),
    message_source_chain,
    raw_hash([b"\x01", message_source]),
    asset_id
  )


def get_locker_puzzle(
    message_destination_chain: bytes,
    message_destination: bytes,
    portal_receiver_launcher_id: bytes32,
    bridging_puzzle_hash: bytes32,
    asset_id: bytes32
) -> Program:
  return LOCKER_MOD.curry(
    message_destination_chain,
    message_destination,
    CAT_MOD_HASH,
    OFFER_MOD_HASH,
    bridging_puzzle_hash,
    get_p2_controller_puzzle_hash_inner_puzzle_hash(
      get_unlocker_puzzle(
        message_destination_chain,
        message_destination,
        portal_receiver_launcher_id,
        asset_id
      ).get_tree_hash()
    ).get_tree_hash(),
    asset_id
  )

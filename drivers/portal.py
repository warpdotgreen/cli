from drivers.utils import load_clvm_hex
from chia.types.blockchain_format.program import Program
from chia.wallet.puzzles.singleton_top_layer_v1_1 import \
    SINGLETON_LAUNCHER_HASH
from chia.wallet.puzzles.singleton_top_layer_v1_1 import SINGLETON_MOD_HASH
from chia.types.blockchain_format.sized_bytes import bytes32
from chia_rs import G1Element
from chia.wallet.puzzles.singleton_top_layer_v1_1 import puzzle_for_singleton
from typing import List

UPGRADE_PUZZLE_MOD = load_clvm_hex("puzzles/upgrade_puzzle.clvm.hex")
MESSAGE_COIN_MOD = load_clvm_hex("puzzles/message_coin.clvm.hex")
PORTAL_RECEIVER_MOD = load_clvm_hex("puzzles/portal_receiver.clvm.hex")

def get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id: bytes32) -> Program:
    return MESSAGE_COIN_MOD.curry(SINGLETON_MOD_HASH, SINGLETON_LAUNCHER_HASH, portal_receiver_launcher_id)

def get_message_coin_puzzle(
    portal_receiver_launcher_id: bytes32,
    sender: bytes,
    target: bytes32,
    target_is_puzzle_hash: bool,
    deadline: int,
    message_hash: bytes32
) -> Program:
  return get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id).curry(
    sender,
    target,
    target_is_puzzle_hash,
    deadline,
    message_hash
  )

def get_portal_receiver_inner_puzzle(
      launcher_id: bytes32,
      signature_treshold: int,
      signature_pubkeys: list[G1Element],
) -> Program:
    return PORTAL_RECEIVER_MOD.curry(
       (signature_treshold, signature_pubkeys), # VALIDATOR_INFO
       get_message_coin_puzzle_1st_curry(launcher_id).get_tree_hash()
    )

def get_portal_receiver_full_puzzle(
      launcher_id: bytes32,
      signature_treshold: int,
      signature_pubkeys: List[G1Element],
) -> Program:
  return puzzle_for_singleton(
     launcher_id,
     get_portal_receiver_inner_puzzle(launcher_id, signature_treshold, signature_pubkeys),
  )

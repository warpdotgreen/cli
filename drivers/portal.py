from drivers.utils import load_clvm_hex
from chia.types.blockchain_format.program import Program
from chia.wallet.puzzles.singleton_top_layer_v1_1 import \
    SINGLETON_LAUNCHER_HASH
from chia.wallet.puzzles.singleton_top_layer_v1_1 import SINGLETON_MOD_HASH
from chia.types.blockchain_format.sized_bytes import bytes32
from chia_rs import G1Element
from chia.wallet.puzzles.singleton_top_layer_v1_1 import puzzle_for_singleton
from typing import List
from chia.types.blockchain_format.coin import Coin

MESSAGE_COIN_MOD = load_clvm_hex("puzzles/message_coin.clsp")
PORTAL_RECEIVER_MOD = load_clvm_hex("puzzles/portal_receiver.clsp")

def get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id: bytes32) -> Program:
    return MESSAGE_COIN_MOD.curry(SINGLETON_MOD_HASH, SINGLETON_LAUNCHER_HASH, portal_receiver_launcher_id)

def get_message_coin_puzzle(
    portal_receiver_launcher_id: bytes32,
    source_info: bytes,
    nonce: int,
    destination_info: bytes32,
    deadline: int,
    message_hash: bytes32,
    source_chain: bytes = b'e', # ethereum
    source_type: bytes = b'c', # contract
    destination_type: bytes = b'p', # puzzle hash
) -> Program:
  return get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id).curry(
    nonce,
    (source_info, (source_chain, source_type)),
    (destination_info, destination_type),
    deadline,
    message_hash
  )

def get_portal_receiver_inner_puzzle(
      launcher_id: bytes32,
      signature_treshold: int,
      signature_pubkeys: list[G1Element],
      last_nonce: int = 0,
) -> Program:
    return PORTAL_RECEIVER_MOD.curry(
       (signature_treshold, signature_pubkeys), # VALIDATOR_INFO
       get_message_coin_puzzle_1st_curry(launcher_id).get_tree_hash(),
       last_nonce
    )

def get_portal_receiver_full_puzzle(
      launcher_id: bytes32,
      signature_treshold: int,
      signature_pubkeys: List[G1Element],
      last_nonce: int = 0,
) -> Program:
  return puzzle_for_singleton(
     launcher_id,
     get_portal_receiver_inner_puzzle(launcher_id, signature_treshold, signature_pubkeys, last_nonce),
  )

def get_portal_receiver_inner_solution(
    validator_sig_switches: List[bool],
    new_inner_puzzle_hash: bytes32,
    nonce: int,
    source_info: bytes,
    destination_info: bytes32,
    deadline: int,
    message: Program,
    source_chain: bytes = b'e', # ethereum
    source_type: bytes = b'c', # contract
    destination_type: bytes = b'p', # puzzle hash
) -> Program:
    return Program.to([
       validator_sig_switches,
       new_inner_puzzle_hash,
       nonce,
       source_chain,
       source_type,
       source_info,
       destination_type,
       destination_info,
       deadline,
       message
    ])

def get_message_coin_solution(
    receiver_coin: Coin,
    parent_parent_info: bytes32,
    parent_inner_puzzle_hash: bytes32,
    message_coin_id: bytes32,
    receiver_singleton_launcher_id: bytes32 | None = None,
    receiver_singleton_inner_puzzle_hash: bytes32 | None = None,
) -> Program:
    return Program.to([
      (receiver_coin.parent_coin_info, (receiver_coin.puzzle_hash, receiver_coin.amount)),
      0 if receiver_singleton_launcher_id is None and receiver_singleton_inner_puzzle_hash is None else (receiver_singleton_launcher_id, receiver_singleton_inner_puzzle_hash),
      (parent_parent_info, parent_inner_puzzle_hash),
      message_coin_id
    ])

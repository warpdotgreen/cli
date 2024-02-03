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
import dataclasses

MESSAGE_COIN_MOD = load_clvm_hex("puzzles/message_coin.clsp")
PORTAL_RECEIVER_MOD = load_clvm_hex("puzzles/portal_receiver.clsp")

def get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id: bytes32) -> Program:
    return MESSAGE_COIN_MOD.curry(SINGLETON_MOD_HASH, SINGLETON_LAUNCHER_HASH, portal_receiver_launcher_id)

def get_message_coin_puzzle(
    portal_receiver_launcher_id: bytes32,
    source_info: bytes,
    nonce: int,
    destination_info: bytes32,
    message_hash: bytes32,
    source_chain: bytes = b'eth', # ethereum
    source_type: bytes = b'c', # contract
    destination_type: bytes = b'p', # puzzle hash
) -> Program:
  return get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id).curry(
    nonce,
    (source_info, (source_chain, source_type)),
    (destination_info, destination_type),
    message_hash
  )

def get_portal_receiver_inner_puzzle(
      launcher_id: bytes32,
      signature_treshold: int,
      signature_pubkeys: list[G1Element],
      update_puzzle_hash: bytes32,
      last_nonces: List[int] = [],
) -> Program:
    first_curry = PORTAL_RECEIVER_MOD.curry(
       (signature_treshold, signature_pubkeys), # VALIDATOR_INFO
       get_message_coin_puzzle_1st_curry(launcher_id).get_tree_hash(),
       update_puzzle_hash
    )
    return first_curry.curry(
       first_curry.get_tree_hash(), # SELF_HASH
       last_nonces
    )

def get_portal_receiver_full_puzzle(
      launcher_id: bytes32,
      signature_treshold: int,
      signature_pubkeys: List[G1Element],
      update_puzzle_hash: bytes32,
      last_nonces: List[int] = [],
) -> Program:
  return puzzle_for_singleton(
     launcher_id,
     get_portal_receiver_inner_puzzle(launcher_id, signature_treshold, signature_pubkeys, update_puzzle_hash, last_nonces),
  )

@dataclasses.dataclass(frozen=True)
class PortalMessage:
    nonce: int
    validator_sig_switches: List[bool]
    source_info: bytes
    destination_info: bytes32
    message: Program
    source_chain: bytes = b'eth'
    source_type: bytes = b'c'
    destination_type: bytes = b'p'

def get_sigs_switch(sig_switches: List[bool]) -> int:
   return int(
       "".join(["1" if x else "0" for x in sig_switches])[::-1],
       2
    )

def get_portal_receiver_inner_solution(
    messages: List[PortalMessage],
    update_puzzle_reveal: Program | None = None,
    update_puzzle_solution: Program | None = None
) -> Program:
    return Program.to([
       0 if update_puzzle_reveal is None or update_puzzle_solution is None else (update_puzzle_reveal, update_puzzle_solution),
       [messages.nonce for messages in messages],
       [
          [
            get_sigs_switch(msg.validator_sig_switches),
            msg.source_chain,
            msg.source_type,
            msg.source_info,
            msg.destination_type,
            msg.destination_info,
            msg.message
          ] for msg in messages
       ]
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

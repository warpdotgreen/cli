from drivers.utils import load_clvm_hex, raw_hash
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
from typing import Tuple
from drivers.multisig import get_multisig_inner_puzzle

MESSAGE_COIN_MOD = load_clvm_hex("puzzles/message_coin.clsp")
PORTAL_RECEIVER_MOD = load_clvm_hex("puzzles/portal_receiver.clsp")
REKEY_PORTAL_MOD = load_clvm_hex("puzzles/rekey_portal.clsp")

def get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id: bytes32) -> Program:
    return MESSAGE_COIN_MOD.curry(
       (SINGLETON_MOD_HASH, (portal_receiver_launcher_id, SINGLETON_LAUNCHER_HASH))
    )

def get_message_coin_puzzle(
    portal_receiver_launcher_id: bytes32,
    source_chain: bytes,
    source: bytes,
    nonce: bytes32,
    destination: bytes32,
    message_hash: bytes32,
) -> Program:
  return get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id).curry(
    (source_chain, nonce),
    source,
    destination,
    message_hash
  )

def get_portal_receiver_inner_puzzle(
      launcher_id: bytes32,
      signature_treshold: int,
      signature_pubkeys: list[G1Element],
      update_puzzle_hash: bytes32,
      last_chains_and_nonces: List[Tuple[bytes, int]] = [],
) -> Program:
    first_curry = PORTAL_RECEIVER_MOD.curry(
       (signature_treshold, signature_pubkeys), # VALIDATOR_INFO
       get_message_coin_puzzle_1st_curry(launcher_id).get_tree_hash(),
       update_puzzle_hash
    )
    return first_curry.curry(
       first_curry.get_tree_hash(), # SELF_HASH
       last_chains_and_nonces
    )

def get_portal_receiver_full_puzzle(
      launcher_id: bytes32,
      signature_treshold: int,
      signature_pubkeys: List[G1Element],
      update_puzzle_hash: bytes32,
      last_chains_and_nonces: List[Tuple[bytes, int]] = [],
) -> Program:
  return puzzle_for_singleton(
     launcher_id,
     get_portal_receiver_inner_puzzle(launcher_id, signature_treshold, signature_pubkeys, update_puzzle_hash, last_chains_and_nonces),
  )

@dataclasses.dataclass(frozen=True)
class PortalMessage:
    nonce: bytes32
    validator_sig_switches: List[bool]
    source_chain: bytes
    source: bytes32
    destination: bytes32
    message: Program

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
       [(message.source_chain, message.nonce) for message in messages],
       [
          [
            get_sigs_switch(msg.validator_sig_switches),
            msg.source,
            msg.destination,
            msg.message
          ] for msg in messages
       ]
    ])

def get_message_coin_solution(
    receiver_coin: Coin,
    parent_parent_info: bytes32,
    parent_inner_puzzle_hash: bytes32,
    message_coin_id: bytes32,
) -> Program:
    return Program.to([
      (receiver_coin.parent_coin_info, receiver_coin.amount),
      (parent_parent_info, parent_inner_puzzle_hash),
      message_coin_id
    ])


def get_portal_rekey_delegated_puzzle(
    portal_receiver_launcher_id: bytes32,
    current_signature_treshold: int,
    current_signature_pubkeys: List[G1Element],
    new_signature_treshold: int,
    new_signature_pubkeys: List[G1Element],
    current_multisig_threshold: int,
    current_multisig_pubkeys: List[G1Element],
    new_multisig_threshold: int,
    new_multisig_pubkeys: List[G1Element],
) -> Program:
  return REKEY_PORTAL_MOD.curry(
    PORTAL_RECEIVER_MOD.get_tree_hash(),
    (SINGLETON_MOD_HASH, (portal_receiver_launcher_id, SINGLETON_LAUNCHER_HASH)), # PORTAL_SINGLETON_STRUCT
    raw_hash([
       b"\x01",
       get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id).get_tree_hash()
    ]), # MESSAGE_COIN_MOD_HASH_HASH
    (current_signature_treshold, current_signature_pubkeys), # CURRENT_VALIDATOR_INFO
    (new_signature_treshold, new_signature_pubkeys), # NEW_VALIDATOR_INFO
    raw_hash([
       b"\x01",
       get_multisig_inner_puzzle(current_multisig_threshold, current_multisig_pubkeys).get_tree_hash()
    ]), # CURRENT_UPDATE_PUZZLE_HASH_HASH
    raw_hash([
       b"\x01",
       get_multisig_inner_puzzle(new_multisig_pubkeys, new_multisig_pubkeys).get_tree_hash()
    ]) # NEW_UPDATE_PUZZLE_HASH_HASH
  )

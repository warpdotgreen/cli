from drivers.utils import load_clvm_hex, raw_hash
from drivers.portal import get_message_coin_puzzle_1st_curry, BRIDGING_PUZZLE_HASH
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH
from chia.types.blockchain_format.coin import Coin

WRAPPED_TAIL_MOD = load_clvm_hex("puzzles/wrapped_assets/wrapped_tail.clsp")
WRAPPED_TAIL_MOD_HASH = WRAPPED_TAIL_MOD.get_tree_hash()

CAT_MINTER_MOD = load_clvm_hex("puzzles/wrapped_assets/cat_minter.clsp")

CAT_MINT_AND_PAYOUT_MOD = load_clvm_hex("puzzles/wrapped_assets/cat_mint_and_payout.clsp")
CAT_MINT_AND_PAYOUT_MOD_HASH = CAT_MINT_AND_PAYOUT_MOD.get_tree_hash()

CAT_BURNER_MOD = load_clvm_hex("puzzles/wrapped_assets/cat_burner.clsp")

BURN_INNER_PUZZLE_MOD = load_clvm_hex("puzzles/wrapped_assets/burn_inner_puzzle.clsp")
BURN_INNER_PUZZLE_MOD_HASH = BURN_INNER_PUZZLE_MOD.get_tree_hash()

def get_cat_burner_puzzle(
    destination_chain: bytes,
    destination: bytes, # address of contract that receives message
) -> Program:
  return CAT_BURNER_MOD.curry(
    CAT_MOD_HASH,
    BURN_INNER_PUZZLE_MOD_HASH,
    BRIDGING_PUZZLE_HASH,
    destination_chain,
    destination
  )

def get_cat_minter_puzzle(
    portal_receiver_launcher_id: bytes32,
    source_chain: bytes,
    source: bytes
) -> Program:
  return CAT_MINTER_MOD.curry(
    get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id).get_tree_hash(),
    CAT_MOD_HASH,
    WRAPPED_TAIL_MOD_HASH,
    CAT_MINT_AND_PAYOUT_MOD_HASH,
    raw_hash([
      b'\x01',
      get_cat_burner_puzzle(source_chain, source).get_tree_hash()
    ]), # CAT_BURNER_PUZZLE_HASH_HASH = (sha256 1 CAT_BURNER_PUZZLE_HASH_HASH)
    BURN_INNER_PUZZLE_MOD_HASH,
    source_chain,
    source
  )

def get_cat_mint_and_payout_inner_puzzle(
    receiver: bytes32
) -> Program:
  return CAT_MINT_AND_PAYOUT_MOD.curry(
    receiver
  )

def get_cat_burn_inner_puzzle_first_curry(
    destination_chain: bytes,
    destination: bytes,
    source_chain_token_contract_address: bytes,
) -> Program:
  return BURN_INNER_PUZZLE_MOD.curry(
    get_cat_burner_puzzle(destination_chain, destination).get_tree_hash(),
    source_chain_token_contract_address
  )

def get_cat_burn_inner_puzzle(
    destination_chain: bytes,
    destination: bytes, # e.g., ETH token bridge
    source_chain_token_contract_address: bytes,
    target_receiver: bytes,
    bridge_toll: int
) -> Program:
  return get_cat_burn_inner_puzzle_first_curry(
    destination_chain,
    destination,
    source_chain_token_contract_address
  ).curry(
    target_receiver,
    bridge_toll
  )

def get_wrapped_tail(
    portal_receiver_launcher_id: bytes32,
    source_chain: bytes,
    source: bytes,
    source_chain_token_contract_address: bytes,
) -> Program:
  if len(source_chain_token_contract_address) < 32:
    source_chain_token_contract_address = b'\x00' * (32 - len(source_chain_token_contract_address)) + source_chain_token_contract_address
    
  return WRAPPED_TAIL_MOD.curry(
    get_cat_minter_puzzle(
      portal_receiver_launcher_id, source_chain, source
    ).get_tree_hash(),
    get_cat_burn_inner_puzzle_first_curry(
      source_chain, source, source_chain_token_contract_address
    ).get_tree_hash(),
  )

def get_burn_inner_puzzle_solution(
    cat_burner_parent_id: bytes32,
    my_coin_id: bytes32,
    tail_reveal: Program
) -> Program:
  return Program.to([
    cat_burner_parent_id,
    my_coin_id,
    tail_reveal
  ])

def get_cat_mint_and_payout_inner_puzzle_solution(
    tail_puzzle: Program,
    my_amount: int,
    parent_parent_info: bytes32,
) -> Program:
  return Program.to([
    tail_puzzle,
    my_amount,
    parent_parent_info
  ])

def get_cat_minter_puzzle_solution(
    nonce: int,
    message: Program,
    my_puzzle_hash: bytes32,
    my_coin_id: bytes32,
    message_coin_parent_info: bytes32,
) -> Program:
  return Program.to([
    nonce,
    message,
    my_puzzle_hash,
    my_coin_id,
    message_coin_parent_info
  ])

def get_cat_burner_puzzle_solution(
    cat_parent_info: bytes32,
    tail_hash: bytes32,
    cat_amount: int,
    source_chain_token_contract_address: bytes,
    destination_receiver_address: bytes,
    my_coin: Coin
) -> Program:
  return Program.to([
    cat_parent_info,
    raw_hash([b'\x01', tail_hash]),
    cat_amount,
    source_chain_token_contract_address,
    destination_receiver_address,
    my_coin.amount,
    my_coin.puzzle_hash,
    my_coin.name()
  ])

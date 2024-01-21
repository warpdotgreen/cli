from drivers.utils import load_clvm_hex
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH

WRAPPED_TAIL_MOD = load_clvm_hex("puzzles/wrapped_tail.clvm.hex")
CAT_MINTER_MOD = load_clvm_hex("puzzles/cat_minter.clvm.hex")
CAT_MINT_AND_PAYOUT_MOD = load_clvm_hex("puzzles/cat_mint_and_payout.clvm.hex")
CAT_BURNER_MOD = load_clvm_hex("puzzles/cat_burner.clvm.hex")
BURN_INNER_PUZZLE_MOD = load_clvm_hex("puzzles/burn_inner_puzzle.clvm.hex")
BURN_INNER_PUZZLE_MOD_HASH = BURN_INNER_PUZZLE_MOD.get_tree_hash()

def get_cat_burner_puzzle(
    bridging_puzzle_hash: bytes32,
    eth_token_bridge_address: bytes,
) -> Program:
  return CAT_BURNER_MOD.curry(
    CAT_MOD_HASH,
    BURN_INNER_PUZZLE_MOD_HASH,
    bridging_puzzle_hash,
    eth_token_bridge_address
  )

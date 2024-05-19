from chia_rs import CoinSpend, Program
from chia.wallet.trading.offer import Offer
from chia.wallet.puzzles.singleton_top_layer_v1_1 import puzzle_for_singleton, solution_for_singleton, lineage_proof_for_coinsol, pay_to_singleton_puzzle
from chia.wallet.puzzles.p2_m_of_n_delegate_direct import puzzle_for_m_of_public_key_list, solution_for_delegated_puzzle
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.condition_opcodes import ConditionOpcode
from chia_rs import G1Element
from typing import List

def get_multisig_inner_puzzle(
    pks: List[G1Element],
    threshold: int,
) -> Program:
    return puzzle_for_m_of_public_key_list(threshold, pks)

def get_multisig_puzzle(
    launcher_id: bytes32,
    pks: List[G1Element],
    threshold: int,
) -> Program:
    return puzzle_for_singleton(
        launcher_id,
        get_multisig_inner_puzzle(pks, threshold),
    )

def get_multisig_delegated_puzzle_for_conditions(
      coin_id: bytes32,
      inner_puzz_hash: bytes32,
      conditions: List[Program],
) -> Program:
    return Program.to((1, conditions + [
        [ConditionOpcode.ASSERT_MY_COIN_ID, coin_id],
        [ConditionOpcode.CREATE_COIN, inner_puzz_hash, 1],
    ]))

def get_multisig_inner_solution(
    treshold: int,
    selectors: List[bool],
    delegated_puzzle: Program,
    delegated_solution: Program = Program.to(0)
) -> Program:
  return solution_for_delegated_puzzle(
    treshold,
    selectors,
    delegated_puzzle,
    delegated_solution
  )

def get_multisig_solution(
    last_spend: CoinSpend,
    threshold: int,
    selectors: List[bool],
    delegated_puzzle: Program,
    delegated_solution: Program = Program.to(0)
) -> Program:
  return solution_for_singleton(
     lineage_proof_for_coinsol(last_spend),
     1,
     get_multisig_inner_solution(
      threshold,
      selectors,
      delegated_puzzle,
      delegated_solution
    )
  )

# get bridging ph:
# from chia.wallet.puzzles.singleton_top_layer_v1_1 import pay_to_singleton_puzzle
# for claiming coins:
# from chia.wallet.puzzles.singleton_top_layer_v1_1 import claim_p2_singleton

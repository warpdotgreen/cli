from chia.types.blockchain_format.serialized_program import SerializedProgram
from chia.types.blockchain_format.sized_bytes import bytes32
from chia_rs import Program
from typing import List
import hashlib

def program_from_hex(h: str) -> Program:
    return SerializedProgram.from_bytes(bytes.fromhex(h)).to_program()


def load_clvm_hex(
    filename
) -> Program:
    if not filename.endswith(".hex"):
        filename += ".hex"
    clvm_hex = open(filename, "r").read().strip()
    assert len(clvm_hex) != 0

    return program_from_hex(clvm_hex)

def raw_hash(args: List[bytes]) -> bytes32:
    h = hashlib.sha256()
    for arg in args:
        h.update(arg)
    return h.digest()

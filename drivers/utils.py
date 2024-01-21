from chia.types.blockchain_format.serialized_program import SerializedProgram
from chia.types.blockchain_format.program import Program

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

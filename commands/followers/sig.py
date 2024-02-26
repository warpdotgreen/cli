from chia.util.bech32m import bech32_encode, convertbits, bech32_decode
from typing import Tuple

def encode_signature(
    origin_chain: bytes,
    destination_chain: bytes,
    nonce: bytes,
    coin_id: bytes | None,
    sig: bytes
) -> str:
    res = ""
    route_data = origin_chain + destination_chain + nonce
    encoded = bech32_encode("r", convertbits(route_data, 8, 5))
    res += encoded
    res += "-"

    if coin_id is not None:
        res += bech32_encode("c", convertbits(coin_id, 8, 5))

    res += "-"

    res += bech32_encode("s", convertbits(sig, 8, 5))

    return res


def decode_signature(enc_sig: str) -> Tuple[
    bytes,  # origin_chain
    bytes,  # destination_chain
    bytes,  # nonce
    bytes | None,  # coin_id
    bytes  # sig
]:
    parts = enc_sig.split("-")
    route_data = convertbits(bech32_decode(parts[0], (32 + 3 + 3) * 2)[1], 5, 8, False)
    origin_chain = route_data[:3]
    destination_chain = route_data[3:6]
    nonce = route_data[6:]

    coin_id = convertbits(bech32_decode(parts[1])[1], 5, 8, False)
    sig = convertbits(bech32_decode(parts[-1], 96 * 2)[1], 5, 8, False)

    return origin_chain, destination_chain, nonce, coin_id, sig

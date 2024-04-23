from chia.util.bech32m import bech32_encode, convertbits, bech32_decode
from typing import Tuple, List
from commands.config import get_config_item
from nostr_sdk import Keys, Client, NostrSigner, EventBuilder, Tag, Filter, SingleLetterTag, Alphabet
from datetime import timedelta
import logging
import time
import click

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


def send_signature(
    sig: str,
    retries: int = 0
):  
    # keep log locally
    try:
        open("messages.txt", "a").write(sig + "\n")
    except:
        open("messages.txt", "w").write(sig + "\n")

    
    try:
        relays = get_config_item(["nostr", "relays"])
        my_private_key = Keys.from_mnemonic(get_config_item(["nostr", "my_mnemonic"]), None)
    except:
        click.echo("Nostr: failed to load config; skipping signature sending.")
        return

    try:
        [route_data, coin_data, sig_data] = sig.split("-")

        signer = NostrSigner.keys(my_private_key)
        client = Client(signer)
        
        client.add_relays(relays)
        client.connect()

        filter = Filter().custom_tag(
            SingleLetterTag.lowercase(Alphabet.R), [route_data]
        ).custom_tag(
            SingleLetterTag.lowercase(Alphabet.C), [coin_data]
        )
        
        events = client.get_events_of([filter], timedelta(seconds=10))
        for event in events:
            if event.author().to_bech32() == signer.public_key().to_bech32():
                logging.info(f"Nostr: signature already sent to relay; only logging it to messages.txt")
                return

        text_note_builder = EventBuilder.text_note(sig_data, [
            Tag.parse(["r", route_data]),
            Tag.parse(["c", coin_data])
        ])

        event_id = client.send_event_builder(text_note_builder)
        logging.info(f"Nostr: sent event {event_id.to_bech32()} to relays.")

        client.disconnect()
    except:
        if retries < 3:
            retries += 1
            logging.error("Nostr: failed to send signature to relays; retrying in 3s...", exc_info=True)
            time.sleep(3)
            send_signature(sig, retries)
        else:
            logging.error(f"Nostr: failed to send signature to relays: {sig}", exc_info=True)

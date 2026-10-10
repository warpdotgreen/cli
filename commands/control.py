import sys

import click
from nostr_sdk import Client, EventBuilder, Keys

from commands.cli_wrappers import async_func
from commands.control_channel import load_control_config, normalize_hex


@click.group()
def control():
    pass


def _prompt_keys() -> Keys:
    mnemonic = click.prompt("Nostr mnemonic", hide_input=True)
    return Keys.from_mnemonic(mnemonic, None)


def _require_note_id(note_id: str) -> None:
    if not note_id or any(ch.isspace() for ch in note_id):
        click.echo("Refused: id must be a single token")
        sys.exit(1)


def _print_event(event) -> None:
    click.echo(f"content: {event.content()}")
    click.echo(f"author: {event.author().to_hex()}")
    click.echo(f"tags: {[t.as_vec() for t in event.tags()]}")
    click.echo(f"created_at: {int(event.created_at().as_secs())}")


async def _publish_to_relays(relays: list[str], event) -> None:
    """Publish the same signed event to each relay independently.

    Exits non-zero when every relay rejects. One acceptance is enough.
    """
    accepted = 0
    for relay in relays:
        client = Client(None)
        try:
            await client.add_relay(relay)
            await client.connect()
            event_id = await client.send_event(event)
            click.echo(f"ACCEPTED {relay} {event_id.to_hex()}")
            accepted += 1
        except Exception as e:
            click.echo(f"REJECTED {relay} {e}")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
    if accepted == 0:
        click.echo("FAILED: no relay accepted the event")
        sys.exit(1)


@control.command()
@click.argument("note_id")
@async_func
async def stop(note_id: str):
    _require_note_id(note_id)
    cfg = load_control_config()
    keys = _prompt_keys()
    pubkey = normalize_hex(keys.public_key().to_hex())
    if pubkey not in cfg.stop_authors:
        click.echo(
            f"Refused: pubkey {pubkey} is not in control_stop_authors"
        )
        sys.exit(1)

    event = EventBuilder.text_note(f"stop {note_id}", []).to_event(keys)
    _print_event(event)
    await _publish_to_relays(cfg.relays, event)


@control.command("start")
@click.argument("note_id")
@async_func
async def start_cmd(note_id: str):
    _require_note_id(note_id)
    cfg = load_control_config()
    keys = _prompt_keys()
    pubkey = normalize_hex(keys.public_key().to_hex())
    if pubkey not in cfg.start_authors:
        click.echo(
            f"Refused: pubkey {pubkey} is not in control_start_authors"
        )
        sys.exit(1)

    event = EventBuilder.text_note(f"start {note_id}", []).to_event(keys)
    _print_event(event)
    await _publish_to_relays(cfg.relays, event)

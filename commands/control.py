import sys
import time

import click
from nostr_sdk import Client, EventBuilder, EventId, Keys, Tag

from commands.cli_wrappers import async_func
from commands.control_channel import (
    CLOCK_SKEW_SECONDS,
    ControlEvent,
    fetch_control_events,
    load_control_config,
    normalize_hex,
)


@click.group()
def control():
    pass


def _prompt_keys() -> Keys:
    mnemonic = click.prompt("Nostr mnemonic", hide_input=True)
    return Keys.from_mnemonic(mnemonic, None)


def _latest_valid_stop(
    events: list[ControlEvent],
    stop_authors: set[str],
    now_unix: int,
) -> ControlEvent | None:
    stop_set = {normalize_hex(a) for a in stop_authors}
    stops = [
        ev
        for ev in events
        if ev.created_at <= now_unix + CLOCK_SKEW_SECONDS
        and normalize_hex(ev.author) in stop_set
        and ev.content == "stop"
    ]
    if not stops:
        return None
    return max(stops, key=lambda e: (e.created_at, normalize_hex(e.id)))


def _print_event(event) -> None:
    click.echo(f"content: {event.content()}")
    click.echo(f"author: {event.author().to_hex()}")
    click.echo(f"tags: {[t.as_vec() for t in event.tags()]}")
    click.echo(f"created_at: {int(event.created_at().as_secs())}")


async def _publish_to_relays(relays: list[str], event) -> None:
    """Publish the same signed event to each relay independently."""
    for relay in relays:
        client = Client(None)
        try:
            await client.add_relay(relay)
            await client.connect()
            event_id = await client.send_event(event)
            click.echo(f"ACCEPTED {relay} {event_id.to_hex()}")
        except Exception as e:
            click.echo(f"REJECTED {relay} {e}")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


@control.command()
@async_func
async def stop():
    cfg = load_control_config()
    keys = _prompt_keys()
    pubkey = normalize_hex(keys.public_key().to_hex())
    if pubkey not in cfg.stop_authors:
        click.echo(
            f"Refused: pubkey {pubkey} is not in control_stop_authors"
        )
        sys.exit(1)

    event = EventBuilder.text_note("stop", []).to_event(keys)
    _print_event(event)
    await _publish_to_relays(cfg.relays, event)


@control.command("start")
@async_func
async def start_cmd():
    cfg = load_control_config()
    keys = _prompt_keys()
    pubkey = normalize_hex(keys.public_key().to_hex())
    if pubkey not in cfg.start_authors:
        click.echo(
            f"Refused: pubkey {pubkey} is not in control_start_authors"
        )
        sys.exit(1)

    events = await fetch_control_events(
        cfg.relays,
        cfg.stop_authors,
        cfg.start_authors,
    )
    latest_stop = _latest_valid_stop(
        events, cfg.stop_authors, int(time.time())
    )
    tags = []
    if latest_stop is not None:
        tags = [Tag.event(EventId.from_hex(normalize_hex(latest_stop.id)))]

    event = EventBuilder.text_note("start", tags).to_event(keys)
    _print_event(event)
    await _publish_to_relays(cfg.relays, event)

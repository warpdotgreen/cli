import asyncio
import sys

import click
from nostr_sdk import Client, Event, EventBuilder, Keys

from commands.cli_wrappers import async_func
from commands.control_channel import (
    _command_and_id,
    load_control_config,
    normalize_hex,
)

# Tests monkeypatch this. Referenced at call time so a patched value is used.
PUBLISH_TIMEOUT_SECONDS = 10


@click.group()
def control():
    pass


def _prompt_keys() -> Keys:
    mnemonic = click.prompt("Nostr mnemonic", hide_input=True, err=True)
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


async def _send_to_relay(client, relay: str, event):
    """add_relay, connect, and send_event. CancelledError is not caught."""
    try:
        await client.add_relay(relay)
        await client.connect()
        return await client.send_event(event)
    except Exception as exc:
        return exc


async def _publish_one(relay: str, event) -> tuple[str, str, str]:
    """Publish to one relay. Returns (relay, "accepted"|"rejected", detail)."""
    client = Client(None)
    try:
        try:
            result = await asyncio.wait_for(
                _send_to_relay(client, relay, event),
                timeout=PUBLISH_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return relay, "rejected", "timeout"
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    if isinstance(result, Exception):
        return relay, "rejected", str(result)
    return relay, "accepted", result.to_hex()


async def _publish_to_relays(relays: list[str], event) -> None:
    """Publish the same signed event to each relay independently.

    Relays are attempted concurrently, and each attempt is bounded by
    PUBLISH_TIMEOUT_SECONDS. Exits non-zero when every relay rejects.
    One acceptance is enough.
    """
    results = await asyncio.gather(
        *(_publish_one(relay, event) for relay in relays)
    )
    accepted = 0
    for relay, status, detail in results:
        if status == "accepted":
            click.echo(f"ACCEPTED {relay} {detail}")
            accepted += 1
        else:
            click.echo(f"REJECTED {relay} {detail}")
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


def _sign_control_note(
    note_id: str,
    command: str,
    authors,
    author_list_name: str,
) -> None:
    """Sign a kind-1 control note and print it. Does not publish."""
    keys = _prompt_keys()
    pubkey = normalize_hex(keys.public_key().to_hex())
    if pubkey not in authors:
        click.echo(
            f"Refused: pubkey {pubkey} is not in {author_list_name}"
        )
        sys.exit(1)

    event = EventBuilder.text_note(f"{command} {note_id}", []).to_event(keys)
    click.echo(f"content: {event.content()}", err=True)
    click.echo(f"author: {event.author().to_hex()}", err=True)
    click.echo(f"tags: {[t.as_vec() for t in event.tags()]}", err=True)
    click.echo(f"created_at: {int(event.created_at().as_secs())}", err=True)
    click.echo(event.as_json())


@control.command("sign-stop")
@click.argument("note_id")
def sign_stop(note_id: str):
    _require_note_id(note_id)
    cfg = load_control_config()
    _sign_control_note(note_id, "stop", cfg.stop_authors, "control_stop_authors")


@control.command("sign-start")
@click.argument("note_id")
def sign_start(note_id: str):
    _require_note_id(note_id)
    cfg = load_control_config()
    _sign_control_note(
        note_id, "start", cfg.start_authors, "control_start_authors"
    )


def _refuse(message: str) -> None:
    click.echo(f"Refused: {message}")
    sys.exit(1)


def _read_event_source(src: str) -> str:
    """Read one event JSON document from a path, or stdin when src is '-'."""
    if src == "-":
        return sys.stdin.read()
    try:
        with open(src, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeError) as exc:
        _refuse(f"cannot read {src}: {exc}")


def _require_signed_control_event(raw: str, command: str):
    """Parse a signed control note. No relay contact and no re-signing."""
    try:
        event = Event.from_json(raw)
    except Exception:
        _refuse("invalid event JSON")

    try:
        verified = event.verify()
    except Exception:
        verified = False
    if not verified:
        _refuse("signature or id did not verify")

    if event.kind().as_u16() != 1:
        _refuse("kind must be 1")

    if list(event.tags()):
        _refuse("tags must be empty")

    parsed = _command_and_id(event.content())
    if parsed is None or parsed[0] != command:
        _refuse(f"content must be exactly '{command} <id>'")
    return event


async def _publish_signed_note(
    src: str, command: str, author_list_name: str
) -> None:
    """Publish an already-signed control note. Does not query relays."""
    event = _require_signed_control_event(_read_event_source(src), command)
    cfg = load_control_config()
    authors = cfg.stop_authors if command == "stop" else cfg.start_authors
    pubkey = normalize_hex(event.author().to_hex())
    if pubkey not in authors:
        _refuse(f"pubkey {pubkey} is not in {author_list_name}")

    _print_event(event)
    await _publish_to_relays(cfg.relays, event)


@control.command("publish-signed-stop")
@click.argument("src")
@async_func
async def publish_signed_stop(src: str):
    await _publish_signed_note(src, "stop", "control_stop_authors")


@control.command("publish-signed-start")
@click.argument("src")
@async_func
async def publish_signed_start(src: str):
    await _publish_signed_note(src, "start", "control_start_authors")

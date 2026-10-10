from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, List, Literal, Optional, Sequence, Set

from nostr_sdk import Client, Filter, Kind, PublicKey

from commands.config import config as _loaded_config

ControlCommand = Literal["start", "stop"]

# Starts may be up to this many seconds behind a stop's created_at.
# Events more than this far ahead of `now` are ignored.
CLOCK_SKEW_SECONDS = 120


@dataclass(frozen=True)
class ControlEvent:
    id: str
    author: str
    content: str
    created_at: int


@dataclass(frozen=True)
class ControlConfig:
    stop_authors: Set[str]
    start_authors: Set[str]
    relays: List[str]


def normalize_hex(value: str) -> str:
    """Lowercase hex without a 0x prefix."""
    return value.lower().removeprefix("0x")


def _parse_pubkey_list(raw: object, field_name: str) -> Set[str]:
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be a list of hex pubkeys")
    out: Set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} entries must be hex strings")
        pk = normalize_hex(item)
        if len(pk) != 64 or any(c not in "0123456789abcdef" for c in pk):
            raise ValueError(f"invalid pubkey in {field_name}: {item!r}")
        out.add(pk)
    return out


def load_control_config(config: Optional[dict] = None) -> ControlConfig:
    """
    Load nostr control-gate settings.

    Requires non-empty, non-overlapping control_stop_authors and
    control_start_authors. control_relays is optional and defaults to nostr.relays.
    """
    cfg = _loaded_config if config is None else config
    try:
        nostr = cfg["nostr"]
    except (KeyError, TypeError) as e:
        raise ValueError("config missing nostr section") from e

    try:
        stop_authors = _parse_pubkey_list(
            nostr["control_stop_authors"], "nostr.control_stop_authors"
        )
        start_authors = _parse_pubkey_list(
            nostr["control_start_authors"], "nostr.control_start_authors"
        )
    except KeyError as e:
        raise ValueError(f"config missing {e.args[0]}") from e

    if not stop_authors:
        raise ValueError("nostr.control_stop_authors must not be empty")
    if not start_authors:
        raise ValueError("nostr.control_start_authors must not be empty")

    overlap = stop_authors & start_authors
    if overlap:
        raise ValueError(
            "nostr.control_stop_authors and nostr.control_start_authors overlap: "
            + ", ".join(sorted(overlap))
        )

    relays = nostr.get("control_relays") or nostr.get("relays")
    if not relays:
        raise ValueError(
            "nostr.control_relays is unset and nostr.relays is missing or empty"
        )
    if not isinstance(relays, list) or not all(isinstance(r, str) for r in relays):
        raise ValueError("nostr control relays must be a list of strings")

    return ControlConfig(
        stop_authors=stop_authors,
        start_authors=start_authors,
        relays=list(relays),
    )


def _command_and_id(content: str) -> Optional[tuple[str, str]]:
    """Parse a note whose entire text is `start <id>` or `stop <id>`.

    The id is one whitespace-delimited token. Comparison is case-insensitive.
    Any other text is not a control note.
    """
    parts = content.split()
    if len(parts) != 2:
        return None
    command, note_id = parts
    if command not in ("start", "stop"):
        return None
    return command, note_id.casefold()


def future_stop_author_notes(
    events: Sequence[ControlEvent],
    now_unix: int,
    stop_authors: Set[str],
) -> List[ControlEvent]:
    """Stop-author notes dated more than CLOCK_SKEW_SECONDS ahead of now.

    Callers log these and leave them out of the decision until a later poll,
    when `now_unix` has caught up. Nothing is discarded permanently.
    """
    stop_set = {normalize_hex(a) for a in stop_authors}
    return [
        ev
        for ev in events
        if ev.created_at > now_unix + CLOCK_SKEW_SECONDS
        and normalize_hex(ev.author) in stop_set
    ]


def decide_control(
    events: Sequence[ControlEvent],
    now_unix: int,
    stop_authors: Set[str],
    start_authors: Set[str],
) -> ControlCommand:
    """
    Pure fail-closed control decision. No I/O. Callers inject `now_unix`.

    A stop note is ``stop <id>`` from a stop author. A start note is
    ``start <id>`` from a start author. Anything else is ignored, including
    notes dated more than CLOCK_SKEW_SECONDS ahead of ``now_unix``.

    Returns \"start\" only when the set of stop ids is non-empty and equal
    to the set of start ids, and every stop note is cancelled by a start of
    the same id with ``start.created_at >= stop.created_at - CLOCK_SKEW_SECONDS``.
    A start timestamped earlier than that cannot pre-cancel a later stop.
    Otherwise returns \"stop\".
    """
    stop_set = {normalize_hex(a) for a in stop_authors}
    start_set = {normalize_hex(a) for a in start_authors}

    stops: List[tuple[str, ControlEvent]] = []
    starts: List[tuple[str, ControlEvent]] = []
    for ev in events:
        if ev.created_at > now_unix + CLOCK_SKEW_SECONDS:
            continue
        parsed = _command_and_id(ev.content)
        if parsed is None:
            continue
        command, note_id = parsed
        author = normalize_hex(ev.author)
        if command == "stop" and author in stop_set:
            stops.append((note_id, ev))
        elif command == "start" and author in start_set:
            starts.append((note_id, ev))

    stop_ids = {note_id for note_id, _ in stops}
    start_ids = {note_id for note_id, _ in starts}
    if not stop_ids or stop_ids != start_ids:
        return "stop"

    starts_by_id: dict[str, List[ControlEvent]] = {}
    for note_id, ev in starts:
        starts_by_id.setdefault(note_id, []).append(ev)

    for note_id, stop in stops:
        if not any(
            start.created_at >= stop.created_at - CLOCK_SKEW_SECONDS
            for start in starts_by_id.get(note_id, ())
        ):
            return "stop"
    return "start"


def control_event_from_nostr(event) -> ControlEvent:
    """Map a nostr_sdk Event into the pure ControlEvent shape."""
    return ControlEvent(
        id=normalize_hex(event.id().to_hex()),
        author=normalize_hex(event.author().to_hex()),
        content=event.content(),
        created_at=int(event.created_at().as_secs()),
    )


def _author_public_keys(authors: Iterable[str]) -> List[PublicKey]:
    return [PublicKey.from_hex(normalize_hex(a)) for a in authors]


async def _fetch_events_from_relay(
    relay: str,
    filt: Filter,
    timeout: timedelta,
) -> List:
    """Query a single relay; raises on failure."""
    client = Client(None)
    await client.add_relay(relay)
    await client.connect()
    try:
        return await client.get_events_of([filt], timeout)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def fetch_control_events(
    relays: Sequence[str],
    stop_authors: Set[str],
    start_authors: Set[str],
    *,
    timeout_seconds: float = 10.0,
) -> List[ControlEvent]:
    """
    Fetch kind-1 notes from every control relay and union by event id.

    Each relay is queried independently so one withholding relay cannot hide a
    stop seen on another. The events are unioned by id only when every
    configured relay answers. If any relay errors, raises RuntimeError and
    does not return the partial union (caller must treat that as stop). One
    live relay cannot outvote a halt the others failed to return. No caching.
    """
    if not relays:
        raise RuntimeError("no control relays configured")

    authors = _author_public_keys(set(stop_authors) | set(start_authors))
    filt = Filter().kinds([Kind(1)]).authors(authors)
    timeout = timedelta(seconds=timeout_seconds)

    by_id: dict[str, ControlEvent] = {}
    errors: List[str] = []
    successes = 0

    for relay in relays:
        try:
            raw_events = await _fetch_events_from_relay(relay, filt, timeout)
            successes += 1
            for raw in raw_events:
                ev = control_event_from_nostr(raw)
                by_id[ev.id] = ev
        except Exception as e:
            errors.append(f"{relay}: {e}")

    if successes != len(relays):
        detail = "; ".join(errors) if errors else "unknown errors"
        if successes == 0:
            raise RuntimeError(f"all control relays failed: {detail}")
        raise RuntimeError(
            f"control relay fetch incomplete ({successes}/{len(relays)} relays): {detail}"
        )

    return list(by_id.values())

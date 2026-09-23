"""Pure control-channel tests: no network, no nostr I/O."""

from __future__ import annotations

import pytest

from commands.control_channel import (
    CLOCK_SKEW_SECONDS,
    ControlEvent,
    decide_control,
    load_control_config,
)

STOP_AUTHOR = "11" * 32
START_AUTHOR = "22" * 32
OTHER_AUTHOR = "33" * 32

NOW = 1_700_000_000


def _ev(
    *,
    id: str,
    author: str,
    content: str,
    created_at: int,
    e_tags: tuple[str, ...] = (),
) -> ControlEvent:
    return ControlEvent(
        id=id,
        author=author,
        content=content,
        created_at=created_at,
        e_tag_ids=e_tags,
    )


def _nostr_config(stop=None, start=None, relays=None):
    return {
        "nostr": {
            "control_stop_authors": stop if stop is not None else [STOP_AUTHOR],
            "control_start_authors": start if start is not None else [START_AUTHOR],
            "control_relays": relays if relays is not None else ["wss://relay.example"],
        }
    }


class TestLoadControlConfig:
    def test_overlapping_authors_fail_load(self):
        overlap = "aa" * 32
        with pytest.raises(ValueError, match="overlap"):
            load_control_config(
                _nostr_config(stop=[STOP_AUTHOR, overlap], start=[START_AUTHOR, overlap])
            )

    def test_loads_disjoint_authors(self):
        cfg = load_control_config(_nostr_config())
        assert STOP_AUTHOR in cfg.stop_authors
        assert START_AUTHOR in cfg.start_authors


class TestDecideControl:
    def test_stop_key_start_ignored(self):
        events = [
            _ev(id="a1", author=STOP_AUTHOR, content="start", created_at=NOW - 10),
        ]
        assert (
            decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"
        )

    def test_start_key_stop_ignored(self):
        events = [
            _ev(id="b1", author=START_AUTHOR, content="stop", created_at=NOW - 10),
        ]
        # No valid stop and no valid start → stop
        assert (
            decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"
        )

    def test_start_without_tagging_latest_stop_does_not_resume(self):
        events = [
            _ev(id="stop1", author=STOP_AUTHOR, content="stop", created_at=NOW - 100),
            _ev(
                id="start1",
                author=START_AUTHOR,
                content="start",
                created_at=NOW - 50,
                e_tags=("deadbeef" + "00" * 28,),  # not stop1
            ),
        ]
        assert (
            decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"
        )

    def test_start_tagging_stop_resumes(self):
        stop_id = "ab" * 32
        events = [
            _ev(id=stop_id, author=STOP_AUTHOR, content="stop", created_at=NOW - 100),
            _ev(
                id="cd" * 32,
                author=START_AUTHOR,
                content="start",
                created_at=NOW - 50,
                e_tags=(stop_id,),
            ),
        ]
        assert (
            decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "start"
        )

    def test_future_dated_start_does_not_override_stop(self):
        stop_id = "ab" * 32
        events = [
            _ev(id=stop_id, author=STOP_AUTHOR, content="stop", created_at=NOW - 10),
            _ev(
                id="cd" * 32,
                author=START_AUTHOR,
                content="start",
                created_at=NOW + CLOCK_SKEW_SECONDS + 1,
                e_tags=(stop_id,),
            ),
        ]
        assert (
            decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"
        )

    def test_no_events_is_stop(self):
        assert decide_control([], NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

    def test_other_note_text_ignored(self):
        events = [
            _ev(id="x1", author=STOP_AUTHOR, content="halt", created_at=NOW - 20),
            _ev(id="x2", author=START_AUTHOR, content="go", created_at=NOW - 10),
            _ev(id="x3", author=OTHER_AUTHOR, content="stop", created_at=NOW - 5),
            _ev(id="x4", author=OTHER_AUTHOR, content="start", created_at=NOW - 1),
        ]
        assert (
            decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"
        )

    def test_only_valid_starts_without_stops_is_start(self):
        events = [
            _ev(id="s1", author=START_AUTHOR, content="start", created_at=NOW - 10),
        ]
        assert (
            decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "start"
        )

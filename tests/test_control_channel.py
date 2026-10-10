"""Control-channel decision, fetch, and publish tests. No live relays."""

from __future__ import annotations

import json
import logging

import pytest

from commands.control_channel import (
    CLOCK_SKEW_SECONDS,
    ControlConfig,
    ControlEvent,
    decide_control,
    fetch_control_events,
    future_stop_author_notes,
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
) -> ControlEvent:
    return ControlEvent(
        id=id,
        author=author,
        content=content,
        created_at=created_at,
    )


def _nostr_config(stop=None, start=None, relays=None):
    return {
        "nostr": {
            "control_stop_authors": stop if stop is not None else [STOP_AUTHOR],
            "control_start_authors": start if start is not None else [START_AUTHOR],
            "control_relays": relays if relays is not None else ["wss://relay.example"],
        }
    }


class _Hex:
    def __init__(self, value: str):
        self._value = value

    def to_hex(self) -> str:
        return self._value


class _Secs:
    def __init__(self, value: int):
        self._value = value

    def as_secs(self) -> int:
        return self._value


class _RawEvent:
    def __init__(self, id: str, author: str, content: str, created_at: int):
        self._id = id
        self._author = author
        self._content = content
        self._created_at = created_at

    def id(self):
        return _Hex(self._id)

    def author(self):
        return _Hex(self._author)

    def content(self) -> str:
        return self._content

    def created_at(self):
        return _Secs(self._created_at)


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
            _ev(id="a1", author=STOP_AUTHOR, content="start a", created_at=NOW - 10),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

    def test_start_key_stop_ignored(self):
        events = [
            _ev(id="b1", author=START_AUTHOR, content="stop a", created_at=NOW - 10),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

    def test_no_events_is_stop(self):
        assert decide_control([], NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

    def test_only_start_stays_stopped(self):
        events = [
            _ev(id="s1", author=START_AUTHOR, content="start a", created_at=NOW - 10),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

    def test_stop_without_start_stays_stopped(self):
        events = [
            _ev(id="s1", author=STOP_AUTHOR, content="stop a", created_at=NOW - 10),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

    def test_matching_stop_and_start_signs(self):
        events = [
            _ev(id="1", author=STOP_AUTHOR, content="stop AbC", created_at=NOW - 100),
            _ev(id="2", author=START_AUTHOR, content="start abc", created_at=NOW - 50),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "start"

    def test_extra_stop_id_stays_stopped(self):
        events = [
            _ev(id="1", author=STOP_AUTHOR, content="stop a", created_at=NOW - 100),
            _ev(id="2", author=START_AUTHOR, content="start a", created_at=NOW - 50),
            _ev(id="3", author=STOP_AUTHOR, content="stop b", created_at=NOW - 40),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

    def test_different_ids_stay_stopped(self):
        events = [
            _ev(id="1", author=STOP_AUTHOR, content="stop a", created_at=NOW - 100),
            _ev(id="2", author=START_AUTHOR, content="start b", created_at=NOW - 50),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

    def test_extra_start_id_stays_stopped(self):
        events = [
            _ev(id="1", author=STOP_AUTHOR, content="stop a", created_at=NOW - 100),
            _ev(id="2", author=START_AUTHOR, content="start a", created_at=NOW - 50),
            _ev(id="3", author=START_AUTHOR, content="start b", created_at=NOW - 40),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

    def test_start_timestamped_before_stop_does_not_cancel(self):
        stop_at = NOW - 10
        too_early = stop_at - CLOCK_SKEW_SECONDS - 1
        events = [
            _ev(id="1", author=STOP_AUTHOR, content="stop a", created_at=stop_at),
            _ev(id="2", author=START_AUTHOR, content="start a", created_at=too_early),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

        on_time = stop_at - CLOCK_SKEW_SECONDS
        cancelled = [
            _ev(id="1", author=STOP_AUTHOR, content="stop a", created_at=stop_at),
            _ev(id="2", author=START_AUTHOR, content="start a", created_at=on_time),
        ]
        assert decide_control(cancelled, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "start"

    def test_earlier_start_does_not_pre_cancel_a_later_stop(self):
        events = [
            _ev(id="1", author=STOP_AUTHOR, content="stop a", created_at=NOW - 300),
            _ev(id="2", author=START_AUTHOR, content="start a", created_at=NOW - 200),
            _ev(id="3", author=STOP_AUTHOR, content="stop a", created_at=NOW - 50),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

        resumed = events + [
            _ev(id="4", author=START_AUTHOR, content="start a", created_at=NOW - 40),
        ]
        assert decide_control(resumed, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "start"

    def test_future_dated_start_does_not_override_stop(self):
        events = [
            _ev(id="stop1", author=STOP_AUTHOR, content="stop a", created_at=NOW - 10),
            _ev(
                id="start1",
                author=START_AUTHOR,
                content="start a",
                created_at=NOW + CLOCK_SKEW_SECONDS + 1,
            ),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"

    def test_future_stop_is_used_once_the_clock_catches_up(self):
        created = NOW + CLOCK_SKEW_SECONDS + 30
        events = [
            _ev(id="s1", author=STOP_AUTHOR, content="stop a", created_at=created),
            _ev(id="t1", author=START_AUTHOR, content="start a", created_at=created),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"
        assert future_stop_author_notes(events, NOW, {STOP_AUTHOR}) == [events[0]]
        assert decide_control(events, created, {STOP_AUTHOR}, {START_AUTHOR}) == "start"
        assert future_stop_author_notes(events, created, {STOP_AUTHOR}) == []

    def test_other_note_text_ignored(self):
        events = [
            _ev(id="x1", author=STOP_AUTHOR, content="halt", created_at=NOW - 20),
            _ev(id="x2", author=START_AUTHOR, content="go", created_at=NOW - 10),
            _ev(id="x3", author=OTHER_AUTHOR, content="stop a", created_at=NOW - 5),
            _ev(id="x4", author=OTHER_AUTHOR, content="start a", created_at=NOW - 1),
        ]
        assert decide_control(events, NOW, {STOP_AUTHOR}, {START_AUTHOR}) == "stop"


def _valid_pubkeys() -> tuple[str, str]:
    from nostr_sdk import Keys

    return (
        Keys.generate().public_key().to_hex(),
        Keys.generate().public_key().to_hex(),
    )


class TestFetchControlEvents:
    @pytest.mark.asyncio
    async def test_partial_relay_failure_is_not_a_successful_fetch(self, monkeypatch):
        stop_author, start_author = _valid_pubkeys()

        async def fake_fetch(relay, filt, timeout):
            if "bad" in relay:
                raise RuntimeError("timeout")
            return [
                _RawEvent("aa" * 32, stop_author, "stop a", NOW - 100),
                _RawEvent("bb" * 32, start_author, "start a", NOW - 50),
            ]

        monkeypatch.setattr(
            "commands.control_channel._fetch_events_from_relay", fake_fetch
        )
        with pytest.raises(RuntimeError, match="incomplete"):
            await fetch_control_events(
                ["wss://good.example", "wss://bad.example"],
                {stop_author},
                {start_author},
            )

    @pytest.mark.asyncio
    async def test_every_relay_must_answer_before_union(self, monkeypatch):
        stop_author, start_author = _valid_pubkeys()

        async def fake_fetch(relay, filt, timeout):
            if relay.endswith("a"):
                return [_RawEvent("aa" * 32, stop_author, "stop a", NOW - 100)]
            return [
                _RawEvent("aa" * 32, stop_author, "stop a", NOW - 100),
                _RawEvent("bb" * 32, start_author, "start a", NOW - 50),
            ]

        monkeypatch.setattr(
            "commands.control_channel._fetch_events_from_relay", fake_fetch
        )
        events = await fetch_control_events(
            ["wss://relay-a", "wss://relay-b"],
            {stop_author},
            {start_author},
        )
        assert {ev.id for ev in events} == {"aa" * 32, "bb" * 32}


class TestPublish:
    @pytest.mark.asyncio
    async def test_zero_accepts_exits_nonzero(self, monkeypatch, capsys):
        from commands.control import _publish_to_relays

        class _Client:
            def __init__(self, signer):
                self.relay = ""

            async def add_relay(self, relay):
                self.relay = relay

            async def connect(self):
                return None

            async def send_event(self, event):
                raise RuntimeError("rejected")

            async def disconnect(self):
                return None

        monkeypatch.setattr("commands.control.Client", _Client)
        with pytest.raises(SystemExit) as exc:
            await _publish_to_relays(["wss://a.example", "wss://b.example"], object())
        assert exc.value.code not in (0, None)
        out = capsys.readouterr().out
        assert "ACCEPTED" not in out
        assert "FAILED" in out

    @pytest.mark.asyncio
    async def test_one_accept_exits_zero(self, monkeypatch, capsys):
        from commands.control import _publish_to_relays

        class _Eid:
            def to_hex(self):
                return "ab" * 32

        class _Client:
            def __init__(self, signer):
                self.relay = ""

            async def add_relay(self, relay):
                self.relay = relay

            async def connect(self):
                return None

            async def send_event(self, event):
                if "bad" in self.relay:
                    raise RuntimeError("rejected")
                return _Eid()

            async def disconnect(self):
                return None

        monkeypatch.setattr("commands.control.Client", _Client)
        await _publish_to_relays(["wss://bad.example", "wss://good.example"], object())
        out = capsys.readouterr().out
        assert "ACCEPTED wss://good.example" in out
        assert "REJECTED wss://bad.example" in out

    @pytest.mark.asyncio
    async def test_stop_and_start_publish_id_text_without_tags(self, monkeypatch):
        import commands.control as control_mod

        seen = {}

        def fake_text_note(content, tags):
            seen["content"] = content
            seen["tags"] = list(tags)

            class _Author:
                def to_hex(self):
                    return seen["pubkey"]

            class _Created:
                def as_secs(self):
                    return NOW

            class _Event:
                def content(self):
                    return content

                def author(self):
                    return _Author()

                def tags(self):
                    return []

                def created_at(self):
                    return _Created()

            class _Builder:
                def to_event(self, keys):
                    return _Event()

            return _Builder()

        class _Pub:
            def __init__(self, pubkey):
                self._pubkey = pubkey

            def to_hex(self):
                return self._pubkey

        class _Keys:
            def __init__(self, pubkey):
                self._pubkey = pubkey

            def public_key(self):
                return _Pub(self._pubkey)

        async def fake_publish(relays, event):
            seen["published"] = event.content()

        async def fail_fetch(*args, **kwargs):
            raise AssertionError("control publish must not query relays")

        monkeypatch.setattr(control_mod.EventBuilder, "text_note", fake_text_note)
        monkeypatch.setattr(control_mod, "_publish_to_relays", fake_publish)
        monkeypatch.setattr(
            "commands.control_channel.fetch_control_events", fail_fetch
        )
        monkeypatch.setattr(
            control_mod,
            "load_control_config",
            lambda: ControlConfig(
                stop_authors={STOP_AUTHOR},
                start_authors={START_AUTHOR},
                relays=["wss://relay.example"],
            ),
        )

        def unwrap(command):
            callback = command.callback
            while hasattr(callback, "__wrapped__"):
                callback = callback.__wrapped__
            return callback

        seen["pubkey"] = STOP_AUTHOR
        monkeypatch.setattr(control_mod, "_prompt_keys", lambda: _Keys(STOP_AUTHOR))
        await unwrap(control_mod.stop)("incident-7")
        assert seen["content"] == "stop incident-7"
        assert seen["tags"] == []
        assert seen["published"] == "stop incident-7"

        seen["pubkey"] = START_AUTHOR
        monkeypatch.setattr(control_mod, "_prompt_keys", lambda: _Keys(START_AUTHOR))
        await unwrap(control_mod.start_cmd)("incident-7")
        assert seen["content"] == "start incident-7"
        assert seen["tags"] == []
        assert seen["published"] == "start incident-7"


def _control_config():
    return ControlConfig(
        stop_authors={STOP_AUTHOR},
        start_authors={START_AUTHOR},
        relays=["wss://relay.example"],
    )


@pytest.fixture
def stops_file(tmp_path, monkeypatch):
    """Point the stops file at a temp path and reset process memory."""
    import commands.policy_runtime as policy_runtime

    path = tmp_path / "stops"
    monkeypatch.setattr(policy_runtime, "STOPS_FILE", path)
    saved_stops = dict(policy_runtime._seen_stops)
    saved_warned = policy_runtime._missing_stops_warned
    saved_command = policy_runtime._last_control_command
    policy_runtime._seen_stops.clear()
    policy_runtime._missing_stops_warned = False
    policy_runtime._last_control_command = None
    yield path
    policy_runtime._seen_stops.clear()
    policy_runtime._seen_stops.update(saved_stops)
    policy_runtime._missing_stops_warned = saved_warned
    policy_runtime._last_control_command = saved_command


def _install_fetch(monkeypatch, responses):
    import commands.policy_runtime as policy_runtime

    pending = list(responses)

    async def fake_fetch(*args, **kwargs):
        nxt = pending.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(policy_runtime, "fetch_control_events", fake_fetch)
    monkeypatch.setattr(policy_runtime.time, "time", lambda: NOW)


@pytest.mark.asyncio
async def test_future_stop_author_drop_is_logged(monkeypatch, caplog, stops_file):
    from commands.policy_runtime import control_allows_start

    created = NOW + CLOCK_SKEW_SECONDS + 5
    events = [
        _ev(id="futurestop", author=STOP_AUTHOR, content="stop a", created_at=created),
        _ev(id="starta", author=START_AUTHOR, content="start a", created_at=NOW),
    ]
    _install_fetch(monkeypatch, [events])
    stops_file.write_text("")
    with caplog.at_level(logging.WARNING):
        assert await control_allows_start(_control_config()) is False
    assert "stop a" in caplog.text
    assert "clock catches up" in caplog.text
    assert "futurestop" in json.loads(stops_file.read_text())


@pytest.mark.asyncio
async def test_missing_stops_file_warns_and_stays_in_memory(monkeypatch, caplog, stops_file):
    from commands.policy_runtime import control_allows_start

    assert not stops_file.exists()
    old_stop = _ev(id="old-stop", author=STOP_AUTHOR, content="stop a", created_at=NOW - 100)
    old_start = _ev(id="old-start", author=START_AUTHOR, content="start a", created_at=NOW - 50)
    new_stop = _ev(id="new-stop", author=STOP_AUTHOR, content="stop b", created_at=NOW - 40)
    _install_fetch(
        monkeypatch,
        [
            [old_stop, old_start, new_stop],
            [old_stop, old_start],
        ],
    )
    with caplog.at_level(logging.WARNING):
        assert await control_allows_start(_control_config()) is False
        assert await control_allows_start(_control_config()) is False
    assert caplog.text.count(
        "WARNING: Could not find the 'stops' file in the app's folder; please create an empty one"
    ) == 1
    assert not stops_file.exists()


@pytest.mark.asyncio
async def test_seen_stops_survive_a_relay_that_omits_them(monkeypatch, stops_file):
    import commands.policy_runtime as policy_runtime
    from commands.policy_runtime import control_allows_start

    stops_file.write_text("")
    old_stop = _ev(id="old-stop", author=STOP_AUTHOR, content="stop a", created_at=NOW - 100)
    old_start = _ev(id="old-start", author=START_AUTHOR, content="start a", created_at=NOW - 50)
    new_stop = _ev(id="new-stop", author=STOP_AUTHOR, content="stop b", created_at=NOW - 40)
    new_start = _ev(id="new-start", author=START_AUTHOR, content="start b", created_at=NOW - 30)
    _install_fetch(
        monkeypatch,
        [
            [old_stop, old_start],
            [old_stop, old_start, new_stop],
            [old_stop, old_start],
            [old_stop, old_start, new_stop, new_start],
        ],
    )
    cfg = _control_config()
    assert await control_allows_start(cfg) is True
    assert set(json.loads(stops_file.read_text())) == {"old-stop"}

    assert await control_allows_start(cfg) is False
    saved = json.loads(stops_file.read_text())
    assert set(saved) == {"old-stop", "new-stop"}
    assert saved["new-stop"]["content"] == "stop b"
    assert "old-start" not in saved

    policy_runtime._seen_stops.clear()
    assert await control_allows_start(cfg) is False
    assert await control_allows_start(cfg) is True
    assert set(json.loads(stops_file.read_text())) == {"old-stop", "new-stop"}


@pytest.mark.asyncio
async def test_failed_fetch_does_not_change_stops_file(monkeypatch, stops_file):
    import commands.policy_runtime as policy_runtime
    from commands.policy_runtime import control_allows_start

    stops_file.write_text("")
    old_stop = _ev(id="old-stop", author=STOP_AUTHOR, content="stop a", created_at=NOW - 100)
    old_start = _ev(id="old-start", author=START_AUTHOR, content="start a", created_at=NOW - 50)
    new_stop = _ev(id="new-stop", author=STOP_AUTHOR, content="stop b", created_at=NOW - 40)
    _install_fetch(
        monkeypatch,
        [
            [old_stop, old_start, new_stop],
            RuntimeError("timeout"),
            [old_stop, old_start],
        ],
    )
    cfg = _control_config()
    assert await control_allows_start(cfg) is False
    saved = stops_file.read_text()
    policy_runtime._seen_stops.clear()
    assert await control_allows_start(cfg) is False
    assert stops_file.read_text() == saved
    assert await control_allows_start(cfg) is False


@pytest.mark.asyncio
async def test_corrupt_stops_file_stays_halted(monkeypatch, caplog, stops_file):
    from commands.policy_runtime import control_allows_start

    stops_file.write_text("{")
    old_stop = _ev(id="old-stop", author=STOP_AUTHOR, content="stop a", created_at=NOW - 100)
    old_start = _ev(id="old-start", author=START_AUTHOR, content="start a", created_at=NOW - 50)
    _install_fetch(monkeypatch, [[old_stop, old_start]])
    with caplog.at_level(logging.ERROR):
        assert await control_allows_start(_control_config()) is False
    assert stops_file.read_text() == "{"
    assert "not a JSON object of stops" in caplog.text


@pytest.mark.asyncio
async def test_non_utf8_stops_file_stays_halted(monkeypatch, caplog, stops_file):
    from commands.policy_runtime import control_allows_start

    stops_file.write_bytes(b"\xff")
    old_stop = _ev(id="old-stop", author=STOP_AUTHOR, content="stop a", created_at=NOW - 100)
    old_start = _ev(id="old-start", author=START_AUTHOR, content="start a", created_at=NOW - 50)
    _install_fetch(monkeypatch, [[old_stop, old_start]])
    with caplog.at_level(logging.ERROR):
        assert await control_allows_start(_control_config()) is False
    assert stops_file.read_bytes() == b"\xff"
    assert "not a JSON object of stops" in caplog.text


@pytest.mark.asyncio
async def test_failed_stops_write_stays_halted(monkeypatch, stops_file):
    import commands.policy_runtime as policy_runtime
    from commands.policy_runtime import control_allows_start

    stops_file.write_text("")
    old_stop = _ev(id="old-stop", author=STOP_AUTHOR, content="stop a", created_at=NOW - 100)
    old_start = _ev(id="old-start", author=START_AUTHOR, content="start a", created_at=NOW - 50)
    new_stop = _ev(id="new-stop", author=STOP_AUTHOR, content="stop b", created_at=NOW - 40)
    _install_fetch(
        monkeypatch,
        [
            [old_stop, old_start, new_stop],
            [old_stop, old_start],
        ],
    )

    original_write = policy_runtime._write_stops_file

    def fail_write(path, stops):
        raise OSError("read-only")

    monkeypatch.setattr(policy_runtime, "_write_stops_file", fail_write)
    cfg = _control_config()
    assert await control_allows_start(cfg) is False
    assert stops_file.read_text() == ""

    monkeypatch.setattr(policy_runtime, "_write_stops_file", original_write)
    assert await control_allows_start(cfg) is False
    assert "new-stop" in json.loads(stops_file.read_text())

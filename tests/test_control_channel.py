"""Control-channel decision, fetch, and publish tests. No live relays."""

from __future__ import annotations

import asyncio
import io
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
        assert cfg.relay_quorum == 5

    def test_relay_quorum_override(self):
        raw = _nostr_config()
        raw["nostr"]["control_relay_quorum"] = 3
        assert load_control_config(raw).relay_quorum == 3

    @pytest.mark.parametrize("quorum", [0, -1, True, False, "5", 1.5])
    def test_relay_quorum_must_be_a_positive_integer(self, quorum):
        raw = _nostr_config()
        raw["nostr"]["control_relay_quorum"] = quorum
        with pytest.raises(ValueError, match="control_relay_quorum"):
            load_control_config(raw)


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

    @pytest.mark.asyncio
    async def test_quorum_allows_some_relays_to_fail(self, monkeypatch):
        stop_author, start_author = _valid_pubkeys()

        async def fake_fetch(relay, filt, timeout):
            if relay.endswith("bad"):
                raise RuntimeError("timeout")
            index = relay[-1]
            return [_RawEvent(index * 64, stop_author, "stop a", NOW - 100)]

        monkeypatch.setattr(
            "commands.control_channel._fetch_events_from_relay", fake_fetch
        )
        relays = [f"wss://relay-{i}" for i in "abcde"] + ["wss://relay-bad"]
        events = await fetch_control_events(
            relays,
            {stop_author},
            {start_author},
            relay_quorum=5,
        )
        assert {ev.id for ev in events} == {i * 64 for i in "abcde"}

    @pytest.mark.asyncio
    async def test_below_quorum_is_incomplete(self, monkeypatch):
        stop_author, start_author = _valid_pubkeys()

        async def fake_fetch(relay, filt, timeout):
            if relay[-1] in "ef":
                raise RuntimeError("timeout")
            return [_RawEvent("aa" * 32, stop_author, "stop a", NOW - 100)]

        monkeypatch.setattr(
            "commands.control_channel._fetch_events_from_relay", fake_fetch
        )
        relays = [f"wss://relay-{i}" for i in "abcdef"]
        with pytest.raises(RuntimeError, match="incomplete"):
            await fetch_control_events(
                relays,
                {stop_author},
                {start_author},
                relay_quorum=5,
            )


class _Hex:
    def __init__(self, value):
        self._value = value

    def to_hex(self):
        return self._value


class _Secs:
    def __init__(self, value):
        self._value = value

    def as_secs(self):
        return self._value


class _KindValue:
    def __init__(self, value):
        self._value = value

    def as_u16(self):
        return self._value


class _FakeControlEvent:
    def __init__(
        self,
        *,
        author,
        content,
        kind=1,
        tags=None,
        verified=True,
        verify_raises=False,
        created_at=NOW,
    ):
        self._author = author
        self._content = content
        self._kind = kind
        self._tags = [] if tags is None else tags
        self._verified = verified
        self._verify_raises = verify_raises
        self._created_at = created_at

    def content(self):
        return self._content

    def author(self):
        return _Hex(self._author)

    def tags(self):
        return self._tags

    def created_at(self):
        return _Secs(self._created_at)

    def kind(self):
        return _KindValue(self._kind)

    def verify(self):
        if self._verify_raises:
            raise RuntimeError("bad signature")
        return self._verified


def _event_json(**overrides) -> str:
    payload = {
        "author": STOP_AUTHOR,
        "content": "stop incident-7",
        "kind": 1,
        "tags": [],
        "created_at": NOW,
        "verified": True,
    }
    payload.update(overrides)
    return json.dumps(payload)


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
    async def test_relays_are_published_concurrently(self, monkeypatch, capsys):
        from commands.control import _publish_to_relays

        # Both sends must be inside send_event together. Sequential publish
        # never starts the second send, so the barrier waits until the test
        # timeout fails it.
        barrier = asyncio.Barrier(2)
        event_hex = "ab" * 32

        class _Eid:
            def to_hex(self):
                return event_hex

        class _Client:
            def __init__(self, signer):
                self.relay = ""

            async def add_relay(self, relay):
                self.relay = relay

            async def connect(self):
                return None

            async def send_event(self, event):
                await barrier.wait()
                return _Eid()

            async def disconnect(self):
                return None

        monkeypatch.setattr("commands.control.Client", _Client)
        await asyncio.wait_for(
            _publish_to_relays(
                ["wss://a.example", "wss://b.example"], object()
            ),
            timeout=1,
        )
        out = capsys.readouterr().out
        assert out.splitlines() == [
            f"ACCEPTED wss://a.example {event_hex}",
            f"ACCEPTED wss://b.example {event_hex}",
        ]

    @pytest.mark.asyncio
    async def test_hung_relay_times_out_other_is_accepted(
        self, monkeypatch, capsys
    ):
        from commands.control import _publish_to_relays

        event_hex = "cd" * 32
        monkeypatch.setattr("commands.control.PUBLISH_TIMEOUT_SECONDS", 0.05)

        class _Eid:
            def to_hex(self):
                return event_hex

        class _Client:
            def __init__(self, signer):
                self.relay = ""

            async def add_relay(self, relay):
                self.relay = relay

            async def connect(self):
                return None

            async def send_event(self, event):
                if "hung" in self.relay:
                    await asyncio.Event().wait()
                return _Eid()

            async def disconnect(self):
                return None

        monkeypatch.setattr("commands.control.Client", _Client)
        await asyncio.wait_for(
            _publish_to_relays(
                ["wss://hung.example", "wss://good.example"], object()
            ),
            timeout=1,
        )
        out = capsys.readouterr().out
        assert out.splitlines() == [
            "REJECTED wss://hung.example timeout",
            f"ACCEPTED wss://good.example {event_hex}",
        ]

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

    def _install_sign_fakes(self, monkeypatch, pubkey: str):
        import commands.control as control_mod

        seen = {"signed": False, "published": False, "pubkey": pubkey}

        def fake_text_note(content, tags):
            seen["signed"] = True
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

                def as_json(self):
                    return json.dumps({"content": content, "tags": []})

            class _Builder:
                def to_event(self, keys):
                    return _Event()

            return _Builder()

        class _Pub:
            def __init__(self, value):
                self._value = value

            def to_hex(self):
                return self._value

        class _Keys:
            def public_key(self):
                return _Pub(pubkey)

        def fail_publish(*args, **kwargs):
            seen["published"] = True
            raise AssertionError("sign must not publish")

        class _Client:
            def __init__(self, *args, **kwargs):
                raise AssertionError("sign must not construct a Client")

        monkeypatch.setattr(control_mod.EventBuilder, "text_note", fake_text_note)
        monkeypatch.setattr(control_mod, "_publish_to_relays", fail_publish)
        monkeypatch.setattr(control_mod, "Client", _Client)
        monkeypatch.setattr(control_mod, "_prompt_keys", lambda: _Keys())
        monkeypatch.setattr(
            control_mod,
            "load_control_config",
            lambda: ControlConfig(
                stop_authors={STOP_AUTHOR},
                start_authors={START_AUTHOR},
                relays=["wss://relay.example"],
            ),
        )
        return control_mod, seen

    @staticmethod
    def _unwrap(command):
        callback = command.callback
        while hasattr(callback, "__wrapped__"):
            callback = callback.__wrapped__
        return callback

    def test_sign_stop_and_sign_start_print_json_without_publishing(
        self, monkeypatch, capsys
    ):
        control_mod, seen = self._install_sign_fakes(monkeypatch, STOP_AUTHOR)
        note_id = "Incident-7"

        self._unwrap(control_mod.sign_stop)(note_id)
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload == {"content": f"stop {note_id}", "tags": []}
        assert captured.out == json.dumps(payload) + "\n"
        assert captured.err.splitlines() == [
            f"content: stop {note_id}",
            f"author: {STOP_AUTHOR}",
            "tags: []",
            f"created_at: {NOW}",
        ]
        assert "content:" not in captured.out
        assert seen["content"] == f"stop {note_id}"
        assert seen["tags"] == []
        assert seen["published"] is False

        control_mod, seen = self._install_sign_fakes(monkeypatch, START_AUTHOR)
        self._unwrap(control_mod.sign_start)(note_id)
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload == {"content": f"start {note_id}", "tags": []}
        assert captured.out == json.dumps(payload) + "\n"
        assert captured.err.splitlines() == [
            f"content: start {note_id}",
            f"author: {START_AUTHOR}",
            "tags: []",
            f"created_at: {NOW}",
        ]
        assert "content:" not in captured.out
        assert seen["content"] == f"start {note_id}"
        assert seen["tags"] == []
        assert seen["published"] is False

    @pytest.mark.parametrize(
        ("command_name", "verb", "author"),
        [
            ("sign-stop", "stop", STOP_AUTHOR),
            ("sign-start", "start", START_AUTHOR),
        ],
    )
    def test_sign_mnemonic_prompt_goes_to_stderr(
        self, monkeypatch, command_name, verb, author
    ):
        from click.testing import CliRunner

        import commands.control as control_mod

        seen = {"published": False, "pubkey": author, "mnemonic": None}

        def fake_text_note(content, tags):
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

                def as_json(self):
                    return json.dumps({"content": content, "tags": []})

            class _Builder:
                def to_event(self, keys):
                    return _Event()

            return _Builder()

        class _Pub:
            def to_hex(self):
                return seen["pubkey"]

        class _Keys:
            def public_key(self):
                return _Pub()

        def fake_from_mnemonic(mnemonic, passphrase):
            seen["mnemonic"] = mnemonic
            return _Keys()

        def fail_publish(*args, **kwargs):
            seen["published"] = True
            raise AssertionError("sign must not publish")

        monkeypatch.setattr(control_mod.EventBuilder, "text_note", fake_text_note)
        monkeypatch.setattr(
            control_mod.Keys, "from_mnemonic", staticmethod(fake_from_mnemonic)
        )
        monkeypatch.setattr(control_mod, "_publish_to_relays", fail_publish)
        monkeypatch.setattr(
            control_mod,
            "load_control_config",
            lambda: ControlConfig(
                stop_authors={STOP_AUTHOR},
                start_authors={START_AUTHOR},
                relays=["wss://relay.example"],
            ),
        )

        note_id = "Incident-7"
        mnemonic = "test mnemonic words"
        result = CliRunner(mix_stderr=False).invoke(
            control_mod.control,
            [command_name, note_id],
            input=f"{mnemonic}\n",
        )
        assert result.exit_code == 0
        stdout = result.stdout.strip()
        assert "Nostr mnemonic" not in stdout
        assert json.loads(stdout) == {
            "content": f"{verb} {note_id}",
            "tags": [],
        }
        assert "Nostr mnemonic" in result.stderr
        assert seen["published"] is False
        assert seen["mnemonic"] == mnemonic

    def test_sign_refuses_pubkey_outside_author_list(self, monkeypatch, capsys):
        control_mod, seen = self._install_sign_fakes(monkeypatch, OTHER_AUTHOR)

        with pytest.raises(SystemExit) as exc:
            self._unwrap(control_mod.sign_stop)("incident-7")
        assert exc.value.code not in (0, None)
        captured = capsys.readouterr()
        assert (
            captured.out
            == f"Refused: pubkey {OTHER_AUTHOR} is not in control_stop_authors\n"
        )
        assert seen["signed"] is False
        assert seen["published"] is False

        with pytest.raises(SystemExit) as exc:
            self._unwrap(control_mod.sign_start)("incident-7")
        assert exc.value.code not in (0, None)
        captured = capsys.readouterr()
        assert (
            captured.out
            == f"Refused: pubkey {OTHER_AUTHOR} is not in control_start_authors\n"
        )
        assert seen["signed"] is False
        assert seen["published"] is False

    @pytest.mark.parametrize("note_id", ["", " ", "bad id", "id\n"])
    @pytest.mark.parametrize(
        "command_name",
        ["sign_stop", "sign_start"],
    )
    def test_sign_refuses_empty_or_whitespace_id(
        self, monkeypatch, capsys, note_id, command_name
    ):
        control_mod, seen = self._install_sign_fakes(monkeypatch, STOP_AUTHOR)

        def fail_prompt():
            raise AssertionError("sign must not prompt for a bad id")

        monkeypatch.setattr(control_mod, "_prompt_keys", fail_prompt)
        with pytest.raises(SystemExit) as exc:
            self._unwrap(getattr(control_mod, command_name))(note_id)
        assert exc.value.code not in (0, None)
        captured = capsys.readouterr()
        assert captured.out == "Refused: id must be a single token\n"
        assert seen["signed"] is False
        assert seen["published"] is False

    def _install_publish_recorder(
        self, monkeypatch, *, stop_authors, start_authors=None
    ):
        import commands.control as control_mod

        seen = {"published": False}

        async def fake_publish(relays, event):
            seen["published"] = True
            seen["relays"] = list(relays)
            seen["published_event"] = event

        async def fail_fetch(*args, **kwargs):
            raise AssertionError("control publish must not query relays")

        def fail_prompt():
            raise AssertionError("publish-signed must not prompt for a mnemonic")

        def fail_text_note(*args, **kwargs):
            raise AssertionError("publish-signed must not re-sign")

        class _Client:
            def __init__(self, *args, **kwargs):
                raise AssertionError("publish-signed must not open a relay client")

        monkeypatch.setattr(control_mod, "_publish_to_relays", fake_publish)
        monkeypatch.setattr(control_mod, "_prompt_keys", fail_prompt)
        monkeypatch.setattr(control_mod.EventBuilder, "text_note", fail_text_note)
        monkeypatch.setattr(control_mod, "Client", _Client)
        monkeypatch.setattr(
            "commands.control_channel.fetch_control_events", fail_fetch
        )
        monkeypatch.setattr(
            control_mod,
            "load_control_config",
            lambda: ControlConfig(
                stop_authors=set(stop_authors),
                start_authors=set(
                    start_authors if start_authors is not None else {START_AUTHOR}
                ),
                relays=["wss://relay.example"],
            ),
        )
        return control_mod, seen

    def _install_fake_signed_event(self, monkeypatch):
        control_mod, seen = self._install_publish_recorder(
            monkeypatch,
            stop_authors={STOP_AUTHOR},
            start_authors={START_AUTHOR},
        )

        def fake_from_json(raw):
            seen["raw"] = raw
            data = json.loads(raw)
            if data.get("parse_error"):
                raise ValueError("structurally invalid event")
            event = _FakeControlEvent(
                author=data["author"],
                content=data["content"],
                kind=data.get("kind", 1),
                tags=data.get("tags", []),
                verified=data.get("verified", True),
                verify_raises=data.get("verify_raises", False),
                created_at=data.get("created_at", NOW),
            )
            seen["event"] = event
            return event

        monkeypatch.setattr(control_mod.Event, "from_json", fake_from_json)
        return control_mod, seen

    @pytest.mark.asyncio
    async def test_publish_signed_stop_publishes_valid_event(
        self, monkeypatch, tmp_path, capsys
    ):
        control_mod, seen = self._install_fake_signed_event(monkeypatch)
        payload = _event_json()
        path = tmp_path / "stop.json"
        path.write_text(payload, encoding="utf-8")

        await self._unwrap(control_mod.publish_signed_stop)(str(path))

        assert seen["raw"] == path.read_text(encoding="utf-8")
        assert seen["published"] is True
        assert seen["published_event"] is seen["event"]
        assert seen["relays"] == ["wss://relay.example"]
        assert seen["published_event"].content() == "stop incident-7"
        assert capsys.readouterr().out.splitlines() == [
            "content: stop incident-7",
            f"author: {STOP_AUTHOR}",
            "tags: []",
            f"created_at: {NOW}",
        ]

    @pytest.mark.asyncio
    async def test_publish_signed_start_publishes_valid_event(
        self, monkeypatch, tmp_path, capsys
    ):
        control_mod, seen = self._install_fake_signed_event(monkeypatch)
        payload = _event_json(
            author=START_AUTHOR,
            content="start incident-7",
        )
        path = tmp_path / "start.json"
        path.write_text(payload, encoding="utf-8")

        await self._unwrap(control_mod.publish_signed_start)(str(path))

        assert seen["raw"] == payload
        assert seen["published"] is True
        assert seen["published_event"] is seen["event"]
        assert seen["relays"] == ["wss://relay.example"]
        assert seen["published_event"].content() == "start incident-7"
        assert capsys.readouterr().out.splitlines() == [
            "content: start incident-7",
            f"author: {START_AUTHOR}",
            "tags: []",
            f"created_at: {NOW}",
        ]

    @pytest.mark.asyncio
    async def test_publish_signed_reads_stdin(
        self, monkeypatch, capsys
    ):
        control_mod, seen = self._install_fake_signed_event(monkeypatch)
        payload = _event_json()
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        await self._unwrap(control_mod.publish_signed_stop)("-")

        assert seen["raw"] == payload
        assert seen["published"] is True
        assert seen["published_event"].content() == "stop incident-7"
        assert "content: stop incident-7" in capsys.readouterr().out

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verify_raises", [False, True])
    async def test_publish_signed_refuses_bad_signature(
        self, monkeypatch, tmp_path, capsys, verify_raises
    ):
        control_mod, seen = self._install_fake_signed_event(monkeypatch)
        path = tmp_path / "bad-sig.json"
        path.write_text(
            _event_json(verified=False, verify_raises=verify_raises),
            encoding="utf-8",
        )

        with pytest.raises(SystemExit) as exc:
            await self._unwrap(control_mod.publish_signed_stop)(str(path))
        assert exc.value.code not in (0, None)
        assert seen["published"] is False
        assert (
            capsys.readouterr().out
            == "Refused: signature or id did not verify\n"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content",
        [
            "start incident-7",
            "stop",
            "stop incident-7 extra",
            "STOP incident-7",
        ],
    )
    async def test_publish_signed_stop_refuses_non_stop_content(
        self, monkeypatch, tmp_path, capsys, content
    ):
        control_mod, seen = self._install_fake_signed_event(monkeypatch)
        path = tmp_path / "content.json"
        path.write_text(_event_json(content=content), encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            await self._unwrap(control_mod.publish_signed_stop)(str(path))
        assert exc.value.code not in (0, None)
        assert seen["published"] is False
        assert (
            capsys.readouterr().out
            == "Refused: content must be exactly 'stop <id>'\n"
        )

    @pytest.mark.asyncio
    async def test_publish_signed_refuses_non_empty_tags(
        self, monkeypatch, tmp_path, capsys
    ):
        control_mod, seen = self._install_fake_signed_event(monkeypatch)
        path = tmp_path / "tagged.json"
        path.write_text(
            _event_json(tags=[["t", "nope"]]),
            encoding="utf-8",
        )

        with pytest.raises(SystemExit) as exc:
            await self._unwrap(control_mod.publish_signed_stop)(str(path))
        assert exc.value.code not in (0, None)
        assert seen["published"] is False
        assert capsys.readouterr().out == "Refused: tags must be empty\n"

    @pytest.mark.asyncio
    async def test_publish_signed_refuses_kind_other_than_1(
        self, monkeypatch, tmp_path, capsys
    ):
        control_mod, seen = self._install_fake_signed_event(monkeypatch)
        path = tmp_path / "kind.json"
        path.write_text(_event_json(kind=0), encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            await self._unwrap(control_mod.publish_signed_stop)(str(path))
        assert exc.value.code not in (0, None)
        assert seen["published"] is False
        assert capsys.readouterr().out == "Refused: kind must be 1\n"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "command_name,content,author_list",
        [
            ("publish_signed_stop", "stop incident-7", "control_stop_authors"),
            ("publish_signed_start", "start incident-7", "control_start_authors"),
        ],
    )
    async def test_publish_signed_refuses_author_outside_list(
        self, monkeypatch, tmp_path, capsys, command_name, content, author_list
    ):
        control_mod, seen = self._install_fake_signed_event(monkeypatch)
        path = tmp_path / "author.json"
        path.write_text(
            _event_json(author=OTHER_AUTHOR, content=content),
            encoding="utf-8",
        )

        with pytest.raises(SystemExit) as exc:
            await self._unwrap(getattr(control_mod, command_name))(str(path))
        assert exc.value.code not in (0, None)
        assert seen["published"] is False
        assert (
            capsys.readouterr().out
            == f"Refused: pubkey {OTHER_AUTHOR} is not in {author_list}\n"
        )

    @pytest.mark.asyncio
    async def test_publish_signed_stop_round_trips_real_event(
        self, monkeypatch, tmp_path, capsys
    ):
        from nostr_sdk import EventBuilder, Keys

        import commands.control as control_mod

        keys = Keys.generate()
        pubkey = control_mod.normalize_hex(keys.public_key().to_hex())
        signed = EventBuilder.text_note("stop incident-7", []).to_event(keys)
        path = tmp_path / "real-stop.json"
        path.write_text(signed.as_json(), encoding="utf-8")
        control_mod, seen = self._install_publish_recorder(
            monkeypatch, stop_authors={pubkey}
        )

        await self._unwrap(control_mod.publish_signed_stop)(str(path))

        event = seen["published_event"]
        assert seen["published"] is True
        assert event.content() == "stop incident-7"
        assert list(event.tags()) == []
        assert event.kind().as_u16() == 1
        assert control_mod.normalize_hex(event.author().to_hex()) == pubkey
        assert event.verify() is True
        assert seen["relays"] == ["wss://relay.example"]
        assert capsys.readouterr().out.splitlines()[:3] == [
            "content: stop incident-7",
            f"author: {keys.public_key().to_hex()}",
            "tags: []",
        ]

    @pytest.mark.asyncio
    async def test_publish_signed_start_round_trips_real_event(
        self, monkeypatch, tmp_path, capsys
    ):
        from nostr_sdk import EventBuilder, Keys

        import commands.control as control_mod

        keys = Keys.generate()
        pubkey = control_mod.normalize_hex(keys.public_key().to_hex())
        signed = EventBuilder.text_note("start incident-7", []).to_event(keys)
        path = tmp_path / "real-start.json"
        path.write_text(signed.as_json(), encoding="utf-8")
        control_mod, seen = self._install_publish_recorder(
            monkeypatch,
            stop_authors={STOP_AUTHOR},
            start_authors={pubkey},
        )

        await self._unwrap(control_mod.publish_signed_start)(str(path))

        event = seen["published_event"]
        assert event.content() == "start incident-7"
        assert list(event.tags()) == []
        assert event.kind().as_u16() == 1
        assert event.verify() is True
        assert seen["relays"] == ["wss://relay.example"]
        assert "content: start incident-7" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_publish_signed_refuses_tampered_real_event(
        self, monkeypatch, tmp_path, capsys
    ):
        from nostr_sdk import EventBuilder, Keys

        import commands.control as control_mod

        keys = Keys.generate()
        pubkey = control_mod.normalize_hex(keys.public_key().to_hex())
        signed = EventBuilder.text_note("stop incident-7", []).to_event(keys)
        data = json.loads(signed.as_json())
        data["content"] = "stop incident-8"
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        control_mod, seen = self._install_publish_recorder(
            monkeypatch, stop_authors={pubkey}
        )

        with pytest.raises(SystemExit) as exc:
            await self._unwrap(control_mod.publish_signed_stop)(str(path))
        assert exc.value.code not in (0, None)
        assert seen["published"] is False
        assert (
            capsys.readouterr().out
            == "Refused: signature or id did not verify\n"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["{", "{}", ""])
    async def test_publish_signed_refuses_invalid_event_json(
        self, monkeypatch, tmp_path, capsys, text
    ):
        control_mod, seen = self._install_publish_recorder(
            monkeypatch, stop_authors={STOP_AUTHOR}
        )
        path = tmp_path / "bad.json"
        path.write_text(text, encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            await self._unwrap(control_mod.publish_signed_stop)(str(path))
        assert exc.value.code not in (0, None)
        assert seen["published"] is False
        assert capsys.readouterr().out == "Refused: invalid event JSON\n"

    @pytest.mark.asyncio
    async def test_publish_signed_missing_file_is_refused(
        self, monkeypatch, tmp_path, capsys
    ):
        control_mod, seen = self._install_publish_recorder(
            monkeypatch, stop_authors={STOP_AUTHOR}
        )
        missing = tmp_path / "missing.json"

        with pytest.raises(SystemExit) as exc:
            await self._unwrap(control_mod.publish_signed_stop)(str(missing))
        assert exc.value.code not in (0, None)
        assert seen["published"] is False
        assert capsys.readouterr().out.startswith("Refused:")


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

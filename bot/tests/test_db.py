#!/usr/bin/env python3
"""Tests for db/ — SQLite CRUD operations."""

from db.challenges import (
    get_all,
    get_by_thread,
    get_existing_ctfd_ids,
    get_unsolved,
    mark_solved,
    upsert_challenge,
)
from db.conversations import add_message, clear_history, get_history
from db.ctfs import get_active_ctf, upsert_ctf


class TestCtfs:
    def test_upsert_and_get(self, db_conn):
        ctf_id = upsert_ctf(db_conn, "test", "https://ctf.test", "guild1", "chan1")
        assert ctf_id > 0

        ctf = get_active_ctf(db_conn, "guild1")
        assert ctf is not None
        assert ctf.name == "test"
        assert ctf.url == "https://ctf.test"
        assert ctf.active is True

    def test_deactivates_previous(self, db_conn):
        upsert_ctf(db_conn, "first", "https://first", "guild1", "chan1")
        id2 = upsert_ctf(db_conn, "second", "https://second", "guild1", "chan2")

        active = get_active_ctf(db_conn, "guild1")
        assert active is not None
        assert active.id == id2
        assert active.name == "second"

    def test_no_active_ctf(self, db_conn):
        assert get_active_ctf(db_conn, "nonexistent") is None


class TestChallenges:
    def test_upsert_and_get_by_thread(self, db_conn):
        ctf_id = upsert_ctf(db_conn, "test", "https://test", "g1", "c1")
        upsert_challenge(
            db_conn,
            ctf_id,
            42,
            "RSA 101",
            "rsa-101",
            "crypto",
            100,
            "Solve RSA",
            "thread_123",
            "/tmp/chall",
        )

        record = get_by_thread(db_conn, "thread_123")
        assert record is not None
        assert record.name == "RSA 101"
        assert record.points == 100
        assert record.solved is False

    def test_get_by_thread_not_found(self, db_conn):
        assert get_by_thread(db_conn, "nonexistent") is None

    def test_upsert_preserves_thread_id(self, db_conn):
        ctf_id = upsert_ctf(db_conn, "test", "https://test", "g1", "c1")
        upsert_challenge(
            db_conn,
            ctf_id,
            42,
            "RSA",
            "rsa",
            "crypto",
            100,
            thread_id="thread_1",
            challenge_dir="/dir1",
        )
        # Upsert again without thread_id
        upsert_challenge(db_conn, ctf_id, 42, "RSA Updated", "rsa", "crypto", 100)

        record = get_by_thread(db_conn, "thread_1")
        assert record is not None
        assert record.thread_id == "thread_1"

    def test_mark_solved(self, db_conn):
        ctf_id = upsert_ctf(db_conn, "test", "https://test", "g1", "c1")
        cid = upsert_challenge(
            db_conn, ctf_id, 42, "RSA", "rsa", "crypto", 100, thread_id="t1"
        )
        mark_solved(db_conn, cid, "picoCTF{flag}")

        record = get_by_thread(db_conn, "t1")
        assert record.solved is True
        assert record.flag == "picoCTF{flag}"

    def test_get_all(self, db_conn):
        ctf_id = upsert_ctf(db_conn, "test", "https://test", "g1", "c1")
        upsert_challenge(db_conn, ctf_id, 1, "Easy", "easy", "misc", 50)
        upsert_challenge(db_conn, ctf_id, 2, "Hard", "hard", "misc", 500)

        all_challs = get_all(db_conn, ctf_id)
        assert len(all_challs) == 2
        assert all_challs[0].points <= all_challs[1].points  # Sorted by points

    def test_get_unsolved(self, db_conn):
        ctf_id = upsert_ctf(db_conn, "test", "https://test", "g1", "c1")
        c1 = upsert_challenge(db_conn, ctf_id, 1, "Solved", "solved", "misc", 50)
        upsert_challenge(db_conn, ctf_id, 2, "Unsolved", "unsolved", "misc", 100)
        mark_solved(db_conn, c1, "flag{test}")

        unsolved = get_unsolved(db_conn, ctf_id)
        assert len(unsolved) == 1
        assert unsolved[0].name == "Unsolved"

    def test_get_existing_ctfd_ids(self, db_conn):
        ctf_id = upsert_ctf(db_conn, "test", "https://test", "g1", "c1")
        upsert_challenge(db_conn, ctf_id, 10, "A", "a", "misc", 50)
        upsert_challenge(db_conn, ctf_id, 20, "B", "b", "misc", 100)

        ids = get_existing_ctfd_ids(db_conn, ctf_id)
        assert ids == {10, 20}

    def test_empty_ctf(self, db_conn):
        ctf_id = upsert_ctf(db_conn, "empty", "https://empty", "g1", "c1")
        assert get_all(db_conn, ctf_id) == []
        assert get_unsolved(db_conn, ctf_id) == []
        assert get_existing_ctfd_ids(db_conn, ctf_id) == set()


class TestConversations:
    def test_add_and_get_history(self, db_conn):
        add_message(db_conn, "thread_1", "user", "hello")
        add_message(db_conn, "thread_1", "assistant", "world")

        history = get_history(db_conn, "thread_1")
        assert len(history) == 2
        assert history[0].role == "user"
        assert history[0].content == "hello"
        assert history[1].role == "assistant"

    def test_history_trimming(self, db_conn):
        # Add messages that exceed token budget
        for _ in range(20):
            add_message(db_conn, "thread_1", "user", "x" * 1000)

        # With a small budget, should trim
        history = get_history(db_conn, "thread_1", max_tokens=1000)
        assert len(history) < 20
        assert len(history) >= 2  # Always keeps at least 2

    def test_clear_history(self, db_conn):
        add_message(db_conn, "thread_1", "user", "hello")
        add_message(db_conn, "thread_1", "assistant", "world")

        clear_history(db_conn, "thread_1")
        assert get_history(db_conn, "thread_1") == []

    def test_separate_threads(self, db_conn):
        add_message(db_conn, "thread_1", "user", "msg1")
        add_message(db_conn, "thread_2", "user", "msg2")

        assert len(get_history(db_conn, "thread_1")) == 1
        assert len(get_history(db_conn, "thread_2")) == 1

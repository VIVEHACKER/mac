"""Order-log durability (settlement P0 completion): every append must reach disk before the
call returns. A bare write() leaves the record in the OS page cache, so a crash between submit
and the next flush loses the order record — the storage-layer form of the "ledger lies" risk the
reconcile-in-flight recovery is meant to make unnecessary. JsonlOrderStore._append fsyncs.
"""

from __future__ import annotations

import errno
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

import trader.execution.order_store as order_store_mod
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore, OrderEvent


def _intent() -> OrderIntent:
    return OrderIntent(
        strategy="s",
        symbol="AAPL",
        market="us",
        side="buy",
        qty=1,
        order_type="limit",
        limit_price=100.0,
    ).normalized()


def test_record_intent_fsyncs_the_order_log(tmp_path, monkeypatch) -> None:
    fsynced_fds: list[int] = []
    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        fsynced_fds.append(fd)
        real_fsync(fd)  # still perform the real durability syscall

    monkeypatch.setattr(os, "fsync", spy_fsync)

    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    intent = _intent()
    store.record_intent(intent)

    assert fsynced_fds, "record_intent must fsync the order log so a crash cannot lose the record"
    # The record is durably readable (functional check alongside the durability contract).
    assert store.has_intent(intent.client_order_id)


def _spy_dir_fsync(monkeypatch) -> list[int]:
    """Record fds that are directories among os.fsync calls (still runs the real syscall)."""
    dir_fsynced: list[int] = []
    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        try:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                dir_fsynced.append(fd)
        except OSError:
            pass
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    return dir_fsynced


def test_first_append_fsyncs_parent_directory(tmp_path, monkeypatch) -> None:
    # codex P1: fsyncing only the file fd does not make a NEWLY-CREATED file's directory entry
    # durable on POSIX. A crash right after the first record can lose the whole log unless the
    # containing directory is also fsynced.
    dir_fsynced = _spy_dir_fsync(monkeypatch)
    store = JsonlOrderStore(tmp_path / "store" / "orders.jsonl")  # file does not exist yet
    store.record_intent(_intent())
    assert dir_fsynced, "creating the order log must fsync its directory so the file entry survives"


def test_every_append_fsyncs_the_parent_directory(tmp_path, monkeypatch) -> None:
    # The parent directory is fsynced on EVERY append (not just first create), so a retry after a
    # first-create whose directory fsync failed still persists the entry rather than skipping it.
    store = JsonlOrderStore(tmp_path / "store" / "orders.jsonl")
    store.record_intent(_intent())  # creates the file

    dir_fsynced = _spy_dir_fsync(monkeypatch)
    store.record_event(OrderEvent(event_type="note", client_order_id="c", ts=datetime.now(UTC)))
    assert dir_fsynced, (
        "every append must fsync the parent directory (covers the failed-create retry)"
    )


def test_init_fsyncs_each_created_directory_in_the_chain(tmp_path, monkeypatch) -> None:
    # codex P1: when the store path's directories are created fresh, each NEW directory's entry
    # must be persisted by fsyncing its parent — not just the leaf — or a crash after the first
    # append can drop the whole created chain.
    synced: list[Path] = []
    real = order_store_mod._fsync_dir

    def spy(path: Path) -> None:
        synced.append(Path(path))
        real(path)

    monkeypatch.setattr(order_store_mod, "_fsync_dir", spy)
    JsonlOrderStore(tmp_path / "a" / "b" / "orders.jsonl")  # a, b created fresh
    # The parents that gained a new child directory must each be fsynced.
    assert tmp_path in synced  # persists the new "a" entry
    assert tmp_path / "a" in synced  # persists the new "b" entry


def _make_dir_fsync_raise(monkeypatch, err: int) -> None:
    real_fsync = os.fsync

    def failing(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(err, os.strerror(err))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing)


def test_directory_fsync_io_error_fails_closed(tmp_path, monkeypatch) -> None:
    # codex P2: a real directory-fsync failure (EIO/ENOSPC) means the entry is NOT durable. The
    # store must fail closed, not return success letting the caller believe the write is safe.
    store = JsonlOrderStore(tmp_path / "orders.jsonl")  # parent exists -> no chain fsync at init
    _make_dir_fsync_raise(monkeypatch, errno.EIO)
    with pytest.raises(OSError):
        store.record_intent(_intent())  # first append creates the file -> leaf dir fsync -> EIO


def test_directory_fsync_einval_is_tolerated(tmp_path, monkeypatch) -> None:
    # EINVAL = this filesystem/platform does not support directory fsync (benign). It must be
    # swallowed so the store still works where directory fsync is unsupported.
    _make_dir_fsync_raise(monkeypatch, errno.EINVAL)
    store = JsonlOrderStore(tmp_path / "store" / "orders.jsonl")
    store.record_intent(_intent())
    assert store.has_intent(_intent().client_order_id)


def test_directory_open_denial_fails_closed_on_posix(tmp_path, monkeypatch) -> None:
    # codex P2: on POSIX an EACCES opening the parent dir (writable/searchable but not readable)
    # is a real failure — the entry was not fsynced — so it must fail closed, not skip silently.
    if os.name != "posix":
        pytest.skip("directory fds are POSIX-only")
    store = JsonlOrderStore(tmp_path / "orders.jsonl")  # parent exists; file not yet created
    real_open = os.open
    target = str(tmp_path)

    def open_denies_dir(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
        if str(path) == target:
            raise OSError(errno.EACCES, "permission denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_denies_dir)
    with pytest.raises(OSError):
        store.record_intent(_intent())  # creates file, then _fsync_dir(parent) os.open -> EACCES

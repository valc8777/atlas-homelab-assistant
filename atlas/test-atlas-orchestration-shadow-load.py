#!/usr/bin/env python3
"""Bounded concurrent-load proof for the non-blocking shadow queue."""

from __future__ import annotations

import queue
import statistics
import threading
import time
from unittest import mock

import atlas_shortcuts_server as server


def main() -> None:
    request_count = 24
    test_queue: queue.Queue = queue.Queue(maxsize=32)
    barrier = threading.Barrier(request_count)
    accepted: list[bool] = []
    enqueue_ms: list[float] = []
    processed: list[str] = []
    result_lock = threading.Lock()

    def fake_run(job: dict) -> None:
        time.sleep(0.02)
        processed.append(job["question"])

    def submit(index: int) -> None:
        barrier.wait()
        started = time.monotonic()
        queued = server.enqueue_shadow_plan(
            f"Synthetic concurrent request {index}",
            {"conversation_id": None, "user_message": {"id": None}},
            {"route": "general_reference", "tool": "test"},
            "personal",
            [],
        )
        elapsed = (time.monotonic() - started) * 1000
        with result_lock:
            accepted.append(queued)
            enqueue_ms.append(elapsed)

    server._SHADOW_WORKER_STARTED = False
    with mock.patch.object(server, "ORCHESTRATOR_SHADOW_ENABLED", True), mock.patch.object(
        server, "_SHADOW_QUEUE", test_queue
    ), mock.patch.object(server, "_run_shadow_job", side_effect=fake_run):
        threads = [threading.Thread(target=submit, args=(index,)) for index in range(request_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        test_queue.join()

    ordered = sorted(enqueue_ms)
    p95 = ordered[round((len(ordered) - 1) * 0.95)]
    assert len(accepted) == request_count
    assert all(accepted), accepted
    assert len(processed) == request_count
    assert p95 < 100, p95
    print(
        f"PASS: {request_count} concurrent shadow submissions, 0 drops, "
        f"mean enqueue {statistics.fmean(enqueue_ms):.2f} ms, p95 {p95:.2f} ms"
    )


if __name__ == "__main__":
    main()

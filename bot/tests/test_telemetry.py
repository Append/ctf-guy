#!/usr/bin/env python3
"""Tests for telemetry exporter."""

import asyncio
import logging

from ai.telemetry import (
    TelemetryExporter,
    VictoriaLogsHandler,
    ship_log,
    ship_metric,
)


def test_ship_log_noop_when_disabled():
    """ship_log should not crash when no exporter is initialized."""
    ship_log("test.event", foo="bar")  # Should not raise


def test_ship_metric_noop_when_disabled():
    """ship_metric should not crash when no exporter is initialized."""
    ship_metric("test_metric", 1.0, label="value")  # Should not raise


def test_exporter_enqueue_log():
    """Events should be enqueued without blocking."""
    exporter = TelemetryExporter(logs_url="http://fake:9428")
    exporter.ship_log("test", foo="bar")
    assert exporter._log_queue.qsize() == 1


def test_exporter_enqueue_metric():
    """Metrics should be enqueued without blocking."""
    exporter = TelemetryExporter(metrics_url="http://fake:8428")
    exporter.ship_metric("test_metric", 42.0, label="value")
    assert exporter._metric_queue.qsize() == 1


def test_exporter_queue_full_no_crash():
    """When queue is full, events should be silently dropped."""
    exporter = TelemetryExporter(logs_url="http://fake:9428")
    exporter._log_queue = asyncio.Queue(maxsize=2)

    exporter.ship_log("event1")
    exporter.ship_log("event2")
    exporter.ship_log("event3")  # Should be dropped, not crash

    assert exporter._log_queue.qsize() == 2


def test_exporter_log_format():
    """Log entries should have _msg and _time fields."""
    exporter = TelemetryExporter(logs_url="http://fake:9428")
    exporter.ship_log("agent.text", challenge="test", text="hello")

    entry = exporter._log_queue.get_nowait()
    assert entry["_msg"] == "agent.text"
    assert "_time" in entry
    assert entry["challenge"] == "test"
    assert entry["text"] == "hello"


def test_exporter_metric_format():
    """Metric entries should have __name__, values, timestamps."""
    exporter = TelemetryExporter(metrics_url="http://fake:8428")
    exporter.ship_metric("ctf_solve_cost", 0.5, model="haiku")

    entry = exporter._metric_queue.get_nowait()
    assert entry["metric"]["__name__"] == "ctf_solve_cost"
    assert entry["metric"]["model"] == "haiku"
    assert entry["values"] == [0.5]
    assert len(entry["timestamps"]) == 1


def test_victoria_logs_handler_excludes_self():
    """VictoriaLogsHandler must not log its own telemetry (infinite loop)."""
    handler = VictoriaLogsHandler()

    # Create a record from the telemetry logger itself
    record = logging.LogRecord(
        name="ai.telemetry",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="test",
        args=(),
        exc_info=None,
    )
    handler.emit(record)  # Should be silently ignored

    # Create a record from httpx (also excluded)
    record2 = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="HTTP Request",
        args=(),
        exc_info=None,
    )
    handler.emit(record2)  # Should be silently ignored

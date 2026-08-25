#!/usr/bin/env python3
"""Telemetry exporter for VictoriaLogs and VictoriaMetrics.

Ships agent events and solve metrics via async batched HTTP.
Callers use synchronous ship_log() / ship_metric() — they just
put items on a queue. Background tasks flush to Victoria.
Disabled entirely if URLs are not set.
"""

import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, datetime

import httpx

log = logging.getLogger(__name__)


class TelemetryExporter:
    """Async batched exporter for Victoria stack."""

    def __init__(
        self,
        logs_url: str | None = None,
        metrics_url: str | None = None,
        batch_size: int = 50,
        flush_interval: float = 1.0,
    ):
        self.logs_url = logs_url
        self.metrics_url = metrics_url
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._log_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=5000)
        self._metric_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=5000)
        self._client: httpx.AsyncClient | None = None
        self._flush_logs_task: asyncio.Task | None = None
        self._flush_metrics_task: asyncio.Task | None = None
        self._last_warn_time: float = 0

    async def start(self):
        self._client = httpx.AsyncClient(timeout=5.0)
        if self.logs_url:
            self._flush_logs_task = asyncio.create_task(self._flush_logs_loop())
        if self.metrics_url:
            self._flush_metrics_task = asyncio.create_task(self._flush_metrics_loop())
        log.info(
            f"Telemetry started (logs={self.logs_url or 'off'}, metrics={self.metrics_url or 'off'})"
        )

    async def stop(self):
        # Cancel flush loops and await them
        for task in (self._flush_logs_task, self._flush_metrics_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        # Final drain of any remaining events
        await self._flush_logs()
        await self._flush_metrics()
        if self._client:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._client.aclose(), timeout=5)
        log.info("Telemetry stopped")

    def ship_log(self, event_type: str, **fields):
        """Enqueue a log event. Non-blocking, silently drops on full queue."""
        entry = {
            "_msg": event_type,
            "_time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            **{k: v for k, v in fields.items() if v is not None},
        }
        with contextlib.suppress(asyncio.QueueFull):
            self._log_queue.put_nowait(entry)

    def ship_metric(self, name: str, value: float, **labels):
        """Enqueue a metric data point. Non-blocking, silently drops on full queue."""
        entry = {
            "metric": {
                "__name__": name,
                **{k: str(v) for k, v in labels.items() if v is not None},
            },
            "values": [value],
            "timestamps": [int(time.time() * 1000)],
        }
        with contextlib.suppress(asyncio.QueueFull):
            self._metric_queue.put_nowait(entry)

    async def _flush_logs_loop(self):
        try:
            while True:
                await asyncio.sleep(self.flush_interval)
                await self._flush_logs()
        except asyncio.CancelledError:
            pass

    async def _flush_metrics_loop(self):
        try:
            while True:
                await asyncio.sleep(self.flush_interval)
                await self._flush_metrics()
        except asyncio.CancelledError:
            pass

    async def _flush_logs(self):
        if not self.logs_url or not self._client:
            return
        batch = self._drain(self._log_queue)
        if not batch:
            return
        body = "\n".join(json.dumps(entry, default=str) for entry in batch)
        try:
            resp = await self._client.post(
                f"{self.logs_url}/insert/jsonline",
                content=body,
                headers={"Content-Type": "application/stream+json"},
            )
            if resp.status_code >= 400:
                self._warn(
                    f"VictoriaLogs returned {resp.status_code}: {resp.text[:200]}"
                )
        except Exception as e:
            self._warn(f"VictoriaLogs flush failed: {e}")

    async def _flush_metrics(self):
        if not self.metrics_url or not self._client:
            return
        batch = self._drain(self._metric_queue)
        if not batch:
            return
        body = "\n".join(json.dumps(entry, default=str) for entry in batch)
        try:
            resp = await self._client.post(
                f"{self.metrics_url}/api/v1/import",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                self._warn(
                    f"VictoriaMetrics returned {resp.status_code}: {resp.text[:200]}"
                )
        except Exception as e:
            self._warn(f"VictoriaMetrics flush failed: {e}")

    def _drain(self, queue: asyncio.Queue) -> list[dict]:
        batch = []
        for _ in range(self.batch_size):
            try:
                batch.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    def _warn(self, msg: str):
        now = time.time()
        if now - self._last_warn_time > 60:
            log.warning(msg)
            self._last_warn_time = now


# Module-level singleton
_exporter: TelemetryExporter | None = None


async def init_telemetry(
    logs_url: str | None = None,
    metrics_url: str | None = None,
    batch_size: int = 50,
    flush_interval: float = 1.0,
) -> None:
    global _exporter
    if not logs_url and not metrics_url:
        return
    _exporter = TelemetryExporter(logs_url, metrics_url, batch_size, flush_interval)
    await _exporter.start()


async def shutdown_telemetry() -> None:
    global _exporter
    if _exporter:
        await _exporter.stop()
        _exporter = None


def ship_log(event_type: str, **fields) -> None:
    """Ship a structured log event. No-op if telemetry is disabled."""
    if _exporter:
        _exporter.ship_log(event_type, **fields)


def ship_metric(name: str, value: float, **labels) -> None:
    """Ship a metric data point. No-op if telemetry is disabled."""
    if _exporter:
        _exporter.ship_metric(name, value, **labels)


class VictoriaLogsHandler(logging.Handler):
    """Python logging handler that ships log records to VictoriaLogs."""

    def __init__(self, level=logging.INFO):
        super().__init__(level)
        # Avoid shipping our own telemetry logs (infinite loop)
        self._ignore_loggers = {"ai.telemetry", "httpx", "httpcore"}

    def emit(self, record: logging.LogRecord):
        if not _exporter:
            return
        if record.name in self._ignore_loggers:
            return
        # Never break the app over telemetry
        with contextlib.suppress(Exception):
            _exporter.ship_log(
                "log",
                level=record.levelname,
                logger=record.name,
                message=self.format(record)[:500],
            )


def install_log_handler() -> None:
    """Attach VictoriaLogs handler to the root logger."""
    handler = VictoriaLogsHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)

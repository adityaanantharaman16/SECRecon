from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk._logs import (
    LoggerProvider,
    LogRecordProcessor,
    ReadableLogRecord,
    ReadWriteLogRecord,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider


class Delivery:
    """One fixed queue/thread per signal. Export never runs on a request/worker thread."""

    def __init__(self, exporter: Any, capacity: int = 256) -> None:
        self.exporter = exporter
        self.items: queue.Queue[Any] = queue.Queue(maxsize=capacity)
        self.dropped = 0
        self.failed = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name="telemetry-export")
        self.thread.start()

    def offer(self, item: Any) -> None:
        try:
            self.items.put_nowait(item)
        except queue.Full:
            self.dropped += 1

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                item = self.items.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                result = self.exporter.export((item,))
                if result.name != "SUCCESS":
                    self.failed += 1
            except Exception:
                self.failed += 1
            finally:
                self.items.task_done()

    def flush(self, timeout_millis: int = 1000) -> bool:
        deadline = time.monotonic() + timeout_millis / 1000
        while self.items.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.items.unfinished_tasks == 0

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)


class SpanDelivery(SpanProcessor):
    def __init__(self, delivery: Delivery) -> None:
        self.delivery = delivery

    def on_end(self, span: ReadableSpan) -> None:
        self.delivery.offer(span)

    def shutdown(self) -> None:
        self.delivery.close()

    def force_flush(self, timeout_millis: int = 1000) -> bool:
        return self.delivery.flush(timeout_millis)


class LogDelivery(LogRecordProcessor):
    def __init__(self, delivery: Delivery) -> None:
        self.delivery = delivery

    def on_emit(self, log_record: ReadWriteLogRecord) -> None:
        self.delivery.offer(
            ReadableLogRecord(
                log_record=log_record.log_record,
                resource=log_record.resource or Resource.create({}),
                instrumentation_scope=log_record.instrumentation_scope,
                limits=log_record.limits,
            )
        )

    def shutdown(self) -> None:
        self.delivery.close()

    def force_flush(self, timeout_millis: int = 1000) -> bool:
        return self.delivery.flush(timeout_millis)


class Telemetry:
    def __init__(self, endpoint: str = "", service: str = "secrecon") -> None:
        resource = Resource.create({"service.name": service})
        self.provider = TracerProvider(resource=resource)
        self.logs = LoggerProvider(resource=resource)
        self.deliveries: dict[str, Delivery] = {}
        self.archive_failures = 0
        self.metrics: MeterProvider | None = None
        if endpoint:
            self.deliveries["traces"] = Delivery(
                OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=1)
            )
            self.deliveries["logs"] = Delivery(
                OTLPLogExporter(endpoint=endpoint + "/v1/logs", timeout=1)
            )
            self.provider.add_span_processor(SpanDelivery(self.deliveries["traces"]))
            self.logs.add_log_record_processor(LogDelivery(self.deliveries["logs"]))
            reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint + "/v1/metrics", timeout=1),
                export_interval_millis=15000,
                export_timeout_millis=2000,
            )
            self.metrics = MeterProvider(resource=resource, metric_readers=[reader])
            meter = self.metrics.get_meter("secrecon")

            def dropped(_: CallbackOptions) -> Iterator[Observation]:
                for signal, delivery in self.deliveries.items():
                    yield Observation(delivery.dropped, {"signal": signal, "component": service})

            def failed(_: CallbackOptions) -> Iterator[Observation]:
                for signal, delivery in self.deliveries.items():
                    yield Observation(delivery.failed, {"signal": signal, "component": service})

            def archive_failed(_: CallbackOptions) -> Iterator[Observation]:
                yield Observation(self.archive_failures, {"component": service})

            meter.create_observable_counter("secrecon_telemetry_dropped", callbacks=[dropped])
            meter.create_observable_counter("secrecon_telemetry_failed", callbacks=[failed])
            meter.create_observable_counter("secrecon_archive_failures", callbacks=[archive_failed])
        self.tracer = self.provider.get_tracer("secrecon")
        self.logger = self.logs.get_logger("secrecon")

    def close(self) -> None:
        self.provider.shutdown()
        self.logs.shutdown()
        if self.metrics:
            self.metrics.shutdown(timeout_millis=2000)


current = Telemetry()


def configure(endpoint: str, service: str) -> None:
    global current
    current.close()
    current = Telemetry(endpoint, service)


def carrier() -> dict[str, str]:
    result: dict[str, str] = {}
    propagate.inject(result)
    return result


def ids() -> tuple[str, str]:
    context = trace.get_current_span().get_span_context()
    return (format(context.trace_id, "032x"), format(context.span_id, "016x"))


@contextmanager
def span(
    name: str, parent: dict[str, str] | None = None, **attributes: Any
) -> Iterator[trace.Span]:
    context: Context | None = propagate.extract(parent) if parent else None
    with current.tracer.start_as_current_span(
        name,
        context=context,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as active:
        try:
            yield active
        except Exception as exc:
            active.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
            if name == "archive.preserve":
                current.archive_failures += 1
            raise


def event(name: str, **attributes: Any) -> None:
    trace_id, span_id = ids()
    safe = {**attributes, "trace_id": trace_id, "span_id": span_id}
    logging.getLogger("secrecon").info(name, extra=safe)
    current.logger.emit(body=name, attributes=safe)


def error(error_class: str) -> None:
    trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, error_class))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        result: dict[str, Any] = {
            "event": record.getMessage(),
            "level": record.levelname,
            "logger": record.name,
        }
        for key in (
            "job_id",
            "accession",
            "source_event_id",
            "projection_version",
            "correlation_id",
            "trace_id",
            "span_id",
            "error_class",
        ):
            if hasattr(record, key):
                result[key] = getattr(record, key)
        return json.dumps(result)


P = ParamSpec("P")
T = TypeVar("T")


def traced(name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorate(fn: Callable[P, T]) -> Callable[P, T]:
        @wraps(fn)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            with span(name):
                return fn(*args, **kwargs)

        return wrapped

    return decorate

import os
import threading
from typing import Dict

from prometheus_client import Counter, Histogram, Gauge, Info, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST, start_http_server

REGISTRY = CollectorRegistry()

_metrics_port = os.getenv("METRICS_PORT", "9090")
_metrics_prefix = os.getenv("METRICS_PREFIX", "mantra")

app_info = Info(
    name=f"{_metrics_prefix}_app_info",
    documentation="Application metadata",
    registry=REGISTRY,
)
app_info.info({"name": "livekit-agent", "version": "0.1.0"})

# ── HTTP / API metrics ─────────────────────────────────────────────
http_requests_total = Counter(
    name=f"{_metrics_prefix}_http_requests_total",
    documentation="Total HTTP requests by method, path, status",
    labelnames=["method", "path", "status"],
    registry=REGISTRY,
)
http_request_duration_seconds = Histogram(
    name=f"{_metrics_prefix}_http_request_duration_seconds",
    documentation="HTTP request latency in seconds",
    labelnames=["method", "path"],
    buckets=(.005, .01, .025, .05, .075, .1, .25, .5, .75, 1.0, 2.5, 5.0, 7.5, 10.0),
    registry=REGISTRY,
)
http_requests_in_flight = Gauge(
    name=f"{_metrics_prefix}_http_requests_in_flight",
    documentation="Current HTTP requests being served",
    labelnames=["method"],
    registry=REGISTRY,
)

# ── Agent / Call lifecycle metrics ──────────────────────────────
calls_total = Counter(
    name=f"{_metrics_prefix}_calls_total",
    documentation="Total calls processed",
    labelnames=["status", "model"],
    registry=REGISTRY,
)
calls_in_progress = Gauge(
    name=f"{_metrics_prefix}_calls_in_progress",
    documentation="Current active calls",
    registry=REGISTRY,
)
call_duration_seconds = Histogram(
    name=f"{_metrics_prefix}_call_duration_seconds",
    documentation="Call duration in seconds",
    labelnames=["status", "model"],
    buckets=(5, 10, 30, 60, 120, 180, 240, 300, 420, 600),
    registry=REGISTRY,
)

# ── Dispatch / Queue metrics ──────────────────────────────────
queue_depth = Gauge(
    name=f"{_metrics_prefix}_queue_depth",
    documentation="Current number of pending calls in the queue",
    registry=REGISTRY,
)
dispatch_attempts_total = Counter(
    name=f"{_metrics_prefix}_dispatch_attempts_total",
    documentation="Dispatch attempts",
    labelnames=["status"],
    registry=REGISTRY,
)
dispatches_in_flight = Gauge(
    name=f"{_metrics_prefix}_dispatches_in_flight",
    documentation="Current dispatches being processed",
    registry=REGISTRY,
)

# ── Pipeline stage metrics (STT / LLM / TTS) ─────────────────
pipeline_duration_seconds = Histogram(
    name=f"{_metrics_prefix}_pipeline_duration_seconds",
    documentation="Pipeline stage duration",
    labelnames=["stage"],
    buckets=(.05, .1, .25, .5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)
pipeline_errors_total = Counter(
    name=f"{_metrics_prefix}_pipeline_errors_total",
    documentation="Pipeline errors by stage",
    labelnames=["stage"],
    registry=REGISTRY,
)

# ── SIP / telephony metrics ──────────────────────────────────
sip_calls_total = Counter(
    name=f"{_metrics_prefix}_sip_calls_total",
    documentation="SIP call attempts by provider",
    labelnames=["provider", "status"],
    registry=REGISTRY,
)

# ── System metrics ─────────────────────────────────────────────
crash_total = Counter(
    name=f"{_metrics_prefix}_crash_total",
    documentation="Crash / exception count by service",
    labelnames=["service"],
    registry=REGISTRY,
)


def generate_metrics() -> bytes:
    return generate_latest(REGISTRY)


def metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST


def start_metrics_server() -> None:
    port = int(os.getenv("METRICS_PORT", "9090"))
    thr = threading.Thread(target=start_http_server, args=(port,), kwargs={"registry": REGISTRY}, daemon=True)
    thr.start()
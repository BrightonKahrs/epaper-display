# OpenTelemetry logs and metrics

The carousel can emit OpenTelemetry logs and metrics locally or export them
using OTLP over HTTP/protobuf. Telemetry is **off by default**: without an
endpoint or extra packages, the carousel continues to run as before and makes
no telemetry network requests. This integration does not emit traces.

## Install

On the Pi, activate the project's virtual environment and install the hardware
and optional telemetry dependencies:

```sh
cd ~/e-paper-display
source .venv/bin/activate
python -m pip install -e '.[hardware,otel]'
```

Adjust the checkout path if you renamed or moved it. For development without
the panel, use `python -m pip install -e '.[otel]'`.
The optional packages are installed only when the `otel` extra is requested.

## Try it without a collector

```sh
export EPD_OTEL_EXPORTER=console
export OTEL_SERVICE_NAME=epaper-display
epd run --fast
```

Structured OpenTelemetry logs and metrics are written to standard error along
with normal application diagnostics. The first display refresh generates a
display event and metric when the gallery contains a slide. Source metrics appear after a source is refreshed.
Metrics export periodically (by default every 60 seconds) and on clean shutdown;
press Ctrl+C to stop the foreground process and flush. The application still
prints its regular diagnostics separately.

To try this without touching the physical panel, stop any running display
process, then use `epd run --preview preview.png`. The preview records the same
display refresh metrics, but measures rendering to the preview file rather than
the physical panel. Disable telemetry for future shell commands with:

```sh
unset EPD_OTEL_EXPORTER
```

## Connect a collector later

Set `EPD_OTEL_EXPORTER=otlp` and supply a collector's OTLP **HTTP** base URL:

```sh
export EPD_OTEL_EXPORTER=otlp
export OTEL_SERVICE_NAME=epaper-display
export OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example.com:4318
epd run --fast
```

Replace the example endpoint with your collector's URL. The OTLP exporter appends
`/v1/logs` and `/v1/metrics` to the base URL. Alternatively, set both
`OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` and `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` to
their full signal-specific URLs. Use the actual paths and authentication
required by your provider; Grafana Cloud or a collector may use different URLs.
The exporter uses HTTP/protobuf, not gRPC.

Standard OpenTelemetry environment variables such as
`OTEL_EXPORTER_OTLP_HEADERS` and `OTEL_METRIC_EXPORT_INTERVAL` are supported by
the SDK. Set credentials only in the process environment or a protected
environment file; never commit them or put them in the endpoint URL. Use HTTPS
for remote endpoints. Logs and metrics are exported only for the process whose
environment enables them: the long-running `run` process, or an explicitly
invoked `epd refresh` process.

For the supplied systemd service, install the `otel` extra in the service's
virtual environment, then edit `/etc/epaper-display/environment` as root. Set
the relevant variables as `KEY=value` lines, restrict the file to root, and
restart the service. The unit already reads that optional file:

```sh
sudo touch /etc/epaper-display/environment
sudo chmod 600 /etc/epaper-display/environment
sudoedit /etc/epaper-display/environment
sudo systemctl restart epaper-display
sudo journalctl -u epaper-display -n 50 --no-pager
```

For a cloud endpoint, configure the collector or provider to accept both OTLP
signals and route logs to a log backend and metrics to a metrics backend;
Grafana queries those backends. Sending OTLP alone does not create dashboards.

## Signals and privacy

| Metric | Meaning | Attributes |
| --- | --- | --- |
| `epaper.display.refreshes` | Display refresh attempts | `outcome` |
| `epaper.display.refresh.duration` | Refresh duration in seconds | `outcome` |
| `epaper.source.refreshes` | Source refresh attempts | `source.type`, `outcome` |
| `epaper.source.refresh.duration` | Source refresh duration in seconds | `source.type`, `outcome` |
| `epaper.source.slides` | Slides imported by successful source refreshes | `source.type` |

`outcome` is `success` or `error`; `source.type` is `json` or `image`.
Durations use a monotonic clock and histograms with second-scale buckets.
The service also emits structured initialization, display refresh, and source
refresh events, including outcome and duration. It does **not** export the
existing general Python log stream: detailed errors remain in local diagnostics
and source status. The app's exported events intentionally exclude slide IDs,
titles, source IDs, URLs, image data, credentials, and exception text.

The SDK adds resource metadata including service name, version and SDK
information. Review any `OTEL_RESOURCE_ATTRIBUTES`, exporter headers, and
collector-side enrichment you configure before sending data externally.
Local application diagnostics and database status may still contain source
IDs or error text and should be protected separately.

Telemetry is best-effort, not a durable queue. Endpoint outages or process
termination can lose data; exporter failures appear in local diagnostics.
Clean shutdown attempts to flush, but a refresh worker still blocked after a
five-second shutdown wait may not contribute its final event. Use
`EPD_OTEL_EXPORTER=none` or unset it to turn telemetry off. Invalid exporter
settings or missing optional dependencies produce an explicit startup error.

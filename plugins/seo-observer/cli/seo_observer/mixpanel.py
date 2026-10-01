"""Mixpanel Raw Export -> daily outcome counters by traffic channel.

Reads the /api/2.0/export stream (one JSON event per line) and aggregates
events into (day, outcome_id, channel) buckets. ``distinct_id`` values are
used only as in-memory unique-actor sets and never reach the output.
"""

from __future__ import annotations

import base64
import datetime
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from seo_observer.channels import event_channel

REGION_EXPORT_URLS = {
    "eu": "https://data-eu.mixpanel.com/api/2.0/export",
    "us": "https://data.mixpanel.com/api/2.0/export",
}
DEFAULT_EVENTS = {"Signup Completed": "registration"}

_STREAM_ERRORS = (OSError, http.client.IncompleteRead, urllib.error.URLError)


class MixpanelRequestError(ValueError):
    pass


@dataclass(frozen=True)
class MixpanelSource:
    project_id: str
    username: str
    secret: str
    region: str = "eu"
    events: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_EVENTS))
    timezone: str = "UTC"
    self_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.project_id:
            raise MixpanelRequestError("project_id is required.")
        try:
            zoneinfo.ZoneInfo(self.timezone)
        except zoneinfo.ZoneInfoNotFoundError as exc:
            raise MixpanelRequestError(f"Unknown timezone {self.timezone!r}.") from exc


class MixpanelExportTransportProtocol(Protocol):
    def iter_lines(self, params: dict[str, str]) -> Iterator[str]:
        ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Basic-auth credentials must never be re-sent to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise MixpanelRequestError(
            "Mixpanel export endpoint must not redirect; refusing to follow the redirect target."
        )


class MixpanelExportTransport:
    """Streams raw export lines from the Mixpanel export API (GET, basic auth)."""

    def __init__(self, base_url: str, username: str, secret: str, timeout: float = 120) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.query or parsed.fragment:
            raise MixpanelRequestError(
                "Mixpanel export URL must not contain a query string or fragment; "
                "the request query is built from the export parameters only."
            )
        if parsed.username is not None or parsed.password is not None:
            raise MixpanelRequestError("Mixpanel export URL must not contain userinfo.")
        if (parsed.scheme or "").lower() != "https" or not parsed.hostname:
            raise MixpanelRequestError("Mixpanel export endpoint must use https.")
        self.base_url = base_url
        self.username = username
        self.secret = secret
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    @classmethod
    def for_region(cls, region: str, username: str, secret: str) -> "MixpanelExportTransport":
        base_url = REGION_EXPORT_URLS.get(region)
        if base_url is None:
            raise MixpanelRequestError(
                f"Unknown Mixpanel region {region!r}; expected one of {sorted(REGION_EXPORT_URLS)}."
            )
        return cls(base_url, username, secret)

    def iter_lines(self, params: dict[str, str]) -> Iterator[str]:
        query = urllib.parse.urlencode(params)
        credentials = base64.b64encode(f"{self.username}:{self.secret}".encode("utf-8")).decode("ascii")
        request = urllib.request.Request(
            f"{self.base_url}?{query}",
            headers={
                "Authorization": f"Basic {credentials}",
                "Accept": "application/json",
            },
            method="GET",
        )
        with self._opener.open(request, timeout=self.timeout) as response:
            for raw in response:
                yield raw.decode("utf-8")


class MixpanelAdapter:
    def __init__(self, source: MixpanelSource, transport: MixpanelExportTransportProtocol) -> None:
        self.source = source
        self.transport = transport

    def fetch_daily_event_counts(self, start: str, end: str) -> dict[str, Any]:
        period_start, period_end = _validate_period(start, end)
        tz = zoneinfo.ZoneInfo(self.source.timezone)
        params = {
            "project_id": self.source.project_id,
            "from_date": start,
            "to_date": end,
            "event": json.dumps(list(self.source.events)),
        }
        counts: dict[tuple[str, str, str], int] = defaultdict(int)
        actors: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        rows_received = 0
        rows_invalid = 0
        stream_error: str | None = None
        try:
            for raw_line in self.transport.iter_lines(params):
                line = raw_line.strip()
                if not line:
                    continue
                rows_received += 1
                parsed = _parse_line(line, self.source.events, tz)
                if parsed is None:
                    rows_invalid += 1
                    continue
                day, outcome_id, props = parsed
                if not period_start <= day <= period_end:
                    rows_invalid += 1
                    continue
                channel = event_channel(
                    props.get("utm_source"),
                    props.get("utm_medium"),
                    props.get("referrer_host") or props.get("$referring_domain"),
                    self_domains=self.source.self_domains,
                )
                key = (day, outcome_id, channel)
                counts[key] += 1
                distinct = props.get("distinct_id")
                if distinct is not None:
                    actors[key].add(str(distinct))
        except _STREAM_ERRORS as exc:
            if rows_received == 0:
                raise
            stream_error = type(exc).__name__
        coverage = "partial" if rows_invalid or stream_error else "complete"
        freshness_cutoff = (
            datetime.datetime.now(tz).date() - datetime.timedelta(days=1)
        ).isoformat()
        observations = [
            {
                "project_id": "__pending__",
                "property_id": self.source.project_id,
                "source": "mixpanel",
                "source_request_id": (
                    f"mixpanel:{self.source.project_id}:{outcome_id}:{day}:{channel}"
                ),
                "outcome_id": outcome_id,
                "evidence_kind": "analytics_event",
                "counting_unit": "event",
                "dedupe_key": "event",
                "population_id": "mixpanel_project",
                "attribution_model": "first_touch_event",
                "attribution_scope": "__all__",
                "traffic_channel": channel,
                "count": count,
                "unique_actors": len(actors[key]),
                "period_start": day,
                "period_end": day,
                "grain_start": day,
                "timezone": self.source.timezone,
                "dataset_coverage": coverage,
                "freshness": "provisional" if day >= freshness_cutoff else "final",
            }
            for key, count in sorted(counts.items())
            for day, outcome_id, channel in [key]
        ]
        return {
            "collection": "outcome_metrics",
            "metadata": {
                "dataset_coverage": coverage,
                "rows_received": rows_received,
                "rows_invalid": rows_invalid,
                "stream_error": stream_error,
                "source": "mixpanel",
                "mixpanel_project_id": self.source.project_id,
            },
            "observations": observations,
        }


def _validate_period(start: str, end: str) -> tuple[str, str]:
    try:
        start_date = datetime.date.fromisoformat(start)
        end_date = datetime.date.fromisoformat(end)
    except ValueError as exc:
        raise MixpanelRequestError("fetch_daily_event_counts requires ISO start/end dates.") from exc
    if end_date < start_date:
        raise MixpanelRequestError("fetch_daily_event_counts requires start <= end.")
    return start, end


def _parse_line(
    line: str,
    events: dict[str, str],
    tz: zoneinfo.ZoneInfo,
) -> tuple[str, str, dict[str, Any]] | None:
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    event = record.get("event")
    props = record.get("properties")
    if not isinstance(event, str) or not isinstance(props, dict):
        return None
    outcome_id = events.get(event)
    if outcome_id is None:
        return None
    timestamp = props.get("time")
    if not isinstance(timestamp, int | float) or isinstance(timestamp, bool):
        return None
    day = datetime.datetime.fromtimestamp(timestamp, tz=tz).date().isoformat()
    return day, outcome_id, props

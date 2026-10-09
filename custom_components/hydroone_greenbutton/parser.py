"""Parse Green Button ESPI interval energy without changing UTC timestamps."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

ATOM = "{http://www.w3.org/2005/Atom}"
ESPI = "{http://naesb.org/espi}"


class FeedError(ValueError):
    """An ESPI document cannot safely be interpreted as hourly consumption."""


def _resource(href: str) -> str:
    path = urlsplit(href).path
    return path.split("/resource/", 1)[-1].strip("/")


def parse_feed(xml: str) -> dict[int, Decimal]:
    """Return hourly imported electricity in kWh keyed by UTC Unix seconds.

    The ReadingType is joined through the MeterReading links, rather than
    guessed from entry order. Identical repeated intervals are deduplicated.
    Conflicting overlaps and unsupported measurements fail explicitly.
    """
    if len(xml.encode("utf-8")) > 20 * 1024 * 1024:
        raise FeedError("The Green Button document exceeds 20 MiB")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
        raise FeedError("DTD and entity declarations are not supported")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as err:
        raise FeedError("Invalid Green Button XML") from err
    entries = root.findall(f"{ATOM}entry")
    types: dict[str, ET.Element] = {}
    meter_types: dict[str, str] = {}
    blocks: list[tuple[str, ET.Element]] = []
    for entry in entries:
        content = entry.find(f"{ATOM}content")
        if content is None:
            continue
        links = entry.findall(f"{ATOM}link")
        own = next((_resource(x.get("href", "")) for x in links if x.get("rel") == "self"), "")
        for node in content:
            if node.tag == f"{ESPI}ReadingType":
                types[own] = node
            elif node.tag == f"{ESPI}MeterReading":
                for link in links:
                    target = _resource(link.get("href", ""))
                    if link.get("rel") == "related" and "ReadingType/" in target:
                        meter_types[own] = target
            elif node.tag == f"{ESPI}IntervalBlock":
                blocks.append((own.split("/IntervalBlock", 1)[0], node))

    result: dict[int, Decimal] = {}
    consumption_meters: set[str] = set()
    for meter, block in blocks:
        reading_type = types.get(meter_types.get(meter, ""))
        if reading_type is None:
            raise FeedError("An interval block has no linked ReadingType")
        get = lambda name, default="": reading_type.findtext(f"{ESPI}{name}", default)
        if get("commodity") != "1" or get("flowDirection") != "1":
            continue  # This integration imports forward electricity only.
        if get("uom") != "72" or get("accumulationBehaviour") != "4" or get("kind") != "12":
            raise FeedError("Expected delta electricity energy in Wh")
        try:
            interval_length = int(get("intervalLength", "0"))
            multiplier = int(get("powerOfTenMultiplier", "0"))
        except ValueError as err:
            raise FeedError("Invalid ReadingType interval length or multiplier") from err
        if interval_length != 3600:
            raise FeedError("Expected 60-minute electricity intervals")
        if not -12 <= multiplier <= 12:
            raise FeedError("Unsupported power-of-ten multiplier")
        consumption_meters.add(meter.split("/MeterReading", 1)[0])
        if len(consumption_meters) > 1:
            raise FeedError("One export contains multiple electricity meters")
        scale = (Decimal(10) ** multiplier) / Decimal(1000)
        for interval in block.findall(f"{ESPI}IntervalReading"):
            try:
                start = int(interval.findtext(f"{ESPI}timePeriod/{ESPI}start", ""))
                duration = int(interval.findtext(f"{ESPI}timePeriod/{ESPI}duration", ""))
                value = Decimal(interval.findtext(f"{ESPI}value", "")) * scale
                datetime.fromtimestamp(start - 3600, timezone.utc)
            except (ValueError, ArithmeticError, OSError) as err:
                raise FeedError("Invalid interval value or timestamp") from err
            if duration != 3600 or start % 3600:
                raise FeedError("Expected UTC-aligned, 60-minute intervals")
            if not value.is_finite() or value < 0:
                raise FeedError("Invalid forward electricity consumption")
            if start in result and result[start] != value:
                raise FeedError("Conflicting consumption readings for the same hour")
            result[start] = value
    return result


def merge_readings(old: dict[str, str], incoming: dict[int, Decimal]) -> dict[str, str]:
    """Replace corrected hours, retaining all other previously imported hours."""
    merged = dict(old)
    merged.update({str(start): str(value) for start, value in incoming.items()})
    return merged


def statistics_rows(readings: dict[str, str]) -> list[dict]:
    """Build a stable cumulative series, including the initial zero baseline."""
    if not readings:
        return []
    ordered = sorted((int(start), Decimal(value)) for start, value in readings.items())
    rows = [{"start": datetime.fromtimestamp(ordered[0][0] - 3600, timezone.utc), "sum": 0.0}]
    total = Decimal(0)
    for start, value in ordered:
        total += value
        rows.append({"start": datetime.fromtimestamp(start, timezone.utc), "sum": float(total)})
    return rows

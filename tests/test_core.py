"""Check parsing and authenticated transport without a live utility account."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "hydroone_test_package"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "custom_components" / "hydroone_greenbutton")]
sys.modules[PACKAGE] = package
parser = importlib.import_module(PACKAGE + ".parser")
client_module = importlib.import_module(PACKAGE + ".client")
history_module = importlib.import_module(PACKAGE + ".history")


def feed(values=(199000, 500000), starts=(1791349200, 1791352800), multiplier=-3,
         duration=3600, flow=1, unit=72):
    intervals = "".join(
        f"<IntervalReading><timePeriod><start>{start}</start><duration>{duration}</duration>"
        f"</timePeriod><value>{value}</value></IntervalReading>"
        for start, value in zip(starts, values)
    )
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        '<entry><link rel="self" href="/resource/ReadingType/type1"/>'
        '<content><ReadingType xmlns="http://naesb.org/espi">'
        f'<commodity>1</commodity><flowDirection>{flow}</flowDirection><uom>{unit}</uom>'
        '<accumulationBehaviour>4</accumulationBehaviour><kind>12</kind><intervalLength>3600</intervalLength>'
        f'<powerOfTenMultiplier>{multiplier}</powerOfTenMultiplier></ReadingType></content></entry>'
        '<entry><link rel="self" href="/resource/UsagePoint/meter1/MeterReading/reading1"/>'
        '<link rel="related" href="/resource/ReadingType/type1"/>'
        '<content><MeterReading xmlns="http://naesb.org/espi"/></content></entry>'
        '<entry><link rel="self" href="/resource/UsagePoint/meter1/MeterReading/reading1/IntervalBlock/block1"/>'
        f'<content><IntervalBlock xmlns="http://naesb.org/espi">{intervals}</IntervalBlock></content></entry>'
        '</feed>'
    )


class ParserTests(unittest.TestCase):
    def test_unit_conversion_uses_reading_type_multiplier(self):
        actual = parser.parse_feed(feed())
        self.assertEqual(list(actual.values()), [Decimal("0.199"), Decimal("0.500")])
        self.assertEqual(list(parser.parse_feed(feed(values=(1000,), starts=(1791349200,), multiplier=0)).values()),
                         [Decimal("1")])

    def test_repeated_download_does_not_add_energy(self):
        incoming = parser.parse_feed(feed())
        stored = parser.merge_readings({}, incoming)
        again = parser.merge_readings(stored, incoming)
        self.assertEqual(stored, again)
        self.assertEqual(parser.statistics_rows(again)[-1]["sum"], 0.699)

    def test_correction_replaces_hour_and_recalculates_later_sums(self):
        original = parser.merge_readings({}, parser.parse_feed(feed()))
        corrected = parser.merge_readings(original, {1791349200: Decimal("0.299")})
        self.assertEqual(len(corrected), 2)
        self.assertEqual(parser.statistics_rows(corrected)[-1]["sum"], 0.799)

    def test_first_hour_is_included_via_zero_baseline(self):
        rows = parser.statistics_rows(parser.merge_readings({}, parser.parse_feed(feed())))
        self.assertEqual(rows[0]["sum"], 0)
        self.assertEqual(rows[1]["sum"] - rows[0]["sum"], 0.199)
        self.assertEqual(rows[0]["start"].timestamp(), 1791349200 - 3600)

    def test_original_utc_timestamp_is_preserved(self):
        rows = parser.statistics_rows(parser.merge_readings({}, parser.parse_feed(feed())))
        self.assertEqual(rows[1]["start"].timestamp(), 1791349200)
        self.assertEqual(rows[1]["start"].tzinfo, timezone.utc)

    def test_two_dst_repeated_local_hours_remain_distinct(self):
        # Both map to 01:00 in Toronto when daylight saving ends.
        starts = (int(datetime(2026, 11, 1, 5, tzinfo=timezone.utc).timestamp()),
                  int(datetime(2026, 11, 1, 6, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(len(parser.parse_feed(feed(starts=starts))), 2)

    def test_identical_duplicate_interval_is_deduplicated(self):
        self.assertEqual(len(parser.parse_feed(feed(values=(199000,199000), starts=(1791349200,1791349200)))), 1)

    def test_conflicting_duplicate_is_rejected(self):
        with self.assertRaises(parser.FeedError):
            parser.parse_feed(feed(starts=(1791349200,1791349200)))

    def test_non_hourly_interval_is_rejected(self):
        with self.assertRaises(parser.FeedError):
            parser.parse_feed(feed(duration=900))

    def test_export_energy_is_not_counted_as_import(self):
        self.assertEqual(parser.parse_feed(feed(flow=2)), {})

    def test_wrong_unit_is_rejected(self):
        with self.assertRaises(parser.FeedError):
            parser.parse_feed(feed(unit=38))

    def test_malformed_or_extreme_metadata_is_rejected(self):
        for multiplier in ("bad", 1000000000):
            with self.subTest(multiplier=multiplier), self.assertRaises(parser.FeedError):
                parser.parse_feed(feed(multiplier=multiplier))

    def test_unrepresentable_timestamp_is_rejected(self):
        with self.assertRaises(parser.FeedError):
            parser.parse_feed(feed(starts=(10**30, 10**30 + 3600)))

    def test_negative_and_nonfinite_consumption_are_rejected(self):
        for value in (-1, "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(parser.FeedError):
                parser.parse_feed(feed(values=(value,), starts=(1791349200,)))

    def test_xml_entity_declarations_are_rejected(self):
        with self.assertRaises(parser.FeedError):
            parser.parse_feed('<!DOCTYPE feed [<!ENTITY a "private">]>' + feed())



class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class HistoryTests(unittest.TestCase):
    def test_first_import_ends_yesterday(self):
        self.assertEqual(history_module.export_window(date(2026,10,9), {}, 90, 14),
                         (date(2026,7,11), date(2026,10,8)))

    def test_recent_overlap(self):
        newest = str(int(datetime(2026,10,8,18,tzinfo=timezone.utc).timestamp()))
        self.assertEqual(history_module.export_window(date(2026,10,9), {newest:"1"}, 90, 14),
                         (date(2026,9,25), date(2026,10,8)))

    def test_long_downtime_is_filled(self):
        newest = str(int(datetime(2026,8,1,18,tzinfo=timezone.utc).timestamp()))
        self.assertEqual(history_module.export_window(date(2026,10,9), {newest:"1"}, 90, 14),
                         (date(2026,7,31), date(2026,10,8)))

    def test_retention_handles_leap_day(self):
        self.assertEqual(history_module.export_window(date(2028,2,29), {}, 1000, 14),
                         (date(2026,2,28), date(2028,2,28)))


class FakeResponse:
    def __init__(self, url, status=200, chunks=(b"{}",), headers=None):
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.charset = "utf-8"
        self.content = FakeStream(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return next(self.responses)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = client_module.HydroOneClient("sample@example.invalid", "private+password&value")

    async def test_transport_reads_all_stream_chunks(self):
        self.client.session = FakeSession([FakeResponse(client_module.BASE, chunks=(b'{"a":', b'1}'))])
        _, text = await self.client._request("GET", client_module.BASE)
        self.assertEqual(text, '{"a":1}')

    async def test_transport_rejects_external_redirect_before_sending(self):
        self.client.session = FakeSession([FakeResponse(client_module.BASE, status=307,
                                                       headers={"Location": "https://untrusted.example/login"})])
        with self.assertRaises(client_module.PortalError):
            await self.client._request("POST", client_module.BASE, data={"password":"secret"})
        self.assertEqual(len(self.client.session.requests), 1)

    async def test_transport_rejects_insecure_url(self):
        self.client.session = FakeSession([])
        with self.assertRaises(client_module.PortalError):
            await self.client._request("POST", "http://www.hydroone.com/login")
        self.assertEqual(self.client.session.requests, [])

    async def test_federation_preserves_html_escaped_hidden_fields(self):
        self.client._request = AsyncMock(return_value=(client_module.BASE, "done"))
        await self.client._submit_federation(client_module.BASE,
            '<form action="/_trust/"><input type="hidden" name="wresult" value="&lt;token a=&quot;x&quot;/&gt;"></form>')
        self.assertEqual(self.client._request.call_args.kwargs["data"]["wresult"], '<token a="x"/>')

    async def test_export_request_matches_current_portal(self):
        document = {"ResponseStatus":200,"Content":feed(),"FileName":"energy.xml"}
        self.client._request = AsyncMock(return_value=(client_module.API, json.dumps([document])))
        readings = await self.client.download("account", "contract", date(2026,10,7), date(2026,10,8))
        self.assertEqual(len(readings), 2)
        self.assertEqual(self.client._request.call_args.kwargs["json"],
                         {"AccountNumber":"account","ContractId":"contract","FromDate":"20261007","ToDate":"20261008"})

    async def test_404_document_does_not_erase_previous_history(self):
        self.client._request = AsyncMock(return_value=(client_module.API, json.dumps([{"ResponseStatus":404}])))
        with self.assertRaises(client_module.NoDataError):
            await self.client.download("account","contract",date(2026,10,7),date(2026,10,8))

    async def test_customer_data_document_is_not_treated_as_energy(self):
        self.client._request = AsyncMock(return_value=(client_module.API,
            json.dumps([{"ResponseStatus":200,"Content":"<customer/>"}, {"ResponseStatus":200,"Content":feed()}])))
        self.assertEqual(len(await self.client.download("account","contract",date(2026,10,7),date(2026,10,8))),2)

    async def test_login_reads_current_public_login_target(self):
        self.client._request = AsyncMock(side_effect=[
            (client_module.BASE, "login"),
            (client_module.BASE, json.dumps({"value":[{"Key":"TivoliSignInUrl","Value":client_module.LOGIN}]})),
            (client_module.BASE,"signed in"),
        ])
        self.client._customer = AsyncMock(return_value={"Accounts":[]})
        await self.client.login()
        request = self.client._request.call_args_list[2]
        self.assertEqual(request.args, ("POST", client_module.LOGIN))
        self.assertEqual(request.kwargs["data"]["password"], "private+password&value")

    async def test_invalid_credentials_do_not_get_retried(self):
        self.client._request = AsyncMock(side_effect=[
            (client_module.BASE,"login"),(client_module.BASE,json.dumps({"value":[]})),
            (client_module.BASE+"/login?EC=0x132120c8","invalid"),
        ])
        with self.assertRaises(client_module.AuthenticationError):
            await self.client.login()
        self.assertEqual(self.client._request.await_count,3)


if __name__ == "__main__":
    unittest.main()

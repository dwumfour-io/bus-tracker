"""Regression coverage for route isolation and aging arrival estimates."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from google.transit import gtfs_realtime_pb2

import api


@pytest.fixture
def now(monkeypatch):
    value = datetime(2026, 9, 30, 8, 8, 30, tzinfo=api.EASTERN_TZ)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", Clock)
    return value


def prediction(**fields):
    return {
        "rt": "13", "stpid": "1009", "vid": "1234", "rtdir": "OUTBOUND",
        "tmstmp": "20260930 08:08:20", "prdtm": "20260930 08:10:00",
        "prdctdn": "3", "typ": "A", **fields,
    }


def arrivals(*records):
    result = api._format_truetime_response({"bustime-response": {"prd": list(records)}}, "13", "1009")
    assert result is not None
    return result["predictions"]["to_west_view"]["arrivals"]


def test_absolute_prediction_overrides_old_countdown(now):
    record, = arrivals(prediction())
    assert record["minutes"] == 1
    assert record["arrival_timestamp"] == (now + timedelta(seconds=90)).timestamp()
    assert record["source_timestamp"] == (now - timedelta(seconds=10)).timestamp()
    assert record["route"] == "13"


@pytest.mark.parametrize("countdown", ["DUE", "bad", None])
def test_non_numeric_countdown_does_not_discard_valid_timestamp(now, countdown):
    record, = arrivals(prediction(prdctdn=countdown))
    assert record["arrival_timestamp"] == (now + timedelta(seconds=90)).timestamp()
    assert record["is_due"] is (countdown == "DUE")


def test_bad_row_does_not_discard_other_arrivals(now):
    records = arrivals(prediction(prdtm="bad", prdctdn="bad"), prediction())
    assert len(records) == 1


def test_countdown_fallback_uses_source_time_not_fetch_time(now):
    record, = arrivals(prediction(prdtm="bad", tmstmp="20260930 08:07:30", prdctdn="3"))
    assert record["arrival_timestamp"] == (now + timedelta(minutes=2)).timestamp()
    assert record["minutes"] == 2


def test_unusable_time_is_not_fabricated(now):
    assert arrivals(prediction(prdtm="bad", tmstmp=None, prdctdn="3")) == []


def test_old_prediction_is_removed(now):
    assert arrivals(prediction(prdtm="20260930 08:00:00")) == []


def test_other_routes_unknown_routes_and_other_stops_are_excluded(now):
    records = arrivals(prediction(rt="8"), prediction(rt=""), prediction(stpid="619"), prediction())
    assert len(records) == 1
    assert records[0]["route"] == "13"


def test_departure_is_not_labeled_as_arrival(now):
    record, = arrivals(prediction(typ="D"))
    assert record["event_type"] == "departure"


def test_requests_seconds_from_truetime(now, monkeypatch):
    get = Mock(return_value=Mock(status_code=200, text='{"bustime-response":{"prd":[]}}'))
    monkeypatch.setattr(api, "PAAC_API_KEY", "test")
    monkeypatch.setattr(api.requests, "get", get)
    api.get_predictions_truetime("13", "1009")
    assert get.call_args.kwargs["params"]["tmres"] == "s"


def test_gtfs_predictions_carry_absolute_times_and_only_selected_route(now, monkeypatch):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(now.timestamp()) - 10
    for route in ("8", "13"):
        entity = feed.entity.add()
        entity.id = route
        entity.trip_update.trip.trip_id = "test-" + route
        entity.trip_update.trip.route_id = route
        entity.trip_update.timestamp = int(now.timestamp()) - 20
        stop = entity.trip_update.stop_time_update.add()
        stop.stop_id = "1009"
        stop.arrival.time = int(now.timestamp()) + 119
    monkeypatch.setattr(api, "_get_gtfsrt_feed", lambda: feed)
    data = api.get_predictions_gtfsrt("13", "1009")
    record, = data["predictions"]["to_west_view"]["arrivals"]
    assert record["route"] == "13"
    assert record["minutes"] == 1
    assert record["arrival_timestamp"] == int(now.timestamp()) + 119
    assert record["source_timestamp"] == int(now.timestamp()) - 20


def test_static_predictions_have_absolute_times(now):
    data = api.get_predictions_static("13", "1009")
    records = data["predictions"]["to_west_view"]["arrivals"]
    assert records
    assert all(record["arrival_timestamp"] > now.timestamp() for record in records)
    assert all(record["route"] == "13" and not record["is_live"] for record in records)


def test_westview_single_route_response_has_clock_and_no_cache(now, monkeypatch):
    fallback = Mock(return_value=api._empty_predictions_response("West View", "13", "619"))
    monkeypatch.setattr(api, "get_predictions_with_fallback", fallback)
    response = api.app.test_client().get("/predictions?route=13&stop=westview")
    assert response.status_code == 200
    assert response.json["route"] == "13"
    assert response.json["server_time"] == now.timestamp()
    assert response.headers["Cache-Control"] == "no-store"
    fallback.assert_called_once_with("13", "619")


def source_data(source, record, direction="to_west_view"):
    return {
        "data_source": source, "is_live": record.get("is_live", False),
        "predictions": {key: {"arrivals": [record] if key == direction else []}
                        for key in ("to_west_view", "to_downtown")},
    }


@pytest.mark.parametrize("primary", ["scheduled", "stale", "unavailable"])
def test_non_live_primary_cannot_block_live_fallback(now, monkeypatch, primary):
    record = {"minutes": 5, "is_live": primary == "stale", "source_timestamp": now.timestamp() - 300}
    if primary == "scheduled":
        record["prediction_type"] = "scheduled"
    truetime = None if primary == "unavailable" else source_data("truetime", record)
    gtfs = source_data("gtfs-rt", {"minutes": 3, "is_live": True, "source_timestamp": now.timestamp()})
    monkeypatch.setattr(api, "PAAC_API_KEY", "test")
    monkeypatch.setattr(api, "get_predictions_truetime", lambda *args: truetime)
    fallback = Mock(return_value=gtfs)
    monkeypatch.setattr(api, "get_predictions_gtfsrt", fallback)
    monkeypatch.setattr(api, "get_predictions_static", lambda *args: None)
    result = api.get_predictions_with_fallback("13", "1009")
    fallback.assert_called_once_with("13", "1009")
    assert result["is_live"] is True
    assert result["predictions"]["to_west_view"]["arrivals"][0]["minutes"] == 3
    assert result["live_sources"]["gtfs-rt"] == "live"


def test_missing_direction_is_filled_from_second_live_source(now, monkeypatch):
    tt = source_data("truetime", {"minutes": 5, "is_live": True})
    gtfs = source_data("gtfs-rt", {"minutes": 8, "is_live": True}, "to_downtown")
    monkeypatch.setattr(api, "get_predictions_truetime", lambda *args: tt)
    monkeypatch.setattr(api, "get_predictions_gtfsrt", lambda *args: gtfs)
    monkeypatch.setattr(api, "get_predictions_static", lambda *args: None)
    result = api.get_predictions_with_fallback("13", "619")
    assert result["data_source"] == "truetime+gtfs-rt"
    assert result["predictions"]["to_downtown"]["arrivals"][0]["minutes"] == 8
    assert result["predictions"]["to_west_view"]["arrivals"][0]["minutes"] == 5


def test_failed_live_feeds_produce_schedule_with_explanation(now, monkeypatch):
    monkeypatch.setattr(api, "PAAC_API_KEY", "")
    monkeypatch.setattr(api, "get_predictions_truetime", lambda *args: None)
    monkeypatch.setattr(api, "get_predictions_gtfsrt", lambda *args: None)
    result = api.app.test_client().get("/predictions?route=13&stop=1009").json
    assert result["is_live"] is False
    assert result["data_source"] == "gtfs-static"
    assert result["live_sources"] == {"truetime": "not_configured", "gtfs-rt": "unavailable"}
    assert result["predictions"]["to_west_view"]["arrivals"]


def test_unknown_gtfs_trip_at_inbound_stop_is_not_misrouted(now, monkeypatch):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(now.timestamp())
    entity = feed.entity.add()
    entity.id = "new-trip"
    entity.trip_update.trip.trip_id = "new-schedule-trip"
    entity.trip_update.trip.route_id = "13"
    stop = entity.trip_update.stop_time_update.add()
    stop.stop_id = "1016"
    stop.departure.time = int(now.timestamp()) + 300
    monkeypatch.setattr(api, "_get_gtfsrt_feed", lambda: feed)
    result = api.get_predictions_gtfsrt("13", "1016")
    assert result["predictions"]["to_west_view"]["arrivals"] == []
    arrival, = result["predictions"]["to_downtown"]["arrivals"]
    assert arrival["event_type"] == "departure"
    assert arrival["is_live"] is True


def test_stale_gtfs_is_not_presented_as_live(now, monkeypatch):
    gtfs = source_data("gtfs-rt", {"minutes": 5, "is_live": True, "source_timestamp": now.timestamp() - 300})
    monkeypatch.setattr(api, "get_predictions_truetime", lambda *args: None)
    monkeypatch.setattr(api, "get_predictions_gtfsrt", lambda *args: gtfs)
    result = api.get_predictions_with_fallback("13", "1009")
    assert result["is_live"] is False
    assert result["live_sources"]["gtfs-rt"] == "stale"


def test_schedule_identity_prevents_duplicate_late_bus():
    live = source_data("gtfs-rt", {"minutes": 25, "trip_id": "same", "is_live": True})
    schedule = source_data("gtfs-static", {"minutes": 5, "trip_id": "same", "is_live": False})
    result = api._merge_live_and_scheduled(live, schedule)
    assert len(result["predictions"]["to_west_view"]["arrivals"]) == 1


def test_distinct_scheduled_trip_is_not_hidden_by_nearby_live_bus():
    live = source_data("gtfs-rt", {"minutes": 5, "trip_id": "one", "is_live": True})
    schedule = source_data("gtfs-static", {"minutes": 7, "trip_id": "two", "is_live": False})
    result = api._merge_live_and_scheduled(live, schedule)
    assert len(result["predictions"]["to_west_view"]["arrivals"]) == 2


def test_health_reports_key_presence_without_exposing_it(monkeypatch):
    monkeypatch.setattr(api, "PAAC_API_KEY", "never-return-this-key")
    result = api.app.test_client().get("/health")
    assert result.json["version"] == "1.3.0"
    assert result.json["live_tracking"]["truetime_configured"] is True
    assert "never-return-this-key" not in result.get_data(as_text=True)

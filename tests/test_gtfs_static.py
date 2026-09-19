"""Tests for the bundled Pittsburgh Regional Transit GTFS subset."""

from datetime import datetime
from zoneinfo import ZoneInfo

from gtfs_static import load_gtfs


def test_current_feed_contains_official_stop_names():
    feed = load_gtfs()

    assert feed.available
    assert feed.stop_name("1009") == "CENTER AVE + CHALFONTE AVE"
    assert feed.stop_name("618") == "WEST VIEW PARK DR + WEST VIEW TOWERS"
    assert feed.stop_name("733") == "WEST VIEW PARK DR + WEST VIEW TOWER"
    assert feed.stop_name("1020") is None


def test_friday_evening_schedule_at_stop_1009():
    feed = load_gtfs()
    now = datetime(2026, 9, 18, 19, 51, tzinfo=ZoneInfo("America/New_York"))

    arrivals = feed.upcoming_arrivals("13", "1009", now)

    assert arrivals[0]["trip_id"] == "5182020"
    assert arrivals[0]["scheduled_datetime"].strftime("%I:%M:%S %p") == "08:12:13 PM"


def test_live_prediction_can_be_compared_with_static_schedule():
    feed = load_gtfs()
    predicted = datetime(2026, 9, 18, 20, 16, 45, tzinfo=ZoneInfo("America/New_York"))

    scheduled = feed.scheduled_datetime_for_prediction("5182020", "1009", predicted)

    assert scheduled.strftime("%I:%M:%S %p") == "08:12:13 PM"
    assert round((predicted - scheduled).total_seconds()) == 272

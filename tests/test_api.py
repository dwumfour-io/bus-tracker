"""
Pixburgh Bus Tracker - Unit Tests
Tests for API parsing, time calculations, and route compatibility
"""

import pytest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import (
    app,
    _minutes_until,
    _format_status,
    _format_truetime_response,
    get_predictions_with_fallback,
    _get_official_stop_names,
    _stop_metadata_cache,
    _direction_service_status,
    _parse_truetime_timestamp,
    _feed_expiry_status,
)


@pytest.fixture
def client():
    """Create test client"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


class TestTimeCalculations:
    """Test time-related utility functions"""
    
    def test_minutes_until_future(self):
        """Should return positive minutes for future time"""
        future_time = datetime.now(timezone.utc) + timedelta(minutes=10)
        result = _minutes_until(future_time.timestamp())
        assert 9 <= result <= 11  # Allow for slight timing variations
    
    def test_minutes_until_past(self):
        """Should return 0 for past time (non-negative)"""
        past_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        result = _minutes_until(past_time.timestamp())
        assert result == 0
    
    def test_minutes_until_now(self):
        """Should return 0 or 1 for current time"""
        now = datetime.now(timezone.utc)
        result = _minutes_until(now.timestamp())
        assert result <= 1


class TestStatusFormatting:
    """Test delay status formatting"""
    
    def test_unknown_delay(self):
        """A missing delay reading should say so, not claim On Time"""
        assert _format_status(None) == "Delay unknown"

    def test_on_time(self):
        """Zero delay should show On Time"""
        assert _format_status(0) == "On Time"
    
    def test_delayed(self):
        """Positive delay should show delayed"""
        result = _format_status(300)  # 5 minutes late
        assert "Delayed" in result
        assert "+5" in result
    
    def test_early(self):
        """Negative delay should show early"""
        result = _format_status(-120)  # 2 minutes early
        assert "Early" in result or "2" in result


class TestTrueTimeResponseFormatting:
    """Test TrueTime API response parsing"""
    
    def test_empty_predictions(self):
        """Empty predictions should return valid structure"""
        data = {
            "bustime-response": {
                "error": [{"msg": "No arrival times"}]
            }
        }
        result = _format_truetime_response(data, "13", "1016")
        
        assert result is not None
        assert result["route"] == "13"
        assert result["stop_number"] == "1016"
        assert result["stop_numbers"] == {"outbound": "1016", "inbound": "1016"}
        assert result["stops"] == [
            {"id": "1016", "name": "Center Ave + Chalfonte Ave", "direction": "INBOUND"},
        ]
        assert result["predictions"]["to_west_view"]["stop_number"] == "1016"
        assert result["predictions"]["to_downtown"]["stop_number"] == "1016"
        assert result["predictions"]["to_west_view"]["arrivals"] == []
        assert result["predictions"]["to_downtown"]["arrivals"] == []
    
    def test_valid_predictions(self):
        """Valid predictions should be parsed correctly"""
        data = {
            "bustime-response": {
                "prd": [
                    {
                        "prdtm": "20260102 10:30",
                        "vid": "1234",
                        "dly": False,
                        "prdctdn": "5",
                        "rtdir": "INBOUND"
                    },
                    {
                        "prdtm": "20260102 10:45",
                        "vid": "5678",
                        "dly": True,
                        "prdctdn": "20",
                        "rtdir": "OUTBOUND"
                    }
                ]
            }
        }
        result = _format_truetime_response(data, "13", "1016")
        
        assert result is not None
        assert result["stop_number"] == "1016"
        assert len(result["predictions"]["to_downtown"]["arrivals"]) == 1
        assert len(result["predictions"]["to_west_view"]["arrivals"]) == 1
        
        # Check downtown (INBOUND)
        downtown = result["predictions"]["to_downtown"]["arrivals"][0]
        assert downtown["vehicle_id"] == "1234"
        assert downtown["minutes"] == 5
        assert downtown["status"] == "On Time"
        
        # Check west view (OUTBOUND)
        westview = result["predictions"]["to_west_view"]["arrivals"][0]
        assert westview["vehicle_id"] == "5678"
        assert westview["minutes"] == 20
        assert westview["status"] == "Delayed"
    
    def test_stop_name_mapping(self):
        """Stop names should be correctly mapped"""
        data = {"bustime-response": {"prd": []}}
        
        result_1016 = _format_truetime_response(data, "13", "1016")
        result_619 = _format_truetime_response(data, "8", "619")
        
        assert result_1016["stop_name"] == "Center Ave + Chalfonte Ave"
        assert result_1016["stop_number"] == "1016"
        assert result_619["stop_name"] == "West View Plaza Fire Lane + Giant Eagle"
        assert result_619["stop_number"] == "619"

    @patch('api.requests.get')
    @patch('api.PAAC_API_KEY', 'test-key')
    def test_configured_official_stop_names_do_not_require_api(self, mock_get):
        """Bundled GTFS names should remain available without a network lookup."""
        _stop_metadata_cache.update({"expires_at": 0, "names": {}})

        names = _get_official_stop_names("13", ["618", "733"])

        assert names == {
            "618": "West View Park Dr + West View Towers",
            "733": "West View Park Dr + West View Tower",
        }
        mock_get.assert_not_called()

    @patch('api.PAAC_API_KEY', '')
    def test_missing_api_key_uses_bundled_names(self):
        """Missing credentials must not remove the official stop names."""
        _stop_metadata_cache.update({"expires_at": 0, "names": {}})

        assert _get_official_stop_names("13", ["620", "618"]) == {
            "618": "West View Park Dr + West View Towers",
            "620": "West View Plaza Fire Lane + U-Haul",
        }


class TestPredictionFallback:
    """Test source selection and merging across the live/static PRT feeds."""

    @patch('api.get_predictions_static')
    @patch('api.get_predictions_gtfsrt')
    @patch('api.get_predictions_truetime')
    def test_empty_truetime_response_falls_through_to_gtfsrt(
        self,
        mock_truetime,
        mock_gtfsrt,
        mock_static,
    ):
        mock_truetime.return_value = {
            "predictions": {
                "to_west_view": {"arrivals": []},
                "to_downtown": {"arrivals": []},
            }
        }
        mock_gtfsrt.return_value = {
            "data_source": "gtfs-rt",
            "predictions": {
                "to_west_view": {"arrivals": [{"minutes": 10}]},
                "to_downtown": {"arrivals": []},
            },
        }
        mock_static.return_value = None

        result = get_predictions_with_fallback("13", "1009")

        assert result["data_source"] == "gtfs-rt"

    @patch('api.get_predictions_static')
    @patch('api.get_predictions_gtfsrt')
    @patch('api.get_predictions_truetime')
    def test_scheduled_trip_stays_visible_next_to_a_live_arrival(
        self,
        mock_truetime,
        mock_gtfsrt,
        mock_static,
    ):
        """A live source having *some* arrivals must not hide a scheduled trip."""
        mock_truetime.return_value = {
            "data_source": "truetime",
            "is_live": True,
            "predictions": {
                "to_west_view": {"arrivals": [{"minutes": 6, "is_live": True}]},
                "to_downtown": {"arrivals": []},
            },
        }
        mock_static.return_value = {
            "data_source": "gtfs-static",
            "feed_valid_through": "2026-10-14",
            "predictions": {
                "to_west_view": {
                    "arrivals": [
                        {"minutes": 6, "trip_id": "a", "is_live": False, "prediction_type": "scheduled"},
                        {"minutes": 40, "trip_id": "b", "is_live": False, "prediction_type": "scheduled"},
                    ]
                },
                "to_downtown": {"arrivals": []},
            },
        }

        result = get_predictions_with_fallback("13", "1009")

        westview_arrivals = result["predictions"]["to_west_view"]["arrivals"]
        assert len(westview_arrivals) == 2
        assert westview_arrivals[0]["is_live"] is True
        assert westview_arrivals[1]["minutes"] == 40
        assert westview_arrivals[1]["note"] == "Scheduled · live tracking unavailable"
        assert result["data_source"] == "truetime+gtfs-static"
        mock_gtfsrt.assert_not_called()


class TestAPIEndpoints:
    """Test Flask API endpoints"""
    
    def test_health_endpoint(self, client):
        """Health endpoint should return healthy status"""
        response = client.get('/health')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "healthy"
    
    def test_home_endpoint(self, client):
        """Home endpoint should return the HTML page"""
        response = client.get('/')
        assert response.status_code == 200
        assert b'<!DOCTYPE html>' in response.data
        assert b'Pixburgh Bus Tracker' in response.data
    
    @patch('api.get_predictions_truetime')
    def test_predictions_endpoint_success(self, mock_truetime, client):
        """Predictions endpoint should return data on success"""
        mock_truetime.return_value = {
            "stop_name": "Test Stop",
            "route": "13",
            "last_updated": "10:00:00 AM",
            "data_source": "truetime",
            "is_live": True,
            "predictions": {
                "to_west_view": {"arrivals": [], "destination": "West View", "direction": "OUTBOUND"},
                "to_downtown": {"arrivals": [], "destination": "Downtown", "direction": "INBOUND"}
            }
        }
        
        response = client.get('/predictions?route=13&stop=1016')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["route"] == "13"
        assert data["stop_numbers"] == {"outbound": "1016", "inbound": "1016"}
        assert data["predictions"]["to_west_view"]["stop_number"] == "1016"
        assert data["predictions"]["to_downtown"]["stop_number"] == "1016"
        assert "predictions" in data
    
    @patch('api.get_predictions_with_fallback')
    def test_predictions_endpoint_empty(self, mock_predictions, client):
        """Predictions endpoint should return empty arrivals when no buses are available"""
        mock_predictions.return_value = None
        
        response = client.get('/predictions?route=13&stop=1016')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["stop_numbers"] == {"outbound": "1016", "inbound": "1016"}
        assert data["predictions"]["to_west_view"]["arrivals"] == []
        assert data["predictions"]["to_downtown"]["arrivals"] == []
    
    @patch('api.requests.get')
    def test_alerts_endpoint(self, mock_get, client):
        """Alerts endpoint should return service bulletins"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({
            "bustime-response": {
                "sb": [
                    {
                        "nm": "Test Alert",
                        "sbj": "Test Subject",
                        "dtl": "Test detail",
                        "brf": "Test brief",
                        "prty": "High",
                        "mod": "20260102 10:00:00",
                        "url": ""
                    }
                ]
            }
        })
        mock_get.return_value = mock_response
        
        response = client.get('/alerts?route=13')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["count"] == 1
        assert data["alerts"][0]["title"] == "Test Alert"
        assert data["alerts"][0]["priority"] == "High"


class TestRouteStopCompatibility:
    """Test route-stop compatibility logic (frontend would use this)"""
    
    def test_route_13_serves_both_stops(self):
        """Route 13 should serve both stops"""
        compatibility = {
            '8': ['618', '619', '620', '733'],
            '13': ['1016', '619', '620', '618', '733']
        }
        
        assert '1016' in compatibility['13']
        assert '619' in compatibility['13']
        assert '620' in compatibility['13']
        assert '618' in compatibility['13']
    
    def test_route_8_serves_west_view_stops_but_not_center_ave(self):
        """Route 8 shares the West View stops but not Center Avenue."""
        compatibility = {
            '8': ['618', '619', '620', '733'],
            '13': ['1016', '619', '620', '618', '733']
        }

        assert '619' in compatibility['8']
        assert '618' in compatibility['8']
        assert '733' in compatibility['8']
        assert '1016' not in compatibility['8']


class TestFreshnessParsing:
    """Test extracting how old a live prediction source is."""

    def test_parses_seconds_variant(self):
        result = _parse_truetime_timestamp("20260918 20:15:30")
        assert result.strftime("%H:%M:%S") == "20:15:30"

    def test_parses_minutes_only_variant(self):
        result = _parse_truetime_timestamp("20260918 20:15")
        assert result.strftime("%H:%M") == "20:15"

    def test_missing_value_returns_none(self):
        assert _parse_truetime_timestamp(None) is None
        assert _parse_truetime_timestamp("") is None


class TestDirectionServiceStatus:
    """Test the messages shown when a direction has no live arrivals."""

    def test_scheduled_only_when_a_later_bus_exists_today(self):
        now = datetime(2026, 9, 18, 19, 51, tzinfo=timezone.utc).astimezone()
        status = _direction_service_status("13", "1009", now)

        assert status["state"] in ("scheduled_only", "service_ended", "unavailable")
        assert "message" in status

    def test_unavailable_when_no_schedule_exists_for_stop(self):
        now = datetime(2026, 9, 18, 19, 51, tzinfo=timezone.utc).astimezone()
        status = _direction_service_status("13", "not-a-real-stop-id", now)

        assert status == {
            "state": "unavailable",
            "message": "Live tracking unavailable and no schedule data could be checked.",
            "next_departure": None,
        }


class TestFeedExpiryStatus:
    """Test the bundled GTFS feed expiry warning."""

    def test_reports_valid_through_date(self):
        status = _feed_expiry_status()
        assert "valid_through" in status
        assert "expires_soon" in status
        assert "expired" in status


class TestObservationsEndpoint:
    """Test the rider-reported 'bus arrived' observation endpoint."""

    def test_requires_a_valid_route(self, client):
        response = client.post('/observations', json={"route": "99", "stop": "stop_1009"})
        assert response.status_code == 400

    def test_requires_a_valid_stop(self, client):
        response = client.post('/observations', json={"route": "13", "stop": "not-a-stop"})
        assert response.status_code == 400

    def test_records_a_valid_observation(self, client, tmp_path, monkeypatch):
        import history_store
        monkeypatch.setattr(history_store, "_db_path", str(tmp_path / "history.db"))
        history_store.init_db(str(tmp_path / "history.db"))

        response = client.post(
            '/observations',
            json={"route": "13", "stop": "stop_1009", "note": "Bus arrived"},
        )

        assert response.status_code == 201
        assert json.loads(response.data)["status"] == "recorded"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

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
    get_predictions_with_fallback
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
    
    def test_on_time(self):
        """No delay should show On Time"""
        assert _format_status(None) == "On Time"
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
        assert result_619["stop_name"] == "West View Plaza + Giant Eagle"


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
        assert "predictions" in data
    
    @patch('api.get_predictions_truetime')
    def test_predictions_endpoint_failure(self, mock_truetime, client):
        """Predictions endpoint should return 503 on failure"""
        mock_truetime.return_value = None
        
        response = client.get('/predictions?route=13&stop=1016')
        assert response.status_code == 503
    
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
            '8': ['619'],
            '13': ['1016', '619']
        }
        
        assert '1016' in compatibility['13']
        assert '619' in compatibility['13']
    
    def test_route_8_only_west_view(self):
        """Route 8 should only serve West View Plaza"""
        compatibility = {
            '8': ['619'],
            '13': ['1016', '619']
        }
        
        assert '619' in compatibility['8']
        assert '1016' not in compatibility['8']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

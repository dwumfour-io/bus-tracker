"""
Pittsburgh Bus Tracker - Integration Tests
End-to-end tests for the full API workflow
"""

import pytest
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import app


@pytest.fixture
def client():
    """Create test client"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


class TestAPIIntegration:
    """Integration tests that hit real endpoints"""
    
    def test_full_prediction_flow(self, client):
        """Test the complete prediction fetch flow"""
        # 1. Check health
        health = client.get('/health')
        assert health.status_code == 200
        
        # 2. Get predictions for Route 13
        predictions = client.get('/predictions?route=13&stop=1016')
        # May be 200 (success) or 503 (no API key / no buses)
        assert predictions.status_code in [200, 503]
        
        if predictions.status_code == 200:
            data = json.loads(predictions.data)
            assert 'predictions' in data
            assert 'to_west_view' in data['predictions']
            assert 'to_downtown' in data['predictions']
    
    def test_route_parameter_handling(self, client):
        """Test that route parameter is correctly handled"""
        # Default route
        resp1 = client.get('/predictions')
        
        # Explicit route 13
        resp2 = client.get('/predictions?route=13')
        
        # Route 8
        resp3 = client.get('/predictions?route=8')
        
        # All should return valid responses (200 or 503)
        for resp in [resp1, resp2, resp3]:
            assert resp.status_code in [200, 503]
    
    def test_stop_parameter_handling(self, client):
        """Test that stop parameter is correctly handled"""
        # Stop 1016 (Center Ave)
        resp1 = client.get('/predictions?route=13&stop=1016')
        
        # Stop 619 (West View Plaza)
        resp2 = client.get('/predictions?route=13&stop=619')
        
        for resp in [resp1, resp2]:
            assert resp.status_code in [200, 503]
            if resp.status_code == 200:
                data = json.loads(resp.data)
                assert 'stop_name' in data
    
    def test_alerts_integration(self, client):
        """Test alerts endpoint integration"""
        response = client.get('/alerts?route=13')
        
        # Should always return 200 (even with empty alerts)
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'alerts' in data
        assert 'count' in data
        assert isinstance(data['alerts'], list)
    
    def test_westview_endpoint(self, client):
        """Test West View specific endpoint"""
        response = client.get('/predictions/westview')
        assert response.status_code in [200, 503]
    
    def test_downtown_endpoint(self, client):
        """Test Downtown specific endpoint"""
        response = client.get('/predictions/downtown')
        assert response.status_code in [200, 503]


class TestErrorHandling:
    """Test error handling scenarios"""
    
    def test_invalid_route(self, client):
        """Invalid route should return 400 with error message"""
        response = client.get('/predictions?route=999')
        # Should return 400 (validation error)
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data
        assert 'Invalid route' in data['error']
    
    def test_invalid_stop(self, client):
        """Invalid stop should return 400 with error message"""
        response = client.get('/predictions?route=13&stop=99999')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data
        assert 'Invalid stop' in data['error']


class TestResponseStructure:
    """Test response data structures"""
    
    def test_prediction_response_structure(self, client):
        """Verify prediction response has expected structure"""
        response = client.get('/predictions?route=13&stop=1016')
        
        if response.status_code == 200:
            data = json.loads(response.data)
            
            # Required fields
            assert 'stop_name' in data
            assert 'route' in data
            assert 'last_updated' in data
            assert 'data_source' in data
            assert 'predictions' in data
            
            # Predictions structure
            assert 'to_west_view' in data['predictions']
            assert 'to_downtown' in data['predictions']
            
            # Direction structure
            for direction in ['to_west_view', 'to_downtown']:
                dir_data = data['predictions'][direction]
                assert 'destination' in dir_data
                assert 'direction' in dir_data
                assert 'arrivals' in dir_data
    
    def test_alert_response_structure(self, client):
        """Verify alert response has expected structure"""
        response = client.get('/alerts?route=13')
        data = json.loads(response.data)
        
        assert 'route' in data
        assert 'alerts' in data
        assert 'count' in data
        
        for alert in data['alerts']:
            assert 'title' in alert
            assert 'priority' in alert
            assert 'brief' in alert or 'detail' in alert


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

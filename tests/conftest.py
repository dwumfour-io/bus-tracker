"""Keep routine tests independent of the live agency download."""

import pytest
import api


@pytest.fixture(autouse=True)
def disable_schedule_download(monkeypatch):
    monkeypatch.setattr(api, "GTFS_STATIC_FEED_URL", "")

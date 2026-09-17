#!/usr/bin/env python3

import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import atlas_app_store
import atlas_signal_refresher


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db = pathlib.Path(temp_dir) / "atlas.db"
        atlas_app_store.init_db(db)
        response = mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch.object(atlas_signal_refresher.urllib.request, "urlopen", return_value=response):
            result = atlas_signal_refresher.refresh_service_reachability(db, (("A", "http://a"), ("B", "http://b")))
        assert result["value"]["total"] == 2
        assert result["value"]["unreachable_count"] == 0
        stored = atlas_app_store.get_signal_observation(db, "service_reachability")
        assert stored and stored["status"] == "ok"
        assert stored["value"]["unreachable_count"] == 0
        now = datetime(2026, 9, 8, 12, 10, tzinfo=ZoneInfo("America/Los_Angeles"))
        atlas_app_store.put_signal_observation(
            db, "freshness_test", {"count": 1}, observed_at="2026-09-08T12:00:00-07:00"
        )
        assert atlas_app_store.get_fresh_signal_value(db, "freshness_test", 900, now) == {"count": 1}
        try:
            atlas_app_store.get_fresh_signal_value(db, "freshness_test", 300, now)
            raise AssertionError("stale observation accepted")
        except RuntimeError:
            pass
        atlas_app_store.put_signal_observation(
            db, "future_test", 1, observed_at="2026-09-08T12:11:00-07:00"
        )
        try:
            atlas_app_store.get_fresh_signal_value(db, "future_test", 900, now)
            raise AssertionError("future observation accepted")
        except RuntimeError:
            pass

        first = mock.MagicMock(side_effect=OSError("offline"))
        with mock.patch.object(atlas_signal_refresher.urllib.request, "urlopen", first):
            result = atlas_signal_refresher.refresh_service_reachability(db, (("A", "http://a"),))
        assert result["value"]["unreachable_count"] == 1
        assert result["value"]["services"][0]["error"] == "OSError"

        healthy = {
            "hostname": "host", "cpu_count": 4, "load_1m": 1.0,
            "memory_total": 1000, "memory_available": 500,
            "mounts": [{"mount": "/", "total": 1000, "used": 500}],
        }
        assert atlas_signal_refresher.assess_host_health(healthy) == (False, [])
        unhealthy = dict(healthy, load_1m=9.0, memory_available=50, mounts=[{"mount": "/", "total": 1000, "used": 950}])
        is_unhealthy, reasons = atlas_signal_refresher.assess_host_health(unhealthy)
        assert is_unhealthy and len(reasons) == 3

        def remote(user, host, command):
            if command == "host-health":
                return dict(healthy, hostname=host)
            return {
                "latest": {"status": "OK"}, "last_ok": {"starttime": 1788880000},
                "last_failure": None, "task_count": 5,
            }
        with mock.patch.object(atlas_signal_refresher, "run_remote", side_effect=remote):
            host_result = atlas_signal_refresher.refresh_host_health(db)
            backup_result = atlas_signal_refresher.refresh_backup_status(db)
        assert host_result["value"]["unhealthy_count"] == 0
        assert backup_result["value"]["latest_ok"] is True
        assert backup_result["value"]["last_success_age_hours"] is not None

        completed = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).with_name("atlas_signal_refresher.py")), "--help"],
            check=True, capture_output=True, text=True,
        )
        assert "--interval-seconds" in completed.stdout
    print("ATLAS signal refresher tests passed")


if __name__ == "__main__":
    main()

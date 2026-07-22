from __future__ import annotations

from longform_config import DEFAULT_CONFIG, validate_longform_config


def test_auto_upload_is_disabled_by_default() -> None:
    config = validate_longform_config(DEFAULT_CONFIG)

    assert config["longform"]["schedule"]["auto_upload"] is False


def test_auto_upload_can_be_enabled_with_schedule() -> None:
    config = validate_longform_config(
        {
            "longform": {
                "duration_minutes": 20,
                "resolution": "1920x1080",
                "fps": 30,
                "schedule": {"enabled": True, "auto_upload": True, "time": "03:10"},
            }
        }
    )

    schedule = config["longform"]["schedule"]
    assert schedule["enabled"] is True
    assert schedule["auto_upload"] is True
    assert schedule["time"] == "03:10"

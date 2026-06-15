import json

from app.services import config_store as config_store_module
from app.services.config_store import ConfigStore


def test_load_backfills_new_provider_fields_from_env(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "plex_url": "http://localhost:32400",
                "plex_token": "plex-token",
                "subsource_api_key": "subsource-key",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config_store_module.settings, "opensubtitles_api_key", "os-key")
    monkeypatch.setattr(config_store_module.settings, "opensubtitles_username", "os-user")
    monkeypatch.setattr(config_store_module.settings, "subdl_api_key", "subdl-key")

    runtime = ConfigStore(config_path).load()

    assert runtime.opensubtitles_api_key == "os-key"
    assert runtime.opensubtitles_username == "os-user"
    assert runtime.subdl_api_key == "subdl-key"

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["opensubtitles_api_key"] == "os-key"
    assert persisted["subdl_api_key"] == "subdl-key"

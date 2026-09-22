import json
from pathlib import Path


DEFAULT_SETTINGS = {
    "excluded_dirs": ["node_modules", ".git", ".vs", ".idea", "__pycache__"],
    "debounce_ms": 120,
    "result_limit": 200,
    "candidate_limit": 3000,
    "fuzzy_threshold": 58,

    # Smart long-term index maintenance.  Real-time watchdog is deliberately
    # OFF by default; periodic incremental synchronization is cheaper/stabler.
    "auto_index_enabled": True,
    "auto_index_mode": "smart",
    "auto_index_startup_check": True,
    "auto_index_startup_delay_sec": 20,
    "auto_index_idle_first": True,
    "auto_index_idle_seconds": 120,
    "auto_index_pause_on_search": True,
    "watch_changes": False,
    "background_update_settings_version": 1,

    "minimize_to_tray": True,
    "close_to_tray": True,
    "tray_notification": True,
    "global_hotkey_enabled": True,
    "global_hotkey": "Ctrl+Alt+Space",
    "quick_hotkey_enabled": True,
    "quick_hotkey": "Ctrl+Alt+F",
    "start_hidden_to_tray": False,
    "esc_hides_window": True,
    "start_with_windows": False,
    "search_history_enabled": True,
    "usage_ranking_enabled": True,
}


class SettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict:
        data = dict(DEFAULT_SETTINGS)
        user_data = {}
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    user_data = dict(loaded)

            # One-time migration from the old "watch everything + reindex on
            # startup" defaults to V2.9 smart scheduled maintenance.  Existing
            # users can still re-enable watchdog in Advanced settings.
            if int(user_data.get("background_update_settings_version", 0) or 0) < 1:
                user_data["auto_index_enabled"] = True
                user_data["auto_index_mode"] = "smart"
                user_data["auto_index_startup_check"] = True
                user_data["auto_index_startup_delay_sec"] = 20
                user_data["auto_index_idle_first"] = True
                user_data["auto_index_idle_seconds"] = 120
                user_data["auto_index_pause_on_search"] = True
                user_data["watch_changes"] = False
                user_data["background_update_settings_version"] = 1
                # Old key is no longer used, but leave it harmlessly in older
                # files rather than failing to parse a user's configuration.

            data.update(user_data)
        except Exception:
            pass
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        self.path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

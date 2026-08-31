import os
from typing import Optional

import yaml

CONFIG_PATH = os.path.join("data", "config.yaml")
DEFAULT_FEATURES = ["welcome", "goodbye", "auto_roles", "starboard", "logging", "report"]
LOGGING_EVENTS = ["member_ban", "member_kick", "member_timeout", "member_role_update", "role_create", "role_delete",
                  "role_update", "bot_permission_error"]

DEFAULT_TEMPLATE = """# Noyabot configuration
# Everything is keyed by guild id. Fill in your server's id and edit the
# settings below, then restart the bot. Channel/role ids can be copied from
# Discord with Developer Mode (right-click -> Copy ID)
#
# Placeholders supported in welcome/goodbye content:
#   {user}          - display name
#   {user_mention}  - @mention
#   {server}        - server name
#   {member_count}  - current member count

# Channel where the bot posts unhandled command errors (with tracebacks)
error_log_channel_id: null

guilds:
  "123456789012345678":

    # Master switches for each feature, toggle them here
    features:
      welcome: true
      goodbye: true
      auto_roles: true
      starboard: true
      logging: true
      report: true

    # ----- Welcome message -----
    welcome:
      channel_id: null          # channel id to post welcome messages in
      content: "Welcome {user_mention} to {server}! You are member #{member_count}"

    # ----- Goodbye message -----
    goodbye:
      channel_id: null
      content: "Bye {user} o/"

    # ----- Auto roles (assigned on join) -----
    auto_roles:
      - role_id: null           # role id to assign
        delay_seconds: 0        # wait this many seconds before assigning
    # - role_id: 222222222222222222
    #   delay_seconds: 10

    # ----- Starboard -----
    starboard:
      channel_id: null          # channel to post starred messages in
      threshold: 3              # stars needed to be posted
      emoji: "⭐"                # emoji that counts as a star
    # emoji: "<:wave:12345>"    # example using a custom emote
      excluded_channels: []     # list of channel ids to ignore

    # ----- Reports (user-reported messages) -----
    report:
      mod_channel_id: null      # channel where reports are sent

    # ----- Logging -----
    logging:
      channel_id: null          # channel where events are sent
      excluded_channels: []
      events:
        member_ban: true
        member_kick: true
        member_timeout: true
        member_role_update: true
        role_create: true
        role_delete: true
        role_update: true
        bot_permission_error: true
"""


class BotConfigError(Exception):
    """Raised when config.yaml is missing required structure or fails to parse"""


class GuildConfig:
    """Read-only view over a single guild's settings in config.yaml"""

    def __init__(self, guild_id: int, data: dict):
        self.guild_id = guild_id
        self._data = data

    # -------------------- FEATURES

    def get_feature_toggle(self, feature: str) -> bool:
        return bool(self._data.get("features", {}).get(feature, True))

    # -------------------- WELCOME & GOODBYE

    def get_welcome_config(self) -> dict:
        return self._data.get("welcome", {})

    def get_goodbye_config(self) -> dict:
        return self._data.get("goodbye", {})

    # -------------------- AUTO ROLES

    def get_auto_roles(self) -> list[dict]:
        return self._data.get("auto_roles", [])

    # -------------------- STARBOARD

    def get_starboard_config(self) -> dict:
        cfg = dict(self._data.get("starboard", {}))
        cfg.setdefault("channel_id", None)
        cfg.setdefault("threshold", 3)
        cfg.setdefault("emoji", "⭐")
        cfg.setdefault("excluded_channels", [])
        return cfg

    def get_starboard_excluded_channels(self) -> list[int]:
        return self.get_starboard_config().get("excluded_channels", [])

    # -------------------- REPORT

    def get_report_config(self) -> dict:
        cfg = dict(self._data.get("report", {}))
        cfg.setdefault("mod_channel_id", None)
        return cfg

    # -------------------- LOGGING

    def get_logging_config(self) -> dict:
        cfg = dict(self._data.get("logging", {}))
        cfg.setdefault("channel_id", None)
        cfg.setdefault("excluded_channels", [])
        cfg.setdefault("events", {})
        return cfg

    def get_logging_event(self, event_type: str) -> Optional[dict]:
        events = self.get_logging_config().get("events", {})
        if event_type not in events:
            return None
        return {"event_type": event_type, "enabled": bool(events[event_type])}

    def get_logging_excluded_channels(self) -> list[int]:
        return self.get_logging_config().get("excluded_channels", [])


class Config:
    """Loads, validates, and serves config.yaml. Cached after first load."""

    _instance: Optional["Config"] = None

    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._guilds: dict[int, dict] = {}
        self._error_log_channel_id: int | None = None
        self.load()

    @classmethod
    def get(cls) -> "Config":
        if cls._instance is None:
            cls._instance = Config()
        return cls._instance

    # -------------------- LOADING

    def load(self) -> None:
        if not os.path.exists(self.path):
            self._write_template()
            print(f"Created default config at {self.path} — edit it and restart to configure the bot.")
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise BotConfigError(f"Could not parse {self.path}: {e}") from e

        if not isinstance(raw, dict):
            raise BotConfigError(f"{self.path} must contain a mapping at the top level")

        # --- Bot-wide settings ---
        raw_error_channel = raw.get("error_log_channel_id")
        self._error_log_channel_id = int(raw_error_channel) if raw_error_channel else None

        # --- Per-guild settings ---
        guilds = raw.get("guilds", {})
        if not isinstance(guilds, dict):
            raise BotConfigError("'guilds' must be a mapping of guild id -> settings")

        self._guilds = {}
        for key, value in guilds.items():
            try:
                gid = int(key)
            except (ValueError, TypeError):
                raise BotConfigError(f"Invalid guild id {key!r} in {self.path} (must be an integer)")
            if not isinstance(value, dict):
                raise BotConfigError(f"Settings for guild {gid} must be a mapping")
            self._guilds[gid] = self._normalize_guild(value)

    def reload(self) -> None:
        self.load()

    # -------------------- NORMALIZATION

    @staticmethod
    def _normalize_guild(data: dict) -> dict:
        features = data.get("features", {})
        if not isinstance(features, dict):
            raise BotConfigError("'features' must be a mapping of feature -> bool")
        merged = {f: bool(features.get(f, True)) for f in DEFAULT_FEATURES}
        merged.update({k: bool(v) for k, v in features.items() if k not in merged})
        data["features"] = merged

        for section in ("welcome", "goodbye", "starboard", "report", "logging"):
            if not isinstance(data.get(section), dict):
                data[section] = {}

        if not isinstance(data.get("auto_roles"), list):
            data["auto_roles"] = []

        logging_cfg = data["logging"]
        events = logging_cfg.get("events", {})
        if not isinstance(events, dict):
            events = {}
        logging_cfg["events"] = {e: bool(events.get(e, False)) for e in LOGGING_EVENTS}
        return data

    def _write_template(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_TEMPLATE)

    # -------------------- ACCESS

    def for_guild(self, guild_id: int) -> GuildConfig:
        return GuildConfig(guild_id, self._guilds.get(guild_id, {}))

    def get_error_log_channel_id(self) -> int | None:
        return self._error_log_channel_id


def get_guild_config(guild_id: int) -> GuildConfig:
    """Convenience accessor used by cogs: get_guild_config(guild.id).get_feature_toggle(...)"""
    return Config.get().for_guild(guild_id)


def get_error_log_channel_id() -> int | None:
    """Bot-wide error log channel, read from config.yaml"""
    return Config.get().get_error_log_channel_id()
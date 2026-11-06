import dataclasses
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from kb import NAME

HOME_ENV = "KB_HOME"


@dataclass(frozen=True)
class Paths:
    config_file: Path
    data_dir: Path
    log_dir: Path
    cache_dir: Path

    @property
    def db(self):
        return self.data_dir / "kb.sqlite"

    @property
    def lock(self):
        return self.data_dir / "kb.lock"

    @property
    def keys_dir(self):
        return self.config_file.parent / "keys"

    @property
    def plugins_dir(self):
        return self.config_file.parent / "plugins"

    @property
    def seen_dir(self):
        return self.cache_dir / "seen"

    def log(self, name):
        return self.log_dir / f"{name}.log"

    def for_try(self):
        """The same config, with the database, logs and cache in a `try` folder of their own."""
        return dataclasses.replace(self, data_dir=self.data_dir / "try", log_dir=self.log_dir / "try",
                                   cache_dir=self.cache_dir / "try")

    @classmethod
    def under(cls, root):
        root = Path(root)
        return cls(root / "config.toml", root / "data", root / "logs", root / "cache")

    @classmethod
    def for_user(cls, env=None, platform=None, home=None):
        """KB_HOME puts everything in one folder. Otherwise the config is under ~/.config on both systems, and the
        database, logs and cache follow the platform: ~/Library on a Mac, the XDG folders on Linux."""
        env = os.environ if env is None else env
        platform = platform or sys.platform
        home = Path(home or os.path.expanduser("~"))
        if env.get(HOME_ENV):
            return cls.under(Path(env[HOME_ENV]).expanduser())

        def xdg(var, default):
            value = env.get(var, "")
            # The XDG spec says to ignore a relative path.
            return Path(value) if os.path.isabs(value) else home / default

        slug = NAME.lower()
        config = xdg("XDG_CONFIG_HOME", ".config") / slug / "config.toml"
        if platform == "darwin":
            library = home / "Library"
            return cls(config, library / "Application Support" / NAME, library / "Logs" / NAME,
                       library / "Caches" / NAME)
        return cls(config, xdg("XDG_DATA_HOME", ".local/share") / slug, xdg("XDG_STATE_HOME", ".local/state") / slug / "logs",
                   xdg("XDG_CACHE_HOME", ".cache") / slug)

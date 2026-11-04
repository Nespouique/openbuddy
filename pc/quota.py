"""Provider quota text ("used / limit") produced by a user-configured command."""

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

MIN_REFRESH_SECONDS = 60
DEFAULT_REFRESH_SECONDS = 300
COMMAND_TIMEOUT = 20
OUTPUT_LIMIT = 65536
TEXT_LIMIT = 24


def config_path():
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "openbuddy/quota.json"


def lookup(value, path):
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError("Field not found in command output: " + path)
        value = value[part]
    return value


def number(value, field):
    if type(value) not in (int, float):
        raise ValueError("Field is not a number: " + field)
    if value == int(value):
        return str(int(value))
    return ("%.1f" % value).rstrip("0").rstrip(".")


class QuotaConfig:
    def __init__(self, command, used, limit, label, refresh):
        self.command, self.used, self.limit = command, used, limit
        self.label, self.refresh = label, refresh


def load_config(path):
    if path.stat().st_size > 8192:
        raise ValueError("Quota configuration is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    providers = data.get("providers")
    name = data.get("provider")
    if not isinstance(providers, dict) or not isinstance(providers.get(name), dict):
        raise ValueError("Quota provider is not defined: %r" % (name,))
    provider = providers[name]
    command = provider.get("command")
    if isinstance(command, str):
        command = shlex.split(command)
    if (not isinstance(command, list) or not command
            or not all(isinstance(item, str) and item for item in command)):
        raise ValueError("Quota command must be a string or a list of strings")
    used, limit = provider.get("used"), provider.get("limit")
    if not isinstance(used, str) or not isinstance(limit, str):
        raise ValueError("Quota provider needs 'used' and 'limit' field names")
    label = provider.get("label", "")
    if not isinstance(label, str):
        raise ValueError("Quota label must be a string")
    refresh = data.get("refresh_seconds", DEFAULT_REFRESH_SECONDS)
    if type(refresh) not in (int, float) or refresh < MIN_REFRESH_SECONDS:
        raise ValueError("refresh_seconds must be at least %d" % MIN_REFRESH_SECONDS)
    return QuotaConfig(command, used, limit, label, refresh)


def format_quota(output, config):
    value = json.loads(output)
    used = number(lookup(value, config.used), config.used)
    limit = number(lookup(value, config.limit), config.limit)
    text = ("%s %s / %s" % (config.label, used, limit)).strip()
    if len(text) > TEXT_LIMIT or any(not 32 <= ord(char) <= 126 for char in text):
        raise ValueError("Quota text is too long or not printable ASCII")
    return text


def fetch(config):
    executable = shutil.which(config.command[0])
    if executable is None:
        raise ValueError("Quota command not found: " + config.command[0])
    result = subprocess.run([executable] + config.command[1:], capture_output=True, timeout=COMMAND_TIMEOUT,
                            stdin=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        raise ValueError("Quota command failed with exit code %d" % result.returncode)
    if len(result.stdout) > OUTPUT_LIMIT:
        raise ValueError("Quota command output is too large")
    return format_quota(result.stdout.decode("utf-8"), config)


class QuotaMonitor:
    """Keeps the last good text; a failing command never disturbs the bridge."""

    def __init__(self, path=None, fetcher=fetch):
        self.path = path or config_path()
        self.fetcher = fetcher
        self.text = ""
        self.error = None
        self.config = None

    async def run(self):
        while True:
            delay = DEFAULT_REFRESH_SECONDS
            try:
                self.config = await asyncio.to_thread(load_config, self.path)
                delay = self.config.refresh
                self.text = await asyncio.to_thread(self.fetcher, self.config)
                self.error = None
            except FileNotFoundError:
                self.text, self.error = "", None  # Quota display is optional.
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                # json.JSONDecodeError is a ValueError.
                if self.error != str(error):
                    print("Quota unavailable:", type(error).__name__, "-", error, flush=True)
                self.error = str(error)
            await asyncio.sleep(delay)

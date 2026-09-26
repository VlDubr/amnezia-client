"""Access to the Qt client's server scripts (client/server_scripts) and its `$VAR` substitution."""

import os
import re
from pathlib import Path

from app.config import REPO_SCRIPTS_DIR

_VAR = re.compile(r"\$([A-Z][A-Z0-9_]*)")
_EMPTY_VALUE_LINE = re.compile(r"^\s*\S+\s*=\s*$")


def scripts_dir() -> Path:
    return Path(os.environ.get("PANEL_SCRIPTS_DIR", REPO_SCRIPTS_DIR))


def script(folder: str | None, name: str) -> str:
    path = scripts_dir() / folder / name if folder else scripts_dir() / name
    return path.read_text(encoding="utf-8")


def replace_vars(text: str, variables: dict[str, str], keep_unknown: bool = True) -> str:
    """Replaces `$NAME` with variables[NAME]; unknown names stay as-is (shell variables in scripts)."""
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name in variables:
            return variables[name]
        return m.group(0) if keep_unknown else ""

    return _VAR.sub(sub, text)


def drop_empty_value_lines(text: str) -> str:
    """Every AWG parameter is optional: drop `Key =` lines whose value came out empty."""
    return "\n".join(line for line in text.split("\n") if not _EMPTY_VALUE_LINE.match(line))

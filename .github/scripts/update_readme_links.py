"""Keep the four README download links on the newest tagged Release asset."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
READMES = ("README.md", "README.en.md", "README.zh-CN.md", "README.zh-TW.md")
DOWNLOAD_LINE = re.compile(
    r"(?m)^### \[(?P<label>.+?)\]"
    r"\(https://github\.com/NakamuraShippo/LoomStroke/releases/download/"
    r"v\d+(?:\.\d+)+/LoomStroke-Windows-v\d+(?:\.\d+)+\.zip\)\r?$"
)


def rewrite_text(text: str, version: str) -> str:
    if not re.fullmatch(r"\d+(?:\.\d+)+", version):
        raise ValueError("Unsupported application version")

    def replace(match: re.Match[str]) -> str:
        label, count = re.subn(r"v\d+(?:\.\d+)+", f"v{version}", match["label"], count=1)
        if count != 1:
            raise ValueError("README download label has no version")
        url = (
            "https://github.com/NakamuraShippo/LoomStroke/releases/download/"
            f"v{version}/LoomStroke-Windows-v{version}.zip"
        )
        ending = "\r" if match.group(0).endswith("\r") else ""
        return f"### [{label}]({url}){ending}"

    updated, count = DOWNLOAD_LINE.subn(replace, text)
    if count != 1:
        raise ValueError("README must have exactly one versioned ZIP link")
    return updated


def update(version: str) -> list[str]:
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if package["project"]["version"] != version:
        raise ValueError("README update version differs from the public application version")
    replacements = {
        name: rewrite_text((ROOT / name).read_text(encoding="utf-8"), version) for name in READMES
    }
    changed = []
    for name, content in replacements.items():
        path = ROOT / name
        if path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            changed.append(name)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print("Updated README links:", ", ".join(update(args.version)) or "already current")


if __name__ == "__main__":
    main()

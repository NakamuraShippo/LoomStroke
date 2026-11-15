"""Create the current source-install ZIP from a committed public snapshot."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path

from update_readme_links import READMES, rewrite_text

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "LoomStroke-main/"  # Existing ZIP updater requires this root.
ROOT_FILES = {
    "CHANGELOG.md",
    "LICENSE",
    "LICENSE_JA",
    "THIRD_PARTY_LICENSES",
    "README.md",
    "README.en.md",
    "README.zh-CN.md",
    "README.zh-TW.md",
    "pyproject.toml",
    "requirements.txt",
    "main.py",
    "run.sh",
    "install.bat",
    "install_python.ps1",
    "boot_LoomStroke.bat",
    "boot_LoomStroke_noAI.bat",
    "update.bat",
    "update.ps1",
}
PREFIXES = ("src/loomstroke/", "img/", "docs/user/", "LICENSES/")
REQUIRED = {
    "src/loomstroke/__init__.py",
    "src/loomstroke/__main__.py",
    "src/loomstroke/io/lstroke_manifest_schema_v1.json",
    "src/loomstroke/ui/resources/i18n/loomstroke_ja.qm",
    "img/favicon.png",
    "LICENSES/LGPL-3.0.txt",
    "install.bat",
    "install_python.ps1",
    "boot_LoomStroke.bat",
    "update.bat",
    "update.ps1",
    "LICENSE",
    "LICENSE_JA",
    "THIRD_PARTY_LICENSES",
    "pyproject.toml",
    "requirements.txt",
    "CHANGELOG.md",
}
FORBIDDEN_NAMES = {"AGENTS.md", "CLAUDE.md", ".env", "credentials.json"}
FORBIDDEN_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".pt",
    ".pth",
    ".ckpt",
    ".safetensors",
    ".log",
    ".sqlite",
    ".sqlite3",
}
SECRET_PATTERNS = (
    re.compile(rb"gh[pousr]_[A-Za-z0-9_]{36,}"),
    re.compile(rb"sk-[A-Za-z0-9]{32,}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
)


def _git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def _version_and_notes() -> tuple[str, str]:
    package = tomllib.loads(_git("show", "HEAD:pyproject.toml").decode("utf-8"))
    version = package["project"]["version"]
    runtime = _git("show", "HEAD:src/loomstroke/__init__.py").decode("utf-8")
    changelog = _git("show", "HEAD:CHANGELOG.md").decode("utf-8")
    if not re.fullmatch(r"\d+(?:\.\d+)+", version):
        raise ValueError("Unsupported application version")
    if not re.search(rf'^__version__ = "{re.escape(version)}"$', runtime, re.MULTILINE):
        raise ValueError("Package and runtime versions differ")
    entries = list(re.finditer(r"^## \[v([^]]+)\] - .+$", changelog, re.MULTILINE))
    if not entries or entries[0].group(1) != version:
        raise ValueError("Latest changelog entry does not match the app version")
    post = _git("show", f"HEAD:release_posts/v{version}.md").decode("utf-8").strip()
    if not post or not post.startswith(f"LoomStroke ベータ版 v{version}"):
        raise ValueError("SNS announcement is missing or has a different version")
    details = f"https://github.com/NakamuraShippo/LoomStroke/blob/v{version}/CHANGELOG.md"
    notes = f"{post}\n\n詳細な変更履歴は [CHANGELOG.md]({details}) をご覧ください。\n"
    return version, notes


def _allowed(name: str) -> bool:
    return name in ROOT_FILES or name.startswith(PREFIXES)


def _check_member(name: str, data: bytes, info: zipfile.ZipInfo) -> None:
    parts = name.split("/")
    if (
        not _allowed(name)
        or any(part in {".", "..", ""} for part in parts)
        or any(part in FORBIDDEN_NAMES or part.lower().startswith(".env") for part in parts)
        or Path(name).suffix.lower() in FORBIDDEN_SUFFIXES
    ):
        raise ValueError(f"Unsafe release member: {name}")
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise ValueError(f"Linked release member: {name}")
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):
        raise ValueError(f"Possible credential in release member: {name}")


def create(output: Path) -> tuple[Path, Path, Path, int]:
    version, notes = _version_and_notes()
    output.mkdir(parents=True, exist_ok=True)
    asset = output / f"LoomStroke-Windows-v{version}.zip"
    checksum = output / f"{asset.name}.sha256"
    notes_file = output / f"release-notes-v{version}.md"
    if any(path.exists() for path in (asset, checksum, notes_file)):
        raise ValueError("Release output already exists")
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        source = Path(temporary) / "tracked.zip"
        staged = Path(temporary) / "release.zip"
        _git("archive", "--format=zip", f"--output={source}", "HEAD")
        names: set[str] = set()
        casefolded: set[str] = set()
        total_size = 0
        with (
            zipfile.ZipFile(source) as tracked,
            zipfile.ZipFile(
                staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as release,
        ):
            for info in tracked.infolist():
                if info.is_dir() or not _allowed(info.filename):
                    continue
                data = tracked.read(info)
                _check_member(info.filename, data, info)
                if info.filename in READMES:
                    try:
                        data = rewrite_text(data.decode("utf-8"), version).encode("utf-8")
                    except ValueError as exc:
                        raise ValueError(f"Invalid download link in {info.filename}") from exc
                if info.filename.lower() in casefolded:
                    raise ValueError(f"Duplicate release member: {info.filename}")
                names.add(info.filename)
                casefolded.add(info.filename.lower())
                total_size += len(data)
                if total_size > 1_073_741_824 or len(names) > 20_000:
                    raise ValueError("ZIP exceeds current updater limits")
                entry = zipfile.ZipInfo(PREFIX + info.filename, date_time=(1980, 1, 1, 0, 0, 0))
                entry.external_attr = 0o100644 << 16
                release.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
        if not REQUIRED.issubset(names):
            raise ValueError(f"Missing runtime files: {sorted(REQUIRED - names)}")
        if staged.stat().st_size > 209_715_200:
            raise ValueError("ZIP exceeds the updater's 200 MiB download limit")
        with zipfile.ZipFile(staged) as release:
            if release.testzip() is not None or len(release.namelist()) != len(names):
                raise ValueError("ZIP integrity check failed")
        staged.replace(asset)
    with asset.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum.write_text(f"{digest}  {asset.name}\n", encoding="ascii")
    notes_file.write_text(notes, encoding="utf-8")
    return asset, checksum, notes_file, len(names)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    asset, checksum, notes, count = create(args.output_dir)
    print(f"Validated {count} committed files: {asset}")
    print(f"SHA-256: {checksum}")
    print(f"Release notes: {notes}")


if __name__ == "__main__":
    main()

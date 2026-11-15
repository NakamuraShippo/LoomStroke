"""Guard the source ZIP's existing install and updater contract."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from create_release_archive import PREFIX, ROOT, _check_member, _version_and_notes, create


class ReleaseArchiveTests(unittest.TestCase):
    def test_rejects_secrets_and_development_files(self) -> None:
        info = zipfile.ZipInfo("source")
        for name, data in (
            ("src/loomstroke/.env", b"value"),
            ("src/loomstroke/AGENTS.md", b"value"),
            ("src/loomstroke/model.safetensors", b"value"),
            ("src/loomstroke/example.py", b"ghp_" + b"a" * 40),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _check_member(name, data, info)

    def test_archive_matches_installer_and_updater_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset, checksum, notes, count = create(Path(directory))
            version, _ = _version_and_notes()
            self.assertGreater(count, 2000)
            self.assertIn(f"-v{version}.zip", asset.name)
            with zipfile.ZipFile(asset) as release:
                members = set(release.namelist())
                self.assertEqual(release.testzip(), None)
                for name in (
                    "install.bat",
                    "boot_LoomStroke.bat",
                    "update.ps1",
                    "src/loomstroke/__init__.py",
                    "LICENSE",
                ):
                    self.assertIn(PREFIX + name, members)
                self.assertFalse(any(".github/" in name for name in members))
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            self.assertEqual(checksum.read_text(encoding="ascii"), f"{digest}  {asset.name}\n")
            second, _, _, _ = create(Path(directory) / "again")
            self.assertEqual(hashlib.sha256(second.read_bytes()).hexdigest(), digest)
            text = notes.read_text(encoding="utf-8")
            social_post = (ROOT / f"release_posts/v{version}.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith(social_post.strip() + "\n\n"))
            self.assertIn(f"/blob/v{version}/CHANGELOG.md", text)


if __name__ == "__main__":
    unittest.main()

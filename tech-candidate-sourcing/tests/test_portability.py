from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class StdlibPortabilityTests(unittest.TestCase):
    def test_query_generator_runs_with_python_s(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jd = Path(temp) / "jd.md"
            out = Path(temp) / "queries.md"
            jd.write_text("Database Kernel Engineer\nRocksDB and distributed consensus", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-S", str(SCRIPTS / "generate_channel_queries.py"), "--jd", str(jd), "--out", str(out)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            rendered = out.read_text(encoding="utf-8")
            self.assertIn("RocksDB", rendered)
            self.assertNotIn("BOSS", rendered)
            self.assertNotIn("Liepin", rendered)

    def test_dependency_manifest_exists(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for package in ["requests", "beautifulsoup4", "PyMuPDF", "lxml", "pypinyin"]:
            self.assertIn(package, requirements)


if __name__ == "__main__":
    unittest.main()

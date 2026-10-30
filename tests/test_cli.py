import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from pbi2html.__main__ import main


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "Revenue-Opportunities.pbix"


class CommandTests(unittest.TestCase):
    def test_command_passes_password_and_prints_warnings(self):
        reader = types.ModuleType("pbi2html.read")
        reader.read = lambda path: {"warnings": ["Visual not supported"]}
        renderer = types.ModuleType("pbi2html.render")
        renderer.to_html = lambda report: "<html></html>"
        calls = []
        renderer.to_pdf = lambda html, pdf, password: calls.append((html, pdf, password))
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, {"pbi2html.read": reader, "pbi2html.render": renderer}
        ), patch.dict(os.environ, {"PBI_TEST_PASSWORD": "secret"}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([str(SAMPLE), "-o", directory, "--password-env", "PBI_TEST_PASSWORD"]), 0)
            self.assertEqual((Path(directory) / (SAMPLE.stem + ".html")).read_text(), "<html></html>")
            self.assertEqual(calls[0][2], "secret")
            self.assertIn("Visual not supported", output.getvalue())
            self.assertNotIn("secret", output.getvalue())

    def test_password_flag_abbreviations_do_not_echo_values(self):
        for flag in ("--password", "--pass"):
            result = subprocess.run(
                [sys.executable, "-m", "pbi2html", str(SAMPLE), "-o", "out",
                 flag, "not-the-real-one"],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("not-the-real-one", result.stdout + result.stderr)

    def test_pdf_failure_reports_warnings_and_html(self):
        reader = types.ModuleType("pbi2html.read")
        reader.read = lambda path: {"warnings": ["Visual not supported"]}
        renderer = types.ModuleType("pbi2html.render")
        renderer.to_html = lambda report: "<html></html>"
        def fail_pdf(html, pdf, password):
            raise FileNotFoundError("chromium")
        renderer.to_pdf = fail_pdf
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, {"pbi2html.read": reader, "pbi2html.render": renderer}
        ), contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main([str(SAMPLE), "-o", directory]), 1)
            self.assertIn("Visual not supported", output.getvalue())
            self.assertIn(str(Path(directory) / (SAMPLE.stem + ".html")), output.getvalue())
            self.assertTrue((Path(directory) / (SAMPLE.stem + ".html")).is_file())
            self.assertIn("chromium", errors.getvalue())

    def test_missing_or_empty_password_is_rejected(self):
        with patch.dict(os.environ, {"PBI_TEST_PASSWORD": ""}), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main([str(SAMPLE), "-o", "out", "--password-env", "PBI_TEST_PASSWORD"])
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main([str(SAMPLE), "-o", "out", "--password-env", "PBI_TEST_PASSWORD"])

    def test_lock_confirms_password(self):
        with patch("getpass.getpass", side_effect=["first", "second"]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main([str(SAMPLE), "-o", "out", "--lock"])


@unittest.skipUnless(
    importlib.util.find_spec("pbi2html.read") and importlib.util.find_spec("pbi2html.render"),
    "reader or renderer has not landed yet",
)
class SampleConversionTests(unittest.TestCase):
    def test_sample_generates_html_and_locked_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["PBI_TEST_PASSWORD"] = "sample-password"
            result = subprocess.run(
                [sys.executable, "-m", "pbi2html", str(SAMPLE), "-o", directory,
                 "--password-env", "PBI_TEST_PASSWORD"],
                cwd=ROOT, env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((Path(directory) / (SAMPLE.stem + ".html")).is_file())
            pdf = Path(directory) / (SAMPLE.stem + ".pdf")
            self.assertTrue(pdf.is_file())
            self.assertNotIn(env["PBI_TEST_PASSWORD"], result.stdout + result.stderr)
            self.assertIsNotNone(shutil.which("qpdf"), "qpdf is required")
            for password, succeeds in [("sample-password", True), ("wrong", False)]:
                checked = subprocess.run(
                    ["qpdf", f"--password={password}", "--check", str(pdf)],
                    capture_output=True,
                )
                self.assertEqual(checked.returncode == 0, succeeds)


if __name__ == "__main__":
    unittest.main()

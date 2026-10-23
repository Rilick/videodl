import tempfile
import unittest
from pathlib import Path

from web.app import _clean_error, app
from web.downloader_core import base_ydl_opts, format_selector


class BaseOptionsTests(unittest.TestCase):
    def test_errors_are_not_ignored(self):
        self.assertFalse(base_ydl_opts()["ignoreerrors"])

    def test_cookie_file_is_used_when_present(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_file = Path(temp_dir) / "cookies.txt"
            cookie_file.write_text("# Netscape HTTP Cookie File\n" + "x" * 20)

            self.assertEqual(
                base_ydl_opts(cookie_file)["cookiefile"], str(cookie_file)
            )

    def test_empty_cookie_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_file = Path(temp_dir) / "cookies.txt"
            cookie_file.write_text("")

            self.assertNotIn("cookiefile", base_ydl_opts(cookie_file))


class FormatSelectorTests(unittest.TestCase):
    def test_default_allows_separate_or_combined_formats(self):
        self.assertEqual(format_selector(), "bestvideo+bestaudio/best")

    def test_height_limit_is_applied(self):
        self.assertEqual(
            format_selector("1080"),
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        )

    def test_invalid_height_uses_default(self):
        self.assertEqual(format_selector("invalid"), "bestvideo+bestaudio/best")


class WebAppTests(unittest.TestCase):
    def test_home_page_loads(self):
        response = app.test_client().get("/")
        self.assertEqual(response.status_code, 200)

    def test_info_requires_a_url(self):
        response = app.test_client().post("/info", data={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "URL em falta.")

    def test_error_cleaner_preserves_actionable_cookie_guidance(self):
        cleaned = _clean_error("ERROR: Access denied. Use --cookies to authenticate")
        self.assertIn("Use --cookies", cleaned)


if __name__ == "__main__":
    unittest.main()

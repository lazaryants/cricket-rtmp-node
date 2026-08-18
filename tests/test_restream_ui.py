import unittest
from pathlib import Path


class RestreamUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "templates"
            / "index.html"
        ).read_text(encoding="utf-8")

    def test_native_blocking_dialogs_and_page_reload_are_absent(self):
        for forbidden in (
            "prompt(",
            "alert(",
            "confirm(",
            "location.reload",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.template)

    def test_custom_dialog_and_toast_regions_exist(self):
        self.assertIn('id="dialogBackdrop"', self.template)
        self.assertIn('id="dialogForm"', self.template)
        self.assertIn('id="toastRegion"', self.template)
        self.assertIn("refreshFieldCard(fieldId)", self.template)

    def test_destination_url_validation_accepts_only_rtmp_schemes(self):
        self.assertIn("rtmps?:", self.template)
        self.assertIn(
            "The URL must start with rtmp:// or rtmps://.",
            self.template,
        )


if __name__ == "__main__":
    unittest.main()

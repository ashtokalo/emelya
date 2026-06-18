"""Tests for i18n — translations, fallback, placeholders."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestI18n(unittest.TestCase):

    def test_english_key(self):
        from emelya.i18n import _STRINGS
        self.assertIn("greeting", _STRINGS["en"])
        self.assertIn("greeting", _STRINGS["ru"])

    def test_t_returns_string(self):
        from emelya.i18n import t
        result = t("greeting")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_t_with_placeholders(self):
        from emelya.i18n import t
        result = t("ready_model", model="TestModel")
        self.assertIn("TestModel", result)

    def test_t_unknown_key_returns_key(self):
        from emelya.i18n import t
        result = t("nonexistent_key_xyz")
        self.assertEqual(result, "nonexistent_key_xyz")

    def test_all_ru_keys_exist_in_en(self):
        """Every Russian key should have an English counterpart."""
        from emelya.i18n import _STRINGS
        for key in _STRINGS["ru"]:
            self.assertIn(key, _STRINGS["en"], f"Key '{key}' in ru but not in en")

    def test_all_en_keys_exist_in_ru(self):
        """Every English key should have a Russian counterpart."""
        from emelya.i18n import _STRINGS
        for key in _STRINGS["en"]:
            self.assertIn(key, _STRINGS["ru"], f"Key '{key}' in en but not in ru")

    def test_context_warning_placeholders(self):
        from emelya.i18n import _STRINGS
        for lang in ("en", "ru"):
            s = _STRINGS[lang]["context_warning"]
            # Should have all three placeholders
            result = s.format(pct=75, tokens=6000, limit=8000)
            self.assertIn("75", result)


if __name__ == "__main__":
    unittest.main()

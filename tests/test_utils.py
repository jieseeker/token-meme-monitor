from __future__ import annotations

import unittest

from token_meme_monitor.utils import first_non_missing


class UtilsTests(unittest.TestCase):
    def test_first_non_missing_preserves_zero(self) -> None:
        self.assertEqual(first_non_missing(0, 12, default=99), 0)

    def test_first_non_missing_skips_none_empty_and_nan(self) -> None:
        self.assertEqual(first_non_missing(None, "", float("nan"), 12, default=99), 12)


if __name__ == "__main__":
    unittest.main()

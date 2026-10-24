import unittest

from main import exit_code_for_run_result


class RunStatusTests(unittest.TestCase):
    def test_explicit_success_returns_zero_exit_code(self):
        self.assertEqual(exit_code_for_run_result(True), 0)

    def test_explicit_failure_returns_nonzero_exit_code(self):
        self.assertEqual(exit_code_for_run_result(False), 1)

    def test_missing_result_is_not_mistaken_for_success(self):
        self.assertEqual(exit_code_for_run_result(None), 1)


if __name__ == "__main__":
    unittest.main()

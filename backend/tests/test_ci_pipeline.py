import unittest


class TestCIProtection(unittest.TestCase):
    def test_ci_branch_protection_gate(self):
        """
        Deliberate test to verify GitHub Actions CI gate and branch protection.

        - DELIBERATE_FAILURE = True:  Pipeline fails (RED)   -> PR merge is BLOCKED.
        - DELIBERATE_FAILURE = False: Pipeline passes (GREEN) -> PR merge is ALLOWED.
        """
        DELIBERATE_FAILURE = True

        self.assertFalse(
            DELIBERATE_FAILURE,
            "Deliberate test failure! Verify that GitHub Actions turns RED "
            "and GitHub blocks merging into develop.",
        )


if __name__ == "__main__":
    unittest.main()

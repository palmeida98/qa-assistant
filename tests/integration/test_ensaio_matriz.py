import unittest


class FirefoxEnsaio(unittest.TestCase):
    def test_deliberate_failure(self):
        self.fail("deliberate rehearsal failure in a Firefox-selected class")


if __name__ == "__main__":
    unittest.main()

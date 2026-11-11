import os
import unittest


class Ensaio(unittest.TestCase):
    def test_fails_only_on_windows(self):
        self.assertNotEqual(os.name, "nt", "deliberate rehearsal failure, Windows only")


if __name__ == "__main__":
    unittest.main()

import unittest


class Ensaio(unittest.TestCase):
    def test_falha_real(self):
        self.assertEqual(1, 2)

    @unittest.skip("a reason nobody planned")
    def test_skip_inesperado(self):
        pass


if __name__ == "__main__":
    unittest.main()

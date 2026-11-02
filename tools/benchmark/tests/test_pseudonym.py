"""A fake must be stable across documents, structurally valid, and never a real account at a real bank."""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from ofinbench import pseudonym


class IbanArithmetic(unittest.TestCase):
    def test_check_digits_against_the_published_specimen(self) -> None:
        """ISO 13616 publishes GB82 WEST 1234 5698 7654 32, which is the only outside check available offline."""
        self.assertEqual("82", pseudonym.iban_check_digits("GB", "WEST12345698765432"))
        self.assertTrue(pseudonym.iban_valid("GB82WEST12345698765432"))
        self.assertTrue(pseudonym.iban_valid("GB82 WEST 1234 5698 7654 32"))

    def test_rejects_a_broken_iban(self) -> None:
        for bad in ("GB83WEST12345698765432", "SA03", "", "888888888888888456", "GBXXWEST12345698765432"):
            with self.subTest(bad=bad):
                self.assertFalse(pseudonym.iban_valid(bad))


class Fakes(unittest.TestCase):
    key = b"k" * 48
    other = b"j" * 48

    def test_fake_iban_is_valid_saudi_format_at_an_unassigned_bank(self) -> None:
        fake = pseudonym.fake_iban(self.key, "SA5900999999999999999999")
        self.assertEqual(24, len(fake))
        self.assertTrue(pseudonym.iban_valid(fake))
        self.assertEqual(pseudonym.FAKE_SA_BANK_CODE, fake[4:6],
                         "a fake must sit at a bank code SAMA does not issue, so it cannot name a real bank")

    def test_the_same_value_maps_to_the_same_fake_across_calls(self) -> None:
        """Without this, an account appearing in two months becomes two accounts and reconciliation is unanswerable."""
        real = "SA5900999999999999999999"
        self.assertEqual(pseudonym.fake_iban(self.key, real), pseudonym.fake_iban(self.key, real))
        self.assertEqual(pseudonym.fake_name(self.key, "عبدالله السبيعي"), pseudonym.fake_name(self.key, "عبدالله السبيعي"))

    def test_a_different_key_gives_a_different_fake(self) -> None:
        real = "SA5900999999999999999999"
        self.assertNotEqual(pseudonym.fake_iban(self.key, real), pseudonym.fake_iban(self.other, real))

    def test_kinds_are_namespaced(self) -> None:
        """The same string used as a name and as an account must not collapse into one stand-in."""
        self.assertNotEqual(pseudonym.fake_account(self.key, "1234567890"),
                            pseudonym.fake_gov_id(self.key, "1234567890"))

    def test_shapes_are_preserved(self) -> None:
        self.assertEqual(10, len(pseudonym.fake_gov_id(self.key, "1099999999")))
        self.assertEqual("1", pseudonym.fake_gov_id(self.key, "1099999999")[0], "the class digit carries meaning")
        self.assertEqual("2", pseudonym.fake_gov_id(self.key, "2099999999")[0])
        self.assertEqual(15, len(pseudonym.fake_vat(self.key, "399999999999993")))
        self.assertTrue(pseudonym.fake_vat(self.key, "399999999999993").startswith("3"))
        self.assertEqual(13, len(pseudonym.fake_phone(self.key, "0599999999")))
        self.assertEqual(12, len(pseudonym.fake_account(self.key, "999999999999")))

    def test_a_name_keeps_its_script(self) -> None:
        self.assertTrue(any("؀" <= c <= "ۿ" for c in pseudonym.fake_name(self.key, "عبدالله السبيعي")))
        self.assertTrue(pseudonym.fake_name(self.key, "Adam Haddad").isascii())

    def test_an_organisation_does_not_become_a_person(self) -> None:
        org = pseudonym.fake_org(self.key, "Oasis Trading Co.")
        self.assertNotEqual(org, pseudonym.fake_name(self.key, "Oasis Trading Co."))
        self.assertTrue(any(word in org for word in ("Co.", "LLC", "Est.", "Holding", "Industries")))


class Keys(unittest.TestCase):
    def test_minting_then_reading_returns_the_same_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            minted = pseudonym.load_key(root, create=True)
            self.assertGreaterEqual(len(minted), 32)
            self.assertEqual(minted, pseudonym.load_key(root))
            self.assertEqual(0o600, (root / "keys" / pseudonym.KEY_FILENAME).stat().st_mode & 0o777)

    def test_a_missing_key_is_an_error_rather_than_a_fresh_one(self) -> None:
        """Minting silently would re-pseudonymise an existing corpus into a second, unlinked identity space."""
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(pseudonym.KeyError_):
            pseudonym.load_key(pathlib.Path(temp))


if __name__ == "__main__":
    unittest.main()

"""Replacements that are stable across documents and reversible only with the key.

Two properties matter and pull in opposite directions. The same IBAN appearing in a March statement and an April
statement must become the same fake IBAN, or no reconciliation question survives anonymisation. At the same time the
fake must not be derivable from the real value by anyone holding the output. Keyed HMAC gives both: deterministic
for whoever holds the key, opaque to everyone else. The key lives in the corpus root, never in the repository, and
losing it only costs the ability to re-identify, which is usually the safer failure.

Fakes are structurally valid, so a parser or an OCR pass treats them as it would the real thing: IBANs carry correct
mod-97 check digits. Saudi IBANs are issued with a two-digit SAMA bank code, and the generator always uses `00`,
which SAMA does not assign, so a fake can never collide with a real account at a real bank.
"""

from __future__ import annotations

import hashlib
import hmac
import pathlib
import secrets
import string

KEY_FILENAME = "deid.key"
FAKE_SA_BANK_CODE = "00"  # unassigned by SAMA: a fake IBAN cannot name a real bank


class KeyError_(Exception):
    """The de-identification key is missing or unusable."""


def load_key(root: pathlib.Path, *, create: bool = False) -> bytes:
    """Read the corpus key, optionally minting one on first use.

    The file is written 0600. It is the only thing that links output back to a client, so it is treated as a secret
    in its own right: not copied beside the anonymised output, not printed, not committed.
    """
    path = root / "keys" / KEY_FILENAME
    if path.is_file():
        raw = path.read_bytes().strip()
        if len(raw) < 32:
            raise KeyError_(f"{path} is too short to be a key; delete it and re-run with --new-key")
        return raw
    if not create:
        raise KeyError_(f"No key at {path}. Run `ofinbench init` first, or pass --new-key to mint one.")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(48)
    path.write_bytes(raw)
    path.chmod(0o600)
    return raw


def _digest(key: bytes, kind: str, value: str) -> bytes:
    """Namespaced by kind so the same string used as a name and as an account does not collapse to one fake."""
    return hmac.new(key, f"{kind}\x00{value.strip().casefold()}".encode(), hashlib.sha256).digest()


def _digits(key: bytes, kind: str, value: str, count: int) -> str:
    """`count` decimal digits drawn from the digest, taken as a big integer so every digit depends on the whole input."""
    n = int.from_bytes(_digest(key, kind, value), "big")
    return str(n % (10 ** count)).zfill(count)


def iban_check_digits(country: str, bban: str) -> str:
    """The two digits that make an IBAN pass mod-97, per ISO 13616."""
    rearranged = f"{bban}{country}00"
    total = 0
    for char in rearranged:
        if char.isdigit():
            total = (total * 10 + int(char)) % 97
        else:
            total = (total * 100 + (ord(char.upper()) - 55)) % 97
    return str(98 - total).zfill(2)


def iban_valid(iban: str) -> bool:
    compact = "".join(iban.split()).upper()
    if len(compact) < 15 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    total = 0
    for char in rearranged:
        if char.isdigit():
            total = (total * 10 + int(char)) % 97
        elif char.isalpha():
            total = (total * 100 + (ord(char) - 55)) % 97
        else:
            return False
    return total == 1


def fake_iban(key: bytes, real: str) -> str:
    """A valid Saudi-format IBAN standing in for `real`, at an unassigned bank code."""
    account = _digits(key, "iban", real, 18)
    bban = f"{FAKE_SA_BANK_CODE}{account}"
    return f"SA{iban_check_digits('SA', bban)}{bban}"


def fake_account(key: bytes, real: str) -> str:
    """A bare account number of the same length, so column widths and any length check still hold."""
    width = max(6, min(24, sum(c.isdigit() for c in real)))
    return _digits(key, "account", real, width)


def fake_gov_id(key: bytes, real: str) -> str:
    """A ten-digit national id, iqama or commercial registration, keeping the leading digit's meaning.

    The first digit is a class marker in Saudi identifiers (1 citizen, 2 resident, and 1/4/7 for registrations), and
    a document often reads differently if it changes, so it is carried over and only the remaining nine are replaced.
    """
    lead = real.strip()[0] if real.strip()[:1].isdigit() else "1"
    return lead + _digits(key, "gov", real, 9)


def fake_vat(key: bytes, real: str) -> str:
    """A fifteen-digit VAT number: ZATCA's format is 3 + 13 digits + 3."""
    return "3" + _digits(key, "vat", real, 13) + "3"


def fake_phone(key: bytes, real: str) -> str:
    return "+9665" + _digits(key, "phone", real, 8)


def fake_email(key: bytes, real: str) -> str:
    return f"user{_digits(key, 'email', real, 6)}@example.invalid"


def fake_name(key: bytes, real: str, *, arabic: bool | None = None) -> str:
    """A stable stand-in name in the same script, built from fixed word lists so it reads as a name, not a hash.

    Script is taken from the input unless `arabic` says otherwise, because a statement that mixes an Arabic
    beneficiary with an English description should keep that mix after anonymisation.
    """
    if arabic is None:
        arabic = any("؀" <= c <= "ۿ" for c in real)
    first, last = (AR_FIRST, AR_LAST) if arabic else (EN_FIRST, EN_LAST)
    digest = _digest(key, "name", real)
    a = first[int.from_bytes(digest[:4], "big") % len(first)]
    b = last[int.from_bytes(digest[4:8], "big") % len(last)]
    return f"{a} {b}"


def fake_org(key: bytes, real: str, *, arabic: bool | None = None) -> str:
    """A stand-in counterparty. Kept distinct from `fake_name` so a supplier does not turn into a person."""
    if arabic is None:
        arabic = any("؀" <= c <= "ۿ" for c in real)
    stems, suffixes = (AR_ORG, AR_ORG_SUFFIX) if arabic else (EN_ORG, EN_ORG_SUFFIX)
    digest = _digest(key, "org", real)
    stem = stems[int.from_bytes(digest[:4], "big") % len(stems)]
    suffix = suffixes[int.from_bytes(digest[4:8], "big") % len(suffixes)]
    return f"{stem} {suffix}"


def fake_token(key: bytes, real: str) -> str:
    """Last resort for a value with no known shape: a short opaque tag, so the output still reads as a document."""
    alphabet = string.ascii_uppercase + string.digits
    n = int.from_bytes(_digest(key, "token", real), "big")
    out = []
    for _ in range(8):
        n, i = divmod(n, len(alphabet))
        out.append(alphabet[i])
    return "".join(out)


AR_FIRST = ("سالم", "ريم", "ماجد", "هند", "خالد", "نورة", "فهد", "لمى", "بدر", "سارة", "طلال", "دانة")
AR_LAST = ("القحطاني", "العتيبي", "الدوسري", "الحربي", "الزهراني", "الغامدي", "الشهري", "المطيري")
EN_FIRST = ("Adam", "Rana", "Omar", "Lina", "Karim", "Dana", "Yusuf", "Mona", "Tariq", "Sara")
EN_LAST = ("Haddad", "Nasser", "Khalil", "Mansour", "Saleh", "Rashid", "Younes", "Habib")
AR_ORG = ("الواحة", "المرتفعات", "النخيل", "البوابة", "المنارة", "الربوة", "السدرة", "الفرات")
AR_ORG_SUFFIX = ("للتجارة", "للمقاولات", "للخدمات اللوجستية", "للتوريدات", "القابضة", "للصناعات")
EN_ORG = ("Oasis", "Highline", "Palmgate", "Meridian", "Northwind", "Sandriver", "Cedarpoint")
EN_ORG_SUFFIX = ("Trading Co.", "Contracting Est.", "Logistics LLC", "Supplies Co.", "Holding", "Industries")

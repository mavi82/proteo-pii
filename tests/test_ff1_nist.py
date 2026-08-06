# -*- coding: utf-8 -*-
"""FF1 contro i vettori di test ufficiali del NIST.

Perche' questi e non solo un round-trip: `decrypt(encrypt(x)) == x` passa anche
con un'implementazione sbagliata, purche' lo sia in modo simmetrico. I vettori
del NIST fissano il testo cifrato atteso, quindi verificano che l'algoritmo sia
*quello* e non uno che gli somiglia.

Fonte: NIST, "Sample Functions for FF1 and FF3" (allegato a SP 800-38G), tutti e
nove i campioni pubblicati: AES-128/192/256 x (tweak vuoto, tweak numerico,
tweak con radix 36).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo.fpe import FF1, InvalidDomain          # noqa: E402

DIGITS = "0123456789"
ALNUM = "0123456789abcdefghijklmnopqrstuvwxyz"      # radix 36

K128 = bytes.fromhex("2B7E151628AED2A6ABF7158809CF4F3C")
K192 = bytes.fromhex("2B7E151628AED2A6ABF7158809CF4F3CEF4359D8D580AA4F")
K256 = bytes.fromhex("2B7E151628AED2A6ABF7158809CF4F3CEF4359D8D580AA4F7F036D6F04FC6A94")

T_EMPTY = b""
T_NUM = bytes.fromhex("39383736353433323130")
T_A36 = bytes.fromhex("3737373770717273373737")

# (nome, chiave, tweak, alfabeto, in chiaro, atteso cifrato)
VECTORS = [
    ("#1  AES-128 tweak vuoto",   K128, T_EMPTY, DIGITS, "0123456789", "2433477484"),
    ("#2  AES-128 tweak numerico", K128, T_NUM,  DIGITS, "0123456789", "6124200773"),
    ("#3  AES-128 radix 36",      K128, T_A36,  ALNUM,  "0123456789abcdefghi", "a9tv40mll9kdu509eum"),
    ("#4  AES-192 tweak vuoto",   K192, T_EMPTY, DIGITS, "0123456789", "2830668132"),
    ("#5  AES-192 tweak numerico", K192, T_NUM,  DIGITS, "0123456789", "2496655549"),
    ("#6  AES-192 radix 36",      K192, T_A36,  ALNUM,  "0123456789abcdefghi", "xbj3kv35jrawxv32ysr"),
    ("#7  AES-256 tweak vuoto",   K256, T_EMPTY, DIGITS, "0123456789", "6657667009"),
    ("#8  AES-256 tweak numerico", K256, T_NUM,  DIGITS, "0123456789", "1001623463"),
    ("#9  AES-256 radix 36",      K256, T_A36,  ALNUM,  "0123456789abcdefghi", "xs8a0azh2avyalyzuwd"),
]


class VettoriNist(unittest.TestCase):
    def test_cifratura(self):
        for nome, key, tweak, alfabeto, chiaro, atteso in VECTORS:
            with self.subTest(nome):
                got = FF1(key).encrypt_str(chiaro, alfabeto, tweak)
                self.assertEqual(got, atteso)

    def test_decifratura(self):
        for nome, key, tweak, alfabeto, chiaro, atteso in VECTORS:
            with self.subTest(nome):
                got = FF1(key).decrypt_str(atteso, alfabeto, tweak)
                self.assertEqual(got, chiaro)


class Proprieta(unittest.TestCase):
    """Le tre proprieta' su cui si regge il progetto."""

    def setUp(self):
        self.ff1 = FF1(K256)

    def test_deterministica(self):
        """Stesso valore -> stesso surrogato: e' cio' che tiene in piedi i JOIN."""
        a = self.ff1.encrypt_str("RSSMRA85H12F205Z", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        b = self.ff1.encrypt_str("RSSMRA85H12F205Z", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        self.assertEqual(a, b)

    def test_biiettiva(self):
        """Nessuna collisione: i vincoli UNIQUE sopravvivono all'anonimizzazione."""
        visti = {self.ff1.encrypt_str("%06d" % i, DIGITS) for i in range(20000)}
        self.assertEqual(len(visti), 20000)

    def test_formato_preservato(self):
        """Stessa lunghezza e stesso alfabeto: nessun ALTER COLUMN."""
        for n in (2, 5, 10, 16, 32):
            with self.subTest(lunghezza=n):
                out = self.ff1.encrypt_str("1" * n, DIGITS)
                self.assertEqual(len(out), n)
                self.assertTrue(set(out) <= set(DIGITS))

    def test_il_tweak_cambia_il_risultato(self):
        """Il tweak separa i domini: stesso valore, colonne diverse, cifrati diversi."""
        a = self.ff1.encrypt_str("0123456789", DIGITS, b"clienti.cf")
        b = self.ff1.encrypt_str("0123456789", DIGITS, b"fornitori.cf")
        self.assertNotEqual(a, b)

    def test_dominio_troppo_piccolo(self):
        """Una colonna quasi-binaria non si protegge cifrandola: meglio fermarsi.

        Sei bit sono 64 combinazioni: sotto il minimo di 100 fissato dallo
        standard, e in pratica enumerabili a mano. Il caso reale e' la colonna
        'sesso' o un flag S/N.
        """
        with self.assertRaises(InvalidDomain):
            self.ff1.encrypt_str("010101", "01")

    def test_dominio_al_limite_consentito(self):
        """10^2 = 100 e' esattamente il minimo: deve passare, non fallire."""
        self.assertEqual(len(self.ff1.encrypt_str("42", DIGITS)), 2)

    def test_carattere_fuori_alfabeto(self):
        with self.assertRaises(ValueError):
            self.ff1.encrypt_str("12345X7890", DIGITS)


if __name__ == "__main__":
    unittest.main(verbosity=2)

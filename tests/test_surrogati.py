# -*- coding: utf-8 -*-
"""I surrogati devono essere validi, reversibili e strutturalmente plausibili.

Tutti i valori qui sono INVENTATI: generati apposta perche' i checksum tornino.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo.checksum import cf_ok, iban_ok, piva_ok                    # noqa: E402
from proteo.surrogati import MESI, Surrogatore, ValoreNonTrattabile    # noqa: E402

CHIAVE = bytes(range(32))
TWEAK = b"codice_fiscale"

CF = ["RSSMRA85H12F205Y", "BNCLGU78T04H501C", "VRDNNA90A41F839L", "MRTPLA65M15L219C"]
PIVA = ["12345678903", "00743110157", "01234567897"]
IBAN = ["IT60X0542811101000000123456", "DE89370400440532013000", "FR1420041010050500013M02606"]


class Validita(unittest.TestCase):
    """Il surrogato deve passare il validatore dell'originale."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def test_i_valori_di_partenza_sono_validi(self):
        """Se il fixture fosse sbagliato, il test passerebbe per il motivo sbagliato."""
        for v in CF:
            self.assertTrue(cf_ok(v), v)
        for v in PIVA:
            self.assertTrue(piva_ok(v), v)
        for v in IBAN:
            self.assertTrue(iban_ok(v), v)

    def test_cf_surrogato_valido(self):
        for v in CF:
            with self.subTest(v):
                out = self.s.cf(v, TWEAK)
                self.assertTrue(cf_ok(out), out)
                self.assertEqual(len(out), 16)
                self.assertNotEqual(out, v)

    def test_piva_surrogata_valida(self):
        for v in PIVA:
            with self.subTest(v):
                out = self.s.piva(v, TWEAK)
                self.assertTrue(piva_ok(out), out)
                self.assertEqual(len(out), 11)

    def test_iban_surrogato_valido(self):
        for v in IBAN:
            with self.subTest(v):
                out = self.s.iban(v, TWEAK)
                self.assertTrue(iban_ok(out), out)
                self.assertEqual(len(out), len(v))
                self.assertEqual(out[:2], v[:2], "il paese non deve cambiare")


class Reversibilita(unittest.TestCase):
    """Con la sola chiave si torna all'originale. Nessun dizionario."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def test_andata_e_ritorno(self):
        for tipo, valori in (("CF", CF), ("PIVA", PIVA), ("IBAN", IBAN)):
            for v in valori:
                with self.subTest(tipo=tipo, valore=v):
                    cifrato = self.s.cifra(tipo, v, TWEAK)
                    self.assertEqual(self.s.decifra(tipo, cifrato, TWEAK), v)

    def test_chiave_diversa_non_riporta_all_originale(self):
        altro = Surrogatore(bytes(range(1, 33)))
        cifrato = self.s.cf(CF[0], TWEAK)
        self.assertNotEqual(altro.cf(cifrato, TWEAK, avanti=False), CF[0])


class Struttura(unittest.TestCase):
    """Il surrogato deve *sembrare* un codice vero, non solo esserlo al checksum."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def test_cf_mantiene_la_struttura(self):
        for v in CF:
            with self.subTest(v):
                o = self.s.cf(v, TWEAK)
                self.assertTrue(o[:6].isalpha(), "le prime 6 devono essere lettere")
                self.assertTrue(o[6:8].isdigit(), "anno")
                self.assertIn(o[8], MESI, "lettera del mese plausibile")
                self.assertTrue(o[9:11].isdigit(), "giorno")
                g = int(o[9:11])
                self.assertTrue(1 <= g <= 31 or 41 <= g <= 71, "giorno %d fuori range" % g)
                self.assertTrue(o[11].isalpha() and o[12:15].isdigit(), "codice comune")

    def test_iban_mantiene_le_classi_dei_caratteri(self):
        """Cifre dove c'erano cifre: un IBAN con lettere nell'ABI si vede subito."""
        for v in IBAN:
            with self.subTest(v):
                o = self.s.iban(v, TWEAK)
                for a, b in zip(v[4:], o[4:]):
                    self.assertEqual(a.isdigit(), b.isdigit())


class Determinismo(unittest.TestCase):
    """Cio' che tiene in piedi i JOIN fra tabelle."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def test_stesso_valore_stesso_tweak_stesso_surrogato(self):
        a = self.s.cf(CF[0], b"codice_fiscale")
        b = self.s.cf(CF[0], b"codice_fiscale")
        self.assertEqual(a, b)

    def test_tweak_diverso_surrogato_diverso(self):
        a = self.s.cf(CF[0], b"codice_fiscale")
        b = self.s.cf(CF[0], b"cf_intestatario")
        self.assertNotEqual(a, b)

    def test_nessuna_collisione(self):
        """FF1 e' una permutazione: i vincoli UNIQUE sopravvivono."""
        visti = {self.s.piva("%010d" % i + "0", TWEAK)[:10] for i in range(3000)}
        self.assertEqual(len(visti), 3000)


class ValoriSporchi(unittest.TestCase):
    """I DB reali sono pieni di dati malformati: meglio fermarsi che sbagliare."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def test_valori_non_trattabili(self):
        casi = [
            ("CF", "RSSMRA85H12F205"),      # 15 caratteri
            ("CF", "RSSMRA85Z12F205Z"),     # 'Z' non e' una lettera di mese
            ("CF", "RSSMRA85H99F205Z"),     # giorno 99
            ("CF", "RSSMRAL5H12F205T"),     # omocodico: lettera fra le cifre dell'anno
            ("PIVA", "1234567890"),         # 10 cifre
            ("IBAN", "XX"),                 # troppo corto
        ]
        for tipo, v in casi:
            with self.subTest(tipo=tipo, valore=v):
                with self.assertRaises(ValoreNonTrattabile):
                    self.s.cifra(tipo, v, TWEAK)

    def test_nessun_ripiego_silenzioso(self):
        """Un valore rifiutato non deve MAI uscire trasformato a meta'."""
        with self.assertRaises(ValoreNonTrattabile):
            self.s.cf("", TWEAK)


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""Il registro e' l'unica cosa che sa se una colonna e' gia' cifrata.

Con FPE il surrogato e' indistinguibile dall'originale: se il registro sbaglia,
si cifra il cifrato e non si torna piu' indietro. Questi test coprono i due
controlli per cui il modulo esiste.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo.registro import (CIFRATA, IN_CHIARO, IN_CORSO,     # noqa: E402
                             Registro, StatoIncoerente)

DB = "VenditeDB"
TAB = "dbo.clienti"
COL = "codice_fiscale"
KID = "a3f21b0c9d4e5f60"


class Base(unittest.TestCase):
    def setUp(self):
        self.r = Registro(Path(tempfile.mkdtemp()) / "registro")

    def _cifra(self, tabella=TAB, colonna=COL, kid=KID, righe=100):
        self.r.avvia(DB, tabella, colonna, "CF", "codice_fiscale", kid, "cifra")
        return self.r.concludi(DB, tabella, colonna, CIFRATA, righe)


class Struttura(Base):
    def test_una_cartella_per_db_un_file_per_colonna(self):
        self._cifra()
        self._cifra(colonna="partita_iva")
        cartella = self.r.cartella(DB)
        self.assertTrue(cartella.is_dir())
        nomi = sorted(p.name for p in cartella.glob("*.json"))
        self.assertEqual(nomi, ["dbo.clienti_codice_fiscale.json",
                                "dbo.clienti_partita_iva.json"])

    def test_colonna_mai_toccata(self):
        self.assertIsNone(self.r.leggi(DB, TAB, COL))
        self.assertEqual(self.r.stato(DB, TAB, COL), IN_CHIARO)

    def test_nomi_con_caratteri_scomodi_non_collidono(self):
        """`a-b` e `a_b` darebbero lo stesso file dopo la ripulitura."""
        p1 = self.r.percorso(DB, "dbo.tab", "col-x")
        p2 = self.r.percorso(DB, "dbo.tab", "col_x")
        self.assertNotEqual(p1.name, p2.name)

    def test_elenco_ordinato(self):
        self._cifra(colonna="partita_iva")
        self._cifra(colonna="codice_fiscale")
        self.assertEqual([v["colonna"] for v in self.r.elenco(DB)],
                         ["codice_fiscale", "partita_iva"])

    def test_file_rinominato_a_mano_viene_scoperto(self):
        self._cifra()
        p = self.r.percorso(DB, TAB, COL)
        d = json.loads(p.read_text(encoding="utf-8"))
        d["colonna"] = "altra_colonna"
        p.write_text(json.dumps(d), encoding="utf-8")
        with self.assertRaises(StatoIncoerente):
            self.r.leggi(DB, TAB, COL)


class ControlloCifratura(Base):
    def test_prima_volta_permessa(self):
        self.r.verifica_prima_di_cifrare(DB, TAB, COL)      # non solleva

    def test_doppia_cifratura_bloccata(self):
        """Il difetto che il registro esiste per impedire."""
        self._cifra()
        with self.assertRaises(StatoIncoerente) as e:
            self.r.verifica_prima_di_cifrare(DB, TAB, COL)
        self.assertIn("gia' cifrata", str(e.exception))

    def test_esecuzione_interrotta_blocca(self):
        self.r.avvia(DB, TAB, COL, "CF", "codice_fiscale", KID, "cifra")
        self.assertEqual(self.r.stato(DB, TAB, COL), IN_CORSO)
        with self.assertRaises(StatoIncoerente):
            self.r.verifica_prima_di_cifrare(DB, TAB, COL)
        self.assertEqual([v["colonna"] for v in self.r.interrotte(DB)], [COL])

    def test_ricifrabile_dopo_essere_tornata_in_chiaro(self):
        self._cifra()
        self.r.avvia(DB, TAB, COL, "CF", "codice_fiscale", KID, "decifra")
        self.r.concludi(DB, TAB, COL, IN_CHIARO, 100)
        self.r.verifica_prima_di_cifrare(DB, TAB, COL)      # non solleva


class ControlloDecifratura(Base):
    def test_chiave_giusta(self):
        self._cifra()
        v = self.r.verifica_prima_di_decifrare(DB, TAB, COL, KID)
        self.assertEqual(v["tipo"], "CF")
        self.assertEqual(v["tweak"], "codice_fiscale")

    def test_chiave_sbagliata_bloccata_prima_di_scrivere(self):
        """Il caso che distrugge i dati: decifrare con l'altra chiave."""
        self._cifra()
        with self.assertRaises(StatoIncoerente) as e:
            self.r.verifica_prima_di_decifrare(DB, TAB, COL, "ffffffffffffffff")
        self.assertIn("irrecuperabili", str(e.exception))

    def test_colonna_non_cifrata(self):
        with self.assertRaises(StatoIncoerente):
            self.r.verifica_prima_di_decifrare(DB, TAB, COL, KID)


class Storico(Base):
    def test_le_operazioni_si_accumulano(self):
        self._cifra(righe=10)
        self.r.avvia(DB, TAB, COL, "CF", "codice_fiscale", KID, "decifra")
        v = self.r.concludi(DB, TAB, COL, IN_CHIARO, 10)
        self.assertGreaterEqual(len(v["storico"]), 2)
        self.assertEqual(v["stato"], IN_CHIARO)

    def test_scrittura_atomica_non_lascia_temporanei(self):
        self._cifra()
        self.assertEqual(list(self.r.cartella(DB).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

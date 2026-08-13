# -*- coding: utf-8 -*-
"""Chiudere a mano una colonna rimasta 'in_corso'.

E' l'unica cosa che Proteo non puo' decidere da solo: un surrogato e'
indistinguibile da un valore vero — e' il punto di tutto il progetto — quindi
nessun controllo automatico puo' dire se quella colonna sia stata scritta. Qui
si verifica che il registro accetti la dichiarazione e torni utilizzabile.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo.registro import (CIFRATA, IN_CHIARO, IN_CORSO,     # noqa: E402
                             Registro, StatoIncoerente)


class Risoluzione(unittest.TestCase):
    def setUp(self):
        self.reg = Registro(Path(tempfile.mkdtemp()) / "r")
        self.reg.avvia("DB", "T_Pazienti", "Nome", "NOME", "nome", "k0", "cifra")

    def test_una_colonna_in_corso_blocca_tutto(self):
        with self.assertRaises(StatoIncoerente) as e:
            self.reg.verifica_prima_di_cifrare("DB", "T_Pazienti", "Nome")
        self.assertIn("in_corso", str(e.exception))

    def test_dichiararla_in_chiaro_la_rende_di_nuovo_cifrabile(self):
        self.reg.concludi("DB", "T_Pazienti", "Nome", IN_CHIARO, None)
        self.assertEqual(self.reg.stato("DB", "T_Pazienti", "Nome"), IN_CHIARO)
        self.reg.verifica_prima_di_cifrare("DB", "T_Pazienti", "Nome")  # non alza

    def test_dichiararla_cifrata_la_rende_decifrabile(self):
        self.reg.concludi("DB", "T_Pazienti", "Nome", CIFRATA, None)
        voce = self.reg.verifica_prima_di_decifrare("DB", "T_Pazienti", "Nome", "k0")
        self.assertEqual(voce["stato"], CIFRATA)

    def test_e_la_chiave_resta_quella_di_prima(self):
        """Dichiarare lo stato non deve far perdere con quale chiave si era
        lavorato: senza, la colonna diventerebbe indecifrabile."""
        self.reg.concludi("DB", "T_Pazienti", "Nome", CIFRATA, None)
        with self.assertRaises(StatoIncoerente):
            self.reg.verifica_prima_di_decifrare("DB", "T_Pazienti", "Nome",
                                                 "un'altra")

    def test_la_dichiarazione_resta_nello_storico(self):
        """Chi guarda dopo deve poter vedere che c'e' stato un intervento."""
        self.reg.concludi("DB", "T_Pazienti", "Nome", IN_CHIARO, None)
        voce = self.reg.leggi("DB", "T_Pazienti", "Nome")
        self.assertEqual([s["stato"] for s in voce["storico"]], [IN_CORSO])

    def test_interrotte_le_elenca(self):
        self.reg.avvia("DB", "T_Pazienti", "Cognome", "COGNOME", "cognome",
                       "k0", "cifra")
        self.assertEqual(len(self.reg.interrotte("DB")), 2)
        self.reg.concludi("DB", "T_Pazienti", "Nome", IN_CHIARO, None)
        self.assertEqual([v["colonna"] for v in self.reg.interrotte("DB")],
                         ["Cognome"])


class ConLotti(unittest.TestCase):
    """A lotti resta scritto dove ci si era fermati: la colonna e' mista."""

    def test_l_ultima_chiave_sopravvive_all_interruzione(self):
        reg = Registro(Path(tempfile.mkdtemp()) / "r")
        reg.avvia("DB", "t", "c", "CF", "c", "k0", "cifra")
        reg.avanzamento("DB", "t", "c", ultima_chiave="4821", righe=4821)
        voce = reg.interrotte("DB")[0]
        self.assertEqual(voce["ultima_chiave"], "4821")
        self.assertEqual(voce["stato"], IN_CORSO)


class DopoUnRipristino(unittest.TestCase):
    """Il registro sta sul client: un restore del database non lo tocca."""

    def setUp(self):
        self.reg = Registro(Path(tempfile.mkdtemp()) / "r")
        self.reg.avvia("DB", "t", "cf", "CF", "cf", "k0", "cifra")
        self.reg.concludi("DB", "t", "cf", CIFRATA, 100)

    def test_il_registro_resta_a_dire_cifrata(self):
        """E' il disallineamento pericoloso: decifrare valori veri produce
        altri valori validi e sbagliati, senza che nulla lo segnali."""
        self.assertEqual(self.reg.stato("DB", "t", "cf"), CIFRATA)

    def test_riallineare_la_riporta_in_chiaro(self):
        self.reg.concludi("DB", "t", "cf", IN_CHIARO, None)
        self.assertEqual(self.reg.stato("DB", "t", "cf"), IN_CHIARO)
        self.reg.verifica_prima_di_cifrare("DB", "t", "cf")     # non alza

    def test_la_chiave_non_si_perde(self):
        """Serve ancora: i surrogati non sono salvati da nessuna parte, si
        ricalcolano da chiave e tweak."""
        self.reg.concludi("DB", "t", "cf", IN_CHIARO, None)
        self.assertEqual(self.reg.leggi("DB", "t", "cf")["chiave_id"], "k0")

    def test_lo_storico_conserva_il_passaggio(self):
        self.reg.concludi("DB", "t", "cf", IN_CHIARO, None)
        stati = [s["stato"] for s in self.reg.leggi("DB", "t", "cf")["storico"]]
        self.assertEqual(stati, [IN_CORSO, CIFRATA])


if __name__ == "__main__":
    unittest.main(verbosity=2)

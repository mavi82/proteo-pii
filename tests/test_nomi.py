# -*- coding: utf-8 -*-
"""Nomi e cognomi: surrogati plausibili, presi da una lista.

Qui non c'e' un checksum da verificare ma tre proprieta' da difendere: il
surrogato e' un nome vero, il giro torna **identico**, e valori distinti danno
surrogati distinti.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo import liste                                      # noqa: E402
from proteo.surrogati import Surrogatore, ValoreNonTrattabile  # noqa: E402

CHIAVE = bytes(range(32))
TW = b"nome"


def _lista_finta(n=120, prefisso="VOCE"):
    return liste.Lista(["%s%03d" % (prefisso, i) for i in range(n)], "finta")


class Struttura(unittest.TestCase):
    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def test_il_surrogato_e_un_nome_della_lista(self):
        surrogato = self.s.cifra("NOME", "Mario", TW)
        self.assertIsNotNone(liste.carica("nomi").posizione(surrogato))

    def test_il_surrogato_di_un_cognome_e_un_cognome(self):
        surrogato = self.s.cifra("COGNOME", "Rossi", TW)
        self.assertIsNotNone(liste.carica("cognomi").posizione(surrogato))

    def test_ritorno_identico_su_tutta_la_lista(self):
        """La proprieta' che conta: nessun valore si perde per strada."""
        for v in liste.carica("nomi").voci:
            self.assertEqual(self.s.decifra("NOME", self.s.cifra("NOME", v, TW), TW), v)

    def test_biiettivo(self):
        """Valori distinti -> surrogati distinti: i JOIN reggono."""
        voci = liste.carica("nomi").voci
        surrogati = {self.s.cifra("NOME", v, TW) for v in voci}
        self.assertEqual(len(surrogati), len(voci))

    def test_deterministico(self):
        self.assertEqual(self.s.cifra("NOME", "Mario", TW),
                         self.s.cifra("NOME", "Mario", TW))

    def test_tweak_diversi_surrogati_diversi(self):
        self.assertNotEqual(self.s.cifra("NOME", "Mario", b"a"),
                            self.s.cifra("NOME", "Mario", b"b"))


class Forma(unittest.TestCase):
    """Lo stile di scrittura dell'originale si conserva, la lunghezza no."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def test_maiuscolo(self):
        self.assertTrue(self.s.cifra("NOME", "MARIO", TW).isupper())

    def test_minuscolo(self):
        self.assertTrue(self.s.cifra("NOME", "mario", TW).islower())

    def test_iniziale_maiuscola(self):
        surrogato = self.s.cifra("NOME", "Mario", TW)
        self.assertEqual(surrogato, surrogato.title())

    def test_apostrofo_e_spazi_nei_cognomi(self):
        for v in ["D'Angelo", "De Luca"]:
            self.assertEqual(self.s.decifra("COGNOME",
                                            self.s.cifra("COGNOME", v, TW), TW), v)

    def test_le_grafie_accentate_sono_voci_distinte(self):
        """`Nicolò` non deve rientrare come `Nicolo`: sarebbe un altro valore."""
        self.assertEqual(self.s.decifra("NOME", self.s.cifra("NOME", "Nicolò", TW),
                                        TW), "Nicolò")
        self.assertNotEqual(self.s.cifra("NOME", "Nicolò", TW),
                            self.s.cifra("NOME", "Nicolo", TW))


class Rifiuti(unittest.TestCase):
    """Nessun ripiego silenzioso: cio' che non torna indietro si ferma."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def test_fuori_lista(self):
        with self.assertRaises(ValoreNonTrattabile) as e:
            self.s.cifra("NOME", "Ludmila", TW)
        self.assertIn("aggiungilo alla lista", str(e.exception))

    def test_spazi_doppi(self):
        """Rientrerebbe con un solo spazio, cioe' diverso dall'originale."""
        with self.assertRaises(ValoreNonTrattabile):
            self.s.cifra("COGNOME", "De  Luca", TW)

    def test_vuoto(self):
        with self.assertRaises(ValoreNonTrattabile):
            self.s.cifra("NOME", "   ", TW)


class Liste(unittest.TestCase):
    def test_una_lista_troppo_corta_e_rifiutata(self):
        """Sotto i 100 valori il surrogato si rimappa contando le occorrenze."""
        with self.assertRaises(ValueError) as e:
            liste.Lista(["A%d" % i for i in range(40)], "corta")
        self.assertIn("dominio", str(e.exception))

    def test_i_duplicati_spariscono(self):
        l = liste.Lista(["mario", "MARIO", " Mario "] + ["X%03d" % i for i in range(120)])
        self.assertEqual(len([v for v in l.voci if v == "MARIO"]), 1)

    def test_impronta_cambia_col_contenuto(self):
        a = _lista_finta()
        b = liste.Lista(a.voci + ["AGGIUNTA"], "finta")
        self.assertNotEqual(a.impronta, b.impronta)

    def test_impronta_stabile(self):
        self.assertEqual(_lista_finta().impronta, _lista_finta().impronta)

    def test_le_liste_del_pacchetto_sono_utilizzabili(self):
        for nome in ("nomi", "cognomi"):
            l = liste.carica(nome)
            self.assertGreaterEqual(len(l), liste.MINIMO)
            self.assertEqual(len(set(l.voci)), len(l.voci))

    def test_lista_da_file_dell_utente(self):
        p = Path(tempfile.mkdtemp()) / "miei.txt"
        p.write_text("# commento\n" + "\n".join("N%03d" % i for i in range(150)),
                     encoding="utf-8")
        self.assertEqual(len(liste.carica("miei", p)), 150)


class ListaSostituita(unittest.TestCase):
    """Cambiare la lista dopo aver cifrato = non tornare piu' indietro."""

    def test_una_voce_in_piu_sposta_i_surrogati(self):
        base = _lista_finta()
        allargata = liste.Lista(["PRIMA"] + base.voci, "finta")
        a = Surrogatore(CHIAVE, {"nomi": base})
        b = Surrogatore(CHIAVE, {"nomi": allargata})
        self.assertNotEqual(a.cifra("NOME", "VOCE050", TW),
                            b.cifra("NOME", "VOCE050", TW))


if __name__ == "__main__":
    unittest.main(verbosity=2)

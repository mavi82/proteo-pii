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


class FuoriLista(unittest.TestCase):
    """Chi non e' in lista si cifra lo stesso, conservando la forma."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def _fuori(self, quale, valore):
        surrogato = self.s.cifra(quale, valore, TW)
        self.assertEqual(self.s.decifra(quale, surrogato, TW), valore)
        return surrogato

    def test_un_nome_straniero_non_si_ferma_piu(self):
        self.assertNotEqual(self._fuori("NOME", "Ludmila"), "Ludmila")

    def test_il_surrogato_non_e_mai_una_voce_di_lista(self):
        """Altrimenti in decifratura verrebbe preso per l'altro percorso e
        restituirebbe un valore diverso dall'originale, in silenzio."""
        for valore in ("Ludmila", "Bartoli", "Xyz", "Kevin", "Ahmed"):
            surrogato = self._fuori("NOME", valore)
            self.assertIsNone(liste.carica("nomi").posizione(surrogato))

    def test_conserva_spazi_e_trattini(self):
        surrogato = self._fuori("NOME", "Maria Matias")
        self.assertEqual([len(p) for p in surrogato.split(" ")], [5, 6])
        self.assertEqual(self._fuori("NOME", "Nome-paz2").count("-"), 1)

    def test_le_cifre_restano_cifre(self):
        surrogato = self._fuori("NOME", "Paz-12")
        self.assertTrue(surrogato[-2:].isdigit())

    def test_conserva_il_maiuscolo_carattere_per_carattere(self):
        surrogato = self._fuori("NOME", "McDonald")
        self.assertEqual([c.isupper() for c in surrogato],
                         [True, False, True, False, False, False, False, False])

    def test_spazi_doppi_passano_dal_ripiego(self):
        """Dalla lista rientrerebbero con un solo spazio."""
        self.assertIn("  ", self._fuori("COGNOME", "De  Luca"))

    def test_forma_mista_passa_dal_ripiego(self):
        """`Rosa maria` da una voce di lista rientrerebbe `Rosa Maria`."""
        surrogato = self._fuori("NOME", "Rosa maria")
        self.assertIsNone(liste.carica("nomi").posizione(surrogato))

    def test_biiettivo_anche_fuori_lista(self):
        fuori = ["Nome-paz%d" % i for i in range(50)]
        self.assertEqual(len({self.s.cifra("NOME", v, TW) for v in fuori}), 50)


class Rifiuti(unittest.TestCase):
    """Resta un solo caso che non si puo' trattare."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def test_troppo_corto_per_essere_cifrato(self):
        """Una lettera sola da' cinque o ventuno combinazioni: sotto il minimo,
        e uscirebbe identica — cioe' in chiaro."""
        with self.assertRaises(ValoreNonTrattabile) as e:
            self.s.cifra("NOME", "A", TW)
        self.assertIn("troppo corto", str(e.exception))

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


class ScheletroConservato(unittest.TestCase):
    """Il ripiego deve restare pronunciabile: e' il punto di tutto il tipo.

    Cifrare le lettere come un blocco unico dava `Acciaro -> Jkjnpmg`: corretto
    e inservibile. Cifrando dentro le classi — vocale con vocale, consonante
    con consonante — lo scheletro resta, e cio' che esce si legge.
    """

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def _fuori(self, valore):
        surrogato = self.s.cifra("COGNOME", valore, TW)
        self.assertEqual(self.s.decifra("COGNOME", surrogato, TW), valore)
        return surrogato

    def _scheletro(self, parola):
        from proteo.surrogati import _VOCALI
        return "".join("V" if c.upper() in _VOCALI else "C"
                       for c in parola if c.isalpha())

    def test_vocali_e_consonanti_restano_al_loro_posto(self):
        for valore in ("Acciaro", "Gamboni", "Micaela", "Zambetta", "Grisai"):
            surrogato = self._fuori(valore)
            self.assertEqual(self._scheletro(surrogato), self._scheletro(valore),
                             "%s -> %s" % (valore, surrogato))

    def test_niente_grappoli_impronunciabili(self):
        """La prova pratica: nessuna sequenza che una lingua non direbbe, se
        l'originale non ce l'aveva."""
        for valore in ("Acciaro", "Gamboni", "Francesco", "Cantone"):
            surrogato = self._fuori(valore)
            self.assertNotRegex(surrogato, r"[BCDFGHJKLMNPQRSTVWXYZ]{4}")

    def test_le_parole_restano_parole(self):
        surrogato = self._fuori("Della barbera")
        self.assertEqual([len(p) for p in surrogato.split(" ")], [5, 7])

    def test_le_cifre_restano_cifre_e_distinte(self):
        due, tre = self._fuori("Cognome-paz2"), self._fuori("Cognome-paz3")
        self.assertTrue(due.endswith(("0", "1", "2", "3", "4", "5", "6", "7",
                                      "8", "9")))
        self.assertNotEqual(due, tre)

    def test_biiettivo(self):
        fuori = ["Acciar%s" % c for c in "abcdefghijklmnopqrstuvwxyz"]
        self.assertEqual(len({self.s.cifra("COGNOME", v, TW) for v in fuori}),
                         len(fuori))

    def test_la_versione_del_ripiego_viaggia_col_registro(self):
        """Cambiare il modo di cifrare i valori fuori lista cambia i surrogati:
        chi decifra deve accorgersene invece di ottenere valori sbagliati."""
        from proteo.surrogati import Surrogatore as S
        self.assertGreaterEqual(S.VERSIONE_RIPIEGO, 2)


class GruppiLegali(unittest.TestCase):
    """Le sequenze che l'italiano non ha non devono comparire nei surrogati."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def _fuori(self, valore):
        surrogato = self.s.cifra("COGNOME", valore, TW)
        self.assertEqual(self.s.decifra("COGNOME", surrogato, TW), valore)
        return surrogato

    def test_un_raddoppio_resta_un_raddoppio(self):
        for valore in ("Zambetta", "Crivelli", "Cichetti", "Genna"):
            surrogato = self._fuori(valore)
            self.assertRegex(surrogato, r"(.)\1", "%s -> %s" % (valore, surrogato))

    def test_un_gruppo_con_liquida_resta_legale(self):
        """`fr` puo' diventare `br` o `gl`, mai `dl` — che non esiste."""
        from proteo.surrogati import _GRUPPI_LIQUIDI, _unita
        for valore in ("Francesco", "Granitto", "Crivelli", "Capri"):
            surrogato = self._fuori(valore).upper()
            for tipo, pezzo in _unita(surrogato):
                if tipo == "CL":
                    self.assertIn(pezzo, _GRUPPI_LIQUIDI)

    def test_niente_vocali_doppie_inventate(self):
        for valore in ("Micaela", "Mariano", "Avellone", "Cantone", "Gamboni"):
            surrogato = self._fuori(valore)
            for i in range(len(surrogato) - 1):
                if surrogato[i].upper() in "AEIOU":
                    self.assertNotEqual(surrogato[i], surrogato[i + 1],
                                        "%s -> %s" % (valore, surrogato))

    def test_niente_grappoli_di_consonanti(self):
        for valore in ("Ferrarini", "Cantone", "Contro", "Curci", "Mariano"):
            self.assertNotRegex(self._fuori(valore).upper(),
                                r"[BCDFGHJKLMNPQRSTVWXYZ]{4}")

    def test_una_forma_diversa_non_passa(self):
        """Il cammino sulla forma: se il surrogato si rileggesse con unita'
        diverse, la decifratura lo scomporrebbe in un altro modo."""
        from proteo.surrogati import _forma
        for valore in ("Acciaro", "Zambetta", "Curci", "Cichetti", "Grisai",
                       "Francesco", "Della", "Contro", "Capri", "Genna"):
            surrogato = self._fuori(valore)
            self.assertEqual(_forma(surrogato.upper()), _forma(valore.upper()),
                             "%s -> %s" % (valore, surrogato))


class CifreIntatte(unittest.TestCase):
    """In un nome un numero non e' un dato personale: e' un contrassegno."""

    def setUp(self):
        self.s = Surrogatore(CHIAVE)

    def _fuori(self, valore):
        surrogato = self.s.cifra("COGNOME", valore, TW)
        self.assertEqual(self.s.decifra("COGNOME", surrogato, TW), valore)
        return surrogato

    def _cifre(self, testo):
        return "".join(c for c in testo if c.isdigit())

    def test_le_cifre_restano_identiche(self):
        for valore in ("Cognome-paz2", "Bianchi1974", "Rossi 2", "Paz-12",
                       "De Luca 3bis"):
            surrogato = self._fuori(valore)
            self.assertEqual(self._cifre(surrogato), self._cifre(valore),
                             "%s -> %s" % (valore, surrogato))

    def test_e_restano_al_loro_posto(self):
        surrogato = self._fuori("Bianchi1974")
        self.assertTrue(surrogato.endswith("1974"))

    def test_valori_che_differiscono_solo_per_il_numero_restano_distinti(self):
        """La corrispondenza deve restare biunivoca anche cosi'."""
        self.assertNotEqual(self._fuori("Cognome-paz2"), self._fuori("Cognome-paz3"))

    def test_le_lettere_cambiano_lo_stesso(self):
        surrogato = self._fuori("Cognome-paz2")
        self.assertNotIn("Cognome", surrogato)
        self.assertNotIn("paz", surrogato)

    def test_un_valore_di_soli_numeri_non_e_trattabile(self):
        """Non c'e' niente da cifrare: uscirebbe identico, cioe' in chiaro."""
        with self.assertRaises(ValoreNonTrattabile) as e:
            self.s.cifra("COGNOME", "12345", TW)
        self.assertIn("non contiene lettere", str(e.exception))

# -*- coding: utf-8 -*-
"""L'avanzamento: a schermo, su log, e nel registro.

La parte che conta davvero e' l'ultima: e' l'unica che sopravvive alla chiusura
del terminale, e quindi l'unica che risponde a "sta ancora lavorando?" da
un'altra sessione.
"""

import io
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo import stampa                                     # noqa: E402
from proteo.avanzamento import (Avanzamento, Silenzioso,      # noqa: E402
                                durata, quantita)
from proteo.registro import Registro                          # noqa: E402


class Formati(unittest.TestCase):
    def test_quantita(self):
        self.assertEqual(quantita(999), "999")
        self.assertEqual(quantita(1500), "1,5k")
        self.assertEqual(quantita(2_400_000), "2,4M")
        self.assertEqual(quantita(None), "?")

    def test_durata(self):
        self.assertEqual(durata(45), "45s")
        self.assertEqual(durata(90), "1m 30s")
        self.assertEqual(durata(3700), "1h 01m")
        self.assertEqual(durata(None), "?")


class SuLog(unittest.TestCase):
    """Uscita non interattiva: righe intere, mai `\\r`."""

    def setUp(self):
        self.out = io.StringIO()
        self.av = Avanzamento(self.out, tty=False)
        self.av.colonna("clienti", "cf", "CF", "cifra", 1000, 400)

    def test_intestazione(self):
        self.assertIn("clienti.cf", self.out.getvalue())
        self.assertIn("400 valori distinti", self.out.getvalue())

    def test_niente_ritorno_carrello(self):
        self.av.avanti(100)
        self.assertNotIn("\r", self.out.getvalue())

    def test_percentuale_e_stima(self):
        self.av.avanti(100)
        riga = self.out.getvalue().splitlines()[-1]
        self.assertIn("100/400", riga)
        self.assertIn("25%", riga)
        self.assertIn("mancano", riga)

    def test_le_fasi_si_leggono(self):
        self.av.fase("eseguo l'UPDATE")
        self.assertIn("eseguo l'UPDATE", self.out.getvalue())

    def test_un_valore_saltato_si_vede_subito(self):
        """Resta in chiaro: dirlo alla fine soltanto e' troppo tardi."""
        self.av.scartato("NON-UN-CF", "struttura errata")
        self.assertIn("RESTA IN CHIARO", self.out.getvalue())


class SuTerminale(unittest.TestCase):
    def test_riscrive_la_stessa_riga(self):
        out = io.StringIO()
        av = Avanzamento(out, tty=True)
        av.colonna("clienti", "cf", "CF", "cifra", 1000, 400)
        av.avanti(100)
        av.ultimo_schermo = 0            # supera l'intervallo minimo
        av.avanti(200)
        self.assertEqual(out.getvalue().count("\r"), 2)

    def test_la_riga_si_chiude_prima_del_messaggio_dopo(self):
        out = io.StringIO()
        av = Avanzamento(out, tty=True)
        av.colonna("clienti", "cf", "CF", "cifra", 1000, 400)
        av.avanti(100)
        av.conclusa(1000)
        self.assertTrue(out.getvalue().endswith("\n"))
        self.assertIn("fatto", out.getvalue())


class NelRegistro(unittest.TestCase):
    """Cio' che permette di seguire da fuori un processo in background."""

    def setUp(self):
        self.reg = Registro(Path(tempfile.mkdtemp()) / "registro")
        self.reg.avvia("DB", "clienti", "cf", "CF", "cf", "k0", "cifra")
        self.av = Avanzamento(io.StringIO(), registro=self.reg, database="DB",
                              tty=False)
        self.av.colonna("clienti", "cf", "CF", "cifra", 1000, 400)

    def test_l_avanzamento_finisce_su_disco(self):
        self.av.avanti(150)
        voce = self.reg.leggi("DB", "clienti", "cf")
        self.assertEqual(voce["elaborati"], 150)
        self.assertEqual(voce["distinti"], 400)

    def test_non_gonfia_lo_storico(self):
        """Si aggiorna ogni pochi secondi: se ogni scrittura lasciasse una voce
        nello storico, le due righe che contano sparirebbero nel rumore."""
        prima = len(self.reg.leggi("DB", "clienti", "cf").get("storico", []))
        for n in range(1, 40):
            self.av.ultimo_registro = 0
            self.av.avanti(n * 10)
        dopo = self.reg.leggi("DB", "clienti", "cf")
        self.assertEqual(len(dopo.get("storico", [])), prima)
        self.assertEqual(dopo["elaborati"], 390)

    def test_non_crea_voci_che_avvia_non_ha_creato(self):
        self.assertIsNone(self.reg.avanzamento("DB", "altra", "colonna", elaborati=1))
        self.assertIsNone(self.reg.leggi("DB", "altra", "colonna"))

    def test_stato_mostra_a_che_punto_e(self):
        self.av.avanti(200)
        voce = self.reg.leggi("DB", "clienti", "cf")
        riga = stampa._avanzamento(voce)
        self.assertIn("200/400", riga)
        self.assertIn("50%", riga)

    def test_niente_avanzamento_per_una_colonna_conclusa(self):
        self.reg.concludi("DB", "clienti", "cf", "cifrata", 1000)
        self.assertIsNone(stampa._avanzamento(self.reg.leggi("DB", "clienti", "cf")))

    def test_registro_non_scrivibile_non_ferma_il_lavoro(self):
        """Si perde la possibilita' di seguire da fuori, non i dati."""
        class Rotto:
            def avanzamento(self, *a, **k):
                raise OSError("disco pieno")
        av = Avanzamento(io.StringIO(), registro=Rotto(), database="DB", tty=False)
        av.colonna("clienti", "cf", "CF", "cifra", 1000, 400)
        av.avanti(10)                       # non deve sollevare


class NessunRumore(unittest.TestCase):
    def test_il_silenzioso_accetta_tutti_gli_eventi(self):
        s = Silenzioso()
        s.colonna("t", "c", "CF", "cifra", 1, 1)
        s.fase("x")
        s.avanti(1)
        s.scartato("v", "m")
        s.conclusa(1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

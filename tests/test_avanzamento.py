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
from proteo.avanzamento import (Avanzamento, Silenzioso, barra,  # noqa: E402
                                durata, quantita)
from proteo.registro import Registro                          # noqa: E402


class Formati(unittest.TestCase):
    def test_quantita(self):
        self.assertEqual(quantita(999), "999")
        self.assertEqual(quantita(1500), "1,5k")
        self.assertEqual(quantita(2_400_000), "2,4M")
        self.assertEqual(quantita(None), "?")

    def test_barra(self):
        self.assertEqual(barra(0, 100, 10), "░" * 10)
        self.assertEqual(barra(50, 100, 10), "█" * 5 + "░" * 5)
        self.assertEqual(barra(100, 100, 10), "█" * 10)

    def test_barra_senza_totale(self):
        """Una fase senza contatore non deve fingere di averne uno."""
        self.assertEqual(barra(5, None, 4), "░░░░")

    def test_durata(self):
        self.assertEqual(durata(45), "45s")
        self.assertEqual(durata(90), "1m 30s")
        self.assertEqual(durata(3700), "1h 01m")
        self.assertEqual(durata(None), "?")


class SuLog(unittest.TestCase):
    """Uscita non interattiva: righe intere, mai `\\r`."""

    def setUp(self):
        self.out = io.StringIO()
        self.av = Avanzamento(self.out, tty=False, battito=False)
        self.av.inizio("clienti", "cf", "CF", "cifra")
        self.av.totali(1000, 400)
        self.av.fase("leggo", contabile=True)

    def test_intestazione(self):
        self.assertIn("clienti.cf", self.out.getvalue())
        self.assertIn("400 valori distinti", self.out.getvalue())

    def test_niente_ritorno_carrello(self):
        self._avanti(100)
        self.assertNotIn("\r", self.out.getvalue())

    def _avanti(self, n):
        """Su log le righe sono rade: qui si supera l'intervallo a mano."""
        self.av.avanti(n)
        self.av.ultimo_log = 0
        self.av._disegna()

    def test_le_righe_sul_log_sono_rade(self):
        """Trenta secondi fra una riga e l'altra, o un 5% percorso: un log, non
        un diario. Venti valori su quattrocento non muovono nessuna delle due."""
        prima = len(self.out.getvalue().splitlines())
        for n in range(20):
            self.av.avanti(n)
        self.assertEqual(len(self.out.getvalue().splitlines()), prima)

    def test_ma_ogni_5_per_cento_lascia_una_riga(self):
        """Un lavoro di due minuti, a solo tempo, non lascerebbe traccia."""
        prima = len(self.out.getvalue().splitlines())
        for n in range(0, 401, 20):          # 400 valori: 5% = 20
            self.av.avanti(n)
        righe = len(self.out.getvalue().splitlines()) - prima
        self.assertGreaterEqual(righe, 15)
        self.assertLessEqual(righe, 25)

    def test_fermo_a_zero_lo_dice(self):
        """Una barra allo 0% con 'mancano ?' fa cercare il difetto dalla parte
        sbagliata: quasi sempre e' il database che non ha ancora risposto."""
        self.av.ultimo_log = 0
        self.av._disegna()
        riga = self.out.getvalue().splitlines()[-1]
        self.assertIn("in attesa della prima risposta del database", riga)
        self.assertNotIn("mancano ?", riga)

    def test_percentuale_e_stima(self):
        self._avanti(100)
        riga = self.out.getvalue().splitlines()[-1]
        self.assertIn("100/400", riga)
        self.assertIn("25%", riga)
        self.assertIn("mancano", riga)

    def test_la_barra_si_riempie(self):
        self._avanti(200)
        self.assertIn("█", self.out.getvalue().splitlines()[-1])

    def test_una_fase_senza_contatore_mostra_il_cronometro(self):
        """L'UPDATE finale non ha niente da contare, ma il tempo passa lo
        stesso — ed e' l'unica cosa che dice 'sta lavorando'."""
        self.av.fase("eseguo l'UPDATE")
        riga = self.out.getvalue().splitlines()[-1]
        self.assertIn("eseguo l'UPDATE", riga)
        self.assertNotIn("%", riga)

    def test_le_fasi_si_leggono(self):
        self.av.fase("eseguo l'UPDATE")
        self.assertIn("eseguo l'UPDATE", self.out.getvalue())

    def test_i_cambi_di_fase_non_si_perdono_nel_log(self):
        """Le righe sono rade, ma l'ossatura del racconto deve restare."""
        for descrizione in ("prima", "seconda", "terza"):
            self.av.fase(descrizione)
        for descrizione in ("prima", "seconda", "terza"):
            self.assertIn(descrizione, self.out.getvalue())

    def test_un_valore_saltato_si_vede_subito(self):
        """Resta in chiaro: dirlo alla fine soltanto e' troppo tardi."""
        self.av.scartato("NON-UN-CF", "struttura errata")
        self.assertIn("RESTA IN CHIARO", self.out.getvalue())


class SuTerminale(unittest.TestCase):
    def _av(self, out):
        av = Avanzamento(out, tty=True, battito=False)
        av.inizio("clienti", "cf", "CF", "cifra")
        av.totali(1000, 400)
        av.fase("leggo", contabile=True)
        return av

    def test_riscrive_la_stessa_riga(self):
        out = io.StringIO()
        av = self._av(out)
        prima = out.getvalue().count("\r")
        av.avanti(100)
        av.avanti(200)
        self.assertEqual(out.getvalue().count("\r"), prima + 2)

    def test_la_riga_si_chiude_prima_del_messaggio_dopo(self):
        out = io.StringIO()
        av = self._av(out)
        av.avanti(100)
        av.conclusa(1000)
        self.assertTrue(out.getvalue().endswith("\n"))
        self.assertIn("fatto", out.getvalue())

    def test_il_battito_scorre_da_solo(self):
        """Senza, la riga resta immobile per tutta la durata dell'UPDATE."""
        import time
        out = io.StringIO()
        av = Avanzamento(out, tty=True, battito=True)
        av.inizio("clienti", "cf", "CF", "cifra")
        av.fase("eseguo l'UPDATE")          # nessun evento successivo
        prima = out.getvalue().count("\r")
        time.sleep(0.8)
        dopo = out.getvalue().count("\r")
        av.chiudi()
        self.assertGreater(dopo, prima, "la riga non si e' mai aggiornata")

    def test_chiudi_ferma_il_thread(self):
        av = Avanzamento(io.StringIO(), tty=True, battito=True)
        av.inizio("clienti", "cf", "CF", "cifra")
        av.chiudi()
        self.assertIsNone(av.battito)


class NelRegistro(unittest.TestCase):
    """Cio' che permette di seguire da fuori un processo in background."""

    def setUp(self):
        self.reg = Registro(Path(tempfile.mkdtemp()) / "registro")
        self.reg.avvia("DB", "clienti", "cf", "CF", "cf", "k0", "cifra")
        self.av = Avanzamento(io.StringIO(), registro=self.reg, database="DB",
                              tty=False, battito=False)
        self.av.inizio("clienti", "cf", "CF", "cifra")
        self.av.totali(1000, 400)
        self.av.fase("leggo", contabile=True)

    def test_l_avanzamento_finisce_su_disco(self):
        self.av.avanti(150)
        self.av._scrivi_registro(forza=True)
        voce = self.reg.leggi("DB", "clienti", "cf")
        self.assertEqual(voce["elaborati"], 150)
        self.assertEqual(voce["distinti"], 400)

    def test_non_gonfia_lo_storico(self):
        """Si aggiorna ogni pochi secondi: se ogni scrittura lasciasse una voce
        nello storico, le due righe che contano sparirebbero nel rumore."""
        prima = len(self.reg.leggi("DB", "clienti", "cf").get("storico", []))
        for n in range(1, 40):
            self.av.avanti(n * 10)
            self.av.ultimo_registro = 0
            self.av._scrivi_registro()
        dopo = self.reg.leggi("DB", "clienti", "cf")
        self.assertEqual(len(dopo.get("storico", [])), prima)
        self.assertEqual(dopo["elaborati"], 390)

    def test_non_crea_voci_che_avvia_non_ha_creato(self):
        self.assertIsNone(self.reg.avanzamento("DB", "altra", "colonna", elaborati=1))
        self.assertIsNone(self.reg.leggi("DB", "altra", "colonna"))

    def test_anche_stato_dice_che_e_fermo_a_zero(self):
        self.av._scrivi_registro(forza=True)
        riga = stampa._avanzamento(self.reg.leggi("DB", "clienti", "cf"))
        self.assertIn("in attesa", riga)

    def test_stato_mostra_a_che_punto_e(self):
        self.av.avanti(200)
        self.av._scrivi_registro(forza=True)
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
        av = Avanzamento(io.StringIO(), registro=Rotto(), database="DB",
                         tty=False, battito=False)
        av.inizio("clienti", "cf", "CF", "cifra")
        av.avanti(10)
        av._scrivi_registro(forza=True)     # non deve sollevare


class NessunRumore(unittest.TestCase):
    def test_il_silenzioso_accetta_tutti_gli_eventi(self):
        s = Silenzioso()
        s.inizio("t", "c", "CF", "cifra")
        s.totali(1, 1)
        s.fase("x", contabile=True)
        s.avanti(1)
        s.scartato("v", "m")
        s.conclusa(1)
        s.chiudi()


if __name__ == "__main__":
    unittest.main(verbosity=2)

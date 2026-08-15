# -*- coding: utf-8 -*-
"""Il diario delle operazioni, e la regola che lo rende utile.

Un diario si scrive per mandarlo a qualcuno: a un collega, a chi ha scritto il
programma, allegato a una segnalazione. Se contenesse valori veri non si
potrebbe mandare, quindi non verrebbe scritto — e allora tanto varrebbe non
averlo. Qui si verifica che non ne contenga.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, text                    # noqa: E402

from proteo import db, diario                                 # noqa: E402
from proteo.avanzamento import Avanzamento                    # noqa: E402
from proteo.motore import Motore                              # noqa: E402
from proteo.policy import Policy                              # noqa: E402
from proteo.registro import Registro                          # noqa: E402

CHIAVE = bytes(range(32))
CF = ["RSSMRA85H12F205Y", "BNCLGU78T04H501C", "VRDNNA90A41F839L"]


def _percorso():
    return Path(tempfile.mkdtemp()) / "sotto" / "proteo.log"


def _db():
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE clienti (id INTEGER PRIMARY KEY, cf TEXT, "
                       "nome TEXT)"))
        for i, cf in enumerate(CF):
            c.execute(text("INSERT INTO clienti VALUES (:i,:cf,:n)"),
                      {"i": i, "cf": cf, "n": ["Mario", "Anna", "X"][i]})
    return e


class Scrittura(unittest.TestCase):
    def setUp(self):
        self.p = _percorso()
        self.d = diario.apri(self.p)

    def tearDown(self):
        self.d.chiudi()

    def test_crea_anche_la_cartella(self):
        self.assertTrue(self.p.exists())

    def test_ogni_riga_ha_l_ora(self):
        self.d.riga("qualcosa")
        prima = self.p.read_text(encoding="utf-8").splitlines()[-1]
        self.assertRegex(prima, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}  qualcosa$")

    def test_si_accoda_fra_sessioni(self):
        """Confrontare l'esecuzione andata bene con quella andata male e' spesso
        la diagnosi: se il file si azzerasse, non ci sarebbe niente da
        confrontare."""
        self.d.riga("prima sessione")
        self.d.chiudi()
        secondo = diario.apri(self.p)
        secondo.riga("seconda sessione")
        secondo.chiudi()
        testo = self.p.read_text(encoding="utf-8")
        self.assertIn("prima sessione", testo)
        self.assertIn("seconda sessione", testo)

    def test_un_errore_porta_la_traccia(self):
        try:
            raise ValueError("qualcosa e' andato storto")
        except ValueError as e:
            self.d.errore(e, "prova")
        testo = self.p.read_text(encoding="utf-8")
        self.assertIn("ValueError: qualcosa e' andato storto", testo)
        self.assertIn("Traceback", testo)

    def test_un_percorso_non_scrivibile_non_ferma_il_lavoro(self):
        """Si perde la possibilita' di capire cosa e' successo, non i dati."""
        silenzioso = diario.apri("/percorso/che/non/esiste/e/non/si/crea\0/x.log")
        self.assertIsInstance(silenzioso, diario.Silenzioso)
        silenzioso.riga("non esplode")

    def test_senza_percorso_e_silenzioso(self):
        self.assertIsInstance(diario.apri(None), diario.Silenzioso)


class NienteValoriVeri(unittest.TestCase):
    """La regola che rende il diario condivisibile."""

    def setUp(self):
        self.p = _percorso()
        self.d = diario.apri(self.p)
        self.engine = _db()
        self.d.collega(self.engine)

    def tearDown(self):
        self.d.chiudi()
        self.engine.dispose()

    def _testo(self):
        return self.p.read_text(encoding="utf-8")

    def test_le_istruzioni_si_scrivono_senza_parametri(self):
        with self.engine.connect() as c:
            c.execute(text("SELECT cf FROM clienti WHERE cf = :v"),
                      {"v": CF[0]})
        testo = self._testo()
        self.assertIn("SELECT cf FROM clienti", testo)
        self.assertNotIn(CF[0], testo)

    def test_un_ciclo_completo_non_lascia_nessun_valore(self):
        """Il controllo che conta: ne' i valori veri ne' i surrogati."""
        p = Policy({"clienti": {"id": {"strategia": "mantieni"},
                                "cf": {"strategia": "cifra", "tipo": "CF"},
                                "nome": {"strategia": "mantieni"}}})
        m = Motore(self.engine, p, CHIAVE, "k0",
                   Registro(Path(tempfile.mkdtemp()) / "r"), "DB")
        m.esegui("cifra", avanzamento=Avanzamento(io.StringIO(), tty=False,
                                                  battito=False, diario=self.d))
        testo = self._testo()
        with self.engine.connect() as c:
            surrogati = [r[0] for r in c.execute(text("SELECT cf FROM clienti"))]
        for valore in CF + surrogati:
            self.assertNotIn(valore, testo, "valore finito nel diario: %s" % valore)

    def test_i_valori_saltati_si_contano_non_si_scrivono(self):
        p = Policy({"clienti": {"id": {"strategia": "mantieni"},
                                "cf": {"strategia": "mantieni"},
                                "nome": {"strategia": "cifra", "tipo": "NOME"}}})
        m = Motore(self.engine, p, CHIAVE, "k0",
                   Registro(Path(tempfile.mkdtemp()) / "r"), "DB")
        m.esegui("cifra", su_valore_non_trattabile="salta",
                 avanzamento=Avanzamento(io.StringIO(), tty=False, battito=False,
                                         diario=self.d))
        testo = self._testo()
        self.assertIn("1 valori saltati", testo)
        self.assertNotIn("Mario", testo)

    def test_le_fasi_e_i_conteggi_ci_sono(self):
        """Senza, il diario non direbbe dove ci si e' fermati."""
        av = Avanzamento(io.StringIO(), tty=False, battito=False, diario=self.d)
        av.inizio("clienti", "cf", "CF", "cifra")
        av.totali(3, 3)
        av.fase("leggo i valori", contabile=True)
        av.conclusa(3)
        testo = self._testo()
        self.assertIn("colonna clienti.cf (CF)", testo)
        self.assertIn("3 righe, 3 valori distinti", testo)
        self.assertIn("fase: leggo i valori", testo)
        self.assertIn("conclusa: 3 righe", testo)


class ComandiRifiutati(unittest.TestCase):
    """Un comando che non parte e' la cosa che si va a cercare rileggendo."""

    def test_l_uscita_finisce_nel_diario(self):
        from proteo import cli
        p = _percorso()
        vecchio, cli._DIARIO = cli._DIARIO, diario.apri(p)
        try:
            with self.assertRaises(SystemExit):
                raise cli.Uscita("gia' cifrata: non si cifra due volte")
        finally:
            cli._DIARIO.chiudi()
            cli._DIARIO = vecchio
        self.assertIn("rifiutato: gia' cifrata", p.read_text(encoding="utf-8"))


class SenzaTraceback(unittest.TestCase):
    """Un valore non trattabile e' una decisione da prendere, non un difetto."""

    def test_la_riga_di_comando_non_mostra_un_traceback(self):
        from proteo import cli
        from proteo.surrogati import ValoreNonTrattabile
        sorgente = Path(cli.__file__).read_text(encoding="utf-8")
        self.assertIn("except ValoreNonTrattabile", sorgente)
        self.assertIn("--su-errore salta", sorgente)
        self.assertTrue(issubclass(ValoreNonTrattabile, ValueError))


class Intestazione(unittest.TestCase):
    def test_dice_versioni_e_opzioni(self):
        """E' la prima cosa che si chiede a chi segnala un problema, e la piu'
        noiosa da raccogliere a mano."""
        p = _percorso()
        d = diario.apri(p)
        d.intestazione(url="mssql+pyodbc://sa:***@127.0.0.1:1433/EasyDiet",
                       comando="cifra", lotto_righe=1000)
        d.chiudi()
        testo = p.read_text(encoding="utf-8")
        self.assertIn("python", testo)
        self.assertIn("sqlalchemy", testo)
        self.assertIn("comando: cifra", testo)
        self.assertIn("lotto_righe: 1000", testo)
        self.assertIn("EasyDiet", testo)
        self.assertNotIn("Alain", testo)      # la password non c'e' mai


if __name__ == "__main__":
    unittest.main(verbosity=2)

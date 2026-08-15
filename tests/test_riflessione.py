# -*- coding: utf-8 -*-
"""La riflessione dello schema, e perche' non deve mai avvenire a transazione aperta.

Su SQL Server un `CREATE TABLE` dentro una transazione tiene lock sui metadati:
chi interroga le viste di sistema da un'altra connessione resta fermo. La
tabella di appoggio si crea proprio cosi', e il generatore che legge i valori
distinti viene consumato dentro quella transazione — se riflettesse li', si
aspetterebbero a vicenda per sempre.

Qui si verifica la proprieta' che lo rende impossibile: la riflessione avviene
una volta sola, e prima.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, event, text            # noqa: E402

from proteo import db                                        # noqa: E402


def _db():
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        for i in range(20):
            c.execute(text("INSERT INTO t VALUES (:i,'A')"), {"i": i})
    return e


class Cache(unittest.TestCase):
    def setUp(self):
        self.engine = _db()

    def tearDown(self):
        db._RIFLESSE.pop(self.engine, None)
        self.engine.dispose()

    def test_riflette_una_volta_sola(self):
        self.assertIs(db._tabella(self.engine, "t"), db._tabella(self.engine, "t"))

    def test_engine_diversi_non_si_mescolano(self):
        altro = _db()
        try:
            self.assertIsNot(db._tabella(self.engine, "t"), db._tabella(altro, "t"))
        finally:
            db._RIFLESSE.pop(altro, None)
            altro.dispose()

    def test_con_metadata_esplicito_resta_indipendente(self):
        """`applica_mappa` ne ha bisogno: la sua tabella di appoggio non deve
        finire nella descrizione condivisa."""
        from sqlalchemy import MetaData
        md = MetaData()
        t = db._tabella(self.engine, "t", md)
        self.assertIsNot(t, db._tabella(self.engine, "t"))


class NienteRiflessioneATransazioneAperta(unittest.TestCase):
    """Il controllo che vale per tutti i motori, anche dove non si vedrebbe."""

    def setUp(self):
        self.engine = _db()
        self.dentro, self.aperta = [], False

        # Si annota ogni interrogazione ai metadati fatta MENTRE la transazione
        # che ha creato la tabella di appoggio e' aperta: e' esattamente quella
        # che su SQL Server resta bloccata per sempre.
        @event.listens_for(self.engine, "before_cursor_execute")
        def spia(conn, cursore, istruzione, parametri, contesto, molte):
            testo = istruzione.lower()
            if db.PREFISSO_MAPPA in testo:
                if testo.strip().startswith("create"):
                    self.aperta = True
                elif testo.strip().startswith("drop"):
                    self.aperta = False
            elif self.aperta and ("sqlite_master" in testo or "pragma" in testo):
                self.dentro.append(istruzione)

    def tearDown(self):
        db._RIFLESSE.pop(self.engine, None)
        self.engine.dispose()

    def test_applica_mappa_non_riflette_dopo_aver_creato_la_tabella(self):
        def coppie():
            # il generatore legge: e' il punto in cui la riflessione tardiva
            # avveniva, dentro la transazione che ha appena creato la mappa
            for blocco in db.leggi_distinti(self.engine, "t", "v"):
                for v in blocco:
                    yield v, "Z"

        db.applica_mappa(self.engine, "t", "v", coppie())
        self.assertEqual(self.dentro, [],
                         "riflessione dei metadati durante la transazione")

    def test_il_risultato_resta_corretto(self):
        def coppie():
            for blocco in db.leggi_distinti(self.engine, "t", "v"):
                for v in blocco:
                    yield v, "Z"

        toccate = db.applica_mappa(self.engine, "t", "v", coppie())
        self.assertEqual(toccate, 20)
        with self.engine.connect() as c:
            self.assertEqual({r[0] for r in c.execute(text("SELECT v FROM t"))},
                             {"Z"})


if __name__ == "__main__":
    unittest.main(verbosity=2)

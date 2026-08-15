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


class NienteAltreConnessioniATransazioneAperta(unittest.TestCase):
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

    def test_niente_metadati_durante_la_transazione(self):
        db.applica_mappa(self.engine, "t", "v", [("A", "Z")])
        self.assertEqual(self.dentro, [],
                         "riflessione dei metadati durante la transazione")

    def test_il_motore_legge_tutto_prima_di_aprire_la_transazione(self):
        """La proprieta' strutturale: mentre si scrive non ci deve essere
        nessun'altra connessione aperta. Su SQL Server, leggere da una seconda
        connessione mentre la prima ha appena creato una tabella si blocca —
        indefinitamente, e in modo indistinguibile da un database lento."""
        import tempfile
        from proteo.motore import Motore
        from proteo.policy import Policy
        from proteo.registro import Registro

        letture = []

        @event.listens_for(self.engine, "before_cursor_execute")
        def spia_letture(conn, cursore, istruzione, parametri, contesto, molte):
            if self.aperta and istruzione.lower().lstrip().startswith("select") \
                    and db.PREFISSO_MAPPA not in istruzione.lower():
                letture.append(istruzione)

        p = Policy({"t": {"id": {"strategia": "mantieni"},
                          "v": {"strategia": "cifra", "tipo": "NOME"}}})
        with self.engine.begin() as c:
            c.execute(text("UPDATE t SET v = 'Mario'"))
        Motore(self.engine, p, bytes(range(32)), "k0",
               Registro(Path(tempfile.mkdtemp()) / "r"), "DB").esegui("cifra")
        self.assertEqual(letture, [],
                         "lettura da un'altra connessione a transazione aperta")

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


class OpzioniDelDriver(unittest.TestCase):
    """`fast_executemany` e' del driver Microsoft, non di ogni pyodbc."""

    def test_col_driver_microsoft_si_attiva(self):
        from sqlalchemy.engine import make_url
        url = make_url("mssql+pyodbc://u:p@h:1433/d"
                       "?driver=ODBC+Driver+18+for+SQL+Server")
        self.assertFalse(db._e_freetds(url))

    def test_con_freetds_no(self):
        """Li' gli array di parametri non sono implementati allo stesso modo, e
        l'inserimento della mappa si pianta invece di fallire."""
        from sqlalchemy.engine import make_url
        self.assertTrue(db._e_freetds(
            make_url("mssql+pyodbc://u:p@h:1433/d?driver=FreeTDS&TDS_Version=7.4")))

    def test_senza_driver_dichiarato_non_esplode(self):
        from sqlalchemy.engine import make_url
        self.assertFalse(db._e_freetds(make_url("mssql+pyodbc://u:p@h:1433/d")))

    def test_la_pagina_di_insert_e_piccola(self):
        """Il flusso TDS si rompe sulle richieste grandi: SQL Server chiude la
        sessione con l'errore 4014 e il client vede solo 'Unexpected EOF'."""
        self.assertLessEqual(db.PAGINA_INSERT_FREETDS, 500)
        self.assertLessEqual(db.LOTTO_SCRITTURA_FREETDS, db.LOTTO_SCRITTURA)


if __name__ == "__main__":
    unittest.main(verbosity=2)

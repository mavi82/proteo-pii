# -*- coding: utf-8 -*-
"""Cosa resta quando un'esecuzione si interrompe a meta'.

Le due domande sono: il database resta coerente, e resta in giro la mappa in
chiaro? La prima la garantisce la transazione, la seconda no su tutti i motori —
e per quello c'e' `mappe_orfane`.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, text                    # noqa: E402

from proteo import db                                         # noqa: E402
from proteo.motore import Motore                              # noqa: E402
from proteo.policy import Policy                              # noqa: E402
from proteo.registro import IN_CORSO, Registro                # noqa: E402

CF = ["RSSMRA85H12F205Y", "BNCLGU78T04H501C", "VRDNNA90A41F839L"]
CHIAVE = bytes(range(32))


def _db():
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE clienti (id INTEGER PRIMARY KEY, cf TEXT)"))
        for i, v in enumerate(CF):
            c.execute(text("INSERT INTO clienti VALUES (:i,:v)"), {"i": i, "v": v})
    return e


def _valori(engine):
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT cf FROM clienti ORDER BY id"))]


class Rollback(unittest.TestCase):
    def setUp(self):
        self.engine = _db()

    def tearDown(self):
        self.engine.dispose()

    def _coppie_che_esplodono(self):
        yield CF[0], "AAAAAA00A00A000A"
        raise KeyboardInterrupt        # come un Ctrl-C a meta' lavoro

    def test_un_ctrl_c_non_lascia_la_colonna_a_meta(self):
        with self.assertRaises(KeyboardInterrupt):
            db.applica_mappa(self.engine, "clienti", "cf",
                             self._coppie_che_esplodono())
        self.assertEqual(_valori(self.engine), CF)

    def test_l_errore_vero_non_viene_mascherato_dalla_pulizia(self):
        """Se la pulizia della tabella di appoggio fallisse a sua volta,
        l'eccezione utile — quella che dice cosa e' successo — sparirebbe."""
        def coppie():
            yield CF[0], "AAAAAA00A00A000A"
            raise ValueError("l'errore vero")

        with self.assertRaises(ValueError) as e:
            db.applica_mappa(self.engine, "clienti", "cf", coppie())
        self.assertIn("l'errore vero", str(e.exception))


class MappeOrfane(unittest.TestCase):
    """La tabella di appoggio non deve sopravvivere. Se succede, si trova."""

    def setUp(self):
        self.engine = _db()

    def tearDown(self):
        self.engine.dispose()

    def test_una_esecuzione_riuscita_non_lascia_niente(self):
        reg = Registro(Path(tempfile.mkdtemp()) / "r")
        p = Policy({"clienti": {"id": {"strategia": "mantieni"},
                                "cf": {"strategia": "cifra", "tipo": "CF"}}})
        Motore(self.engine, p, CHIAVE, "k0", reg, "DB").esegui("cifra")
        self.assertEqual(db.mappe_orfane(self.engine), [])

    def test_una_mappa_rimasta_indietro_si_trova(self):
        with self.engine.begin() as c:
            c.execute(text("CREATE TABLE %sabc123 (vecchio TEXT, nuovo TEXT)"
                           % db.PREFISSO_MAPPA))
        self.assertEqual(db.mappe_orfane(self.engine),
                         [db.PREFISSO_MAPPA + "abc123"])

    def test_e_si_elimina(self):
        with self.engine.begin() as c:
            c.execute(text("CREATE TABLE %sabc123 (vecchio TEXT, nuovo TEXT)"
                           % db.PREFISSO_MAPPA))
        db.elimina_mappa(self.engine, db.PREFISSO_MAPPA + "abc123")
        self.assertEqual(db.mappe_orfane(self.engine), [])

    def test_le_tabelle_vere_non_si_toccano(self):
        self.assertNotIn("clienti", db.mappe_orfane(self.engine))


class StatoDopoUnInterruzione(unittest.TestCase):
    def test_la_colonna_resta_in_corso(self):
        """E' il segnale corretto: la voce dice che qualcosa non e' finito."""
        reg = Registro(Path(tempfile.mkdtemp()) / "r")
        reg.avvia("DB", "clienti", "cf", "CF", "cf", "k0", "cifra")
        self.assertEqual(reg.stato("DB", "clienti", "cf"), IN_CORSO)
        self.assertEqual(len(reg.interrotte("DB")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

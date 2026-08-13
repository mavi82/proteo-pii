# -*- coding: utf-8 -*-
"""Scrittura a lotti: meno lock, stessa mappa applicata.

Il rischio di scrivere a pezzi e' l'effetto domino descritto in `db.py`: se
A->B e B->C, un pezzo che porta le righe A in B le espone al pezzo successivo,
che le ripesca e le manda in C. Qui si verifica che non succeda — ed e' il
motivo per cui i lotti sono per chiave primaria e non per valore.
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
from proteo.registro import Registro                          # noqa: E402

CHIAVE = bytes(range(32))
CF = ["RSSMRA85H12F205Y", "BNCLGU78T04H501C", "VRDNNA90A41F839L",
      "MRTPLA65M15L219C"]


def _db(valori):
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        for i, v in enumerate(valori):
            c.execute(text("INSERT INTO t VALUES (:i,:v)"), {"i": i, "v": v})
    return e


def _valori(engine):
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT v FROM t ORDER BY id"))]


class NienteDomino(unittest.TestCase):
    """La prova che i lotti per chiave sono sicuri."""

    def test_una_catena_non_si_propaga(self):
        """A->B, B->C: le righe che erano A devono finire in B, non in C."""
        engine = _db(["A", "B", "C", "A", "B"])
        toccate = db.applica_mappa_a_lotti(
            engine, "t", "v", [("A", "B"), ("B", "C"), ("C", "A")], "id",
            lotto_righe=2)
        self.assertEqual(_valori(engine), ["B", "C", "A", "B", "C"])
        self.assertEqual(toccate, 5)
        engine.dispose()

    def test_un_ciclo_di_due_si_scambia(self):
        """A<->B e' il caso in cui un UPDATE per valore distrugge tutto."""
        engine = _db(["A", "B", "A", "B"])
        db.applica_mappa_a_lotti(engine, "t", "v", [("A", "B"), ("B", "A")],
                                 "id", lotto_righe=1)
        self.assertEqual(_valori(engine), ["B", "A", "B", "A"])
        engine.dispose()

    def test_stesso_risultato_della_transazione_unica(self):
        engine1, engine2 = _db(["A", "B", "C"]), _db(["A", "B", "C"])
        mappa = [("A", "B"), ("B", "C"), ("C", "A")]
        db.applica_mappa(engine1, "t", "v", list(mappa))
        db.applica_mappa_a_lotti(engine2, "t", "v", list(mappa), "id",
                                 lotto_righe=1)
        self.assertEqual(_valori(engine1), _valori(engine2))
        engine1.dispose()
        engine2.dispose()


class Lotti(unittest.TestCase):
    def setUp(self):
        self.engine = _db(["A"] * 25)

    def tearDown(self):
        self.engine.dispose()

    def test_tocca_tutte_le_righe(self):
        toccate = db.applica_mappa_a_lotti(self.engine, "t", "v", [("A", "Z")],
                                           "id", lotto_righe=10)
        self.assertEqual(toccate, 25)
        self.assertEqual(set(_valori(self.engine)), {"Z"})

    def test_le_righe_fuori_mappa_restano(self):
        engine = _db(["A", "ignoto", "A"])
        db.applica_mappa_a_lotti(engine, "t", "v", [("A", "Z")], "id",
                                 lotto_righe=1)
        self.assertEqual(_valori(engine), ["Z", "ignoto", "Z"])
        engine.dispose()

    def test_mappa_vuota(self):
        self.assertEqual(db.applica_mappa_a_lotti(self.engine, "t", "v", [],
                                                  "id", lotto_righe=10), 0)

    def test_non_lascia_tabelle_di_appoggio(self):
        db.applica_mappa_a_lotti(self.engine, "t", "v", [("A", "Z")], "id",
                                 lotto_righe=10)
        self.assertEqual(db.mappe_orfane(self.engine), [])

    def test_riporta_l_ultima_chiave_di_ogni_lotto(self):
        """E' l'unico appiglio per sapere, dopo un'interruzione, dove si era
        arrivati: la colonna e' a meta' e nessuno puo' dedurlo guardandola."""
        visti = []
        db.applica_mappa_a_lotti(self.engine, "t", "v", [("A", "Z")], "id",
                                 lotto_righe=10,
                                 su_lotto=lambda chiave, righe: visti.append(
                                     (chiave, righe)))
        self.assertEqual(visti, [(9, 10), (19, 20), (24, 25)])


class Scelta(unittest.TestCase):
    """Quando il motore puo' usare i lotti, e quando torna alla via sicura."""

    def _motore(self, engine, lotto_righe=1000):
        p = Policy({"t": {"id": {"strategia": "mantieni"},
                          "v": {"strategia": "cifra", "tipo": "CF"}}})
        return Motore(engine, p, CHIAVE, "k0",
                      Registro(Path(tempfile.mkdtemp()) / "r"), "DB",
                      lotto_righe=lotto_righe)

    def test_con_chiave_primaria_si_usano_i_lotti(self):
        engine = _db(CF)
        m = self._motore(engine)
        self.assertEqual(m._chiave_primaria("t"), "id")
        m.esegui("cifra")
        self.assertNotEqual(_valori(engine), CF)
        m.esegui("decifra")
        self.assertEqual(_valori(engine), CF)
        engine.dispose()

    def test_senza_chiave_primaria_si_torna_alla_transazione_unica(self):
        """Senza un ordine stabile i lotti si sovrapporrebbero: meglio un lock
        lungo che due lotti che si contendono la stessa riga."""
        engine = create_engine("sqlite://")
        with engine.begin() as c:
            c.execute(text("CREATE TABLE t (id INTEGER, v TEXT)"))
            for i, v in enumerate(CF):
                c.execute(text("INSERT INTO t VALUES (:i,:v)"), {"i": i, "v": v})
        m = self._motore(engine)
        self.assertIsNone(m._chiave_primaria("t"))
        m.esegui("cifra")                     # deve funzionare lo stesso
        self.assertNotEqual(_valori(engine), CF)
        engine.dispose()

    def test_il_default_resta_la_transazione_unica(self):
        engine = _db(CF)
        self.assertIsNone(self._motore(engine, lotto_righe=None).lotto_righe)
        engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)

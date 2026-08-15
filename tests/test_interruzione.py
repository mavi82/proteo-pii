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


class ErroreVero(unittest.TestCase):
    """Cio' che ha fatto fallire non deve essere coperto da cio' che segue."""

    def test_un_rollback_fallito_non_copre_l_errore_originale(self):
        """Se la connessione muore, anche il ROLLBACK fallisce — e la sua
        eccezione prenderebbe il posto di quella che spiega cosa e' successo.
        Si resterebbe con 'Unexpected EOF (SQLEndTran)', che dice come e'
        finita e mai perche' e' cominciata."""
        engine = _db()

        def coppie():
            yield CF[0], "AAAAAA00A00A000A"
            raise ValueError("l'errore vero")

        rotture = []

        class TransazioneRotta:
            """Simula una connessione caduta: il rollback fallisce."""
            def __init__(self, dietro):
                self.dietro = dietro

            def commit(self):
                self.dietro.commit()

            def rollback(self):
                rotture.append(True)
                raise RuntimeError("Unexpected EOF from the server (SQLEndTran)")

        vero_connect = engine.connect

        def connect_finto(*a, **k):
            conn = vero_connect(*a, **k)
            vero_begin = conn.begin

            def begin_finto():
                return TransazioneRotta(vero_begin())
            conn.begin = begin_finto
            return conn

        engine.connect = connect_finto
        try:
            with self.assertRaises(ValueError) as e:
                db.applica_mappa(engine, "clienti", "cf", coppie())
            self.assertIn("l'errore vero", str(e.exception))
            self.assertTrue(rotture, "il rollback non e' stato nemmeno tentato")
        finally:
            engine.connect = vero_connect
            engine.dispose()


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


class ScritturaSenzaEffetto(unittest.TestCase):
    """Zero righe toccate con una mappa piena e' un guasto, non un risultato.

    E' successo su SQL Server per un tipo sbagliato nella tabella di appoggio:
    la colonna era rimasta in chiaro, l'esecuzione era finita senza errori e il
    registro aveva segnato 'cifrata'. Da fuori sembrava tutto a posto.
    """

    def setUp(self):
        self.engine = _db()

    def tearDown(self):
        self.engine.dispose()

    def test_una_mappa_che_non_corrisponde_a_niente_si_ferma(self):
        with self.assertRaises(db.ScritturaSenzaEffetto) as e:
            db.applica_mappa(self.engine, "clienti", "cf",
                             [("VALORE-CHE-NON-C-E", "ALTRO")])
        self.assertIn("non confronta", str(e.exception))

    def test_e_non_scrive_niente(self):
        with self.assertRaises(db.ScritturaSenzaEffetto):
            db.applica_mappa(self.engine, "clienti", "cf",
                             [("VALORE-CHE-NON-C-E", "ALTRO")])
        self.assertEqual(_valori(self.engine), CF)
        # la mappa in chiaro non deve restare su disco nemmeno fallendo
        self.assertEqual(db.mappe_orfane(self.engine), [])

    def test_una_colonna_intatta_non_risulta_cifrata(self):
        """Il punto: 'cifrata' deve voler dire che qualcosa e' stato cifrato.

        Se ogni valore viene saltato non cambia niente, e segnarla 'cifrata'
        bloccherebbe il tentativo successivo autorizzando per giunta una
        decifratura su valori mai cifrati.
        """
        reg = Registro(Path(tempfile.mkdtemp()) / "r")
        p = Policy({"clienti": {"id": {"strategia": "mantieni"},
                                "cf": {"strategia": "cifra", "tipo": "CF"}}})
        with self.engine.begin() as c:
            c.execute(text("UPDATE clienti SET cf = 'NON-UN-CF'"))
        r = Motore(self.engine, p, CHIAVE, "k0", reg, "DB").esegui(
            "cifra", su_valore_non_trattabile="salta")
        self.assertEqual(r["colonne"][0]["righe_aggiornate"], 0)
        self.assertEqual(reg.stato("DB", "clienti", "cf"), "in_chiaro")

    def test_una_mappa_vuota_resta_lecita(self):
        """Nessun valore da trattare non e' un guasto: e' una colonna vuota."""
        self.assertEqual(db.applica_mappa(self.engine, "clienti", "cf", []), 0)

    def test_a_lotti_vale_lo_stesso_controllo(self):
        with self.assertRaises(db.ScritturaSenzaEffetto):
            db.applica_mappa_a_lotti(self.engine, "clienti", "cf",
                                     [("VALORE-CHE-NON-C-E", "ALTRO")], "id",
                                     lotto_righe=2)
        self.assertEqual(_valori(self.engine), CF)

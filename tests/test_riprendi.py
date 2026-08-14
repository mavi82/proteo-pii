# -*- coding: utf-8 -*-
"""Riprendere una cifratura interrotta.

La proprieta' da difendere e' una sola, e vale la pena scriverla per esteso:
**riprendere deve dare lo stesso risultato di una corsa mai interrotta**. Il
rischio, se si sbaglia, non e' un errore ma un dato corrotto in silenzio — le
righe gia' trattate cifrate due volte, o quelle nuove saltate.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, text                    # noqa: E402

from proteo import db                                         # noqa: E402
from proteo.checksum import cf_ok                             # noqa: E402
from proteo.motore import Motore                              # noqa: E402
from proteo.policy import Policy                              # noqa: E402
from proteo.registro import CIFRATA, IN_CORSO, Registro       # noqa: E402

CHIAVE = bytes(range(32))
CF = ["RSSMRA85H12F205Y", "BNCLGU78T04H501C", "VRDNNA90A41F839L",
      "MRTPLA65M15L219C"]


def _db(righe=40):
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, cf TEXT)"))
        for i in range(righe):
            c.execute(text("INSERT INTO t VALUES (:i,:v)"),
                      {"i": i, "v": CF[i % len(CF)]})
    return e


def _valori(engine):
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT cf FROM t ORDER BY id"))]


def _motore(engine, registro=None, lotto=10):
    p = Policy({"t": {"id": {"strategia": "mantieni"},
                      "cf": {"strategia": "cifra", "tipo": "CF"}}})
    return Motore(engine, p, CHIAVE, "k0",
                  registro or Registro(Path(tempfile.mkdtemp()) / "r"), "DB",
                  lotto_righe=lotto)


class Interrotta(unittest.TestCase):
    """Si ferma dopo due lotti, come un ssh che cade."""

    def setUp(self):
        self.engine = _db()
        self.reg = Registro(Path(tempfile.mkdtemp()) / "r")
        self.m = _motore(self.engine, self.reg)
        self._interrompi(dopo=2)

    def tearDown(self):
        self.engine.dispose()

    def _interrompi(self, dopo):
        vero = db.applica_mappa_a_lotti

        def a_meta(*a, **k):
            originale, contatore = k.get("su_lotto"), []

            def su_lotto(chiave, righe):
                originale(chiave, righe)
                contatore.append(chiave)
                if len(contatore) >= dopo:
                    raise KeyboardInterrupt      # la sessione cade qui
            k["su_lotto"] = su_lotto
            return vero(*a, **k)

        with mock.patch.object(db, "applica_mappa_a_lotti", a_meta):
            with self.assertRaises(KeyboardInterrupt):
                self.m.esegui("cifra")

    def test_meta_colonna_e_trattata(self):
        valori = _valori(self.engine)
        self.assertTrue(all(v not in CF for v in valori[:20]), "prima meta'")
        self.assertEqual(valori[20:], [CF[i % 4] for i in range(20, 40)])

    def test_il_registro_sa_dove_si_era_fermata(self):
        voce = self.reg.leggi("DB", "t", "cf")
        self.assertEqual(voce["stato"], IN_CORSO)
        self.assertEqual(voce["ultima_chiave"], "19")

    def test_e_la_colonna_risulta_riprendibile(self):
        voci = self.m.riprendibili("cifra")
        self.assertEqual([(v["tabella"], v["colonna"]) for v in voci], [("t", "cf")])

    def test_riprendere_da_lo_stesso_risultato_di_una_corsa_intera(self):
        """La proprieta' che rende utile tutto il resto."""
        intero = _db()
        _motore(intero).esegui("cifra")
        atteso = _valori(intero)
        intero.dispose()

        self.m.esegui("cifra", riprendi=self.reg.leggi("DB", "t", "cf"))
        self.assertEqual(_valori(self.engine), atteso)

    def test_dopo_la_ripresa_la_colonna_risulta_cifrata(self):
        self.m.esegui("cifra", riprendi=self.reg.leggi("DB", "t", "cf"))
        self.assertEqual(self.reg.stato("DB", "t", "cf"), CIFRATA)
        self.assertEqual(self.m.riprendibili("cifra"), [])

    def test_e_si_torna_indietro(self):
        self.m.esegui("cifra", riprendi=self.reg.leggi("DB", "t", "cf"))
        self.m.esegui("decifra")
        self.assertEqual(_valori(self.engine), [CF[i % 4] for i in range(40)])

    def test_i_valori_gia_trattati_non_si_ricifrano(self):
        """Il rischio vero: rileggere le righe gia' fatte significa cifrare il
        cifrato, e da li' non si torna indietro."""
        prima_meta = _valori(self.engine)[:20]
        self.m.esegui("cifra", riprendi=self.reg.leggi("DB", "t", "cf"))
        self.assertEqual(_valori(self.engine)[:20], prima_meta)

    def test_senza_riprendere_l_esecuzione_resta_bloccata(self):
        """Il cancello vale ancora: 'in_corso' non si scavalca per distrazione."""
        from proteo.motore import VerificaFallita
        with self.assertRaises(VerificaFallita) as e:
            self.m.esegui("cifra")
        self.assertIn("in_corso", str(e.exception))

    def test_i_surrogati_restano_validi_ovunque(self):
        self.m.esegui("cifra", riprendi=self.reg.leggi("DB", "t", "cf"))
        self.assertTrue(all(cf_ok(v) for v in _valori(self.engine)))


class NonRiprendibili(unittest.TestCase):
    def test_senza_lotti_non_c_e_niente_da_riprendere(self):
        """In transazione unica o e' passata tutta o e' tornata indietro: non
        esiste un 'a meta'' da cui ripartire."""
        engine = _db()
        reg = Registro(Path(tempfile.mkdtemp()) / "r")
        reg.avvia("DB", "t", "cf", "CF", "cf", "k0", "cifra")
        self.assertEqual(_motore(engine, reg).riprendibili("cifra"), [])
        engine.dispose()

    def test_una_decifratura_non_si_riprende_come_cifratura(self):
        engine = _db()
        reg = Registro(Path(tempfile.mkdtemp()) / "r")
        reg.avvia("DB", "t", "cf", "CF", "cf", "k0", "decifra")
        reg.avanzamento("DB", "t", "cf", ultima_chiave="19")
        m = _motore(engine, reg)
        self.assertEqual(m.riprendibili("cifra"), [])
        self.assertEqual(len(m.riprendibili("decifra")), 1)
        engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)

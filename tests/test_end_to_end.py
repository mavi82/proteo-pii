# -*- coding: utf-8 -*-
"""Il flusso completo su un database vero (SQLite): cifra, verifica, decifra.

SQLite perche' e' nella libreria standard: il percorso intero — introspezione,
policy, registro, UPDATE, ritorno — si prova senza un server acceso. Cio' che
resta specifico di SQL Server (scrittura massiva, clone) sta fuori da qui.

Tutti i valori sono INVENTATI e costruiti perche' i checksum tornino.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, text                       # noqa: E402

from proteo import db                                            # noqa: E402
from proteo.checksum import cf_ok                                # noqa: E402
from proteo.motore import Motore, VerificaFallita                # noqa: E402
from proteo.policy import Policy                                 # noqa: E402
from proteo.registro import (AZZERATA, CIFRATA, IN_CHIARO,       # noqa: E402
                             Registro, StatoIncoerente)

CF = ["RSSMRA85H12F205Y", "BNCLGU78T04H501C", "VRDNNA90A41F839L", "MRTPLA65M15L219C"]
CHIAVE = bytes(range(32))
KID = "abcdef0123456789"


def _db():
    """clienti(id, codice_fiscale, citta) + contratti che la referenzia."""
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE clienti ("
                       "id INTEGER PRIMARY KEY, codice_fiscale TEXT, citta TEXT)"))
        c.execute(text("CREATE TABLE contratti ("
                       "id INTEGER PRIMARY KEY, cf_intestatario TEXT NOT NULL, "
                       "importo TEXT NOT NULL, "
                       "FOREIGN KEY (cf_intestatario) REFERENCES clienti(codice_fiscale))"))
        for i, cf in enumerate(CF):
            c.execute(text("INSERT INTO clienti VALUES (:i, :cf, :ci)"),
                      {"i": i, "cf": cf, "ci": "Milano"})
        # il primo cliente ha due contratti: verifica che le righe ripetute
        # ricevano tutte lo stesso surrogato
        for i, cf in enumerate(CF + [CF[0]]):
            c.execute(text("INSERT INTO contratti VALUES (:i, :cf, '100')"),
                      {"i": i, "cf": cf})
    return e


def _policy():
    return Policy({
        "clienti": {
            "id": {"strategia": "mantieni"},
            "codice_fiscale": {"strategia": "cifra", "tipo": "CF"},
            "citta": {"strategia": "mantieni"},
        },
        "contratti": {
            "id": {"strategia": "mantieni"},
            "cf_intestatario": {"strategia": "cifra", "tipo": "CF",
                                "tweak": "codice_fiscale"},
            "importo": {"strategia": "mantieni"},
        },
    })


def _valori(engine, tabella, colonna):
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT %s FROM %s ORDER BY id"
                                             % (colonna, tabella)))]


class Base(unittest.TestCase):
    def setUp(self):
        self.engine = _db()
        self.reg = Registro(Path(tempfile.mkdtemp()) / "registro")
        self.m = Motore(self.engine, _policy(), CHIAVE, KID, self.reg, "ProvaDB")

    def tearDown(self):
        self.engine.dispose()


class Introspezione(Base):
    def test_tabelle_e_colonne(self):
        s = db.introspeziona(self.engine, ["clienti", "contratti"])
        self.assertEqual(sorted(s["tabelle"]), ["clienti", "contratti"])
        self.assertIn("codice_fiscale", s["tabelle"]["clienti"])
        self.assertFalse(s["tabelle"]["contratti"]["cf_intestatario"]["nullable"])

    def test_foreign_key_lette(self):
        s = db.introspeziona(self.engine, ["clienti", "contratti"])
        self.assertIn((("contratti", "cf_intestatario"), ("clienti", "codice_fiscale")),
                      s["foreign_key"])

    def test_conteggi(self):
        righe, distinti = db.conta(self.engine, "contratti", "cf_intestatario")
        self.assertEqual((righe, distinti), (5, 4))     # 5 righe, 4 CF distinti


class CicloCompleto(Base):
    def test_cifra_verifica_decifra(self):
        originali = _valori(self.engine, "clienti", "codice_fiscale")

        self.assertEqual(self.m.errori("cifra"), [])
        rapporto = self.m.esegui("cifra")

        cifrati = _valori(self.engine, "clienti", "codice_fiscale")
        self.assertNotEqual(cifrati, originali)
        for v in cifrati:
            self.assertTrue(cf_ok(v), "%s non e' un CF valido" % v)
        self.assertEqual(sum(c["righe_aggiornate"] for c in rapporto["colonne"]), 9)

        # il registro sa cosa e' successo
        self.assertEqual(self.reg.stato("ProvaDB", "clienti", "codice_fiscale"), CIFRATA)

        # ... e si torna indietro
        self.m.esegui("decifra")
        self.assertEqual(_valori(self.engine, "clienti", "codice_fiscale"), originali)
        self.assertEqual(self.reg.stato("ProvaDB", "clienti", "codice_fiscale"), IN_CHIARO)

    def test_il_join_regge(self):
        """Il punto di tutto: dopo la cifratura le due tabelle restano collegate."""
        self.m.esegui("cifra")
        with self.engine.connect() as c:
            n = c.execute(text(
                "SELECT COUNT(*) FROM contratti k "
                "JOIN clienti c ON c.codice_fiscale = k.cf_intestatario")).scalar_one()
        self.assertEqual(n, 5, "il JOIN si e' rotto: tweak divergenti")

    def test_valori_ripetuti_stesso_surrogato(self):
        self.m.esegui("cifra")
        v = _valori(self.engine, "contratti", "cf_intestatario")
        self.assertEqual(v[0], v[4], "la stessa persona deve avere lo stesso surrogato")

    def test_colonne_non_dichiarate_intatte(self):
        prima = _valori(self.engine, "clienti", "citta")
        self.m.esegui("cifra")
        self.assertEqual(_valori(self.engine, "clienti", "citta"), prima)

    def test_nessuna_tabella_di_appoggio_sopravvive(self):
        """La mappa vecchio->nuovo e' l'unica cosa che somigli a un dizionario."""
        self.m.esegui("cifra")
        with self.engine.connect() as c:
            resti = [r[0] for r in c.execute(text(
                "SELECT name FROM sqlite_master WHERE name LIKE '_proteo_map%'"))]
        self.assertEqual(resti, [])


class Cancelli(Base):
    def test_doppia_cifratura_bloccata(self):
        self.m.esegui("cifra")
        with self.assertRaises(VerificaFallita) as e:
            self.m.esegui("cifra")
        self.assertIn("gia' cifrata", str(e.exception))

    def test_chiave_sbagliata_in_decifratura_bloccata(self):
        self.m.esegui("cifra")
        altro = Motore(self.engine, _policy(), bytes(range(1, 33)), "ffffffffffffffff",
                       self.reg, "ProvaDB")
        with self.assertRaises(VerificaFallita) as e:
            altro.esegui("decifra")
        self.assertIn("irrecuperabili", str(e.exception))

    def test_colonna_non_dichiarata_blocca_tutto(self):
        p = _policy()
        del p.tabelle["clienti"]["citta"]
        m = Motore(self.engine, p, CHIAVE, KID, self.reg, "ProvaDB")
        with self.assertRaises(VerificaFallita):
            m.esegui("cifra")
        # e non ha scritto nulla
        self.assertEqual(_valori(self.engine, "clienti", "codice_fiscale")[0], CF[0])

    def test_foreign_key_con_tweak_divergenti_blocca(self):
        p = _policy()
        p.tabelle["contratti"]["cf_intestatario"] = {"strategia": "cifra", "tipo": "CF"}
        m = Motore(self.engine, p, CHIAVE, KID, self.reg, "ProvaDB")
        with self.assertRaises(VerificaFallita) as e:
            m.esegui("cifra")
        self.assertIn("JOIN", str(e.exception))


class ValoriSporchi(unittest.TestCase):
    def setUp(self):
        self.engine = _db()
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO clienti VALUES (99, 'NON-UN-CF', 'Roma')"))
        self.reg = Registro(Path(tempfile.mkdtemp()) / "registro")
        self.m = Motore(self.engine, _policy(), CHIAVE, KID, self.reg, "ProvaDB")

    def tearDown(self):
        self.engine.dispose()

    def test_ferma_e_il_predefinito(self):
        from proteo.surrogati import ValoreNonTrattabile
        with self.assertRaises(ValoreNonTrattabile):
            self.m.esegui("cifra")

    def test_salta_lascia_il_valore_in_chiaro_e_lo_riporta(self):
        r = self.m.esegui("cifra", su_valore_non_trattabile="salta")
        scarti = r["colonne"][0]["non_trattabili"]
        self.assertEqual([v for v, _ in scarti], ["NON-UN-CF"])
        self.assertIn("NON-UN-CF", _valori(self.engine, "clienti", "codice_fiscale"))


class Azzeramento(Base):
    """`azzera` e' l'unica operazione che distrugge invece di trasformare."""

    def setUp(self):
        super().setUp()
        p = _policy()
        p.tabelle["clienti"]["citta"] = {"strategia": "azzera"}
        self.m = Motore(self.engine, p, CHIAVE, KID, self.reg, "ProvaDB")

    def test_la_colonna_viene_svuotata(self):
        r = self.m.esegui("cifra")
        self.assertEqual(_valori(self.engine, "clienti", "citta"), [None] * 4)
        voce = [c for c in r["colonne"] if c["colonna"] == "citta"][0]
        self.assertEqual((voce["operazione"], voce["righe_aggiornate"]), ("azzera", 4))

    def test_il_registro_la_distingue_da_una_cifrata(self):
        self.m.esegui("cifra")
        self.assertEqual(self.reg.stato("ProvaDB", "clienti", "citta"), AZZERATA)
        voce = self.reg.leggi("ProvaDB", "clienti", "citta")
        # nessuna chiave associata: suggerirebbe un recupero che non esiste
        self.assertIsNone(voce["chiave_id"])

    def test_decifra_non_riazzera_e_avvisa(self):
        self.m.esegui("cifra")
        avvisi = [p for p in self.m.verifica("decifra") if p.livello == "avviso"]
        self.assertTrue(any("backup" in p.messaggio for p in avvisi))
        # la decifratura delle altre colonne non deve essere bloccata da questa
        self.assertEqual(self.m.errori("decifra"), [])
        self.m.esegui("decifra")
        self.assertEqual(_valori(self.engine, "clienti", "codice_fiscale"), CF)

    def test_decifrarla_come_fosse_cifrata_e_bloccato(self):
        self.m.esegui("cifra")
        with self.assertRaises(StatoIncoerente) as e:
            self.reg.verifica_prima_di_decifrare("ProvaDB", "clienti", "citta", KID)
        self.assertIn("nessuna chiave", str(e.exception))

    def test_anteprima_mostra_cosa_sparisce_senza_scrivere(self):
        voci = [c for c in self.m.anteprima() if c["operazione"] == "azzera"]
        self.assertEqual(len(voci), 1)
        self.assertEqual(voci[0]["campione"], [("Milano", None)])
        self.assertEqual(_valori(self.engine, "clienti", "citta"), ["Milano"] * 4)

    def test_not_null_bloccato_prima_di_scrivere(self):
        p = _policy()
        p.tabelle["contratti"]["importo"] = {"strategia": "azzera"}   # NOT NULL
        m = Motore(self.engine, p, CHIAVE, KID, self.reg, "ProvaDB")
        with self.assertRaises(VerificaFallita):
            m.esegui("cifra")
        self.assertEqual(_valori(self.engine, "clienti", "codice_fiscale"), CF)


class Anteprima(Base):
    def test_non_scrive_nulla(self):
        prima = _valori(self.engine, "clienti", "codice_fiscale")
        a = self.m.anteprima()
        self.assertEqual(_valori(self.engine, "clienti", "codice_fiscale"), prima)
        self.assertEqual(len(a), 2)
        self.assertTrue(all(cf_ok(n) for _, n in a[0]["campione"]))
        self.assertEqual(a[0]["distinti"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)

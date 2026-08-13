# -*- coding: utf-8 -*-
"""L'anteprima per righe: com'era il record, cosa diventa.

L'altra anteprima guarda i valori distinti, che e' la prospettiva del motore.
Questa guarda i record, che e' quella di chi conosce i dati — ed e' l'unica che
fa vedere di aver puntato la colonna sbagliata.
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, text                    # noqa: E402

from proteo import stampa                                     # noqa: E402
from proteo.motore import Motore                              # noqa: E402
from proteo.policy import Policy                              # noqa: E402
from proteo.registro import Registro                          # noqa: E402

CHIAVE = bytes(range(32))
CF = ["RSSMRA85H12F205Y", "BNCLGU78T04H501C", "VRDNNA90A41F839L"]


def _db():
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text("CREATE TABLE clienti (id INTEGER PRIMARY KEY, "
                       "codice_fiscale TEXT, nome TEXT, citta TEXT, note TEXT)"))
        for i, cf in enumerate(CF):
            c.execute(text("INSERT INTO clienti VALUES (:i,:cf,:n,'Milano','x')"),
                      {"i": i, "cf": cf, "n": ["Mario", "Anna", "Ludmila"][i]})
        c.execute(text("INSERT INTO clienti VALUES (9, NULL, NULL, 'Roma', NULL)"))
    return e


def _motore(engine):
    p = Policy({"clienti": {
        "id": {"strategia": "mantieni"},
        "codice_fiscale": {"strategia": "cifra", "tipo": "CF"},
        "nome": {"strategia": "cifra", "tipo": "NOME"},
        "citta": {"strategia": "mantieni"},
        "note": {"strategia": "azzera"},
    }})
    return Motore(engine, p, CHIAVE, "k0",
                  Registro(Path(tempfile.mkdtemp()) / "r"), "ProvaDB")


class Contenuto(unittest.TestCase):
    def setUp(self):
        self.engine = _db()
        self.m = _motore(self.engine)
        self.voci = self.m.anteprima_righe(30)

    def tearDown(self):
        self.engine.dispose()

    def test_una_voce_per_tabella(self):
        self.assertEqual([v["tabella"] for v in self.voci], ["clienti"])

    def test_mostra_la_chiave_primaria(self):
        """Senza, un valore isolato non si ritrova nel database vero."""
        self.assertEqual(self.voci[0]["chiave"], ["id"])
        self.assertEqual(self.voci[0]["righe"][0]["chiave"], {"id": 0})

    def test_solo_le_colonne_trattate(self):
        self.assertEqual(sorted(self.voci[0]["colonne"]),
                         ["codice_fiscale", "nome", "note"])

    def test_prima_e_dopo(self):
        prima, dopo, errore = self.voci[0]["righe"][0]["celle"]["codice_fiscale"]
        self.assertEqual(prima, CF[0])
        self.assertNotEqual(dopo, CF[0])
        self.assertIsNone(errore)

    def test_azzera_mostra_il_valore_che_sparisce(self):
        prima, dopo, _ = self.voci[0]["righe"][0]["celle"]["note"]
        self.assertEqual((prima, dopo), ("x", None))

    def test_un_valore_non_trattabile_e_segnalato_non_nascosto(self):
        _, dopo, errore = self.voci[0]["righe"][2]["celle"]["nome"]
        self.assertIsNone(dopo)
        self.assertIn("non e' fra le", errore)

    def test_i_nulli_restano_nulli(self):
        self.assertEqual(self.voci[0]["righe"][3]["celle"]["nome"], (None, None, None))

    def test_non_scrive_niente(self):
        with self.engine.connect() as c:
            valori = [r[0] for r in c.execute(text(
                "SELECT codice_fiscale FROM clienti ORDER BY id"))]
        self.assertEqual(valori[:3], CF)

    def test_quante_righe_si_chiede(self):
        self.assertEqual(len(self.m.anteprima_righe(2)[0]["righe"]), 2)

    def test_in_decifratura_l_azzeramento_non_compare(self):
        """Non c'e' niente da riportare in chiaro, e mostrarlo illuderebbe."""
        voci = self.m.anteprima_righe(30, verso="decifra")
        self.assertNotIn("note", voci[0]["colonne"])

    def test_ristretta_a_una_colonna(self):
        voci = self.m.anteprima_righe(30, solo=[("clienti", "nome")])
        self.assertEqual(voci[0]["colonne"], ["nome"])


class Resa(unittest.TestCase):
    def setUp(self):
        self.engine = _db()
        self.voci = _motore(self.engine).anteprima_righe(30)
        out = io.StringIO()
        with redirect_stdout(out):
            stampa.anteprima_righe(self.voci)
        self.testo = out.getvalue()

    def tearDown(self):
        self.engine.dispose()

    def test_nessuna_riga_supera_la_larghezza(self):
        """Una tabella piu' larga del terminale viene spezzata dal terminale, e
        a quel punto non si capisce piu' quale valore sia di quale colonna."""
        # Solo le righe della tabella: i motivi degli scarti sono prose, e
        # vanno a capo da soli senza confondere nessuna colonna.
        for riga in self.testo.splitlines():
            if " | " in riga:
                self.assertLessEqual(len(riga), stampa.LARGHEZZA_RIGA + 10, riga)

    def test_la_chiave_si_ripete_in_ogni_blocco(self):
        blocchi = [b for b in self.testo.split("\n\n") if " | " in b]
        self.assertGreaterEqual(len(blocchi), 1)
        for blocco in blocchi:
            testata = [r for r in blocco.splitlines() if " | " in r][0]
            self.assertIn("id", testata)

    def test_il_motivo_di_uno_scarto_si_legge_per_esteso(self):
        self.assertIn("aggiungilo alla lista", self.testo)

    def test_i_valori_lunghi_si_tagliano(self):
        lunga = [{"tabella": "t", "chiave": [], "colonne": ["c"],
                  "righe": [{"chiave": {}, "celle": {"c": ("x" * 200, "y" * 200,
                                                           None)}}]}]
        out = io.StringIO()
        with redirect_stdout(out):
            stampa.anteprima_righe(lunga)
        self.assertIn("…", out.getvalue())

    def test_tabella_vuota(self):
        out = io.StringIO()
        with redirect_stdout(out):
            stampa.anteprima_righe([{"tabella": "t", "chiave": [], "colonne": [],
                                     "righe": []}])
        self.assertIn("nessuna riga", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)

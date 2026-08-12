# -*- coding: utf-8 -*-
"""Riconoscere una colonna dai valori, non dal nome.

Tutti i valori sono INVENTATI e costruiti perche' i checksum tornino.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo import rilevamento                                # noqa: E402
from proteo.policy import Policy                              # noqa: E402

CF = ["RSSMRA85H12F205Y", "BNCLGU78T04H501C", "VRDNNA90A41F839L",
      "MRTPLA65M15L219C"]
PIVA = ["00743110157", "17497033260", "00159560366", "01234567897"]
IBAN = ["IT60X0542811101000000123456", "IT12X1328544065150486661544"]


class Analisi(unittest.TestCase):
    def test_codici_fiscali(self):
        self.assertEqual(rilevamento.analizza(CF)[0], "CF")

    def test_partite_iva(self):
        self.assertEqual(rilevamento.analizza(PIVA)[0], "PIVA")

    def test_citta_non_sono_niente(self):
        tipo, _, _ = rilevamento.analizza(["Milano", "Roma", "Napoli", "Torino"])
        self.assertIsNone(tipo)

    def test_il_nome_della_colonna_non_conta(self):
        """E' il punto: `campo7` piena di CF resta una colonna di CF."""
        self.assertEqual(rilevamento.analizza(CF)[0], "CF")

    def test_qualche_riga_sporca_non_annulla_la_proposta(self):
        tipo, quanti, esaminati = rilevamento.analizza(CF * 3 + ["NON-UN-CF"])
        self.assertEqual((tipo, quanti, esaminati), ("CF", 12, 13))

    def test_troppa_sporcizia_e_nessuna_proposta(self):
        """Meta' e meta' non e' una colonna di codici fiscali."""
        self.assertIsNone(rilevamento.analizza(CF + ["a", "b", "c", "d"])[0])

    def test_pochi_valori_non_bastano(self):
        """Su due valori un checksum azzeccato per caso e' verosimile."""
        self.assertIsNone(rilevamento.analizza(CF[:2])[0])

    def test_i_nulli_non_contano(self):
        tipo, quanti, esaminati = rilevamento.analizza(CF + [None, None, "  "])
        self.assertEqual((tipo, quanti, esaminati), ("CF", 4, 4))

    def test_colonna_vuota(self):
        self.assertEqual(rilevamento.analizza([None, None])[0], None)


class Proposte(unittest.TestCase):
    def setUp(self):
        self.schema = {"tabelle": {"clienti": {
            "id": {"tipo": "INTEGER", "nullable": False},
            "codice_fiscale": {"tipo": "VARCHAR(16)", "nullable": True},
            "citta": {"tipo": "VARCHAR(50)", "nullable": True},
            "piva_testo": {"tipo": "VARCHAR(11)", "nullable": True},
            "piva_numero": {"tipo": "BIGINT", "nullable": True},
        }}}
        self.valori = {"codice_fiscale": CF, "citta": ["Milano"] * 4,
                       "piva_testo": PIVA, "piva_numero": PIVA, "id": [1, 2, 3, 4]}

    def _campiona(self, tabella, colonna):
        return self.valori[colonna]

    def test_propone_solo_cio_che_riconosce(self):
        p = rilevamento.proponi(self._campiona, self.schema)
        self.assertEqual(sorted(p["clienti"]), ["codice_fiscale", "piva_testo"])
        self.assertEqual(p["clienti"]["codice_fiscale"][0], "CF")

    def test_le_colonne_numeriche_si_saltano(self):
        """Un surrogato con zeri iniziali in una colonna numerica non torna."""
        p = rilevamento.proponi(self._campiona, self.schema)
        self.assertNotIn("piva_numero", p["clienti"])


class Aggiornamento(unittest.TestCase):
    """La policy cresce senza perdere le decisioni prese a mano."""

    def setUp(self):
        self.policy = Policy({"clienti": {
            "id": {"strategia": "mantieni"},
            "codice_fiscale": {"strategia": "cifra", "tipo": "CF",
                               "tweak": "condiviso"},
        }})
        self.schema = {"tabelle": {"clienti": {
            "id": {"tipo": "INTEGER", "nullable": False},
            "codice_fiscale": {"tipo": "VARCHAR(16)", "nullable": True},
            "pec": {"tipo": "VARCHAR(120)", "nullable": True},
        }}}

    def test_le_colonne_nuove_nascono_mantieni(self):
        aggiunte, tolte, fuori = self.policy.aggiorna(self.schema)
        self.assertEqual(aggiunte, [("clienti.pec", "mantieni")])
        self.assertEqual((tolte, fuori), ([], []))

    def test_le_scelte_gia_fatte_non_si_toccano(self):
        self.policy.aggiorna(self.schema)
        r = self.policy.regola("clienti", "codice_fiscale")
        self.assertEqual(r, {"strategia": "cifra", "tipo": "CF",
                             "tweak": "condiviso"})

    def test_una_proposta_diventa_cifra(self):
        proposte = {"clienti": {"pec": ("CF", 10, 10)}}
        aggiunte, _, _ = self.policy.aggiorna(self.schema, proposte)
        self.assertEqual(aggiunte, [("clienti.pec", "cifra")])
        self.assertEqual(self.policy.regola("clienti", "pec"),
                         {"strategia": "cifra", "tipo": "CF"})

    def test_le_colonne_sparite_vengono_tolte(self):
        del self.schema["tabelle"]["clienti"]["id"]
        _, tolte, _ = self.policy.aggiorna(self.schema)
        self.assertEqual(tolte, ["clienti.id"])
        self.assertIsNone(self.policy.regola("clienti", "id"))

    def test_tabella_della_policy_non_piu_nel_database(self):
        self.policy.tabelle["ordini"] = {"id": {"strategia": "mantieni"}}
        _, _, fuori = self.policy.aggiorna(self.schema)
        self.assertEqual(fuori, ["ordini"])

    def test_seconda_esecuzione_non_cambia_niente(self):
        self.policy.aggiorna(self.schema)
        aggiunte, tolte, fuori = self.policy.aggiorna(self.schema)
        self.assertEqual((aggiunte, tolte, fuori), ([], [], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)

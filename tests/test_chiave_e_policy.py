# -*- coding: utf-8 -*-
"""Custodia della chiave e verifica fail-closed della policy."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo import keyfile                                      # noqa: E402
from proteo.policy import Policy, PolicyNonValida               # noqa: E402


class Chiave(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.p = self.dir / "prova.key"

    def test_genera_e_ricarica(self):
        k, kid = keyfile.genera(self.p)
        self.assertEqual(len(k), 32)
        k2, kid2 = keyfile.carica(self.p)
        self.assertEqual((k, kid), (k2, kid2))

    def test_non_sovrascrive_mai(self):
        """Rigenerare sopra una chiave esistente distruggerebbe i dati cifrati."""
        keyfile.genera(self.p)
        with self.assertRaises(keyfile.ChiaveEsistente):
            keyfile.genera(self.p)

    def test_id_non_rivela_la_chiave(self):
        k, kid = keyfile.genera(self.p)
        self.assertEqual(len(kid), 16)
        self.assertNotIn(kid.encode(), k)
        # chiavi diverse -> id diversi
        altri = {keyfile.chiave_id(bytes([i]) * 32) for i in range(50)}
        self.assertEqual(len(altri), 50)

    def test_id_manomesso_viene_scoperto(self):
        """Un id falsificato aggirerebbe il controllo 'chiave giusta?' in decifratura."""
        keyfile.genera(self.p)
        d = json.loads(self.p.read_text(encoding="utf-8"))
        d["id"] = "0" * 16
        self.p.write_text(json.dumps(d), encoding="utf-8")
        with self.assertRaises(keyfile.ChiaveNonValida):
            keyfile.carica(self.p)

    def test_file_illeggibile(self):
        self.p.write_text("non sono json", encoding="utf-8")
        with self.assertRaises(keyfile.ChiaveNonValida):
            keyfile.carica(self.p)


SCHEMA = {
    "tabelle": {
        "dbo.clienti": {
            "id": {"tipo": "int", "nullable": False},
            "codice_fiscale": {"tipo": "char(16)", "nullable": True},
            "citta": {"tipo": "varchar(60)", "nullable": True},
        },
        "dbo.contratti": {
            "id": {"tipo": "int", "nullable": False},
            "cf_intestatario": {"tipo": "char(16)", "nullable": False},
            "importo": {"tipo": "decimal", "nullable": False},
        },
    },
    "foreign_key": [
        (("dbo.contratti", "cf_intestatario"), ("dbo.clienti", "codice_fiscale")),
    ],
}


def _policy(**override):
    t = {
        "dbo.clienti": {
            "id": {"strategia": "mantieni"},
            "codice_fiscale": {"strategia": "cifra", "tipo": "CF"},
            "citta": {"strategia": "mantieni"},
        },
        "dbo.contratti": {
            "id": {"strategia": "mantieni"},
            # nome diverso -> tweak diverso: si dichiara esplicitamente
            "cf_intestatario": {"strategia": "cifra", "tipo": "CF",
                                "tweak": "codice_fiscale"},
            "importo": {"strategia": "mantieni"},
        },
    }
    for k, v in override.items():
        tab, col = k.split("__")
        t[tab.replace("_", ".", 1)][col] = v
    return Policy(t)


class VerificaPolicy(unittest.TestCase):
    def test_policy_corretta_non_ha_errori(self):
        self.assertEqual(_policy().errori(SCHEMA), [])

    def test_colonna_non_dichiarata_blocca(self):
        """Il caso vero: una migration aggiunge una colonna e nessuno se ne accorge."""
        p = _policy()
        del p.tabelle["dbo.clienti"]["citta"]
        errori = p.errori(SCHEMA)
        self.assertEqual(len(errori), 1)
        self.assertIn("dbo.clienti.citta", errori[0].dove)
        self.assertIn("non dichiarata", errori[0].messaggio)

    def test_colonna_inesistente_blocca(self):
        p = _policy()
        p.tabelle["dbo.clienti"]["pec"] = {"strategia": "cifra", "tipo": "CF"}
        self.assertTrue(any("pec" in e.dove for e in p.errori(SCHEMA)))

    def test_tabella_inesistente_blocca(self):
        p = _policy()
        p.tabelle["dbo.fantasma"] = {"x": {"strategia": "mantieni"}}
        self.assertTrue(any("fantasma" in e.dove for e in p.errori(SCHEMA)))

    def test_tipo_mancante_blocca(self):
        p = _policy(dbo_clienti__codice_fiscale={"strategia": "cifra"})
        self.assertTrue(any("tipo valido" in e.messaggio for e in p.errori(SCHEMA)))

    def test_strategia_sconosciuta_blocca(self):
        p = _policy(dbo_clienti__citta={"strategia": "offusca"})
        self.assertTrue(any("sconosciuta" in e.messaggio for e in p.errori(SCHEMA)))

    def test_azzera_su_not_null_blocca(self):
        p = _policy(dbo_contratti__importo={"strategia": "azzera"})
        self.assertTrue(any("NOT NULL" in e.messaggio for e in p.errori(SCHEMA)))

    def test_foreign_key_con_tweak_diversi_blocca(self):
        """Senza il tweak esplicito i due lati divergono e il JOIN muore in silenzio."""
        p = _policy(dbo_contratti__cf_intestatario={"strategia": "cifra", "tipo": "CF"})
        errori = p.errori(SCHEMA)
        self.assertTrue(any("JOIN" in e.messaggio for e in errori),
                        [e.messaggio for e in errori])

    def test_foreign_key_con_strategie_diverse_blocca(self):
        p = _policy(dbo_contratti__cf_intestatario={"strategia": "mantieni"})
        self.assertTrue(any("strategie diverse" in e.messaggio for e in p.errori(SCHEMA)))

    def test_tweak_condiviso_fra_tipi_diversi_e_solo_un_avviso(self):
        p = _policy(dbo_contratti__cf_intestatario={"strategia": "cifra", "tipo": "PIVA",
                                                    "tweak": "codice_fiscale"})
        problemi = p.verifica(SCHEMA)
        self.assertTrue(any(x.livello == "avviso" for x in problemi))


class Persistenza(unittest.TestCase):
    def test_salva_e_ricarica(self):
        d = Path(tempfile.mkdtemp()) / "policy.json"
        _policy().salva(d)
        ricaricata = Policy.carica(d)
        self.assertEqual(ricaricata.errori(SCHEMA), [])
        self.assertEqual(ricaricata.tweak("dbo.contratti", "cf_intestatario"),
                         b"codice_fiscale")

    def test_formato_sconosciuto(self):
        d = Path(tempfile.mkdtemp()) / "policy.json"
        d.write_text('{"formato": "altro"}', encoding="utf-8")
        with self.assertRaises(PolicyNonValida):
            Policy.carica(d)

    def test_tweak_predefinito_e_il_nome_colonna(self):
        self.assertEqual(_policy().tweak("dbo.clienti", "codice_fiscale"),
                         b"codice_fiscale")


if __name__ == "__main__":
    unittest.main(verbosity=2)

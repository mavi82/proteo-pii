# -*- coding: utf-8 -*-
"""Il file di configurazione, e le condizioni a cui puo' contenere una password."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo import config as cfg                              # noqa: E402

SENZA_PASSWORD = {
    "formato": cfg.FORMATO,
    "predefinito": "vendite",
    "database": {
        "vendite": {"url": "postgresql+psycopg://utente@host:5432/vendite",
                    "chiave": "vendite.key", "policy": "policy.json",
                    "registro": "registro", "etichetta": "VenditeDB"},
        "prova": {"url": "sqlite:///prova.db"},
    },
}


def _scrivi(cartella, dati, permessi=0o600):
    p = Path(cartella) / cfg.NOME_PREDEFINITO
    p.write_text(json.dumps(dati), encoding="utf-8")
    os.chmod(p, permessi)
    return p


class Lettura(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_carica_e_sceglie_il_predefinito(self):
        c = cfg.carica(_scrivi(self.d, SENZA_PASSWORD))
        self.assertEqual(c.nomi(), ["prova", "vendite"])
        self.assertEqual(c.predefinito(), "vendite")
        self.assertEqual(c.voce("vendite")["etichetta"], "VenditeDB")

    def test_un_solo_database_e_sempre_il_predefinito(self):
        dati = {"formato": cfg.FORMATO, "database": {"solo": {"url": "sqlite://"}}}
        self.assertEqual(cfg.carica(_scrivi(self.d, dati)).predefinito(), "solo")

    def test_piu_database_senza_predefinito_e_ambiguo(self):
        dati = dict(SENZA_PASSWORD)
        dati.pop("predefinito")
        self.assertIsNone(cfg.carica(_scrivi(self.d, dati)).predefinito())

    def test_formato_sconosciuto(self):
        with self.assertRaises(cfg.ConfigNonValida):
            cfg.carica(_scrivi(self.d, {"formato": "altro", "database": {}}))

    def test_database_inesistente(self):
        c = cfg.carica(_scrivi(self.d, SENZA_PASSWORD))
        with self.assertRaises(cfg.ConfigNonValida) as e:
            c.voce("acquisti")
        self.assertIn("prova, vendite", str(e.exception))


class Percorsi(unittest.TestCase):
    """I percorsi relativi guardano al config, non alla directory corrente.

    E' la differenza fra un registro che sta sempre nello stesso posto e un
    registro che cambia con la cartella da cui lanci — cioe' un registro perso.
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.c = cfg.carica(_scrivi(self.d, SENZA_PASSWORD))

    def test_relativo_al_config(self):
        atteso = (Path(self.d) / "registro").resolve()
        self.assertEqual(self.c.risolvi(self.c.voce("vendite"), "registro"), atteso)

    def test_assoluto_resta_com_e(self):
        voce = dict(self.c.voce("vendite"), registro="/var/proteo/registro")
        self.assertEqual(str(self.c.risolvi(voce, "registro")), "/var/proteo/registro")

    def test_campo_assente(self):
        self.assertIsNone(self.c.risolvi(self.c.voce("prova"), "chiave"))


class PasswordNelConfig(unittest.TestCase):
    """Ammessa, ma solo se il file e' davvero protetto."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.dati = {"formato": cfg.FORMATO,
                     "database": {"v": {"url": "postgresql://utente@host/v",
                                        "password": "segreto"}}}

    @unittest.skipIf(os.name == "nt", "i bit POSIX su Windows non descrivono nulla")
    def test_rifiutata_se_il_file_e_leggibile_da_altri(self):
        p = _scrivi(self.d, self.dati, permessi=0o644)
        with self.assertRaises(cfg.ConfigNonValida) as e:
            cfg.carica(p)
        self.assertIn("chmod 600", str(e.exception))

    def test_accettata_se_il_file_e_stretto(self):
        c = cfg.carica(_scrivi(self.d, self.dati, permessi=0o600))
        self.assertEqual(c.voce("v")["password"], "segreto")

    @unittest.skipIf(os.name == "nt", "i bit POSIX su Windows non descrivono nulla")
    def test_riconosciuta_anche_dentro_l_url(self):
        """La password si scrive quasi sempre nell'URL, non in un campo a parte."""
        dati = {"formato": cfg.FORMATO,
                "database": {"v": {"url": "postgresql://utente:segreto@host/v"}}}
        with self.assertRaises(cfg.ConfigNonValida):
            cfg.carica(_scrivi(self.d, dati, permessi=0o644))

    @unittest.skipIf(os.name == "nt", "i bit POSIX su Windows non descrivono nulla")
    def test_senza_password_nessun_vincolo(self):
        """Non c'e' niente da proteggere: i permessi larghi non sono un problema."""
        cfg.carica(_scrivi(self.d, SENZA_PASSWORD, permessi=0o644))


def _git(cartella, *argomenti):
    return subprocess.run(("git",) + argomenti, cwd=str(cartella),
                          capture_output=True, text=True)


def _repo():
    """Un repository git vero: le regole di esclusione le deve valutare git."""
    d = Path(tempfile.mkdtemp())
    if _git(d, "init", "-q").returncode != 0:
        raise unittest.SkipTest("git non disponibile")
    return d


class ConfigDentroUnRepo(unittest.TestCase):
    """Puo' starci — anche in una sottocartella — purche' sia escluso dai commit."""

    def setUp(self):
        self.repo = _repo()
        self.dati = {"formato": cfg.FORMATO,
                     "database": {"v": {"url": "postgresql://utente@host/v",
                                        "password": "segreto"}}}

    def test_rifiutato_se_verrebbe_committato(self):
        p = _scrivi(self.repo, self.dati)
        with self.assertRaises(cfg.ConfigNonValida) as e:
            cfg.carica(p)
        self.assertIn("gitignore", str(e.exception))

    def test_accettato_se_escluso(self):
        p = _scrivi(self.repo, self.dati)
        (self.repo / ".gitignore").write_text(cfg.NOME_PREDEFINITO + "\n")
        self.assertEqual(cfg.carica(p).voce("v")["password"], "segreto")

    def test_accettato_in_sottocartella_esclusa(self):
        sotto = self.repo / "distribuzione" / "locale"
        sotto.mkdir(parents=True)
        p = _scrivi(sotto, self.dati)
        (self.repo / ".gitignore").write_text("distribuzione/locale/\n")
        self.assertEqual(cfg.carica(p).voce("v")["password"], "segreto")

    def test_un_file_gia_tracciato_non_e_escluso(self):
        """Il .gitignore non toglie dai commit cio' che git segue gia'."""
        p = _scrivi(self.repo, self.dati)
        _git(self.repo, "add", "-f", cfg.NOME_PREDEFINITO)
        (self.repo / ".gitignore").write_text(cfg.NOME_PREDEFINITO + "\n")
        with self.assertRaises(cfg.ConfigNonValida) as e:
            cfg.carica(p)
        self.assertIn("git rm --cached", str(e.exception))

    def test_senza_password_nessun_vincolo(self):
        cfg.carica(_scrivi(self.repo, SENZA_PASSWORD))


class Scrittura(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "i bit POSIX su Windows non descrivono nulla")
    def test_nasce_a_0600(self):
        """Protetto dalla nascita, non dal giorno in cui ci si scrive una password."""
        p = Path(tempfile.mkdtemp()) / "sotto" / cfg.NOME_PREDEFINITO
        cfg.Config({"formato": cfg.FORMATO, "database": {}}).salva(p)
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_riletto_uguale(self):
        p = Path(tempfile.mkdtemp()) / cfg.NOME_PREDEFINITO
        cfg.Config(dict(SENZA_PASSWORD)).salva(p)
        self.assertEqual(cfg.carica(p).dati["database"], SENZA_PASSWORD["database"])


class UrlCompleto(unittest.TestCase):
    def test_password_innestata(self):
        c = cfg.Config({"formato": cfg.FORMATO, "database": {}})
        url = c.url_completo({"url": "postgresql://utente@host/v", "password": "s"})
        self.assertEqual(url.password, "s")
        # e non deve comparire quando l'URL si stampa
        self.assertNotIn("s@", url.render_as_string(hide_password=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)

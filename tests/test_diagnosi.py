# -*- coding: utf-8 -*-
"""Dagli errori veri dei driver alla mossa successiva.

I messaggi qui sotto sono copiati da fallimenti reali: e' l'unico modo di
verificare un riconoscimento per sottostringa, che altrimenti si prova solo
contro le stringhe che ha in mente chi lo ha scritto.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo.diagnosi import suggerimento                     # noqa: E402


class Riconoscimento(unittest.TestCase):
    def test_gestore_odbc_mancante(self):
        e = ImportError("libodbc.so.2: cannot open shared object file: "
                        "No such file or directory")
        self.assertIn("unixodbc", suggerimento(e))

    def test_driver_odbc_mancante(self):
        e = Exception("('IM002', '[IM002] [unixODBC][Driver Manager]Data source "
                      "name not found and no default driver specified (0) "
                      "(SQLDriverConnect)')")
        self.assertIn("msodbcsql18", suggerimento(e))

    def test_pacchetto_python_mancante(self):
        self.assertIn("pip install", suggerimento(
            ModuleNotFoundError("No module named 'psycopg'")))

    def test_credenziali(self):
        e = Exception("FATAL: password authentication failed for user \"proteo\"")
        self.assertIn("password", suggerimento(e))

    def test_certificato_autofirmato(self):
        e = Exception("[08001] [Microsoft][ODBC Driver 18 for SQL Server]"
                      "SSL Provider: certificate verify failed:self-signed certificate")
        self.assertIn("TrustServerCertificate", suggerimento(e))

    def test_host_sbagliato(self):
        e = OSError("could not translate host name \"sql.interno\" to address: "
                    "Name or service not known")
        self.assertIn("host", suggerimento(e))

    def test_porta_chiusa(self):
        self.assertIn("porta", suggerimento(OSError("Connection refused")))

    def test_firewall(self):
        e = Exception("Login timeout expired (0) (SQLDriverConnect)")
        self.assertIn("firewall", suggerimento(e))

    def test_la_causa_precisa_vince_su_quella_generica(self):
        """'file not found' compare anche nell'errore di libodbc, che e' altro."""
        e = ImportError("libodbc.so.2: cannot open shared object file: "
                        "No such file or directory")
        self.assertIn("unixodbc", suggerimento(e))
        self.assertNotIn("msodbcsql18", suggerimento(e))

    def test_errore_sconosciuto_nessun_suggerimento(self):
        """Meglio niente che una pista falsa: un suggerimento sbagliato si prova."""
        self.assertIsNone(suggerimento(Exception("qualcosa di mai visto")))


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""Le domande y/n, e la regola che le distingue.

Le domande ordinarie hanno un predefinito, che si vede dalla maiuscola: [Y/n]
o [y/N]. Le azioni che SCRIVONO no ([y/n]): un invio di troppo non deve poter
confermare un'operazione che non si annulla.
"""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proteo import menu                                        # noqa: E402


def _chiedi(risposte, predefinito=None):
    """Esegue `_conferma` dando le risposte indicate. Ritorna (esito, testo)."""
    out = io.StringIO()
    with mock.patch("builtins.input", side_effect=risposte) as finto:
        with redirect_stdout(out):
            esito = menu._conferma("domanda", predefinito)
    return esito, "".join(str(c.args[0]) for c in finto.call_args_list) + out.getvalue()


class Etichette(unittest.TestCase):
    def test_il_predefinito_e_maiuscolo(self):
        self.assertIn("[Y/n]", _chiedi(["y"], True)[1])
        self.assertIn("[y/N]", _chiedi(["y"], False)[1])

    def test_senza_predefinito_sono_entrambe_minuscole(self):
        self.assertIn("[y/n]", _chiedi(["y"], None)[1])


class Risposte(unittest.TestCase):
    def test_y_e_n(self):
        self.assertTrue(_chiedi(["y"])[0])
        self.assertFalse(_chiedi(["n"])[0])

    def test_anche_in_italiano(self):
        """Chi scrive in un menu italiano prova `s` prima di `y`."""
        for si in ("s", "si", "sì", "yes", "Y", " y "):
            self.assertTrue(_chiedi([si])[0], si)
        for no in ("n", "no", "NO"):
            self.assertFalse(_chiedi([no])[0], no)

    def test_l_invio_prende_il_predefinito(self):
        self.assertTrue(_chiedi([""], True)[0])
        self.assertFalse(_chiedi([""], False)[0])

    def test_una_risposta_incomprensibile_ripete_la_domanda(self):
        esito, testo = _chiedi(["boh", "y"], True)
        self.assertTrue(esito)
        self.assertIn("rispondi y o n", testo)


class SenzaPredefinito(unittest.TestCase):
    """La regola che protegge le operazioni che scrivono."""

    def test_l_invio_non_risponde(self):
        esito, testo = _chiedi(["", "", "n"], None)
        self.assertFalse(esito)
        self.assertEqual(testo.count("rispondi y o n"), 2)

    def test_e_non_suggerisce_una_scorciatoia(self):
        self.assertNotIn("invio per", _chiedi(["boh", "n"], None)[1])

    def test_le_scritture_del_menu_non_hanno_predefinito(self):
        """Il controllo che conta: se qualcuno aggiungesse un predefinito alla
        conferma di CIFRA, l'invio basterebbe a scrivere sul database."""
        sorgente = Path(menu.__file__).read_text(encoding="utf-8")
        for riga in sorgente.splitlines():
            if "_conferma(" in riga and "procedere" in riga:
                self.assertNotIn(", True)", riga)
                self.assertNotIn(", False)", riga)


if __name__ == "__main__":
    unittest.main(verbosity=2)

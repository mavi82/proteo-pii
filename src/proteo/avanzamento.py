# -*- coding: utf-8 -*-
"""Dire a che punto siamo — a schermo, in un log, e nel registro.

Su una colonna da milioni di valori la cifratura dura minuti o ore, e senza
niente da guardare non si distingue un lavoro che procede da uno che si e'
piantato. La differenza pratica e' che nel dubbio si interrompe, e interrompere
a meta' lascia la colonna in uno stato misto.

Tre destinazioni diverse, perche' sono tre domande diverse:

  * **il terminale** — chi sta guardando adesso vuole una riga che si aggiorna,
    con percentuale, velocita' e tempo rimasto;
  * **un file di log** — chi ha lanciato con `nohup` rileggera' dopo: li' una
    riga che si riscrive su se' stessa produce solo spazzatura, quindi si
    scrivono righe intere, di rado;
  * **il registro** — chi torna domani, da un'altra sessione, e vuole sapere se
    quel processo sta ancora lavorando. E' l'unica delle tre che sopravvive
    alla chiusura del terminale, ed e' il motivo per cui l'avanzamento si
    scrive su disco e non solo a schermo.

Il costo di scrivere spesso e' reale (un file JSON riscritto a ogni lotto), per
questo entrambe le destinazioni persistenti hanno un intervallo minimo: si
aggiornano a tempo, non a ogni valore.
"""

import sys
import time

__all__ = ["Avanzamento", "Silenzioso", "durata", "quantita"]

INTERVALLO_SCHERMO = 0.2         # s — sotto, l'occhio non distingue
INTERVALLO_LOG = 30.0            # s — un log leggibile, non un diario
INTERVALLO_REGISTRO = 5.0        # s — quanto si accetta di essere "indietro"


def quantita(n):
    """1234567 -> '1,2M'. I numeri lunghi non si leggono a colpo d'occhio."""
    if n is None:
        return "?"
    for soglia, suffisso in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if n >= soglia:
            return ("%.1f%s" % (n / soglia, suffisso)).replace(".", ",")
    return str(n)


def durata(secondi):
    """90 -> '1m 30s'. Nessun decimale: e' una stima, non una misura."""
    if secondi is None or secondi != secondi or secondi < 0:
        return "?"
    secondi = int(secondi)
    if secondi < 60:
        return "%ds" % secondi
    if secondi < 3600:
        return "%dm %02ds" % (secondi // 60, secondi % 60)
    return "%dh %02dm" % (secondi // 3600, (secondi % 3600) // 60)


class Silenzioso:
    """Non dice niente. Il default quando nessuno sta guardando."""

    def colonna(self, tabella, colonna, tipo, verso, righe, distinti):
        pass

    def fase(self, descrizione):
        pass

    def avanti(self, elaborati):
        pass

    def conclusa(self, righe_toccate):
        pass

    def scartato(self, valore, motivo):
        pass


class Avanzamento(Silenzioso):
    """Racconta a schermo (o su log) e aggiorna il registro.

    `registro` e `database` servono a scrivere l'avanzamento su disco: senza,
    resta solo la parte a schermo — che e' cio' che serve ai test e alle
    anteprime.
    """

    def __init__(self, uscita=None, registro=None, database=None, tty=None):
        self.uscita = uscita or sys.stdout
        self.registro = registro
        self.database = database
        # Una riga che si riscrive con \r ha senso solo su un terminale. In un
        # file di log produrrebbe una riga chilometrica illeggibile.
        self.tty = self.uscita.isatty() if tty is None else tty
        self._reset()

    def _reset(self):
        self.tabella = self.colonna_ = None
        self.distinti = None
        self.elaborati = 0
        self.inizio = time.time()
        self.ultimo_schermo = 0.0
        self.ultimo_registro = 0.0
        self.descrizione = ""
        self.aperta = False

    # -- eventi ------------------------------------------------------------- #
    def colonna(self, tabella, colonna, tipo, verso, righe, distinti):
        self._reset()
        self.tabella, self.colonna_ = tabella, colonna
        self.distinti = distinti
        self._riga("\n%s %s.%s (%s) — %s righe, %s valori distinti"
                   % (verso, tabella, colonna, tipo,
                      quantita(righe), quantita(distinti)))

    def fase(self, descrizione):
        self.descrizione = descrizione
        self._chiudi_riga()
        self._riga("  %s" % descrizione)
        self._scrivi_registro(forza=True)

    def avanti(self, elaborati):
        self.elaborati = elaborati
        adesso = time.time()
        if adesso - self.ultimo_schermo >= (INTERVALLO_SCHERMO if self.tty
                                            else INTERVALLO_LOG):
            self.ultimo_schermo = adesso
            self._mostra()
        if adesso - self.ultimo_registro >= INTERVALLO_REGISTRO:
            self.ultimo_registro = adesso
            self._scrivi_registro()

    def conclusa(self, righe_toccate):
        self._chiudi_riga()
        self._riga("  fatto: %s righe aggiornate in %s"
                   % (quantita(righe_toccate), durata(time.time() - self.inizio)))

    def scartato(self, valore, motivo):
        self._chiudi_riga()
        self._riga("  saltato (RESTA IN CHIARO): %r — %s" % (valore, motivo))

    # -- resa --------------------------------------------------------------- #
    def _testo(self):
        trascorso = max(time.time() - self.inizio, 1e-6)
        velocita = self.elaborati / trascorso
        pezzi = ["  %s/%s" % (quantita(self.elaborati), quantita(self.distinti))]
        if self.distinti:
            pezzi.append("%3d%%" % (100 * self.elaborati // max(self.distinti, 1)))
        pezzi.append("%s/s" % quantita(int(velocita)))
        if self.distinti and velocita > 0:
            pezzi.append("mancano %s" % durata((self.distinti - self.elaborati)
                                               / velocita))
        return "  ".join(pezzi)

    def _mostra(self):
        if self.tty:
            self.uscita.write("\r\033[K" + self._testo())
            self.uscita.flush()
            self.aperta = True
        else:
            self._riga(self._testo())

    def _chiudi_riga(self):
        if self.aperta:
            self.uscita.write("\n")
            self.aperta = False

    def _riga(self, testo):
        self._chiudi_riga()
        self.uscita.write(testo + "\n")
        self.uscita.flush()

    def _scrivi_registro(self, forza=False):
        """L'unica parte che sopravvive alla fine della sessione."""
        if not (self.registro and self.database and self.tabella):
            return
        try:
            self.registro.avanzamento(self.database, self.tabella, self.colonna_,
                                      elaborati=self.elaborati,
                                      distinti=self.distinti,
                                      fase=self.descrizione,
                                      iniziato=self.inizio)
        except OSError:
            # Un registro non scrivibile non deve fermare una cifratura in
            # corso: si perde la possibilita' di seguirla da fuori, non i dati.
            pass

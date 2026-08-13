# -*- coding: utf-8 -*-
"""Dire a che punto siamo — a schermo, in un log, e nel registro.

Su una colonna da milioni di valori la cifratura dura minuti o ore, e senza
niente da guardare non si distingue un lavoro che procede da uno piantato. La
differenza pratica e' che nel dubbio si interrompe, e interrompere a meta'
lascia la colonna in uno stato misto.

## Perche' c'e' un thread

La prima versione mostrava l'avanzamento solo quando il chiamante lo chiedeva,
cioe' a ogni lotto scritto. Il risultato era una riga che restava immobile
proprio nei momenti in cui serviva di piu':

  * durante il conteggio iniziale (una scansione della tabella: minuti, zero
    eventi);
  * durante l'UPDATE finale, che e' **una sola istruzione** e su una tabella
    grande e' il pezzo piu' lungo di tutti — nessun evento da cui aggiornare;
  * su una colonna con meno valori distinti della dimensione di un lotto, dove
    l'aggiornamento arrivava una volta sola, alla fine.

Il tempo pero' passa lo stesso, ed e' l'informazione che dice "sta lavorando".
Quindi un thread ridisegna la riga a intervalli fissi, e gli eventi si limitano
ad aggiornare i contatori: cosi' il cronometro scorre anche quando il thread
principale e' fermo dentro una chiamata al database.

## Tre destinazioni, tre domande diverse

  * **il terminale** — chi guarda adesso vuole una barra, una percentuale, la
    velocita' e il tempo rimasto;
  * **un file di log** — chi ha lanciato con `nohup` rileggera' dopo: li' una
    riga che si riscrive su se' stessa produce spazzatura, quindi si scrivono
    righe intere, di rado;
  * **il registro** — chi torna domani da un'altra sessione. E' l'unica delle
    tre che sopravvive alla chiusura del terminale.
"""

import sys
import threading
import time

__all__ = ["Avanzamento", "Silenzioso", "durata", "quantita", "barra"]

INTERVALLO_SCHERMO = 0.25        # s — il battito a schermo
INTERVALLO_LOG = 30.0            # s — un log leggibile, non un diario
INTERVALLO_REGISTRO = 2.0        # s — quanto si accetta di essere "indietro"
                                 #     quando si guarda da un'altra sessione

LARGHEZZA_BARRA = 24
GIRANDOLA = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


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


def barra(fatto, totale, larghezza=LARGHEZZA_BARRA):
    """[███████░░░░░░░] — la percentuale si legge prima di leggerla."""
    if not totale:
        return "░" * larghezza
    pieni = min(larghezza, int(larghezza * fatto / totale))
    return "█" * pieni + "░" * (larghezza - pieni)


class Silenzioso:
    """Non dice niente. Il default quando nessuno sta guardando."""

    def inizio(self, tabella, colonna, tipo, verso):
        pass

    def totali(self, righe, distinti):
        pass

    def fase(self, descrizione, contabile=False, totale=None):
        pass

    def avanti(self, elaborati):
        pass

    def conclusa(self, righe_toccate):
        pass

    def scartato(self, valore, motivo):
        pass

    def chiudi(self):
        pass


class Avanzamento(Silenzioso):
    """Racconta a schermo (o su log) e aggiorna il registro.

    `registro` e `database` servono a scrivere l'avanzamento su disco: senza,
    resta solo la parte a schermo. `battito=False` disattiva il thread e
    ridisegna sugli eventi — serve ai test, che devono essere deterministici.
    """

    def __init__(self, uscita=None, registro=None, database=None, tty=None,
                 battito=True):
        self.uscita = uscita or sys.stdout
        self.registro = registro
        self.database = database
        # Una riga che si riscrive con \r ha senso solo su un terminale. In un
        # file di log produrrebbe una riga chilometrica illeggibile.
        self.tty = self.uscita.isatty() if tty is None else tty
        self.lucchetto = threading.Lock()
        self.battito = None
        self._ferma = threading.Event()
        self._vuole_battito = battito
        self._reset()

    def _reset(self):
        self.tabella = self.colonna_ = self.tipo = self.verso = None
        self.righe = self.distinti = self.totale = None
        self.elaborati = 0
        self.inizio_colonna = time.time()
        self.inizio_fase = time.time()
        self.descrizione = ""
        self.contabile = False
        self.ultimo_log = 0.0
        self.ultimo_passo = -1
        self.ultimo_registro = 0.0
        self.giro = 0
        self.aperta = False

    # -- eventi ------------------------------------------------------------- #
    def inizio(self, tabella, colonna, tipo, verso):
        self._ferma_battito()
        self._reset()
        self.tabella, self.colonna_ = tabella, colonna
        self.tipo, self.verso = tipo, verso
        self._riga("\n%s %s.%s (%s)" % (verso, tabella, colonna, tipo))
        self._avvia_battito()

    def totali(self, righe, distinti):
        self.righe, self.distinti = righe, distinti
        self.totale = distinti
        self._riga("  %s righe, %s valori distinti"
                   % (quantita(righe), quantita(distinti)))

    def fase(self, descrizione, contabile=False, totale=-1):
        """`contabile` = questa fase ha un avanzamento da contare.

        Le altre — il conteggio, l'UPDATE in un colpo solo — non ne hanno: li'
        si mostra il cronometro, che e' l'unica cosa che continua a muoversi ed
        e' quanto basta a distinguere "sta lavorando" da "e' piantato".

        `totale` cambia il denominatore: le fasi contano cose diverse — la prima
        i valori distinti, la scrittura a lotti le righe — e una barra che
        cambia unita' senza dirlo mente. `None` significa "usa il numero di
        righe della tabella".
        """
        with self.lucchetto:
            self.descrizione = descrizione
            self.contabile = contabile
            self.inizio_fase = time.time()
            if totale != -1:
                self.totale = totale if totale is not None else self.righe
                self.elaborati = 0
                self.ultimo_passo = -1
        self._scrivi_registro(forza=True)
        self._disegna(forza=True)

    def avanti(self, elaborati):
        """Aggiorna i contatori e basta: a disegnare ci pensa il battito.

        Il massimo, non l'ultimo valore: i conteggi arrivano da due punti — chi
        calcola i surrogati e chi li scrive nella tabella di appoggio — e il
        secondo e' indietro di un lotto. Una barra che torna indietro fa
        sembrare rotto proprio cio' che deve rassicurare.
        """
        self.elaborati = max(self.elaborati, elaborati)
        if not self.battito:
            self._disegna()

    def conclusa(self, righe_toccate):
        self._ferma_battito()
        self._riga("  fatto: %s righe aggiornate in %s"
                   % (quantita(righe_toccate),
                      durata(time.time() - self.inizio_colonna)))
        self._scrivi_registro(forza=True)

    def scartato(self, valore, motivo):
        self._riga("  saltato (RESTA IN CHIARO): %r — %s" % (valore, motivo))

    def chiudi(self):
        """Da chiamare sempre, anche se l'esecuzione e' fallita a meta'."""
        self._ferma_battito()

    # -- battito ------------------------------------------------------------ #
    def _avvia_battito(self):
        if not self._vuole_battito:
            return
        self._ferma.clear()
        self.battito = threading.Thread(target=self._batti, daemon=True)
        self.battito.start()

    def _ferma_battito(self):
        if self.battito:
            self._ferma.set()
            self.battito.join(timeout=2)
            self.battito = None

    def _batti(self):
        # `wait` invece di `sleep`: alla fine della colonna il thread esce
        # subito, invece di far aspettare un intervallo intero.
        while not self._ferma.wait(INTERVALLO_SCHERMO):
            self._disegna()
            self._scrivi_registro()

    # -- resa --------------------------------------------------------------- #
    def _testo(self):
        adesso = time.time()
        trascorso = max(adesso - self.inizio_colonna, 1e-6)
        self.giro += 1

        if self.contabile and self.totale:
            velocita = self.elaborati / trascorso
            mancano = ((self.totale - self.elaborati) / velocita
                       if velocita > 0 else None)
            return "  %s %3d%%  %s/%s  %s/s  trascorsi %s  mancano %s" % (
                barra(self.elaborati, self.totale),
                100 * self.elaborati // max(self.totale, 1),
                quantita(self.elaborati), quantita(self.totale),
                quantita(int(velocita)), durata(trascorso), durata(mancano))

        # Fase senza contatore: il cronometro e' l'unica cosa che si muove, e
        # muoversi e' precisamente il messaggio.
        return "  %s %s  da %s" % (GIRANDOLA[self.giro % len(GIRANDOLA)],
                                   self.descrizione,
                                   durata(adesso - self.inizio_fase))

    def _disegna(self, forza=False):
        with self.lucchetto:
            if self.tty:
                self.uscita.write("\r\033[K" + self._testo())
                self.uscita.flush()
                self.aperta = True
                return
            # Su log: righe intere, di rado. `forza` serve ai cambi di fase,
            # che sono l'ossatura del racconto e non vanno persi.
            #
            # Oltre al tempo si scrive a ogni 5% percorso: un lavoro di due ore
            # e uno di due minuti danno cosi' lo stesso numero di righe, una
            # ventina, che e' quanto serve a ricostruire l'andamento rileggendo
            # il file. A solo tempo, il lavoro corto non lascerebbe traccia.
            adesso = time.time()
            passo = (100 * self.elaborati // max(self.totale or 0, 1) // 5
                     if self.contabile and self.totale else self.ultimo_passo)
            if forza or adesso - self.ultimo_log >= INTERVALLO_LOG \
                    or passo > self.ultimo_passo:
                self.ultimo_log, self.ultimo_passo = adesso, passo
                self.uscita.write(self._testo() + "\n")
                self.uscita.flush()

    def _chiudi_riga(self):
        if self.aperta:
            self.uscita.write("\n")
            self.aperta = False

    def _riga(self, testo):
        with self.lucchetto:
            self._chiudi_riga()
            self.uscita.write(testo + "\n")
            self.uscita.flush()

    def _scrivi_registro(self, forza=False):
        """L'unica parte che sopravvive alla fine della sessione."""
        if not (self.registro and self.database and self.tabella):
            return
        adesso = time.time()
        if not forza and adesso - self.ultimo_registro < INTERVALLO_REGISTRO:
            return
        self.ultimo_registro = adesso
        try:
            self.registro.avanzamento(self.database, self.tabella, self.colonna_,
                                      elaborati=self.elaborati,
                                      distinti=self.distinti,
                                      fase=self.descrizione,
                                      iniziato=self.inizio_colonna)
        except OSError:
            # Un registro non scrivibile non deve fermare una cifratura in
            # corso: si perde la possibilita' di seguirla da fuori, non i dati.
            pass

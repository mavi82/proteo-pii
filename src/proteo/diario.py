# -*- coding: utf-8 -*-
"""Il diario: cosa ha fatto Proteo, minuto per minuto, su file.

Serve a rispondere a una domanda che a schermo non trova posto: *cos'e'
successo davvero?* La riga di avanzamento dice a che punto siamo adesso; il
diario resta, e si puo' rileggere, allegare a una segnalazione, confrontare con
il log del server.

## La regola che decide tutto: si deve poter mandare a qualcuno

Un diario che nessuno puo' condividere non viene condiviso, e allora tanto vale
non scriverlo. Quindi **non contiene nessun valore del database**:

  * delle istruzioni SQL si scrive il testo, mai i parametri — sono i valori
    veri e i loro surrogati;
  * dei valori non trattabili si scrive quanti sono e perche', mai quali;
  * dell'URL si scrive la forma con la password nascosta.

Non e' prudenza generica: la tabella di appoggio contiene la corrispondenza in
chiaro fra valore e surrogato, e un diario che ne riportasse i parametri
sarebbe esattamente quel dizionario che il progetto rifiuta di produrre — solo,
in un file di testo che nessuno considera sensibile.

## Cosa ci finisce

L'intestazione di sessione (versioni, driver, opzioni, percorsi) e' li' perche'
e' la prima cosa che si chiede a chi segnala un problema, e la piu' noiosa da
raccogliere a mano. Poi ogni fase, ogni istruzione con la sua durata, ogni
errore con la traccia.
"""

import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

__all__ = ["Diario", "Silenzioso", "apri"]

LARGHEZZA_SQL = 300          # oltre, un'istruzione non si legge comunque


class Silenzioso:
    """Nessun diario. Tutte le chiamate non fanno niente."""

    percorso = None

    def riga(self, testo, *argomenti):
        pass

    def errore(self, eccezione, dove=""):
        pass

    def collega(self, engine):
        pass

    def intestazione(self, **campi):
        pass

    def chiudi(self):
        pass


class Diario(Silenzioso):
    def __init__(self, percorso):
        self.percorso = Path(percorso).expanduser()
        self.percorso.parent.mkdir(parents=True, exist_ok=True)
        # Append: le sessioni si accumulano, e il confronto fra una che ha
        # funzionato e una che no e' spesso la diagnosi.
        self.file = open(self.percorso, "a", encoding="utf-8")
        self.riga("=" * 70)

    def riga(self, testo, *argomenti):
        if argomenti:
            testo = testo % argomenti
        self.file.write("%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                      testo))
        self.file.flush()          # un diario che si perde in un crash e' inutile

    def errore(self, eccezione, dove=""):
        self.riga("ERRORE%s: %s: %s", " in %s" % dove if dove else "",
                  type(eccezione).__name__, eccezione)
        for pezzo in traceback.format_exception(type(eccezione), eccezione,
                                                eccezione.__traceback__):
            for r in pezzo.rstrip().splitlines():
                self.riga("  | %s", r)

    def intestazione(self, **campi):
        """Le versioni e le opzioni: cio' che si chiede sempre per prima cosa."""
        self.riga("proteo avviato — %s", " ".join(sys.argv[:3]))
        self.riga("  python %s su %s", platform.python_version(), platform.platform())
        for nome in ("sqlalchemy", "pyodbc", "psycopg", "pymysql"):
            try:
                modulo = __import__(nome)
                self.riga("  %s %s", nome, getattr(modulo, "__version__", "?"))
            except ImportError:
                pass
        for chiave, valore in sorted(campi.items()):
            if valore is not None:
                self.riga("  %s: %s", chiave, valore)

    def collega(self, engine):
        """Registra ogni istruzione SQL con la sua durata. Mai i parametri.

        I parametri sono i valori veri e i loro surrogati: scriverli
        equivarrebbe a produrre il dizionario che il progetto esiste per non
        avere. Il testo dell'istruzione basta a capire dove ci si e' fermati.
        """
        import time

        from sqlalchemy import event

        @event.listens_for(engine, "before_cursor_execute")
        def prima(conn, cursore, istruzione, parametri, contesto, molte):
            conn.info["proteo_inizio"] = time.time()
            self.riga("SQL   %s", _compatta(istruzione))

        @event.listens_for(engine, "after_cursor_execute")
        def dopo(conn, cursore, istruzione, parametri, contesto, molte):
            durata = time.time() - conn.info.get("proteo_inizio", time.time())
            righe = getattr(cursore, "rowcount", -1)
            self.riga("  ok  %.3fs%s", durata,
                      "  righe=%d" % righe if righe and righe >= 0 else "")

        self.riga("collegato al motore %s (%s)", engine.dialect.name,
                  engine.dialect.driver)

    def chiudi(self):
        # Chiudibile due volte: la riga di comando chiude in un `finally`, e chi
        # ha gia' chiuso non deve trovarsi un errore al posto del suo.
        if self.file.closed:
            return
        self.riga("fine")
        self.file.close()


def _compatta(istruzione):
    """Una riga sola, tagliata: un diario si legge, non si studia."""
    testo = " ".join(str(istruzione).split())
    return testo if len(testo) <= LARGHEZZA_SQL else testo[:LARGHEZZA_SQL] + " […]"


def apri(percorso):
    """Diario su file, o `Silenzioso` se il percorso e' vuoto o non scrivibile.

    Non poter scrivere il diario non deve impedire di lavorare: si perde la
    possibilita' di capire cosa e' successo, non i dati.
    """
    if not percorso:
        return Silenzioso()
    try:
        return Diario(percorso)
    except (OSError, ValueError):
        # ValueError: un percorso malformato (byte nullo) non e' un OSError, ma
        # il motivo per rinunciare al diario e' lo stesso.
        return Silenzioso()

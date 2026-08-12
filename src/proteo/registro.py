# -*- coding: utf-8 -*-
"""Il registro: cosa e' stato cifrato, con quale chiave, quando.

    registro/
      <database>/
        <tabella>_<colonna>.json

Un file per colonna, non un unico indice: due esecuzioni su colonne diverse non
si contendono lo stesso file, un'interruzione sporca una sola voce invece di
tutte, e il contenuto resta leggibile e confrontabile a mano.

**Perche' e' indispensabile.** La cifratura preserva il formato, quindi un
codice fiscale surrogato e' indistinguibile da uno vero: guardando la colonna
NON si puo' sapere se e' gia' stata trattata. Il registro e' l'unica cosa che lo
sa. Senza, un secondo passaggio cifrerebbe il cifrato — e nessuno se ne
accorgerebbe fino al momento di tornare indietro.

Da qui i due controlli che questo modulo esiste per fare:

  * prima di cifrare: la colonna non deve risultare gia' cifrata;
  * prima di decifrare: la colonna deve risultare cifrata **con questa chiave**.
    L'identificativo di chiave si confronta *prima* di scrivere: con la chiave
    sbagliata ci si ferma, invece di riempire la colonna di valori irrecuperabili.

Il registro sta sul client, non nel database: se sposti o ripristini il database
il registro non lo segue. Va conservato e copiato **insieme alla chiave**.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["Registro", "StatoIncoerente", "IN_CHIARO", "CIFRATA", "AZZERATA",
           "IN_CORSO"]

FORMATO = "proteo-registro-v1"

IN_CHIARO = "in_chiaro"
CIFRATA = "cifrata"
AZZERATA = "azzerata"          # svuotata: non e' cifratura, non torna indietro
IN_CORSO = "in_corso"          # esecuzione iniziata e mai conclusa: vedi `interrotte()`

_SICURI = re.compile(r"[^A-Za-z0-9._-]")


class StatoIncoerente(RuntimeError):
    """L'operazione richiesta contraddice quanto risulta al registro."""


def _nome_file(tabella, colonna):
    """Nome file sicuro, senza collisioni.

    `dbo.a-b` e `dbo.a_b` diventerebbero lo stesso file dopo la ripulitura dei
    caratteri: quando la ripulitura cambia qualcosa si aggiunge un breve digest
    del nome vero, cosi' due colonne diverse non possono mai finire sullo stesso
    percorso.
    """
    grezzo = "%s_%s" % (tabella, colonna)
    pulito = _SICURI.sub("_", grezzo)
    if pulito != grezzo:
        pulito += "-" + hashlib.sha256(grezzo.encode("utf-8")).hexdigest()[:8]
    return pulito + ".json"


class Registro:
    def __init__(self, radice):
        self.radice = Path(radice)

    # -- percorsi ----------------------------------------------------------- #
    def cartella(self, database):
        return self.radice / _SICURI.sub("_", database)

    def percorso(self, database, tabella, colonna):
        return self.cartella(database) / _nome_file(tabella, colonna)

    # -- lettura ------------------------------------------------------------ #
    def leggi(self, database, tabella, colonna):
        """Voce del registro, o None se la colonna non e' mai stata toccata."""
        p = self.percorso(database, tabella, colonna)
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("formato") != FORMATO:
            raise StatoIncoerente("%s: formato sconosciuto %r" % (p, d.get("formato")))
        # I nomi veri sono salvati dentro: se il file e' stato rinominato a mano,
        # o se la ripulitura dei caratteri ha fatto collidere due colonne, ce ne
        # accorgiamo qui invece di applicare la voce sbagliata.
        if (d.get("tabella"), d.get("colonna")) != (tabella, colonna):
            raise StatoIncoerente(
                "%s contiene la voce di %s.%s, non di %s.%s"
                % (p, d.get("tabella"), d.get("colonna"), tabella, colonna))
        return d

    def stato(self, database, tabella, colonna):
        v = self.leggi(database, tabella, colonna)
        return v["stato"] if v else IN_CHIARO

    def elenco(self, database):
        """Tutte le voci registrate per un database, ordinate."""
        c = self.cartella(database)
        if not c.is_dir():
            return []
        voci = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(c.glob("*.json"))]
        return sorted(voci, key=lambda v: (v.get("tabella", ""), v.get("colonna", "")))

    def interrotte(self, database):
        """Voci rimaste `in_corso`: un'esecuzione precedente non e' mai finita.

        Vanno risolte a mano prima di procedere. La colonna e' in uno stato misto
        (una parte trattata, il resto no) e nessuna operazione automatica sarebbe
        corretta su entrambe le meta'.
        """
        return [v for v in self.elenco(database) if v.get("stato") == IN_CORSO]

    # -- scrittura ---------------------------------------------------------- #
    def _scrivi(self, database, tabella, colonna, **campi):
        p = self.percorso(database, tabella, colonna)
        p.parent.mkdir(parents=True, exist_ok=True)
        precedente = self.leggi(database, tabella, colonna)
        storico = (precedente or {}).get("storico", [])
        voce = {
            "formato": FORMATO,
            "database": database,
            "tabella": tabella,
            "colonna": colonna,
            "aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        voce.update(campi)
        if precedente:
            storico = storico + [{k: precedente.get(k)
                                  for k in ("stato", "chiave_id", "aggiornato", "righe")}]
        voce["storico"] = storico[-20:]
        # scrittura atomica: un'interruzione a meta' lascerebbe un file JSON
        # troncato, cioe' un registro illeggibile proprio quando serve di piu'
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(voce, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return voce

    def avvia(self, database, tabella, colonna, tipo, tweak, chiave_id, verso,
              lista=None):
        """Segna l'inizio di un'operazione. `verso` = 'cifra' | 'decifra'.

        `lista` e' l'impronta della lista di nomi usata, per i tipi che ne
        dipendono: una lista modificata sposta le posizioni e cambia i
        surrogati, quindi va riconosciuta prima di decifrare.
        """
        return self._scrivi(database, tabella, colonna, stato=IN_CORSO, tipo=tipo,
                            tweak=tweak, chiave_id=chiave_id, operazione=verso,
                            lista=lista)

    def concludi(self, database, tabella, colonna, stato, righe):
        v = self.leggi(database, tabella, colonna)
        if v is None:
            raise StatoIncoerente("nessuna operazione avviata per %s.%s" % (tabella, colonna))
        return self._scrivi(database, tabella, colonna, stato=stato, tipo=v.get("tipo"),
                            tweak=v.get("tweak"), chiave_id=v.get("chiave_id"),
                            operazione=v.get("operazione"), lista=v.get("lista"),
                            righe=righe)

    # -- controlli ---------------------------------------------------------- #
    def verifica_prima_di_cifrare(self, database, tabella, colonna):
        v = self.leggi(database, tabella, colonna)
        if v is None:
            return
        dove = "%s.%s" % (tabella, colonna)
        if v["stato"] == CIFRATA:
            raise StatoIncoerente(
                "%s risulta gia' cifrata (chiave %s, %s). Cifrarla di nuovo cifrerebbe "
                "il cifrato, e non si torna piu' indietro."
                % (dove, v.get("chiave_id"), v.get("aggiornato")))
        if v["stato"] == IN_CORSO:
            raise StatoIncoerente(
                "%s e' rimasta in stato 'in_corso' da %s: un'esecuzione precedente non "
                "e' mai finita e la colonna e' in uno stato misto. Va risolta a mano."
                % (dove, v.get("aggiornato")))

    def verifica_prima_di_decifrare(self, database, tabella, colonna, chiave_id,
                                    lista=None):
        v = self.leggi(database, tabella, colonna)
        dove = "%s.%s" % (tabella, colonna)
        if v is None or v["stato"] == IN_CHIARO:
            raise StatoIncoerente("%s non risulta cifrata: non c'e' nulla da riportare "
                                  "in chiaro." % dove)
        if v["stato"] == AZZERATA:
            # Non e' un caso di chiave sbagliata: qui non esiste chiave che basti.
            # Va detto con parole diverse, o si perde tempo a cercare il file giusto.
            raise StatoIncoerente(
                "%s e' stata azzerata il %s: i valori non ci sono piu' e nessuna "
                "chiave li riporta indietro. Servono un backup o il database di "
                "origine." % (dove, v.get("aggiornato")))
        if v["stato"] == IN_CORSO:
            raise StatoIncoerente(
                "%s e' rimasta in stato 'in_corso' da %s: va risolta a mano."
                % (dove, v.get("aggiornato")))
        if v.get("chiave_id") != chiave_id:
            raise StatoIncoerente(
                "%s e' stata cifrata con la chiave %s, ma quella caricata e' %s. "
                "Decifrare con la chiave sbagliata riempirebbe la colonna di valori "
                "irrecuperabili: serve il file di chiave giusto."
                % (dove, v.get("chiave_id"), chiave_id))
        if v.get("lista") and v["lista"] != lista:
            # Una voce aggiunta in mezzo sposta la posizione di tutte quelle che
            # seguono: i surrogati prodotti prima non si ritrovano piu'.
            raise StatoIncoerente(
                "%s e' stata cifrata con la lista %s, ma quella caricata e' %s. "
                "La lista fa parte di cio' che serve per tornare indietro: "
                "recupera la versione con cui e' stata cifrata."
                % (dove, v.get("lista"), lista))
        return v

# -*- coding: utf-8 -*-
"""Le liste di nomi e cognomi: dominio della permutazione, non dizionario.

Un nome non ha checksum ne' struttura: cifrarlo lettera per lettera darebbe
`Mario -> Xqfkz`, reversibile ma inguardabile, e un report costruito su quella
colonna smetterebbe di sembrare un report. Qui invece il surrogato di un nome e'
**un altro nome**, preso da una lista pubblica e fissa: si cerca la posizione
del valore nella lista, si cifra la posizione con FF1, si legge il nome che sta
nella posizione risultante.

## Perche' non e' il dizionario che il progetto rifiuta

Il dizionario rifiutato e' la mappa `valore -> surrogato`, che cresce quanto i
dati e va custodita come i dati. Questa e' una lista di 250 nomi comuni, uguale
per tutti, versionata col codice e pubblica: non contiene nulla del database e
non dice niente su chi ci sta dentro. La corrispondenza fra un nome e il suo
surrogato non e' scritta da nessuna parte — la determina la chiave.

## Il prezzo, dichiarato

**Chi non e' in lista non e' trattabile.** Un nome straniero, un doppio nome
raro, un errore di battitura: si solleva `ValoreNonTrattabile` e decide la
policy. E' la conseguenza inevitabile di volere un surrogato plausibile, e la
lista si allarga — ma solo *prima* di cifrare (vedi sotto).

**La lista fa parte della chiave, di fatto.** Aggiungere un nome sposta le
posizioni di tutti quelli che vengono dopo, quindi cambia i surrogati gia'
prodotti: una colonna gia' cifrata con una lista non si riporta in chiaro con
un'altra. Per questo di ogni lista si calcola un'impronta, che finisce nel
registro accanto alla colonna: chi prova a decifrare con una lista diversa si
ferma prima di scrivere, invece di riempire la colonna di nomi sbagliati.
"""

import hashlib
import unicodedata
from pathlib import Path

__all__ = ["Lista", "carica", "normalizza", "MINIMO"]

CARTELLA = Path(__file__).resolve().parent / "dati"

# Sotto i 100 valori il dominio e' troppo piccolo: il surrogato si rimappa
# contando le occorrenze. E' la stessa soglia che vale per tutto il progetto.
MINIMO = 100

_CACHE = {}


def normalizza(valore):
    """Forma di confronto: maiuscolo e spazi ridotti. **Gli accenti restano.**

    La tentazione e' di togliere anche gli accenti, cosi' `Nicolò` e `Nicolo`
    trovano la stessa voce. Ma il surrogato torna indietro come **la voce della
    lista**, quindi `Nicolò` rientrerebbe `Nicolo`: un valore diverso
    dall'originale, restituito in silenzio, che e' il tipo di errore che questo
    progetto rifiuta ovunque.

    Le due grafie stanno quindi in lista come voci distinte, e chi non c'e' non
    e' trattabile — che e' un rifiuto rumoroso, non una perdita muta.
    """
    v = unicodedata.normalize("NFC", (valore or "").strip().upper())
    return " ".join(v.split())


class Lista:
    """Voci ordinate, ricerca per posizione nei due sensi."""

    def __init__(self, voci, nome="lista"):
        pulite = []
        viste = set()
        for v in voci:
            n = normalizza(v)
            if not n or n in viste:
                continue
            viste.add(n)
            pulite.append(n)
        # L'ordine e' quello del file, non alfabetico: riordinare qui
        # cambierebbe i surrogati a ogni riordino del file, in silenzio.
        self.voci = pulite
        self.nome = nome
        self.posizioni = {v: i for i, v in enumerate(pulite)}
        if len(self.voci) < MINIMO:
            raise ValueError(
                "%s ha %d voci: sotto %d il dominio e' troppo piccolo e il "
                "surrogato si rimappa contando le occorrenze."
                % (nome, len(self.voci), MINIMO))

    def __len__(self):
        return len(self.voci)

    def posizione(self, valore):
        """Indice della voce, o None se non c'e'."""
        return self.posizioni.get(normalizza(valore))

    def voce(self, posizione):
        return self.voci[posizione]

    @property
    def impronta(self):
        """16 esadecimali che identificano il contenuto della lista.

        Finisce nel registro: e' cio' che permette di accorgersi che la lista e'
        cambiata *prima* di decifrare, invece di scoprirlo dai nomi sbagliati.
        """
        h = hashlib.sha256("\n".join(self.voci).encode("utf-8"))
        return h.hexdigest()[:16]

    @property
    def lunghezza_massima(self):
        """Serve a sapere se i surrogati entrano nella colonna."""
        return max(len(v) for v in self.voci)


def carica(nome, percorso=None):
    """Carica `nomi` o `cognomi`, o un file scelto dall'utente. Con cache."""
    p = Path(percorso) if percorso else CARTELLA / ("%s.txt" % nome)
    chiave = str(p.resolve())
    if chiave not in _CACHE:
        righe = [r.strip() for r in p.read_text(encoding="utf-8").splitlines()]
        _CACHE[chiave] = Lista([r for r in righe if r and not r.startswith("#")],
                               nome=p.name)
    return _CACHE[chiave]

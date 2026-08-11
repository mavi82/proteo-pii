# -*- coding: utf-8 -*-
"""La chiave: generazione, custodia, identificazione.

In Proteo la chiave NON protegge una copia dei dati: quando si lavora sul posto,
e' l'unica cosa che separa il database dai suoi valori veri. Perderla significa
perdere i dati, definitivamente. Da qui tre precauzioni che non sono paranoia:

  * **non si sovrascrive mai** un file di chiave esistente. Un `genera` lanciato
    due volte per distrazione renderebbe illeggibile tutto cio' che era stato
    cifrato con la prima;
  * ogni chiave ha un **identificativo pubblico** (`id`), che va nel registro
    accanto a ogni colonna cifrata. Prima di decifrare si confronta: con la
    chiave sbagliata ci si ferma *prima* di scrivere, invece di riempire la
    colonna di spazzatura irrecuperabile;
  * si rifiuta di scrivere la chiave **dentro un repository git**, dove finirebbe
    in un commit e da li' in ogni clone.

L'`id` e' un HMAC della chiave su un'etichetta fissa, troncato: identifica senza
rivelare nulla, e non e' invertibile.
"""

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, hmac

from .repo import dentro_un_repo_git

__all__ = ["genera", "carica", "chiave_id", "ChiaveEsistente", "ChiaveNonValida"]

FORMATO = "proteo-key-v1"
_ETICHETTA_ID = b"proteo/identificativo-chiave"
DIMENSIONE = 32                      # AES-256


class ChiaveEsistente(FileExistsError):
    """Esiste gia' un file di chiave: sovrascriverlo distrugge dati."""


class ChiaveNonValida(ValueError):
    pass


def chiave_id(key):
    """Identificativo pubblico della chiave: 16 esadecimali, non invertibile."""
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(_ETICHETTA_ID)
    return h.finalize()[:8].hex()


def genera(percorso, forza_dentro_git=False):
    """Crea una nuova chiave a 256 bit e la salva. Ritorna (chiave, id).

    Non sovrascrive: se il file esiste solleva ChiaveEsistente. E' deliberato e
    non si aggira con un flag — per rigenerare, sposta a mano il file vecchio,
    cosi' l'atto di perdere la chiave precedente resta cosciente.
    """
    p = Path(percorso)
    if p.exists():
        raise ChiaveEsistente(
            "%s esiste gia'. Sovrascriverlo renderebbe illeggibile tutto cio' che "
            "e' stato cifrato con la chiave attuale: spostalo a mano se sei sicuro." % p)
    if not forza_dentro_git and dentro_un_repo_git(p):
        # Piu' severo di quanto si chiede al file di configurazione, che dentro
        # un repo si accetta se e' ignorato: per la chiave un .gitignore non
        # basta, perche' `git clean -xdf` cancella proprio i file ignorati e una
        # chiave persa sono dati persi.
        raise ChiaveNonValida(
            "%s e' dentro un repository git: una chiave committata finisce in ogni "
            "clone e in tutta la storia, e un .gitignore non basta — `git clean "
            "-xdf` cancella proprio i file ignorati, e perdere la chiave significa "
            "perdere i dati. Scegli un percorso fuori dal repo." % p)

    key = os.urandom(DIMENSIONE)
    kid = chiave_id(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    # apertura esclusiva: se il file compare fra il controllo e la scrittura
    # (altro processo, altra finestra) si fallisce invece di sovrascriverlo
    fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({
            "formato": FORMATO,
            "id": kid,
            "creata": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chiave": base64.b64encode(key).decode("ascii"),
        }, f, indent=2)
    return key, kid


def carica(percorso):
    """Rilegge una chiave dal file. Ritorna (chiave, id)."""
    p = Path(percorso)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ChiaveNonValida("%s non e' un file di chiave leggibile: %s" % (p, e)) from None
    if d.get("formato") != FORMATO:
        raise ChiaveNonValida("formato sconosciuto: %r" % d.get("formato"))
    try:
        key = base64.b64decode(d["chiave"], validate=True)
    except (KeyError, ValueError):
        raise ChiaveNonValida("campo 'chiave' assente o non decodificabile") from None
    if len(key) != DIMENSIONE:
        raise ChiaveNonValida("la chiave deve essere di %d byte, ne ha %d"
                              % (DIMENSIONE, len(key)))
    # L'id salvato viene ricalcolato, non creduto: un file manomesso o corrotto
    # potrebbe dichiarare l'id di un'altra chiave e superare il controllo che
    # dovrebbe impedire di decifrare con la chiave sbagliata.
    atteso = chiave_id(key)
    if d.get("id") not in (None, atteso):
        raise ChiaveNonValida(
            "il file dichiara id %r ma la chiave che contiene ha id %r: file "
            "corrotto o manomesso." % (d["id"], atteso))
    return key, atteso

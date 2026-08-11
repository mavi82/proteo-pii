# -*- coding: utf-8 -*-
"""Riga di comando: l'unico punto da cui Proteo tocca un database vero.

    python -m proteo.cli <comando> [opzioni]

I comandi seguono l'ordine in cui vanno usati, e solo l'ultimo scrive:

    chiave        genera il file di chiave (una volta sola, per sempre)
    tabelle       elenca le tabelle del database — serve a scrivere la policy
    bozza-policy  policy con TUTTE le colonne a 'mantieni', da correggere a mano
    stato         cosa risulta al registro
    verifica      i cancelli fail-closed, senza toccare nulla
    anteprima     prima/dopo su un campione, senza toccare nulla
    cifra         SCRIVE
    decifra       SCRIVE

## Perche' la connessione si passa da variabile d'ambiente

Un URL su riga di comando contiene la password, e finisce nella storia della
shell e nell'output di `ps` — visibile a ogni altro utente della macchina. Il
default e' quindi `$PROTEO_URL`; `--url` esiste per i casi in cui non c'e'
alternativa, ma avvisa.

## Perche' `cifra` pretende --si

`esegui()` e' l'unica operazione irreversibile senza la chiave. Un comando che
scrive non deve poter partire per una freccia-su di troppo.
"""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from . import db, keyfile
from .motore import Motore, VerificaFallita
from .policy import Policy, PolicyNonValida
from .registro import Registro

__all__ = ["main"]


class Uscita(SystemExit):
    """Errore d'uso: messaggio all'utente, niente traceback."""

    def __init__(self, messaggio):
        # stdout puo' essere bufferizzato (pipe, redirezione): senza flush il
        # messaggio d'errore comparirebbe *prima* dell'elenco dei problemi che
        # lo spiega, e si leggerebbe al contrario.
        sys.stdout.flush()
        print("errore: %s" % messaggio, file=sys.stderr)
        super().__init__(2)


# --------------------------------------------------------------------------- #
# Pezzi comuni
# --------------------------------------------------------------------------- #
def _url(args):
    grezzo = args.url or os.environ.get("PROTEO_URL")
    if not grezzo:
        raise Uscita("manca l'URL del database: esporta PROTEO_URL "
                     "(es. postgresql+psycopg://utente@host:5432/vendite) oppure usa --url")
    if args.url:
        print("attenzione: --url mette la password nella storia della shell e in "
              "`ps`. Su una macchina condivisa usa PROTEO_URL.", file=sys.stderr)
    url = make_url(grezzo)
    # Password fuori dall'URL: la si chiede qui, cosi' non sta scritta da nessuna
    # parte. Se l'URL gia' la contiene si rispetta la scelta e non si chiede.
    if url.username and not url.password and sys.stdin.isatty():
        url = url.set(password=getpass.getpass("password per %s@%s: "
                                               % (url.username, url.host)))
    return url


def _engine(args):
    url = _url(args)
    opzioni = {}
    if url.drivername.startswith("mssql+pyodbc"):
        # senza questo pyodbc manda gli INSERT della tabella di appoggio uno per
        # uno: su un DB remoto e' un round-trip di rete per valore distinto.
        opzioni["fast_executemany"] = True
    return create_engine(url, **opzioni)


def _nome_database(args, engine):
    """Etichetta del registro. Deve restare identica fra cifra e decifra."""
    return args.database or engine.url.database or "database"


def _policy(args):
    p = Path(args.policy)
    if not p.exists():
        raise Uscita("policy non trovata: %s (creane una bozza con 'bozza-policy')" % p)
    try:
        return Policy.carica(p)
    except (PolicyNonValida, json.JSONDecodeError) as e:
        raise Uscita("policy illeggibile: %s" % e)


def _motore(args):
    try:
        chiave, kid = keyfile.carica(args.chiave)
    except FileNotFoundError:
        raise Uscita("chiave non trovata: %s (generala con 'chiave')" % args.chiave)
    except keyfile.ChiaveNonValida as e:
        raise Uscita(str(e))
    engine = _engine(args)
    motore = Motore(engine, _policy(args), chiave, kid,
                    Registro(args.registro), _nome_database(args, engine))
    print("database: %s   chiave: %s   registro: %s"
          % (motore.database, kid, Path(args.registro).resolve()), file=sys.stderr)
    return motore


def _stampa_problemi(problemi):
    for p in sorted(problemi, key=lambda x: (x.livello != "errore", x.dove)):
        print("  [%s] %s: %s" % (p.livello, p.dove, p.messaggio))
    return sum(1 for p in problemi if p.livello == "errore")


# --------------------------------------------------------------------------- #
# Comandi che non toccano il database
# --------------------------------------------------------------------------- #
def cmd_chiave(args):
    try:
        _, kid = keyfile.genera(args.percorso)
    except keyfile.ChiaveEsistente as e:
        raise Uscita(str(e))
    except keyfile.ChiaveNonValida as e:
        raise Uscita(str(e))
    print("chiave creata: %s" % Path(args.percorso).resolve())
    print("identificativo: %s" % kid)
    print("\nCopiala in un posto sicuro insieme al registro. Perderla significa "
          "perdere i dati: non esiste alcun dizionario da cui recuperarli.")


# --------------------------------------------------------------------------- #
# Comandi in sola lettura
# --------------------------------------------------------------------------- #
def cmd_tabelle(args):
    engine = _engine(args)
    for t in db.elenco_tabelle(engine, args.schema):
        print(t)


def cmd_bozza_policy(args):
    """Policy con ogni colonna a 'mantieni': il fail-closed vuole tutte le colonne.

    Si scrive la bozza completa e la si corregge, invece di far scrivere a mano
    una riga per colonna — il costo che rende il fail-closed accettabile.
    """
    engine = _engine(args)
    tabelle = args.tabelle or db.elenco_tabelle(engine, args.schema)
    schema = db.introspeziona(engine, sorted(tabelle))
    policy = Policy({t: {c: {"strategia": "mantieni"} for c in sorted(colonne)}
                     for t, colonne in schema["tabelle"].items()})

    destinazione = Path(args.policy)
    if destinazione.exists() and not args.sovrascrivi:
        raise Uscita("%s esiste gia': una bozza rigenerata cancellerebbe le "
                     "strategie gia' scelte. Usa --sovrascrivi se e' cio' che vuoi."
                     % destinazione)
    policy.salva(destinazione)
    print("bozza scritta in %s (%d tabelle, %d colonne, tutte 'mantieni')"
          % (destinazione, len(policy.tabelle),
             sum(len(c) for c in policy.tabelle.values())))
    if schema["foreign_key"]:
        print("\nforeign key rilevate — i due lati devono ricevere lo stesso tweak:")
        for (t1, c1), (t2, c2) in schema["foreign_key"]:
            print("  %s.%s -> %s.%s" % (t1, c1, t2, c2))
    print("\nOra apri il file e metti {\"strategia\": \"cifra\", \"tipo\": \"CF\"} "
          "sulle colonne da trattare (tipi: CF, PIVA, IBAN).")


def cmd_stato(args):
    engine = _engine(args)
    registro = Registro(args.registro)
    database = _nome_database(args, engine)
    voci = registro.elenco(database)
    if not voci:
        print("nessuna colonna registrata per %s" % database)
        return
    for v in voci:
        # una colonna azzerata non ha ne' tipo ne' chiave: stamparne i None
        # farebbe pensare a un'informazione persa invece che mai esistita
        campi = " ".join("%s=%s" % (k, v.get(k)) for k in ("tipo", "tweak", "chiave_id")
                         if v.get(k) is not None)
        print("%-9s %s.%s  %s  righe=%s  %s"
              % (v.get("stato"), v.get("tabella"), v.get("colonna"), campi,
                 v.get("righe", "-"), v.get("aggiornato")))
    interrotte = registro.interrotte(database)
    if interrotte:
        print("\n%d colonne in stato 'in_corso': un'esecuzione non e' mai finita e "
              "la colonna e' in uno stato misto. Vanno risolte a mano."
              % len(interrotte))


def cmd_verifica(args):
    motore = _motore(args)
    problemi = motore.verifica(args.verso)
    if not problemi:
        print("nessun problema: si puo' procedere con '%s'." % args.verso)
        return
    errori = _stampa_problemi(problemi)
    if errori:
        raise SystemExit(1)


def cmd_anteprima(args):
    motore = _motore(args)
    for c in motore.anteprima(n=args.campione, verso=args.verso):
        etichetta = ("AZZERA — i valori spariscono" if c["operazione"] == "azzera"
                     else "%s, tweak=%s" % (c["tipo"], c["tweak"]))
        print("\n%s.%s  [%s]  %s righe, %s valori distinti"
              % (c["tabella"], c["colonna"], etichetta, c["righe"], c["distinti"]))
        for prima, dopo in c["campione"]:
            print("    %-32s -> %s" % (prima, "(NULL)" if dopo is None else dopo))
        for valore, motivo in c["non_trattabili"]:
            print("    %-32s !! %s" % (valore, motivo))


# --------------------------------------------------------------------------- #
# Comandi che scrivono
# --------------------------------------------------------------------------- #
def _esegui(args, verso):
    motore = _motore(args)

    problemi = motore.verifica(verso)
    if _stampa_problemi(problemi):
        raise Uscita("la verifica ha trovato errori bloccanti: nulla e' stato scritto.")

    colonne = [(t, c, r["tipo"]) for t, c, r in motore.policy.colonne_da_cifrare()]
    if verso == "cifra":
        # elencate insieme e marcate: l'azzeramento e' l'unica cosa in questo
        # elenco che nessuna chiave annulla, e va vista prima di rispondere 'si'.
        colonne += [(t, c, "AZZERA, irreversibile")
                    for t, c, _ in motore.policy.colonne_da_azzerare()]
    print("\n%s: %d colonne su %s" % (verso, len(colonne), motore.database))
    for t, c, etichetta in colonne:
        print("  %s.%s (%s)" % (t, c, etichetta))

    if not args.si:
        risposta = input("\nprocedere? il database verra' modificato [scrivi 'si']: ")
        if risposta.strip().lower() not in ("si", "sì"):
            print("annullato: nulla e' stato scritto.")
            return

    try:
        rapporto = motore.esegui(verso, su_valore_non_trattabile=args.su_errore,
                                 progresso=lambda d: print("  %s..." % d, flush=True))
    except VerificaFallita as e:
        raise Uscita(str(e))

    print("\nfatto.")
    for c in rapporto["colonne"]:
        print("  %s.%s: %d righe %s"
              % (c["tabella"], c["colonna"], c["righe_aggiornate"],
                 "AZZERATE" if c["operazione"] == "azzera" else "aggiornate"))
        if c["non_trattabili"]:
            # 'salta' lascia il valore in chiaro: e' una fuga di dati, e come tale
            # va detta a voce alta invece di finire in una riga di riepilogo.
            print("    %d valori SALTATI, rimasti IN CHIARO nella colonna:"
                  % len(c["non_trattabili"]))
            for valore, motivo in c["non_trattabili"][:20]:
                print("      %s (%s)" % (valore, motivo))
            if len(c["non_trattabili"]) > 20:
                print("      ... e altri %d" % (len(c["non_trattabili"]) - 20))

    if args.rapporto:
        Path(args.rapporto).write_text(
            json.dumps(rapporto, indent=2, ensure_ascii=False), encoding="utf-8")
        print("\nrapporto in %s" % args.rapporto)


def cmd_cifra(args):
    _esegui(args, "cifra")


def cmd_decifra(args):
    _esegui(args, "decifra")


# --------------------------------------------------------------------------- #
# Argomenti
# --------------------------------------------------------------------------- #
def _parser():
    p = argparse.ArgumentParser(
        prog="python -m proteo.cli",
        description="Anonimizzazione reversibile di colonne di database.")
    sub = p.add_subparsers(dest="comando", required=True)

    def con_db(nome, aiuto, chiede_chiave=False):
        s = sub.add_parser(nome, help=aiuto)
        s.add_argument("--url", help="URL SQLAlchemy (default: $PROTEO_URL)")
        s.add_argument("--database", help="etichetta del registro "
                                          "(default: il nome nell'URL)")
        s.add_argument("--registro", default="registro",
                       help="cartella del registro (default: ./registro)")
        if chiede_chiave:
            s.add_argument("--chiave", required=True, help="file di chiave")
            s.add_argument("--policy", default="policy.json",
                           help="file di policy (default: policy.json)")
        return s

    s = sub.add_parser("chiave", help="genera un file di chiave")
    s.add_argument("percorso")
    s.set_defaults(func=cmd_chiave)

    s = con_db("tabelle", "elenca le tabelle del database")
    s.add_argument("--schema", help="limita a uno schema")
    s.set_defaults(func=cmd_tabelle)

    s = con_db("bozza-policy", "scrive una policy con tutte le colonne a 'mantieni'")
    s.add_argument("--policy", default="policy.json")
    s.add_argument("--schema", help="limita a uno schema")
    s.add_argument("--tabelle", nargs="+", help="solo queste tabelle")
    s.add_argument("--sovrascrivi", action="store_true")
    s.set_defaults(func=cmd_bozza_policy)

    s = con_db("stato", "cosa risulta al registro")
    s.set_defaults(func=cmd_stato)

    s = con_db("verifica", "i cancelli fail-closed, senza scrivere", chiede_chiave=True)
    s.add_argument("--verso", choices=("cifra", "decifra"), default="cifra")
    s.set_defaults(func=cmd_verifica)

    s = con_db("anteprima", "prima/dopo su un campione, senza scrivere",
               chiede_chiave=True)
    s.add_argument("--verso", choices=("cifra", "decifra"), default="cifra")
    s.add_argument("--campione", type=int, default=8)
    s.set_defaults(func=cmd_anteprima)

    for nome, aiuto, funzione in (("cifra", "SCRIVE: applica la policy", cmd_cifra),
                                  ("decifra", "SCRIVE: riporta in chiaro", cmd_decifra)):
        s = con_db(nome, aiuto, chiede_chiave=True)
        s.add_argument("--si", action="store_true",
                       help="salta la conferma interattiva")
        s.add_argument("--su-errore", dest="su_errore",
                       choices=("ferma", "salta"), default="ferma",
                       help="valore malformato: fermarsi (default) o saltarlo "
                            "lasciandolo IN CHIARO")
        s.add_argument("--rapporto", help="scrive il rapporto JSON qui")
        s.set_defaults(func=funzione)

    return p


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        # Un'interruzione durante `esegui` lascia la voce del registro in
        # 'in_corso': e' il segnale corretto, non un difetto. La transazione
        # invece torna indietro da sola.
        print("\ninterrotto. Controlla 'stato': una colonna in 'in_corso' va "
              "risolta a mano.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

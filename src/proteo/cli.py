# -*- coding: utf-8 -*-
"""Riga di comando: l'unico punto da cui Proteo tocca un database vero.

    python -m proteo.cli              il menu guidato
    python -m proteo.cli <comando>    un'azione sola, per script e cron

Senza argomenti parte il menu (`menu.py`): e' il modo giusto per lavorare a
mano, perche' tiene sotto gli occhi su quale database si sta lavorando e cosa
risulta gia' fatto. I comandi singoli restano perche' un menu non si mette in
uno script, e seguono l'ordine in cui vanno usati:

    chiave        genera il file di chiave (una volta sola, per sempre)
    tabelle       elenca le tabelle del database — serve a scrivere la policy
    bozza-policy  allinea la policy allo schema (--rileva riconosce le colonne)
    prova         prova solo la connessione
    stato         cosa risulta al registro
    verifica      i cancelli fail-closed, senza toccare nulla
    anteprima     prima/dopo su un campione, senza toccare nulla
    cifra         SCRIVE
    decifra       SCRIVE

## Da dove arrivano i parametri

Nell'ordine: opzione esplicita, poi variabile d'ambiente, poi file di
configurazione (`config.py`). Il config e' la via normale — tiene URL, chiave,
policy e registro di ciascun database — e le opzioni servono a scavalcarlo un
volta sola senza modificarlo.

Un URL su riga di comando contiene la password, e finisce nella storia della
shell e nell'output di `ps`, visibile a ogni altro utente della macchina. Per
questo `--url` avvisa, e le alternative sono `$PROTEO_URL`, `$PROTEO_PASSWORD`
o il config, che si legge solo se e' protetto davvero.

## Perche' `cifra` pretende una conferma

`esegui()` e' l'unica operazione irreversibile senza la chiave. Un comando che
scrive non deve poter partire per una freccia-su di troppo.
"""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from sqlalchemy.exc import ArgumentError
from sqlalchemy.engine import make_url

from . import config as cfg
from . import db, diagnosi, keyfile, rilevamento, stampa
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
# Parametri: opzione, ambiente, configurazione
# --------------------------------------------------------------------------- #
def _config(args):
    percorso = getattr(args, "config", None) or cfg.percorso_predefinito()
    if not percorso:
        return cfg.Config(), {}
    try:
        config = cfg.carica(percorso)
    except cfg.ConfigNonValida as e:
        raise Uscita(str(e))
    nome = getattr(args, "db", None) or config.predefinito()
    if nome is None:
        raise Uscita("il config elenca piu' database (%s): scegline uno con --db"
                     % ", ".join(config.nomi()))
    try:
        return config, config.voce(nome)
    except cfg.ConfigNonValida as e:
        raise Uscita(str(e))


def _percorso(args, config, voce, campo):
    """Opzione esplicita, altrimenti il config (con i suoi percorsi risolti)."""
    esplicito = getattr(args, campo, None)
    if esplicito:
        return Path(esplicito).expanduser()
    return config.risolvi(voce, campo)


def _url(args, config, voce):
    if args.url:
        print("attenzione: --url mette la password nella storia della shell e in "
              "`ps`. Su una macchina condivisa usa PROTEO_URL o il config.",
              file=sys.stderr)
        url = make_url(args.url)
    elif os.environ.get("PROTEO_URL"):
        url = make_url(os.environ["PROTEO_URL"])
    elif voce.get("url"):
        url = config.url_completo(voce)
    else:
        raise Uscita("manca l'URL del database: mettilo nel config "
                     "(python -m proteo.cli con il menu lo crea), oppure esporta "
                     "PROTEO_URL, oppure usa --url")

    if url.username and not url.password:
        # La password fuori dall'URL non e' un ripiego: e' il caso normale
        # quando il config e' condiviso o versionato.
        if os.environ.get("PROTEO_PASSWORD"):
            url = url.set(password=os.environ["PROTEO_PASSWORD"])
        elif sys.stdin.isatty():
            url = url.set(password=getpass.getpass(
                "password per %s@%s: " % (url.username, url.host)))
    return url


def _fallita(errore, cosa):
    """Errore di connessione tradotto in una mossa. Non torna mai."""
    cosa_fare = diagnosi.suggerimento(errore)
    raise Uscita("%s: %s: %s%s"
                 % (cosa, type(errore).__name__, errore,
                    "\n\n  %s" % cosa_fare if cosa_fare else ""))


def _apri(args, config, voce):
    """Engine, o un errore leggibile: un URL storto non merita un traceback."""
    try:
        return db.crea_engine(_url(args, config, voce))
    except ArgumentError as e:
        raise Uscita("URL del database non utilizzabile: %s" % e)
    except Exception as e:                                  # noqa: BLE001
        # `create_engine` importa il driver, e un driver che manca solleva di
        # tutto — ImportError, OSError, eccezioni proprie. Vedi `diagnosi.py`.
        _fallita(e, "non riesco a preparare la connessione")


def _engine(args):
    config, voce = _config(args)
    return _apri(args, config, voce)


def _nome_database(args, config, voce, engine):
    """Etichetta del registro. Deve restare identica fra cifra e decifra."""
    return (getattr(args, "database", None) or voce.get("etichetta")
            or engine.url.database or "database")


def _motore(args):
    config, voce = _config(args)
    chiave_p = _percorso(args, config, voce, "chiave")
    policy_p = _percorso(args, config, voce, "policy")
    registro_p = _percorso(args, config, voce, "registro")
    if not chiave_p:
        raise Uscita("manca il file di chiave: --chiave, oppure mettilo nel config")
    if not policy_p:
        raise Uscita("manca il file di policy: --policy, oppure mettilo nel config")

    try:
        chiave, kid = keyfile.carica(chiave_p)
    except FileNotFoundError:
        raise Uscita("chiave non trovata: %s (generala con 'chiave')" % chiave_p)
    except keyfile.ChiaveNonValida as e:
        raise Uscita(str(e))

    try:
        policy = Policy.carica(policy_p)
    except FileNotFoundError:
        raise Uscita("policy non trovata: %s (creane una bozza con 'bozza-policy')"
                     % policy_p)
    except (PolicyNonValida, json.JSONDecodeError) as e:
        raise Uscita("policy illeggibile: %s" % e)

    engine = _apri(args, config, voce)
    motore = Motore(engine, policy, chiave, kid, Registro(registro_p or "registro"),
                    _nome_database(args, config, voce, engine))
    print("database: %s   chiave: %s   registro: %s"
          % (motore.database, kid, Path(registro_p or "registro").resolve()),
          file=sys.stderr)
    return motore


# --------------------------------------------------------------------------- #
# Comandi
# --------------------------------------------------------------------------- #
def cmd_menu(args):
    from . import menu                    # importato qui: il menu usa questo
    raise SystemExit(menu.avvia(args.config))   # modulo solo per essere avviato


def cmd_chiave(args):
    config, voce = _config(args)
    percorso = args.percorso or _percorso(args, config, voce, "chiave")
    if not percorso:
        raise Uscita("indica dove creare la chiave, o mettila nel config")
    try:
        _, kid = keyfile.genera(percorso)
    except (keyfile.ChiaveEsistente, keyfile.ChiaveNonValida) as e:
        raise Uscita(str(e))
    print("chiave creata: %s" % Path(percorso).resolve())
    print("identificativo: %s" % kid)
    print("\nCopiala in un posto sicuro insieme al registro. Perderla significa "
          "perdere i dati: non esiste alcun dizionario da cui recuperarli.")


def cmd_prova(args):
    """Solo la connessione: nessuna chiave, nessuna policy, nessuna scrittura."""
    config, voce = _config(args)
    engine = _apri(args, config, voce)
    for anomalia in db.anomalie_url(engine.url):
        print("attenzione: %s" % anomalia)
    print("provo %s..." % engine.url.render_as_string(hide_password=True))
    try:
        versione = db.prova_connessione(engine)
    except Exception as e:                                  # noqa: BLE001
        _fallita(e, "connessione fallita")
    print("connessione riuscita — %s" % versione)


def cmd_tabelle(args):
    for t in db.elenco_tabelle(_engine(args), args.schema):
        print(t)


def cmd_bozza_policy(args):
    """Allinea la policy allo schema: il fail-closed vuole tutte le colonne.

    Le colonne nuove entrano come `mantieni`, oppure come `cifra` se `--rileva`
    le riconosce dai valori. Le scelte gia' scritte non si toccano mai: una
    policy che si rigenera da capo perde ogni decisione presa a mano, quindi in
    pratica non la si rigenera e la si lascia invecchiare.
    """
    config, voce = _config(args)
    engine = _apri(args, config, voce)
    destinazione = _percorso(args, config, voce, "policy") or Path("policy.json")

    if destinazione.exists():
        policy = Policy.carica(destinazione)
        tabelle = args.tabelle or sorted(policy.tabelle) or \
            db.elenco_tabelle(engine, args.schema)
    else:
        policy = Policy()
        tabelle = args.tabelle or db.elenco_tabelle(engine, args.schema)

    schema = db.introspeziona(engine, sorted(tabelle))
    proposte, cambiate = {}, []
    if args.rileva:
        # Di norma solo le colonne ancora da decidere: campionare quelle gia'
        # scelte costerebbe letture inutili. Ma una policy generata tutta
        # 'mantieni' non ha colonne "nuove" pur non essendo stata guardata da
        # nessuno: --rivedi e' per quel caso.
        def da_decidere(tabella, colonna):
            regola = policy.regola(tabella, colonna)
            return regola is None or (args.rivedi
                                      and regola.get("strategia") == "mantieni")

        proposte = rilevamento.proponi(
            lambda t, c: db.campiona(engine, t, c, args.campione),
            {"tabelle": {t: {c: d for c, d in colonne.items()
                             if da_decidere(t, c)}
                         for t, colonne in schema["tabelle"].items()}})

        for tabella, colonne in proposte.items():
            for colonna, (tipo, _, _) in colonne.items():
                if policy.regola(tabella, colonna):
                    policy.tabelle[tabella][colonna] = {"strategia": "cifra",
                                                        "tipo": tipo}
                    cambiate.append("%s.%s" % (tabella, colonna))

    aggiunte, tolte, fuori = policy.aggiorna(schema, proposte)
    policy.salva(destinazione)

    cifrate = [d for d, s in aggiunte if s == "cifra"]
    print("%s: %d colonne aggiunte, di cui %d da cifrare"
          % (destinazione, len(aggiunte), len(cifrate)))
    if cambiate:
        print("%d colonne passate da 'mantieni' a 'cifra'" % len(cambiate))
    for tabella in sorted(proposte):
        for colonna, (tipo, quanti, esaminati) in sorted(proposte[tabella].items()):
            print("  %-45s %-5s %d/%d" % ("%s.%s" % (tabella, colonna),
                                          tipo, quanti, esaminati))
    if tolte:
        print("\n%d colonne non esistono piu' e sono state tolte: %s"
              % (len(tolte), ", ".join(tolte)))
        print("Se sono state RINOMINATE, le nuove sono nate 'mantieni', "
              "cioe' in chiaro.")
    if fuori:
        print("\nla policy nomina tabelle che nel database non ci sono: %s"
              % ", ".join(fuori))
    if schema["foreign_key"]:
        print("\nforeign key — i due lati devono ricevere lo stesso tweak:")
        for (t1, c1), (t2, c2) in schema["foreign_key"]:
            print("  %s.%s -> %s.%s" % (t1, c1, t2, c2))


def cmd_stato(args):
    config, voce = _config(args)
    engine = _apri(args, config, voce)
    registro = Registro(_percorso(args, config, voce, "registro") or "registro")
    database = _nome_database(args, config, voce, engine)
    print("registro di %s:" % database)
    stampa.stato(registro.elenco(database), registro.interrotte(database))


def cmd_verifica(args):
    problemi = _motore(args).verifica(args.verso)
    if not problemi:
        print("nessun problema: si puo' procedere con '%s'." % args.verso)
        return
    if stampa.problemi(problemi):
        raise SystemExit(1)


def cmd_anteprima(args):
    stampa.anteprima(_motore(args).anteprima(n=args.campione, verso=args.verso))


def _esegui(args, verso):
    motore = _motore(args)

    if stampa.problemi(motore.verifica(verso)):
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
    stampa.rapporto(rapporto)

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
        # bin/proteo esporta PROTEO_PROG: l'aiuto deve mostrare il comando che
        # l'utente ha davvero digitato, non quello interno.
        prog=os.environ.get("PROTEO_PROG", "python -m proteo.cli"),
        description="Anonimizzazione reversibile di colonne di database. "
                    "Senza argomenti parte il menu guidato.")
    p.add_argument("--config", help="file di configurazione "
                                    "(default: ./proteo.json, ~/.proteo/proteo.json)")
    p.set_defaults(func=cmd_menu, db=None)
    sub = p.add_subparsers(dest="comando")

    def comune(nome, aiuto, con_chiave=False):
        s = sub.add_parser(nome, help=aiuto)
        s.add_argument("--config")
        s.add_argument("--db", help="quale database del config")
        s.add_argument("--url", help="URL SQLAlchemy (scavalca config e $PROTEO_URL)")
        s.add_argument("--database", help="etichetta del registro")
        s.add_argument("--registro", help="cartella del registro")
        if con_chiave:
            s.add_argument("--chiave", help="file di chiave")
            s.add_argument("--policy", help="file di policy")
        return s

    s = sub.add_parser("menu", help="il menu guidato")
    s.add_argument("--config")
    s.set_defaults(func=cmd_menu, db=None)

    s = comune("chiave", "genera un file di chiave", con_chiave=True)
    s.add_argument("percorso", nargs="?", help="dove crearla (default: dal config)")
    s.set_defaults(func=cmd_chiave)

    s = comune("prova", "prova solo la connessione")
    s.set_defaults(func=cmd_prova)

    s = comune("tabelle", "elenca le tabelle del database")
    s.add_argument("--schema", help="limita a uno schema")
    s.set_defaults(func=cmd_tabelle)

    s = comune("bozza-policy", "scrive una policy con tutte le colonne a 'mantieni'",
               con_chiave=True)
    s.add_argument("--schema", help="limita a uno schema")
    s.add_argument("--tabelle", nargs="+", help="solo queste tabelle")
    s.add_argument("--rileva", action="store_true",
                   help="riconosce CF/PIVA/IBAN campionando i valori e propone "
                        "'cifra' sulle colonne nuove")
    s.add_argument("--rivedi", action="store_true",
                   help="con --rileva, riguarda anche le colonne gia' "
                        "dichiarate 'mantieni'")
    s.add_argument("--campione", type=int, default=200,
                   help="quanti valori guardare per colonna (default: 200)")
    s.set_defaults(func=cmd_bozza_policy)

    s = comune("stato", "cosa risulta al registro")
    s.set_defaults(func=cmd_stato)

    s = comune("verifica", "i cancelli fail-closed, senza scrivere", con_chiave=True)
    s.add_argument("--verso", choices=("cifra", "decifra"), default="cifra")
    s.set_defaults(func=cmd_verifica)

    s = comune("anteprima", "prima/dopo su un campione, senza scrivere",
               con_chiave=True)
    s.add_argument("--verso", choices=("cifra", "decifra"), default="cifra")
    s.add_argument("--campione", type=int, default=8)
    s.set_defaults(func=cmd_anteprima)

    for nome, aiuto, funzione in (("cifra", "SCRIVE: applica la policy", cmd_cifra),
                                  ("decifra", "SCRIVE: riporta in chiaro", cmd_decifra)):
        s = comune(nome, aiuto, con_chiave=True)
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

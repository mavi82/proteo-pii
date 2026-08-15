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
    riprendi      continua una cifratura interrotta, da dove si era fermata
    risolvi       chiude a mano una colonna rimasta 'in_corso'
    ripristino    riallinea il registro dopo un ripristino del database
    pulisci       elimina le tabelle di appoggio rimaste indietro
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

## Se sembra piantato

    kill -USR1 <pid>

stampa dove si trova il processo in quel momento, thread per thread: dice se
sta aspettando il database o se sta calcolando, che e' l'unica domanda che
conta e dall'esterno non si distingue.

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
from . import avanzamento as av
from . import db, diagnosi, diario as dia, keyfile, rilevamento, stampa
from .stampa import SI
from .motore import Motore, VerificaFallita
from .policy import Policy, PolicyNonValida
from .registro import AZZERATA, CIFRATA, IN_CHIARO, Registro
from .surrogati import ValoreNonTrattabile

__all__ = ["main"]


class Uscita(SystemExit):
    """Errore d'uso: messaggio all'utente, niente traceback."""

    def __init__(self, messaggio):
        # stdout puo' essere bufferizzato (pipe, redirezione): senza flush il
        # messaggio d'errore comparirebbe *prima* dell'elenco dei problemi che
        # lo spiega, e si leggerebbe al contrario.
        sys.stdout.flush()
        # Anche nel diario: un comando che si e' rifiutato di partire e' la
        # cosa che si va a cercare rileggendo, e senza questa riga il diario
        # finirebbe con un "fine" che sembra tutto a posto.
        _DIARIO.riga("rifiutato: %s", " ".join(str(messaggio).split()))
        print("errore: %s" % messaggio, file=sys.stderr)
        super().__init__(2)


# --------------------------------------------------------------------------- #
# Parametri: opzione, ambiente, configurazione
# --------------------------------------------------------------------------- #
# Uno solo per esecuzione: aperto alla prima richiesta, chiuso alla fine.
_DIARIO = dia.Silenzioso()


def _apri_diario(args, config=None, voce=None):
    """Percorso: opzione, poi config, poi accanto al file di configurazione."""
    global _DIARIO
    if not isinstance(_DIARIO, dia.Silenzioso):
        return _DIARIO
    if getattr(args, "senza_diario", False):
        return _DIARIO

    percorso = getattr(args, "diario", None)
    if not percorso and voce:
        percorso = voce.get("diario")
    if not percorso:
        vicino = getattr(args, "config", None) or cfg.percorso_predefinito()
        percorso = (Path(vicino).with_suffix(".log") if vicino
                    else Path("proteo.log"))
    _DIARIO = dia.apri(percorso)
    return _DIARIO


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
    diario = _apri_diario(args, config, voce)
    try:
        url = _url(args, config, voce)
        diario.intestazione(url=url.render_as_string(hide_password=True),
                            comando=getattr(args, "comando", None),
                            lotto_righe=getattr(args, "lotto_righe", None),
                            registro=_percorso(args, config, voce, "registro"),
                            policy=_percorso(args, config, voce, "policy"))
        engine = db.crea_engine(url)
        diario.collega(engine)
        return engine
    except ArgumentError as e:
        diario.errore(e, "URL")
        raise Uscita("URL del database non utilizzabile: %s" % e)
    except Exception as e:                                  # noqa: BLE001
        # `create_engine` importa il driver, e un driver che manca solleva di
        # tutto — ImportError, OSError, eccezioni proprie. Vedi `diagnosi.py`.
        diario.errore(e, "connessione")
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
    lotto = getattr(args, "lotto_righe", None)
    if lotto is None:
        lotto = voce.get("lotto_righe")
    motore = Motore(engine, policy, chiave, kid, Registro(registro_p or "registro"),
                    _nome_database(args, config, voce, engine),
                    lotto_righe=int(lotto) if lotto else None)
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


def cmd_pulisci(args):
    """Cerca ed elimina le tabelle di appoggio rimaste indietro.

    Ne resta una solo se un processo e' stato ucciso mentre lavorava. Contiene
    la corrispondenza in chiaro fra valori veri e surrogati — la cosa piu'
    pericolosa che Proteo scriva — quindi si cerca invece di aspettare che
    qualcuno la noti.
    """
    engine = _engine(args)
    orfane = db.mappe_orfane(engine, args.schema)
    if not orfane:
        print("nessuna tabella di appoggio rimasta indietro.")
        return

    print("%d tabelle di appoggio ancora nel database:" % len(orfane))
    for tabella in orfane:
        righe, _ = db.conta(engine, tabella)
        print("  %s  (%d righe: valore vero -> surrogato, IN CHIARO)"
              % (tabella, righe))
    print("\nSe una cifratura e' in corso ADESSO, una di queste e' la sua: "
          "eliminarla\nla farebbe fallire. Controlla prima con 'stato'.")
    if not args.si:
        if input("\neliminarle? [y/n]: ").strip().lower() not in SI:
            print("annullato.")
            return
    for tabella in orfane:
        db.elimina_mappa(engine, tabella)
        print("  eliminata %s" % tabella)


def cmd_risolvi(args):
    """Chiude a mano una colonna rimasta 'in_corso'.

    Non tocca il database: con la cifratura che preserva il formato nessun
    controllo automatico puo' dire se quella colonna e' stata scritta o no —
    un surrogato e' indistinguibile da un valore vero, ed e' il motivo per cui
    il registro esiste. Qui si dichiara cio' che si e' accertato.
    """
    config, voce = _config(args)
    registro = Registro(_percorso(args, config, voce, "registro") or "registro")
    database = args.database or voce.get("etichetta") or "database"
    interrotte = registro.interrotte(database)
    if not interrotte:
        print("nessuna colonna in stato 'in_corso' per %s." % database)
        return

    if not (args.tabella and args.colonna and args.stato):
        print("%d colonne in stato 'in_corso' per %s:" % (len(interrotte), database))
        for v in interrotte:
            print("  %s.%s  avviata %s  operazione=%s  elaborati=%s/%s%s"
                  % (v["tabella"], v["colonna"], v.get("aggiornato"),
                     v.get("operazione"), v.get("elaborati", "?"),
                     v.get("distinti", "?"),
                     "  ultima_chiave=%s" % v["ultima_chiave"]
                     if v.get("ultima_chiave") else ""))
        print("\nSenza 'ultima_chiave' l'esecuzione era in un'unica transazione: "
              "o e' passata\ntutta — e allora il registro direbbe 'cifrata' — o "
              "il database l'ha annullata,\ne la colonna e' rimasta com'era. Con "
              "'ultima_chiave' la colonna e' MISTA e va\nsistemata a mano prima "
              "di dichiarare qualunque cosa.")
        print("\nPer dichiararlo:\n  %s risolvi --tabella T --colonna C --stato "
              "in_chiaro|cifrata" % os.environ.get("PROTEO_PROG", "proteo"))
        return

    voci = [v for v in interrotte
            if (v["tabella"], v["colonna"]) == (args.tabella, args.colonna)]
    if not voci:
        raise Uscita("%s.%s non risulta in stato 'in_corso'"
                     % (args.tabella, args.colonna))
    if not args.si:
        risposta = input("dichiarare %s.%s '%s'? Sbagliare qui significa cifrare "
                         "due volte\no non poter piu' decifrare [y/n]: "
                         % (args.tabella, args.colonna, args.stato))
        if risposta.strip().lower() not in SI:
            print("annullato.")
            return
    registro.concludi(database, args.tabella, args.colonna, args.stato,
                      voci[0].get("righe"))
    print("registro aggiornato: %s.%s -> %s"
          % (args.tabella, args.colonna, args.stato))


def _in_sottofondo(args):
    """Rilancia se stesso staccato dal terminale. Ritorna True se ha delegato.

    Una sessione SSH che cade porta con se' il processo, e su una cifratura di
    ore succede. `setsid` + reindirizzamento su file e' esattamente cio' che si
    farebbe a mano con nohup: farlo qui evita di doverselo ricordare, e
    soprattutto evita di scoprirlo dopo.

    La conferma resta in primo piano: cio' che scrive non deve poter partire
    senza che qualcuno abbia risposto.
    """
    import subprocess
    registro_log = Path(args.log or "proteo-%s.log" % args.comando).expanduser()
    argomenti = [a for a in sys.argv[1:] if a not in ("--sfondo", "--log")
                 and a != args.log]
    if "-y" not in argomenti and "--si" not in argomenti:
        argomenti.append("-y")

    with open(registro_log, "a", encoding="utf-8") as f:
        processo = subprocess.Popen(
            [sys.executable, "-m", "proteo.cli"] + argomenti,
            stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True)          # niente SIGHUP quando cade la ssh
    print("avviato in sottofondo: pid %d" % processo.pid)
    print("  log:     tail -f %s" % registro_log)
    print("  stato:   %s stato" % os.environ.get("PROTEO_PROG", "proteo"))
    print("\nPuoi chiudere la sessione: il processo continua.")
    return True


def cmd_ripristino(args):
    """Riallinea il registro dopo un ripristino del database da un backup.

    Il registro sta sul client e il restore non lo tocca: resta a dire
    'cifrata' su colonne tornate in chiaro. Con quel disallineamento `decifra`
    decifrerebbe valori veri, producendone altri formalmente validi e
    completamente sbagliati, in silenzio.
    """
    config, voce = _config(args)
    registro = Registro(_percorso(args, config, voce, "registro") or "registro")
    database = args.database or voce.get("etichetta") or "database"
    trattate = [v for v in registro.elenco(database)
                if v.get("stato") in (CIFRATA, AZZERATA)]
    if not trattate:
        print("il registro di %s non dichiara nessuna colonna trattata." % database)
        return

    print("il registro dice che queste colonne sono state trattate:")
    for v in trattate:
        print("  %-9s %s.%s  del %s"
              % (v["stato"], v["tabella"], v["colonna"], v.get("aggiornato")))
    print("\nSe il backup ripristinato e' ANTERIORE a questi trattamenti, nel "
          "database quei\nvalori sono tornati quelli veri e il registro va "
          "riportato a 'in chiaro'.\nSe e' POSTERIORE, i dati sono gia' "
          "trattati e il registro ha ragione: non\ntoccare niente, e usa "
          "'decifra' con la stessa chiave.")

    if not args.si:
        risposta = input("\nil backup e' anteriore, e vanno segnate 'in chiaro' "
                         "[y/n]: ")
        if risposta.strip().lower() not in SI:
            print("annullato: il registro resta com'e'.")
            return
    for v in trattate:
        registro.concludi(database, v["tabella"], v["colonna"], IN_CHIARO, None)
        print("  %s.%s -> in_chiaro" % (v["tabella"], v["colonna"]))


def cmd_stato(args):
    config, voce = _config(args)
    engine = _apri(args, config, voce)
    registro = Registro(_percorso(args, config, voce, "registro") or "registro")
    database = _nome_database(args, config, voce, engine)
    print("registro di %s:" % database)
    stampa.stato(registro.elenco(database), registro.interrotte(database))
    stampa.orfane(db.mappe_orfane(engine))


def cmd_verifica(args):
    problemi = _motore(args).verifica(args.verso)
    if not problemi:
        print("nessun problema: si puo' procedere con '%s'." % args.verso)
        return
    if stampa.problemi(problemi):
        raise SystemExit(1)


def cmd_anteprima(args):
    motore = _motore(args)
    stampa.anteprima(motore.anteprima(n=args.campione, verso=args.verso))
    if args.righe:
        stampa.anteprima_righe(motore.anteprima_righe(args.righe, args.verso),
                               args.verso)


def _esegui(args, verso):
    # `--righe` passato a mano si distingue dal suo valore predefinito: con
    # --si (uso da script) l'anteprima si stampa solo se qualcuno l'ha chiesta.
    args.righe_esplicite = any(a.startswith("--righe") for a in sys.argv)
    motore = _motore(args)

    if stampa.problemi(motore.verifica(verso)):
        raise Uscita("la verifica ha trovato errori bloccanti: nulla e' stato scritto.")

    solo = getattr(args, "solo_colonna", None)
    colonne = [(t, c, r["tipo"]) for t, c, r in motore.policy.colonne_da_cifrare()
               if solo is None or (t, c) == solo]
    if verso == "cifra":
        # elencate insieme e marcate: l'azzeramento e' l'unica cosa in questo
        # elenco che nessuna chiave annulla, e va vista prima di rispondere 'si'.
        colonne += [(t, c, "AZZERA, irreversibile")
                    for t, c, _ in motore.policy.colonne_da_azzerare()
                    if solo is None]
    print("\n%s: %d colonne su %s" % (verso, len(colonne), motore.database))
    for t, c, etichetta in colonne:
        print("  %s.%s (%s)" % (t, c, etichetta))

    # L'anteprima per righe mostra valori VERI accanto ai loro surrogati. Con
    # --si l'uscita finisce quasi sempre in un file di log, dove resterebbero
    # scritti in chiaro: li' si stampa solo se qualcuno l'ha chiesta a mano.
    if args.righe and (not args.si or args.righe_esplicite):
        stampa.anteprima_righe(motore.anteprima_righe(args.righe, verso), verso)

    if not args.si:
        # Nessun predefinito: l'invio non conferma un'operazione che scrive.
        risposta = input("\nprocedere? il database verra' modificato [y/n]: ")
        if risposta.strip().lower() not in SI:
            print("annullato: nulla e' stato scritto.")
            return

    # Dopo la conferma, non prima: chi delega al sottofondo ha gia' risposto.
    if getattr(args, "sfondo", False):
        _in_sottofondo(args)
        return

    try:
        rapporto = motore.esegui(verso, su_valore_non_trattabile=args.su_errore,
                                 riprendi=getattr(args, "voce_ripresa", None),
                                 solo=[solo] if solo else None,
                                 avanzamento=av.Avanzamento(
                                     registro=motore.registro,
                                     database=motore.database,
                                     diario=_DIARIO))
    except VerificaFallita as e:
        raise Uscita(str(e))
    except ValoreNonTrattabile as e:
        # Il menu offre la scelta a video; qui si dice il comando, che e' la
        # stessa decisione. Un traceback, invece, non e' ne' l'una ne' l'altra.
        raise Uscita(
            "fermato su un valore che non so trattare: %s\n"
            "  La colonna NON e' stata modificata.\n"
            "  Due strade: correggere quei valori nel database (la piu' "
            "pulita),\n"
            "  oppure rilanciare con --su-errore salta — ma quei valori "
            "RESTANO IN CHIARO,\n"
            "  cioe' quei record restano riconoscibili, e il rapporto finale li "
            "elenca." % e)

    print("\nfatto.")
    stampa.rapporto(rapporto)

    if args.rapporto:
        Path(args.rapporto).write_text(
            json.dumps(rapporto, indent=2, ensure_ascii=False), encoding="utf-8")
        print("\nrapporto in %s" % args.rapporto)


def cmd_riprendi(args):
    """Continua una cifratura interrotta, dall'ultima chiave committata."""
    motore = _motore(args)
    riprendibili = motore.riprendibili(args.verso)
    if not riprendibili:
        interrotte = motore.registro.interrotte(motore.database)
        if interrotte:
            raise Uscita(
                "ci sono %d colonne 'in_corso', ma nessuna e' riprendibile: "
                "l'esecuzione\nnon scriveva a lotti, quindi o e' passata tutta o "
                "e' tornata indietro. Usa 'risolvi'." % len(interrotte))
        print("niente da riprendere: nessuna esecuzione interrotta.")
        return

    voce = riprendibili[0]
    if len(riprendibili) > 1 and not (args.tabella and args.colonna):
        print("%d colonne riprendibili:" % len(riprendibili))
        for v in riprendibili:
            print("  %s.%s  ferma a %s = %s  (%s righe fatte)"
                  % (v["tabella"], v["colonna"], "chiave", v["ultima_chiave"],
                     v.get("righe", "?")))
        raise Uscita("scegline una con --tabella e --colonna")
    if args.tabella and args.colonna:
        scelte = [v for v in riprendibili
                  if (v["tabella"], v["colonna"]) == (args.tabella, args.colonna)]
        if not scelte:
            raise Uscita("%s.%s non e' riprendibile" % (args.tabella, args.colonna))
        voce = scelte[0]

    print("riprendo %s.%s dalla chiave %s (%s righe gia' fatte il %s)"
          % (voce["tabella"], voce["colonna"], voce["ultima_chiave"],
             voce.get("righe", "?"), voce.get("aggiornato")))
    args.voce_ripresa = voce
    args.solo_colonna = (voce["tabella"], voce["colonna"])
    _esegui(args, args.verso)


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
        s.add_argument("--diario", help="file del diario delle operazioni "
                                        "(default: accanto al config)")
        s.add_argument("--senza-diario", dest="senza_diario",
                       action="store_true", help="non scrivere il diario")
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

    s = comune("risolvi", "chiude a mano una colonna rimasta 'in_corso'")
    s.add_argument("--tabella")
    s.add_argument("--colonna")
    s.add_argument("--stato", choices=(IN_CHIARO, CIFRATA))
    s.add_argument("--si", "-y", action="store_true",
                   help="salta la conferma interattiva")
    s.set_defaults(func=cmd_risolvi)

    s = comune("ripristino",
               "riallinea il registro dopo un ripristino del database")
    s.add_argument("--si", "-y", action="store_true",
                   help="salta la conferma interattiva")
    s.set_defaults(func=cmd_ripristino)

    s = comune("pulisci", "elimina le tabelle di appoggio rimaste indietro")
    s.add_argument("--schema", help="limita a uno schema")
    s.add_argument("--si", "-y", action="store_true",
                   help="salta la conferma interattiva")
    s.set_defaults(func=cmd_pulisci)

    s = comune("verifica", "i cancelli fail-closed, senza scrivere", con_chiave=True)
    s.add_argument("--verso", choices=("cifra", "decifra"), default="cifra")
    s.set_defaults(func=cmd_verifica)

    s = comune("anteprima", "prima/dopo su un campione, senza scrivere",
               con_chiave=True)
    s.add_argument("--verso", choices=("cifra", "decifra"), default="cifra")
    s.add_argument("--campione", type=int, default=8,
                   help="valori distinti per colonna (default: 8)")
    s.add_argument("--righe", type=int, default=30, nargs="?", const=30,
                   help="prime N righe con prima/dopo (default: 30, 0 per "
                        "non mostrarle)")
    s.set_defaults(func=cmd_anteprima)

    s = comune("riprendi", "continua una cifratura interrotta", con_chiave=True)
    s.add_argument("--verso", choices=("cifra", "decifra"), default="cifra")
    s.add_argument("--tabella")
    s.add_argument("--colonna")
    s.add_argument("--si", "-y", action="store_true")
    s.add_argument("--sfondo", action="store_true",
                   help="stacca dal terminale: sopravvive alla sessione ssh")
    s.add_argument("--log", help="dove scrivere in sottofondo")
    s.add_argument("--su-errore", dest="su_errore",
                   choices=("ferma", "salta"), default="ferma")
    s.add_argument("--righe", type=int, default=0)
    s.add_argument("--rapporto")
    s.add_argument("--lotto-righe", dest="lotto_righe", type=int)
    s.set_defaults(func=cmd_riprendi)

    for nome, aiuto, funzione in (("cifra", "SCRIVE: applica la policy", cmd_cifra),
                                  ("decifra", "SCRIVE: riporta in chiaro", cmd_decifra)):
        s = comune(nome, aiuto, con_chiave=True)
        s.add_argument("--si", "-y", action="store_true",
                       help="salta la conferma interattiva")
        s.add_argument("--su-errore", dest="su_errore",
                       choices=("ferma", "salta"), default="ferma",
                       help="valore malformato: fermarsi (default) o saltarlo "
                            "lasciandolo IN CHIARO")
        s.add_argument("--lotto-righe", dest="lotto_righe", type=int,
                       help="scrive a lotti di N righe invece che in un'unica "
                            "transazione: meno lock, ma un'interruzione lascia "
                            "la colonna a meta'")
        s.add_argument("--righe", type=int, default=30, nargs="?", const=30,
                       help="prime N righe con prima/dopo prima di confermare "
                            "(default: 30, 0 per non mostrarle). Con --si sono "
                            "escluse per difetto: finirebbero in un log")
        s.add_argument("--sfondo", action="store_true",
                       help="stacca dal terminale: sopravvive alla sessione ssh, "
                            "e si segue con 'stato' o dal log")
        s.add_argument("--log", help="dove scrivere quando gira in sottofondo "
                                     "(default: ./proteo-cifra.log)")
        s.add_argument("--rapporto", help="scrive il rapporto JSON qui")
        s.set_defaults(func=funzione)

    return p


def _dove_sono_fermo():
    """`kill -USR1 <pid>` stampa dove sta il processo, thread per thread.

    Quando un'esecuzione sembra piantata la domanda e' sempre la stessa: sta
    aspettando il database o sta calcolando? Dall'esterno non si distingue, e
    senza distinguerla si cerca il difetto dalla parte sbagliata. Questo lo dice
    con certezza, e costa una riga: la traccia finisce nello stesso posto dove
    va tutto il resto — il terminale, o il log se si gira in sottofondo.
    """
    try:
        import faulthandler
        import signal
        faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
    except (ImportError, AttributeError, ValueError):
        pass          # su Windows SIGUSR1 non esiste: si perde la diagnosi, non altro


def main(argv=None):
    _dove_sono_fermo()
    args = _parser().parse_args(argv)
    try:
        try:
            args.func(args)
        except SystemExit:
            raise
        except BaseException as e:
            _DIARIO.errore(e, getattr(args, "comando", ""))
            raise
    except KeyboardInterrupt:
        # Un'interruzione durante `esegui` lascia la voce del registro in
        # 'in_corso': e' il segnale corretto, non un difetto. La transazione
        # invece torna indietro da sola — ma il rollback puo' durare quanto il
        # lavoro fatto, e in quel tempo il database tiene ancora i lock.
        _DIARIO.riga("interrotto dall'utente (Ctrl-C)")
        print("\ninterrotto. Il database sta annullando la transazione: puo' "
              "durare\nquanto il lavoro fatto finora, e fino ad allora la "
              "tabella resta bloccata.\nControlla poi 'stato' e 'pulisci'.",
              file=sys.stderr)
        return 130
    finally:
        _DIARIO.chiudi()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Il menu: Proteo guidato a schermo, per chi non vuole ricordare le opzioni.

Fa le stesse cose della riga di comando e nello stesso ordine — verifica,
anteprima, esecuzione — ma tenendo sotto gli occhi due informazioni che a
comando si perdono: **su quale database si sta lavorando** e **cosa risulta
gia' fatto**. Sono le due domande a cui si sbaglia risposta, e sbagliarle
significa cifrare il database sbagliato o cifrare due volte lo stesso.

Tre regole di condotta:

  * il motore viene ricostruito prima di ogni azione, non tenuto da parte: cosi'
    una policy corretta in un altro terminale vale subito, senza riavviare;
  * le azioni che scrivono non si confermano con un tasto ma scrivendo `si`.
    Da un menu numerato, un tasto di troppo e' esattamente il modo in cui si
    lancia il comando accanto a quello che si voleva;
  * cio' che non si puo' fare resta visibile e spiega perche', invece di sparire
    dall'elenco: una voce che manca sembra un difetto del programma.
"""

import getpass
from pathlib import Path

from sqlalchemy.engine import URL

from . import config as cfg
from . import avanzamento as av
from . import db, diagnosi, keyfile, rilevamento, repo, stampa
from .motore import Motore, VerificaFallita
from .policy import Policy, PolicyNonValida
from .registro import Registro
from .surrogati import ValoreNonTrattabile

__all__ = ["avvia"]

RIGA = "-" * 72

# (driver SQLAlchemy, come si chiama, porta consueta). La porta e' un default,
# non un vincolo: si puo' sempre scrivere altro.
MOTORI = [
    ("mssql+pyodbc",       "SQL Server",       1433),
    ("postgresql+psycopg", "PostgreSQL",       5432),
    ("mysql+pymysql",      "MySQL / MariaDB",  3306),
    ("sqlite",             "SQLite (file)",    None),
]

DRIVER_ODBC = "ODBC Driver 18 for SQL Server"


# --------------------------------------------------------------------------- #
# Domande
# --------------------------------------------------------------------------- #
def _chiedi(domanda, predefinito=None, obbligatorio=True):
    while True:
        suffisso = " [%s]" % predefinito if predefinito else ""
        risposta = input("%s%s: " % (domanda, suffisso)).strip()
        if risposta:
            return risposta
        if predefinito is not None:
            return predefinito
        if not obbligatorio:
            return None
        print("  serve una risposta.")


def _conferma(domanda):
    """Conferma esplicita: si scrive `si`, non si preme un tasto."""
    return input("%s [scrivi 'si']: " % domanda).strip().lower() in ("si", "sì")


def _pausa():
    input("\n(invio per tornare al menu) ")


def _scegli(titolo, voci):
    """voci = [(chiave, descrizione)]. Ritorna la chiave scelta."""
    print("\n%s" % titolo)
    for i, (_, descrizione) in enumerate(voci, 1):
        print("  %d) %s" % (i, descrizione))
    while True:
        r = input("\nscelta: ").strip()
        if r.isdigit() and 1 <= int(r) <= len(voci):
            return voci[int(r) - 1][0]
        print("  scrivi un numero da 1 a %d." % len(voci))


# --------------------------------------------------------------------------- #
# La connessione, un pezzo alla volta
# --------------------------------------------------------------------------- #
def _chiedi_host():
    """Host, con un controllo su un errore che si vede solo molto dopo.

    Un host di sole cifre e' quasi sempre la porta scritta una riga troppo in
    alto: l'URL che ne esce (`@1433/edw`) e' formalmente valido, quindi nessuno
    protesta finche' non si prova a connettersi — e a quel punto l'errore parla
    di host irraggiungibile, che manda a cercare firewall e DNS.
    """
    while True:
        host = _chiedi("host (nome o IP)")
        if not host.isdigit():
            return host
        print("  '%s' sembra un numero di porta, non un host.\n"
              "  Per un database sulla stessa macchina (anche in un container "
              "Docker): 127.0.0.1" % host)


def _chiedi_porta(consueta):
    while True:
        risposta = _chiedi("porta", str(consueta))
        try:
            return int(risposta)
        except ValueError:
            print("  la porta e' un numero (quella consueta e' %d)." % consueta)


def _componi_url():
    """Host, porta, utente, password — invece di un URL da scrivere a memoria.

    La stringa di connessione e' il punto in cui si perde piu' tempo: un URL
    sbagliato non da' un errore che parli di URL, ma un errore del driver. E le
    regole di scrittura sono insidiose — una password con `@` o `/` dentro
    spezza l'URL a meta'. `URL.create` la codifica da solo, ed e' l'unico motivo
    per cui questa funzione compone invece di far scrivere una riga.

    Ritorna (url, password): la password sta a parte perche' chi chiama deve
    poter decidere se salvarla o no.
    """
    driver = _scegli("Che database e'?",
                     [(d, "%s%s" % (nome, "   (porta %d)" % porta if porta else ""))
                      for d, nome, porta in MOTORI])
    porta_consueta = dict((d, p) for d, _, p in MOTORI)[driver]

    if driver == "sqlite":
        percorso = Path(_chiedi("percorso del file .db")).expanduser().resolve()
        return URL.create("sqlite", database=str(percorso)), None

    host = _chiedi_host()
    porta = _chiedi_porta(porta_consueta)
    database = _chiedi("nome del database")
    utente = _chiedi("utente")
    password = getpass.getpass("password (invio per chiederla a ogni avvio): ") or None

    query = {}
    if driver == "mssql+pyodbc":
        query["driver"] = _chiedi("driver ODBC", DRIVER_ODBC)
        query["Encrypt"] = "yes"
        # Sui server interni il certificato e' quasi sempre autofirmato, e senza
        # questa risposta la connessione fallisce con un errore che parla di
        # catena di certificati e non di configurazione.
        if _conferma("il certificato del server e' autofirmato o interno?"):
            query["TrustServerCertificate"] = "yes"
    elif driver == "postgresql+psycopg":
        if _conferma("pretendere una connessione cifrata (sslmode=require)?"):
            query["sslmode"] = "require"

    url = URL.create(driver, username=utente, password=password, host=host,
                     port=porta, database=database, query=query)
    return url.set(password=None) if password is None else url, password


def _prova(url):
    """Prova la connessione e racconta com'e' andata. True se ha funzionato."""
    for anomalia in db.anomalie_url(url):
        print("  attenzione: %s" % anomalia)
    print("\nprovo a connettermi a %s..." % url.render_as_string(hide_password=True))
    try:
        engine = db.crea_engine(url)
        versione = db.prova_connessione(engine)
        engine.dispose()
    except Exception as e:                              # noqa: BLE001
        # Qualunque cosa: il fallimento arriva dal driver, e i driver sollevano
        # di tutto — ImportError, OSError, eccezioni proprie. Quello che conta
        # e' che il menu resti in piedi e dica cosa fare.
        print("\nnon riesco a connettermi.\n  %s: %s" % (type(e).__name__, e))
        cosa_fare = diagnosi.suggerimento(e)
        if cosa_fare:
            print("\n  %s" % cosa_fare)
        return False
    print("connessione riuscita — %s" % versione)
    return True


def _chiedi_connessione():
    """Compone, prova, e non si arrende al primo errore."""
    while True:
        url, password = _componi_url()
        if _prova(url if password is None else url.set(password=password)):
            return url, password
        if not _conferma("\nvuoi correggere i dati e riprovare?"):
            print("i dati vengono salvati lo stesso: si correggono dal menu, "
                  "voce 'connessione'.")
            return url, password


# --------------------------------------------------------------------------- #
# Configurazione
# --------------------------------------------------------------------------- #
def _crea_config(percorso):
    """Prima configurazione, guidata. Nessun database viene toccato qui."""
    print("\nNon c'e' ancora una configurazione. La creo con qualche domanda.\n"
          "Nulla di quello che scrivi qui tocca il database: si salva e basta.\n")

    nome = _chiedi("nome breve per questo database (es. vendite)")
    url, password = _chiedi_connessione()
    voce = {"url": url.render_as_string(hide_password=False)}
    if password:
        voce["password"] = password

    base = Path.home() / ".proteo"
    voce["chiave"] = _chiedi("file di chiave", str(base / ("%s.key" % nome)))
    voce["policy"] = _chiedi("file di policy", str(base / ("%s-policy.json" % nome)))
    voce["registro"] = _chiedi("cartella del registro", str(base / "registro"))
    voce["etichetta"] = _chiedi("nome nel registro", nome)

    c = cfg.Config({"formato": cfg.FORMATO, "predefinito": nome,
                    "database": {nome: voce}})
    salvato = c.salva(percorso)
    print("\nconfigurazione salvata in %s" % salvato)
    _avvisa_se_committabile(salvato, password)
    return c


def _avvisa_se_committabile(percorso, password):
    """Un config con password che finirebbe in un commit non si rileggera'.

    L'avviso sta qui e non solo in `config.carica`: scoprirlo al riavvio
    successivo, quando non si ricorda piu' di aver risposto a queste domande,
    e' il momento peggiore per scoprirlo.
    """
    if not password:
        return
    print("contiene la password: permessi 0600.")
    if repo.dentro_un_repo_git(percorso) and not repo.ignorato_da_git(percorso):
        print("\nATTENZIONE: sta dentro un repository git e non e' escluso dai "
              "commit,\nquindi al prossimo avvio verra' rifiutato. Escludilo:\n"
              "    echo '%s' >> %s/.gitignore"
              % (percorso.name, percorso.parent))


def _config(percorso=None):
    p = Path(percorso) if percorso else cfg.percorso_predefinito()
    if p and p.is_file():
        return cfg.carica(p)
    predefinito = p or (Path.home() / ".proteo" / cfg.NOME_PREDEFINITO)
    return _crea_config(predefinito)


def _scegli_database(config):
    nomi = config.nomi()
    if not nomi:
        return None
    if len(nomi) == 1:
        return nomi[0]
    voci = [(n, "%s  (%s)" % (n, config.voce(n).get("url", "?"))) for n in nomi]
    return _scegli("Su quale database vuoi lavorare?", voci)


# --------------------------------------------------------------------------- #
# Costruzione del motore
# --------------------------------------------------------------------------- #
def _password_mancante(config, voce):
    """Password non salvata: si chiede qui, una volta per sessione."""
    url = config.url_completo(voce)
    if url.username and not url.password:
        return url.set(password=getpass.getpass(
            "password per %s@%s: " % (url.username, url.host)))
    return url


def _motore(config, nome, engine=None):
    """Motore pronto, o None con la spiegazione gia' stampata."""
    voce = config.voce(nome)
    chiave_p = config.risolvi(voce, "chiave")
    policy_p = config.risolvi(voce, "policy")

    try:
        chiave, kid = keyfile.carica(chiave_p)
    except FileNotFoundError:
        print("\nmanca il file di chiave: %s\n"
              "  Generalo dal menu ('genera la chiave') prima di procedere." % chiave_p)
        return None
    except keyfile.ChiaveNonValida as e:
        print("\nchiave non utilizzabile: %s" % e)
        return None

    try:
        policy = Policy.carica(policy_p)
    except FileNotFoundError:
        print("\nmanca il file di policy: %s\n"
              "  Creane una bozza dal menu ('crea la bozza di policy')." % policy_p)
        return None
    except (PolicyNonValida, ValueError) as e:
        print("\npolicy non leggibile: %s" % e)
        return None

    return Motore(engine, policy, chiave, kid,
                  Registro(config.risolvi(voce, "registro")),
                  voce.get("etichetta") or nome)


# --------------------------------------------------------------------------- #
# Azioni
# --------------------------------------------------------------------------- #
def _azione_stato(config, nome, engine):
    voce = config.voce(nome)
    registro = Registro(config.risolvi(voce, "registro"))
    etichetta = voce.get("etichetta") or nome
    print("\nregistro di %s:" % etichetta)
    stampa.stato(registro.elenco(etichetta), registro.interrotte(etichetta))


def _azione_verifica(config, nome, engine, verso="cifra"):
    m = _motore(config, nome, engine)
    if m is None:
        return
    elenco = m.verifica(verso)
    if not elenco:
        print("\nnessun problema: si puo' procedere con '%s'." % verso)
        return
    print("")
    if not stampa.problemi(elenco):
        print("\nnessun errore bloccante: si puo' procedere con '%s'." % verso)


def _azione_anteprima(config, nome, engine, verso="cifra"):
    m = _motore(config, nome, engine)
    if m is None:
        return
    print("\nnessuna scrittura: e' solo un campione.")
    stampa.anteprima(m.anteprima(verso=verso))


def _stato_colonna(m, tabella, colonna):
    regola = m.policy.regola(tabella, colonna) or {}
    strategia = regola.get("strategia") or "—"
    if strategia == "cifra":
        strategia = "cifra %s" % regola.get("tipo")
    return "%-14s registro: %s" % (strategia,
                                   m.registro.stato(m.database, tabella, colonna))


def _scegli_colonna(m, engine, tabella):
    """Elenco delle colonne con quello che serve per decidere: regola e stato."""
    schema = db.introspeziona(engine, [tabella])
    colonne = sorted(schema["tabelle"].get(tabella, {}))
    if not colonne:
        print("\n%s non ha colonne leggibili." % tabella)
        return None
    voci = [(c, "%-30s %s" % (c, _stato_colonna(m, tabella, c))) for c in colonne]
    voci.append((None, "torna indietro"))
    return _scegli("Quale colonna di %s?" % tabella, voci)


def _decidi_strategia(m, engine, tabella, colonna):
    """Chiede cosa fare della colonna, dopo aver guardato cosa contiene.

    Il campione viene mostrato prima della domanda: decidere la strategia di una
    colonna senza vederne i valori e' esattamente il modo in cui si cifra la
    colonna sbagliata.
    """
    valori = db.campiona(engine, tabella, colonna, 200)
    tipo, quanti, esaminati = rilevamento.analizza(valori)

    print("\n%s.%s — %d valori guardati" % (tabella, colonna, esaminati))
    for v in valori[:5]:
        print("    %s" % v)
    if tipo:
        print("\n  riconosciuto: %s (%d valori su %d passano il checksum)"
              % (tipo, quanti, esaminati))
    else:
        print("\n  nessun tipo riconosciuto: i valori non passano i checksum di "
              "CF, partita IVA o IBAN.")

    # Il tipo riconosciuto sta in cima, ma le altre scelte restano tutte a
    # video: una proposta che nasconde le alternative non e' una proposta.
    scelte = [("cifra:%s" % t, "cifra come %s%s"
               % (t, "   <- riconosciuto" if t == tipo else ""))
              for t in ([tipo] if tipo else []) + [x for x, _ in rilevamento.TIPI
                                                   if x != tipo]]
    scelte += [("mantieni", "lascia in chiaro (mantieni)"),
               ("azzera", "SVUOTA la colonna (azzera) — non torna indietro"),
               (None, "torna indietro")]
    return _scegli("Cosa faccio di %s.%s?" % (tabella, colonna), scelte)


def _applica_strategia(config, nome, m, tabella, colonna, scelta):
    """Scrive la scelta nella policy. Ritorna la regola, o None se annullata."""
    if scelta is None:
        return None
    if scelta.startswith("cifra:"):
        regola = {"strategia": "cifra", "tipo": scelta.split(":", 1)[1]}
    else:
        regola = {"strategia": scelta}

    m.policy.tabelle.setdefault(tabella, {})[colonna] = regola
    destinazione = config.risolvi(config.voce(nome), "policy")
    m.policy.salva(destinazione)
    print("\npolicy aggiornata: %s.%s -> %s" % (tabella, colonna, regola["strategia"]))
    return regola


def _cifra_una_colonna(config, nome, engine, verso):
    """Una colonna alla volta: si sceglie, si decide, si guarda, si scrive."""
    m = _motore(config, nome, engine)
    if m is None:
        return

    tabelle = sorted(set(db.elenco_tabelle(engine)) | set(m.policy.tabelle))
    voci = [(t, t) for t in tabelle] + [(None, "torna indietro")]
    tabella = _scegli("Su quale tabella?", voci)
    if tabella is None:
        return

    colonna = _scegli_colonna(m, engine, tabella)
    if colonna is None:
        return

    regola = m.policy.regola(tabella, colonna)
    if not regola:
        print("\n%s.%s non e' dichiarata nella policy." % (tabella, colonna))
        regola = _applica_strategia(config, nome, m, tabella, colonna,
                                    _decidi_strategia(m, engine, tabella, colonna))
        if regola is None:
            return
    elif _conferma("\n%s.%s e' dichiarata '%s'. Vuoi cambiarla?"
                   % (tabella, colonna, regola.get("strategia"))):
        nuova = _applica_strategia(config, nome, m, tabella, colonna,
                                   _decidi_strategia(m, engine, tabella, colonna))
        regola = nuova or regola

    if regola.get("strategia") == "mantieni":
        print("\n'mantieni': non c'e' niente da scrivere su questa colonna.")
        return

    # Il fail-closed vale comunque sull'intera policy: si scrive su una colonna,
    # ma non si parte se il documento nel suo insieme non sta in piedi.
    solo = [(tabella, colonna)]
    print("\ncontrollo prima di scrivere...")
    if stampa.problemi(m.verifica(verso, solo)):
        print("\nci sono errori bloccanti: nulla e' stato scritto.")
        return

    stampa.anteprima(m.anteprima(verso=verso, solo=solo))
    if not _conferma("\nprocedere su %s.%s? il database verra' modificato"
                     % (tabella, colonna)):
        print("annullato: nulla e' stato scritto.")
        return

    try:
        r = m.esegui(verso, solo=solo,
                     avanzamento=av.Avanzamento(registro=m.registro,
                                                database=m.database))
    except (VerificaFallita, ValoreNonTrattabile) as e:
        print("\nfermato prima di finire: %s" % e)
        return
    print("\nfatto.")
    stampa.rapporto(r)


def _azione_scrittura(config, nome, engine, verso):
    scelta = _scegli("Come vuoi procedere?", [
        ("una",   "una colonna alla volta (guidato)"),
        ("tutto", "tutto quello che la policy dichiara"),
        (None,    "torna al menu"),
    ])
    if scelta is None:
        return
    if scelta == "una":
        return _cifra_una_colonna(config, nome, engine, verso)

    m = _motore(config, nome, engine)
    if m is None:
        return

    print("\ncontrollo prima di scrivere...")
    if stampa.problemi(m.verifica(verso)):
        print("\nci sono errori bloccanti: nulla e' stato scritto.")
        return

    colonne = [(t, c, r["tipo"]) for t, c, r in m.policy.colonne_da_cifrare()]
    if verso == "cifra":
        colonne += [(t, c, "AZZERA, irreversibile")
                    for t, c, _ in m.policy.colonne_da_azzerare()]
    if not colonne:
        print("\nla policy non dichiara nessuna colonna da trattare.")
        return

    print("\nsto per %s su %s:" % (verso, m.database))
    for t, c, etichetta in colonne:
        print("  %s.%s (%s)" % (t, c, etichetta))
    if not _conferma("\nprocedere? il database verra' modificato"):
        print("annullato: nulla e' stato scritto.")
        return

    try:
        r = m.esegui(verso, avanzamento=av.Avanzamento(registro=m.registro,
                                                       database=m.database))
    except VerificaFallita as e:
        print("\nfermato prima di scrivere:\n%s" % e)
        return
    except ValoreNonTrattabile as e:
        # Il default e' 'ferma'. Il menu non offre 'salta': lascerebbe valori in
        # chiaro, ed e' una decisione che va presa a mente fredda modificando la
        # policy, non scegliendo una voce mentre si guarda un errore.
        print("\nfermato su un valore malformato: %s\n"
              "  La colonna non e' stata modificata. Correggi il valore nel "
              "database, oppure usa la riga di comando con --su-errore salta se "
              "vuoi lasciarlo IN CHIARO." % e)
        return

    print("\nfatto.")
    stampa.rapporto(r)


def _scegli_tabelle(engine):
    tabelle = db.elenco_tabelle(engine)
    print("\n%d tabelle nel database." % len(tabelle))
    if not _conferma("vuoi lavorare solo su alcune tabelle?"):
        return tabelle
    for t in tabelle:
        print("  %s" % t)
    print("\nScrivile separate da spazio. Vale anche un pezzo di nome: 'clienti' "
          "prende\ntutte quelle che lo contengono.")
    pezzi = _chiedi("tabelle").split()
    scelte = [t for t in tabelle
              if t in pezzi or any(p.lower() in t.lower() for p in pezzi)]
    if not scelte:
        print("  nessuna corrispondenza: le prendo tutte.")
        return tabelle
    for t in scelte:
        print("  scelta: %s" % t)
    return scelte


def _conferma_proposte(proposte):
    """Colonna per colonna, con i numeri sotto gli occhi. Ritorna le accettate."""
    quante = sum(len(c) for c in proposte.values())
    print("\nRiconosciute %d colonne guardando i valori (non i nomi):" % quante)
    for tabella in sorted(proposte):
        for colonna, (tipo, quanti, esaminati) in sorted(proposte[tabella].items()):
            print("  %-45s %-5s %d valori su %d passano il checksum"
                  % ("%s.%s" % (tabella, colonna), tipo, quanti, esaminati))

    scelta = _scegli("Cosa ne faccio?", [
        ("tutte",  "cifra tutte quelle riconosciute"),
        ("scelgo", "decido colonna per colonna"),
        ("niente", "nessuna: le metto tutte a 'mantieni' e scelgo dal file"),
    ])
    if scelta == "niente":
        return {}
    if scelta == "tutte":
        return proposte

    accettate = {}
    for tabella in sorted(proposte):
        for colonna, dati in sorted(proposte[tabella].items()):
            tipo, quanti, esaminati = dati
            if _conferma("  cifrare %s.%s come %s (%d/%d)?"
                         % (tabella, colonna, tipo, quanti, esaminati)):
                accettate.setdefault(tabella, {})[colonna] = dati
    return accettate


def _azione_policy(config, nome, engine):
    """Crea la policy, o la aggiorna senza perdere le scelte gia' fatte."""
    destinazione = config.risolvi(config.voce(nome), "policy")
    esistente = destinazione.exists()
    if esistente:
        print("\n%s esiste gia': le scelte gia' fatte restano, aggiungo solo le "
              "colonne\nche nel frattempo sono comparse nel database." % destinazione)
        policy = Policy.carica(destinazione)
        tabelle = sorted(policy.tabelle) or _scegli_tabelle(engine)
        if _conferma("vuoi aggiungere altre tabelle a quelle gia' nella policy?"):
            tabelle = sorted(set(tabelle) | set(_scegli_tabelle(engine)))
    else:
        policy = Policy()
        tabelle = _scegli_tabelle(engine)

    schema = db.introspeziona(engine, sorted(tabelle))
    nuove = [(t, c) for t in schema["tabelle"]
             for c in schema["tabelle"][t] if not policy.regola(t, c)]

    # Una policy gia' completa non ha colonne nuove, quindi non ci sarebbe nulla
    # da riconoscere: ma e' proprio il caso in cui serve, perche' tutte le
    # colonne sono nate 'mantieni' e nessuno le ha ancora guardate.
    rivedi = False
    if not nuove:
        print("\nnessuna colonna nuova: la policy e' gia' allineata allo schema.")
        rivedi = _conferma("vuoi che guardi i valori delle colonne dichiarate "
                           "'mantieni' e proponga cosa cifrare?")
        if not rivedi:
            return
    elif esistente:
        rivedi = _conferma("\noltre alle %d colonne nuove, vuoi che riguardi "
                           "anche quelle gia' dichiarate 'mantieni'?" % len(nuove))

    def da_decidere(tabella, colonna):
        regola = policy.regola(tabella, colonna)
        return regola is None or (rivedi and regola.get("strategia") == "mantieni")

    quante = sum(1 for t, colonne in schema["tabelle"].items()
                 for c in colonne if da_decidere(t, c))
    print("\n%d colonne da esaminare. Campiono i valori per riconoscerle..." % quante)
    proposte = rilevamento.proponi(
        lambda t, c: db.campiona(engine, t, c),
        {"tabelle": {t: {c: d for c, d in colonne.items() if da_decidere(t, c)}
                     for t, colonne in schema["tabelle"].items()}})

    accettate = _conferma_proposte(proposte) if proposte else {}
    if not proposte:
        print("\nnessuna colonna riconosciuta: nessun valore passa i checksum di "
              "CF, partita IVA o IBAN.")

    # Le accettate si scrivono qui e non in `aggiorna`, che per disegno non
    # tocca le colonne gia' dichiarate: qui invece la decisione e' appena stata
    # presa a video, e deve valere anche su una 'mantieni' preesistente.
    cambiate = []
    for tabella, colonne in accettate.items():
        for colonna, (tipo, _, _) in colonne.items():
            if policy.regola(tabella, colonna):
                policy.tabelle[tabella][colonna] = {"strategia": "cifra", "tipo": tipo}
                cambiate.append("%s.%s" % (tabella, colonna))

    aggiunte, tolte, fuori = policy.aggiorna(schema, accettate)
    policy.salva(destinazione)

    cifrate = [d for d, s in aggiunte if s == "cifra"]
    print("\npolicy %s: %s" % ("aggiornata" if esistente else "creata", destinazione))
    print("  %d colonne aggiunte, di cui %d da cifrare:" % (len(aggiunte), len(cifrate)))
    for d in cifrate:
        print("    %s" % d)
    if cambiate:
        print("  %d colonne passate da 'mantieni' a 'cifra':" % len(cambiate))
        for d in sorted(cambiate):
            print("    %s" % d)
    if tolte:
        # Una colonna rinominata si presenta cosi': una tolta e una aggiunta come
        # 'mantieni'. Non si puo' distinguere dallo schema, ma si puo' dire.
        print("\n  %d colonne non esistono piu' e sono state tolte:" % len(tolte))
        for d in tolte:
            print("    %s" % d)
        print("  Se sono state RINOMINATE, le nuove sono nate 'mantieni', "
              "cioe' in chiaro:\n  controllale prima di cifrare.")
    if fuori:
        print("\n  la policy nomina tabelle che nel database non ci sono: %s"
              % ", ".join(fuori))
    if schema["foreign_key"]:
        print("\nforeign key — i due lati devono ricevere lo stesso tweak:")
        for (t1, c1), (t2, c2) in schema["foreign_key"]:
            print("  %s.%s -> %s.%s" % (t1, c1, t2, c2))
    print("\nIl resto e' 'mantieni'. Per svuotare una colonna di testo libero "
          "metti\n\"strategia\": \"azzera\" nel file: quello va deciso a mano, "
          "perche' non torna indietro.")


def _azione_connessione(config, nome):
    """Prova la connessione, o la ricompone. True se i dati sono cambiati."""
    voce = config.voce(nome)
    scelta = _scegli("Connessione a %s" % (voce.get("etichetta") or nome), [
        ("prova", "provala com'e'"),
        ("rifai", "rifalla da capo (host, porta, utente, password)"),
        ("indietro", "torna al menu"),
    ])
    if scelta == "indietro":
        return False
    if scelta == "prova":
        _prova(_password_mancante(config, voce))
        return False

    url, password = _chiedi_connessione()
    voce = dict(voce, url=url.render_as_string(hide_password=False))
    voce.pop("password", None)
    if password:
        voce["password"] = password
    config.dati["database"][nome] = voce
    salvato = config.salva()
    print("\nconfigurazione aggiornata: %s" % salvato)
    _avvisa_se_committabile(salvato, password)
    return True


def _azione_chiave(config, nome, engine):
    percorso = config.risolvi(config.voce(nome), "chiave")
    print("\nLa chiave sostituisce il dizionario: e' l'unica cosa che riporta "
          "indietro i\nvalori. Perderla significa perdere i dati, "
          "definitivamente.")
    try:
        _, kid = keyfile.genera(percorso)
    except keyfile.ChiaveEsistente:
        print("\n%s esiste gia', e non si sovrascrive: cio' che e' stato cifrato "
              "con quella\nchiave diventerebbe illeggibile. Per rigenerarla, "
              "spostala a mano." % percorso)
        return
    except keyfile.ChiaveNonValida as e:
        print("\n%s" % e)
        return
    print("\nchiave creata: %s\nidentificativo: %s" % (percorso, kid))
    print("\nCopiala in un posto sicuro insieme alla cartella del registro: "
          "servono entrambi,\ne il registro non viaggia col database.")


# --------------------------------------------------------------------------- #
# Ciclo principale
# --------------------------------------------------------------------------- #
VOCI = [
    ("stato",       "stato del registro — cosa risulta gia' fatto"),
    ("verifica",    "verifica (non scrive)"),
    ("anteprima",   "anteprima prima/dopo (non scrive)"),
    ("cifra",       "CIFRA — scrive sul database"),
    ("decifra",     "DECIFRA — riporta in chiaro, scrive sul database"),
    ("policy",      "policy: creala o aggiornala (riconosce le colonne)"),
    ("chiave",      "genera la chiave"),
    ("connessione", "connessione: provala o rifalla"),
    ("cambia",      "cambia database"),
    ("esci",        "esci"),
]


def _intestazione(config, nome, voce):
    url = config.url_completo(voce)
    print("\n%s\nProteo — %s\n  %s\n  policy %s\n  registro %s\n%s"
          % (RIGA, voce.get("etichetta") or nome,
             url.render_as_string(hide_password=True),
             config.risolvi(voce, "policy"), config.risolvi(voce, "registro"), RIGA))


def avvia(percorso_config=None):
    try:
        config = _config(percorso_config)
    except cfg.ConfigNonValida as e:
        print("configurazione non utilizzabile:\n%s" % e)
        return 2

    nome = _scegli_database(config)
    if nome is None:
        print("la configurazione non contiene nessun database.")
        return 2

    engine, aperto_per = None, None
    try:
        while True:
            voce = config.voce(nome)
            _intestazione(config, nome, voce)
            scelta = _scegli("Cosa vuoi fare?", VOCI)

            if scelta == "esci":
                return 0
            if scelta == "cambia":
                nome = _scegli_database(config) or nome
                continue
            if scelta == "connessione":
                if _azione_connessione(config, nome):
                    if engine is not None:                # i dati sono cambiati:
                        engine.dispose()                  # la vecchia non vale piu'
                    engine, aperto_per = None, None
                _pausa()
                continue

            # La connessione si apre alla prima azione che ne ha bisogno e resta
            # aperta finche' si lavora sullo stesso database: aprirne una nuova a
            # ogni voce di menu significa una password chiesta ogni volta.
            if scelta != "stato" and (engine is None or aperto_per != nome):
                try:
                    engine = db.crea_engine(_password_mancante(config, voce))
                    db.prova_connessione(engine)
                except Exception as e:                    # noqa: BLE001
                    # Il driver puo' sollevare di tutto, ImportError compreso: un
                    # menu che muore qui costringe a ricominciare da capo, mentre
                    # la cosa da fare e' quasi sempre a portata di una riga.
                    print("\nnon riesco a connettermi.\n  %s: %s"
                          % (type(e).__name__, e))
                    cosa_fare = diagnosi.suggerimento(e)
                    if cosa_fare:
                        print("\n  %s" % cosa_fare)
                    print("\nDalla voce 'connessione' puoi provarla o rifarla.")
                    engine, aperto_per = None, None
                    _pausa()
                    continue
                aperto_per = nome

            azione = {
                "stato": _azione_stato,
                "verifica": _azione_verifica,
                "anteprima": _azione_anteprima,
                "policy": _azione_policy,
                "chiave": _azione_chiave,
                "cifra": lambda c, n, e: _azione_scrittura(c, n, e, "cifra"),
                "decifra": lambda c, n, e: _azione_scrittura(c, n, e, "decifra"),
            }[scelta]
            try:
                azione(config, nome, engine)
            except KeyboardInterrupt:
                # Un'interruzione durante una scrittura lascia la voce del
                # registro in 'in_corso': e' il segnale corretto. La transazione
                # invece torna indietro da sola.
                print("\n\ninterrotto. Controlla lo stato del registro: una "
                      "colonna rimasta 'in_corso' va risolta a mano.")
            except Exception as e:                      # noqa: BLE001
                # Un menu che muore su un errore del database costringe a
                # ricominciare da capo, password compresa.
                print("\nerrore: %s: %s" % (type(e).__name__, e))
            _pausa()
    except (EOFError, KeyboardInterrupt):
        print("\nuscita.")
        return 130
    finally:
        if engine is not None:
            engine.dispose()

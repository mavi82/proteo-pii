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

from . import config as cfg
from . import db, keyfile, stampa
from .motore import Motore, VerificaFallita
from .policy import Policy, PolicyNonValida
from .registro import Registro
from .surrogati import ValoreNonTrattabile

__all__ = ["avvia"]

RIGA = "-" * 72


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
# Configurazione
# --------------------------------------------------------------------------- #
def _crea_config(percorso):
    """Prima configurazione, guidata. Nessun database viene toccato qui."""
    print("\nNon c'e' ancora una configurazione. La creo con qualche domanda.\n"
          "Nulla di quello che scrivi qui tocca il database: si salva e basta.\n")

    nome = _chiedi("nome breve per questo database (es. vendite)")
    print("\nURL SQLAlchemy. Esempi:\n"
          "  postgresql+psycopg://utente@host:5432/vendite\n"
          "  mssql+pyodbc://utente@host:1433/VenditeDB"
          "?driver=ODBC+Driver+18+for+SQL+Server\n"
          "  mysql+pymysql://utente@host:3306/vendite\n")
    url = _chiedi("url")

    voce = {"url": url}
    print("\nLa password puo' stare nel config, che verra' creato a 0600 e solo\n"
          "fuori da un repository git. Se preferisci non scriverla, lasciala\n"
          "vuota: verra' chiesta a ogni avvio.")
    password = getpass.getpass("password (invio per non salvarla): ")
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
    if password:
        print("contiene la password: permessi 0600, e non spostarla dentro il repo.")
    return c


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


def _azione_scrittura(config, nome, engine, verso):
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
        r = m.esegui(verso, progresso=lambda d: print("  %s..." % d, flush=True))
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


def _azione_bozza_policy(config, nome, engine):
    voce = config.voce(nome)
    destinazione = config.risolvi(voce, "policy")
    if destinazione.exists():
        print("\n%s esiste gia'." % destinazione)
        if not _conferma("rigenerarla cancellerebbe le strategie gia' scelte. Procedo?"):
            return

    tabelle = db.elenco_tabelle(engine)
    print("\n%d tabelle nel database." % len(tabelle))
    if _conferma("limitare la policy ad alcune tabelle?"):
        for t in tabelle:
            print("  %s" % t)
        scelte = _chiedi("quali (separate da spazio)").split()
        tabelle = [t for t in tabelle if t in scelte] or tabelle

    schema = db.introspeziona(engine, sorted(tabelle))
    policy = Policy({t: {c: {"strategia": "mantieni"} for c in sorted(colonne)}
                     for t, colonne in schema["tabelle"].items()})
    policy.salva(destinazione)
    print("\nbozza scritta in %s (%d tabelle, %d colonne, tutte 'mantieni')"
          % (destinazione, len(policy.tabelle),
             sum(len(c) for c in policy.tabelle.values())))
    if schema["foreign_key"]:
        print("\nforeign key rilevate — i due lati devono ricevere lo stesso tweak:")
        for (t1, c1), (t2, c2) in schema["foreign_key"]:
            print("  %s.%s -> %s.%s" % (t1, c1, t2, c2))
    print("\nOra apri il file e metti {\"strategia\": \"cifra\", \"tipo\": \"CF\"}\n"
          "sulle colonne da trattare (tipi: CF, PIVA, IBAN; 'azzera' per svuotare).\n"
          "Poi torna qui: la policy viene riletta a ogni azione.")


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
    ("stato",     "stato del registro — cosa risulta gia' fatto"),
    ("verifica",  "verifica (non scrive)"),
    ("anteprima", "anteprima prima/dopo (non scrive)"),
    ("cifra",     "CIFRA — scrive sul database"),
    ("decifra",   "DECIFRA — riporta in chiaro, scrive sul database"),
    ("policy",    "crea la bozza di policy"),
    ("chiave",    "genera la chiave"),
    ("cambia",    "cambia database"),
    ("esci",      "esci"),
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

            # La connessione si apre alla prima azione che ne ha bisogno e resta
            # aperta finche' si lavora sullo stesso database: aprirne una nuova a
            # ogni voce di menu significa una password chiesta ogni volta.
            if scelta != "stato" and (engine is None or aperto_per != nome):
                if engine is not None:
                    engine.dispose()
                engine = db.crea_engine(_password_mancante(config, voce))
                aperto_per = nome

            azione = {
                "stato": _azione_stato,
                "verifica": _azione_verifica,
                "anteprima": _azione_anteprima,
                "policy": _azione_bozza_policy,
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

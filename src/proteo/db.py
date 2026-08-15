# -*- coding: utf-8 -*-
"""Adattatore ai database, sopra SQLAlchemy Core.

Un solo strato per SQL Server, PostgreSQL e MySQL: introspezione, lettura dei
valori distinti, applicazione della mappa. Cio' che resta specifico del motore
(scrittura massiva ottimizzata, clone del database) sta fuori di qui.

## Perche' si lavora sui valori DISTINTI, non sulle righe

Il surrogato e' una funzione deterministica del valore: due righe con lo stesso
codice fiscale danno per forza lo stesso risultato. Cifrare riga per riga
significherebbe ripetere lo stesso calcolo milioni di volte. Su una tabella da
100 milioni di righe con 2 milioni di codici fiscali distinti si fa 2 milioni di
volte il lavoro, non 100.

## Perche' un solo UPDATE con tabella di appoggio

L'idea ovvia — un UPDATE per valore, `SET c='nuovo' WHERE c='vecchio'` — e'
**sbagliata**, e in modo silenzioso. FF1 e' una permutazione, quindi le catene e
i cicli sono la norma: se A->B e B->C, il primo UPDATE porta le righe A in B, e
il secondo (B->C) le ripesca e le manda in C. Le righe che erano A finiscono in
C invece che in B, e nessuno se ne accorge finche' non si prova a tornare
indietro.

Si scrive quindi la mappa completa in una tabella di appoggio e si esegue **un
solo** UPDATE con sottoquery: ogni riga viene calcolata una volta sola, dal suo
valore originale.
"""

import secrets

from sqlalchemy import (Column, MetaData, Table, Unicode, create_engine,
                       delete, func, insert, inspect, select, text, update)
from sqlalchemy.engine import make_url

__all__ = ["crea_engine", "prova_connessione", "anomalie_url",
           "elenco_tabelle", "introspeziona", "conta", "leggi_distinti",
           "applica_mappa", "applica_mappa_a_lotti", "azzera", "dividi_nome",
           "mappe_orfane", "elimina_mappa"]

LOTTO_LETTURA = 50_000

# pyodbc alloca un buffer per riga e per colonna quando gli si chiede
# `fetchmany(N)`: con una NVARCHAR larga e N=50.000 sono centinaia di MB, e il
# processo sembra piantato mentre sta solo allocando. Trenta round-trip in piu'
# non si notano; un'allocazione da mezzo giga si'.
LOTTO_LETTURA_PYODBC = 1_000

LOTTO_SCRITTURA = 10_000

# FreeTDS non regge gli stessi lotti del driver Microsoft. Il sintomo, dal lato
# server, e':
#
#   Error: 4014, Severity: 20 — A fatal error occurred while reading the input
#   stream from the network. The session will be terminated.
#
# cioe' SQL Server chiude la sessione perche' non riesce piu' a leggere il flusso
# TDS che gli arriva; dal lato client si vede solo "Unexpected EOF from the
# server". Non e' un errore di SQL: e' una richiesta troppo grande per come
# FreeTDS la impacchetta.
LOTTO_SCRITTURA_FREETDS = 1_000

# Quante righe SQLAlchemy accorpa in una singola INSERT ... VALUES. Il valore
# predefinito (1.000) su due colonne da 512 caratteri produce una richiesta da
# un paio di MB, che FreeTDS spezza in centinaia di pacchetti: e' li' che il
# flusso si rompe. Duecento righe sono ~200 KB per istruzione.
PAGINA_INSERT_FREETDS = 200

# Le tabelle di appoggio si riconoscono dal nome: e' cio' che permette di
# ritrovarne una rimasta indietro dopo un processo ucciso.
PREFISSO_MAPPA = "_proteo_map_"

# Righe per lotto quando si scrive a lotti invece che in un'unica transazione.
LOTTO_RIGHE = 1000


class _Niente:
    """Avanzamento che non dice niente: evita un `if` a ogni lotto."""

    def fase(self, descrizione, contabile=False, totale=-1):
        pass

    def avanti(self, elaborati):
        pass


_NIENTE = _Niente()


def crea_engine(url, **opzioni):
    """Engine con le opzioni che servono a Proteo, per tutti i punti d'ingresso."""
    u = make_url(url) if isinstance(url, str) else url
    if u.drivername.startswith("mssql+pyodbc") and not _e_freetds(u):
        # senza questo pyodbc manda gli INSERT della tabella di appoggio uno per
        # uno: su un database remoto e' un round-trip di rete per valore distinto.
        #
        # Solo con il driver Microsoft, pero': `fast_executemany` passa gli
        # array di parametri di ODBC, che FreeTDS non implementa allo stesso
        # modo — e li' l'inserimento della mappa si pianta invece di fallire.
        opzioni.setdefault("fast_executemany", True)
    return create_engine(u, **opzioni)


def _e_freetds(url):
    return "freetds" in (url.query.get("driver") or "").lower()


def anomalie_url(url):
    """Cose sospette in un URL, prima ancora di provarlo.

    Un URL puo' essere formalmente valido e insensato: `//sa@1433/edw` mette il
    numero di porta al posto dell'host, e SQLAlchemy non ha motivo di
    protestare. L'errore arriva molto dopo, dal driver, e parla di host
    irraggiungibile — cioe' manda a cercare firewall e DNS invece che una riga
    scritta storta. Qui si guarda l'URL per quello che vuole dire.
    """
    if url.drivername.startswith("sqlite"):
        return []
    fuori = []
    if not url.host:
        fuori.append("manca l'host: senza, il driver non sa a chi rivolgersi")
    elif url.host.isdigit():
        fuori.append("l'host e' fatto di sole cifre (%s): sembra il numero di "
                     "porta finito nel campo sbagliato" % url.host)
    if not url.database:
        fuori.append("manca il nome del database")
    if not url.port:
        fuori.append("manca la porta: si usera' quella predefinita del driver")
    return fuori


def prova_connessione(engine):
    """Apre una connessione e la chiude. Ritorna la versione del server.

    `create_engine` non contatta nessuno: e' pigro, e un URL sbagliato sembra
    funzionare finche' non serve davvero — cioe' a meta' di un'operazione. Qui
    si paga subito il costo di scoprirlo.
    """
    with engine.connect() as conn:
        conn.execute(select(1))
    versione = getattr(engine.dialect, "server_version_info", None)
    return "%s %s" % (engine.dialect.name,
                      ".".join(str(x) for x in versione) if versione else "")


def dividi_nome(qualificato):
    """'dbo.clienti' -> ('dbo', 'clienti');  'clienti' -> (None, 'clienti')."""
    if "." in qualificato:
        s, t = qualificato.split(".", 1)
        return s, t
    return None, qualificato


def _qualifica(schema, nome):
    return "%s.%s" % (schema, nome) if schema else nome


# Una tabella riflessa una volta resta riflessa: la riflessione interroga le
# viste di sistema, e quelle sono la cosa che NON si puo' interrogare mentre e'
# aperta una transazione che ha appena fatto un CREATE TABLE.
_RIFLESSE = {}


def _tabella(engine, qualificato, metadata=None):
    """Descrizione della tabella, riflessa una sola volta per engine.

    ## Perche' la cache non e' un'ottimizzazione

    Su SQL Server un `CREATE TABLE` dentro una transazione aperta tiene dei lock
    sui metadati, e blocca le interrogazioni alle viste di sistema fatte da
    altre sessioni. La tabella di appoggio viene creata cosi', e il generatore
    che legge i valori distinti veniva consumato *dentro* quella transazione:
    se avesse dovuto riflettere la tabella in quel momento, avrebbe interrogato
    le viste di sistema da una seconda connessione — bloccata dalla prima, che a
    sua volta aspettava il generatore.

    Un'attesa circolare che nessuno dei due lati puo' sciogliere, e che a video
    appariva come "in attesa della prima risposta del database", per sempre.
    Riflettere una volta sola, prima, la rende impossibile.

    Su SQLite e PostgreSQL non si vedeva: le loro letture dei metadati non si
    bloccano allo stesso modo.
    """
    schema, nome = dividi_nome(qualificato)
    if metadata is not None:
        return Table(nome, metadata, autoload_with=engine, schema=schema)

    md = _RIFLESSE.setdefault(engine, MetaData())
    chiave = "%s.%s" % (schema, nome) if schema else nome
    if chiave not in md.tables:
        Table(nome, md, autoload_with=engine, schema=schema)
    return md.tables[chiave]


# --------------------------------------------------------------------------- #
# Introspezione
# --------------------------------------------------------------------------- #
def elenco_tabelle(engine, schema=None):
    insp = inspect(engine)
    return sorted(_qualifica(schema, t) for t in insp.get_table_names(schema=schema))


def introspeziona(engine, tabelle):
    """Descrizione dello schema nella forma attesa da `Policy.verifica`.

    Le foreign key vengono lette qui perche' sono cio' che permette di scoprire
    il difetto piu' insidioso del tweak-per-nome-colonna: due lati di una
    relazione che riceverebbero surrogati diversi, rompendo il JOIN in silenzio.
    """
    insp = inspect(engine)
    fuori = {t for t in tabelle}
    out = {"tabelle": {}, "foreign_key": [], "chiavi_primarie": {}}

    for qualificato in tabelle:
        schema, nome = dividi_nome(qualificato)
        out["tabelle"][qualificato] = {
            c["name"]: {"tipo": str(c["type"]), "nullable": bool(c["nullable"])}
            for c in insp.get_columns(nome, schema=schema)
        }
        pk = insp.get_pk_constraint(nome, schema=schema) or {}
        out["chiavi_primarie"][qualificato] = list(pk.get("constrained_columns") or [])

        for fk in insp.get_foreign_keys(nome, schema=schema):
            riferita = _qualifica(fk.get("referred_schema") or schema,
                                  fk["referred_table"])
            # Le FK verso tabelle non selezionate non servono: la policy non le
            # conosce e segnalarle produrrebbe solo rumore.
            if riferita not in fuori:
                continue
            for c1, c2 in zip(fk["constrained_columns"], fk["referred_columns"]):
                out["foreign_key"].append(((qualificato, c1), (riferita, c2)))

    out["foreign_key"].sort()
    return out


# --------------------------------------------------------------------------- #
# Lettura
# --------------------------------------------------------------------------- #
def conta(engine, tabella, colonna=None):
    """(righe, valori distinti non nulli). `colonna=None` -> solo le righe."""
    t = _tabella(engine, tabella)
    with engine.connect() as conn:
        righe = conn.execute(select(func.count()).select_from(t)).scalar_one()
        if colonna is None:
            return righe, None
        c = t.c[colonna]
        distinti = conn.execute(
            select(func.count(func.distinct(c))).where(c.is_not(None))).scalar_one()
    return righe, distinti


def prime_righe(engine, tabella, colonne, quante=30):
    """Le prime righe della tabella, solo per le colonne indicate.

    Senza `ORDER BY`: ordinare costerebbe quanto scandire la tabella, e qui
    servono righe *qualsiasi* purche' vere — l'anteprima e' un controllo a
    occhio, non un campione statistico.
    """
    t = _tabella(engine, tabella)
    scelte = [t.c[c] for c in colonne if c in t.c]
    if not scelte:
        return []
    with engine.connect() as conn:
        righe = conn.execute(select(*scelte).limit(quante))
        return [dict(zip([c.name for c in scelte], r)) for r in righe]


def campiona(engine, tabella, colonna, quanti=200):
    """Pochi valori non nulli, per capire cosa c'e' dentro la colonna.

    `LIMIT` senza `DISTINCT` e senza `ORDER BY`: qui non serve un campione
    rappresentativo, serve sapere se i valori passano un checksum. Su una
    colonna con milioni di valori distinti un `SELECT DISTINCT` per rispondere a
    questa domanda costerebbe piu' dell'intera cifratura.
    """
    t = _tabella(engine, tabella)
    c = t.c[colonna]
    with engine.connect() as conn:
        return [r[0] for r in
                conn.execute(select(c).where(c.is_not(None)).limit(quanti))]


def leggi_distinti(engine, tabella, colonna, lotto=LOTTO_LETTURA,
                   chiave=None, da=None):
    """Genera i valori distinti non nulli, a lotti, senza caricarli tutti.

    `stream_results` evita che il driver materializzi l'intero risultato: su una
    colonna con milioni di valori distinti la differenza e' fra qualche decina di
    MB e l'esaurimento della memoria.
    """
    t = _tabella(engine, tabella)
    c = t.c[colonna]
    if engine.dialect.driver == "pyodbc":
        lotto = min(lotto, LOTTO_LETTURA_PYODBC)
    stmt = select(c).where(c.is_not(None)).distinct()
    if chiave is not None and da is not None:
        # Riprendendo, la colonna e' MISTA: le righe fino a `da` contengono gia'
        # surrogati. Leggerle insieme alle altre significherebbe cifrare il
        # cifrato — la cosa che il registro esiste per impedire.
        stmt = stmt.where(t.c[chiave] > da)
    with engine.connect().execution_options(stream_results=True, yield_per=lotto) as conn:
        for blocco in conn.execute(stmt).partitions(lotto):
            yield [r[0] for r in blocco]


# --------------------------------------------------------------------------- #
# Scrittura
# --------------------------------------------------------------------------- #
def azzera(engine, tabella, colonna):
    """Mette a NULL la colonna. Ritorna le righe toccate.

    Non c'e' mappa e non c'e' ritorno: e' l'unica operazione di Proteo che
    distrugge i dati invece di trasformarli. Si toccano solo le righe non nulle,
    cosi' il conteggio dice quanti valori sono stati davvero eliminati e una
    seconda esecuzione riporta zero invece dell'intera tabella.
    """
    t = _tabella(engine, tabella)
    c = t.c[colonna]
    with engine.begin() as conn:
        return conn.execute(update(t).where(c.is_not(None))
                                     .values({colonna: None})).rowcount


# Lunghezza massima di una colonna che SQL Server accetta come chiave di indice
# (900 byte, cioe' 450 caratteri se sono a due byte). Oltre, niente chiave.
MAX_CHIAVE = 450


def _tabella_mappa(md, schema, tipo=None):
    """Tabella di appoggio, **con lo stesso tipo della colonna bersaglio**.

    Il tipo non e' un dettaglio di forma: la mappa viene confrontata con la
    colonna vera (`WHERE vecchio = Cognome`), e due tipi diversi si confrontano
    solo dopo una conversione implicita.

    Con `VARCHAR` contro `NVARCHAR` — il caso di ogni database SQL Server con
    colonne Unicode — la conversione passa dalla codepage della collazione, e
    tutto cio' che non ci sta dentro diventa `?`. Un cognome accentato scritto
    nella mappa come `Farn?` non e' uguale a `Farnè`, quindi quelle righe **non
    vengono aggiornate**: nessun errore, nessun avviso, semplicemente restano
    in chiaro. E' il tipo di guasto che si scopre contando le righe.

    Prendendo il tipo dalla colonna vera — lunghezza e collazione comprese — il
    confronto e' esatto per costruzione, e sparisce anche il rischio di
    troncare un valore piu' lungo di quanto la mappa si aspettava.
    """
    tipo = tipo if tipo is not None else Unicode(512)
    lunghezza = getattr(tipo, "length", None)
    # Una colonna senza lunghezza dichiarata (TEXT, NVARCHAR(MAX)) non puo'
    # fare da chiave: si rinuncia all'indice, non alla correttezza.
    indicizzabile = bool(lunghezza) and lunghezza <= MAX_CHIAVE
    return Table(
        PREFISSO_MAPPA + secrets.token_hex(6), md,
        Column("vecchio", tipo, primary_key=indicizzabile, nullable=False),
        Column("nuovo", tipo, nullable=False),
        schema=schema,
    )


def _riempi(conn, mappa, coppie, lotto, avanzamento):
    """Versa le coppie nella tabella di appoggio. Ritorna quante ne ha scritte."""
    scrivi = insert(mappa)
    if _e_freetds(conn.engine.url):
        lotto = min(lotto, LOTTO_SCRITTURA_FREETDS)
        scrivi = scrivi.execution_options(
            insertmanyvalues_page_size=PAGINA_INSERT_FREETDS)
    n_mappate, buffer = 0, []
    for vecchio, nuovo in coppie:
        buffer.append({"vecchio": vecchio, "nuovo": nuovo})
        if len(buffer) >= lotto:
            conn.execute(scrivi, buffer)
            n_mappate += len(buffer)
            buffer = []
            avanzamento.avanti(n_mappate)
    if buffer:
        conn.execute(scrivi, buffer)
        n_mappate += len(buffer)
    avanzamento.avanti(n_mappate)
    return n_mappate


def applica_mappa(engine, tabella, colonna, coppie, lotto=LOTTO_SCRITTURA,
                  avanzamento=None):
    """Applica {vecchio: nuovo} con un solo UPDATE. Ritorna le righe toccate.

    `coppie` e' un iterabile di (vecchio, nuovo). Il lavoro in tre passi:

      1. si crea una tabella di appoggio con la mappa completa;
      2. un UNICO update la applica via sottoquery, cosi' ogni riga si calcola
         dal proprio valore originale (vedi la nota in testa al modulo sul
         perche' un update per valore corromperebbe i dati);
      3. la tabella di appoggio viene sempre eliminata, anche in caso di errore.

    Tutto dentro una transazione: se qualcosa fallisce a meta', la colonna resta
    com'era invece di restare per meta' cifrata.

    `coppie` puo' essere un generatore: si consuma a lotti riempiendo la tabella
    di appoggio, cosi' la memoria resta limitata anche con milioni di valori
    distinti. L'UPDATE parte **solo dopo** che la mappa e' completa — applicarla
    a pezzi reintrodurrebbe l'effetto domino descritto sopra.

    `avanzamento` riceve gli eventi: e' l'unico posto da cui si possa dire a che
    punto siamo, perche' e' qui che il generatore viene davvero consumato.
    """
    avanzamento = avanzamento or _NIENTE
    # La tabella si riflette qui, fuori da ogni transazione e una volta sola:
    # dentro sarebbe un'attesa circolare senza uscita. Vedi `_tabella`.
    t = _tabella(engine, tabella)
    schema, _ = dividi_nome(tabella)
    mappa = _tabella_mappa(MetaData(), schema, t.c[colonna].type)

    # Tutto in UNA transazione, ed e' una scelta con un prezzo: le righe della
    # tabella restano bloccate dall'inizio del calcolo fino alla fine
    # dell'UPDATE, quindi su una tabella grande chi legge da un'altra sessione
    # aspetta. Il prezzo si paga perche' l'alternativa — riempire la tabella di
    # appoggio fuori dalla transazione — lascerebbe su disco la mappa in chiaro
    # ogni volta che il processo muore.
    fallita = False
    # Transazione governata a mano invece che con `engine.begin()`, per un
    # motivo solo: se la connessione muore, il ROLLBACK dell'uscita fallisce a
    # sua volta, e l'eccezione del rollback SOSTITUISCE quella originale. Si
    # resta con "Unexpected EOF from the server (SQLEndTran)" — che dice come e'
    # finita, mai perche' e' cominciata. Qui l'errore vero sopravvive sempre.
    conn = engine.connect()
    transazione = conn.begin()
    try:
        avanzamento.fase("scrivo la mappa nella tabella di appoggio",
                         contabile=True, totale=None)
        mappa.create(conn)
        try:
            n_mappate = _riempi(conn, mappa, coppie, lotto, avanzamento)
            if not n_mappate:
                return 0

            # Una sola istruzione, che tocca tutte le righe: da qui in poi non
            # c'e' piu' granularita' da mostrare, e puo' durare a lungo. Dirlo
            # e' l'unico modo perche' un'attesa lunga non sembri un blocco.
            # Da qui in poi non c'e' piu' niente da contare: una sola
            # istruzione, che pero' su una tabella grande e' il pezzo piu' lungo.
            avanzamento.fase("eseguo l'UPDATE sulle righe (istruzione unica)")
            c = t.c[colonna]
            sub = select(mappa.c.nuovo).where(mappa.c.vecchio == c).scalar_subquery()
            res = conn.execute(
                update(t).values({colonna: sub})
                         .where(c.in_(select(mappa.c.vecchio))))
            toccate = res.rowcount
        except BaseException:
            # BaseException e non Exception: un Ctrl-C arriva come
            # KeyboardInterrupt, ed e' proprio il caso da cui ci si vuole
            # difendere qui.
            fallita = True
            avanzamento.fase("annullo la transazione (rollback): puo' durare "
                             "quanto il lavoro fatto finora — NON uccidere il "
                             "processo, peggiorerebbe le cose")
            raise
        finally:
            # nella stessa transazione: la tabella di appoggio contiene la mappa
            # in chiaro fra valore vero e surrogato, ed e' l'unica cosa in tutto
            # il progetto che somigli a un dizionario. Non deve sopravvivere.
            #
            # Se pero' si sta gia' uscendo per un errore, questa pulizia non
            # deve poter sollevarne un secondo: mascherebbe quello vero, che e'
            # l'unico che dice cosa e' successo. Il rollback fa sparire la
            # tabella dove il DDL e' transazionale (SQL Server, PostgreSQL);
            # dove non lo e' — MySQL, e SQLite per come il driver gestisce le
            # transazioni implicite — resta questa drop. Se anche quella non
            # arriva, perche' il processo e' stato ucciso, la tabella si ritrova
            # con `mappe_orfane`.
            try:
                conn.execute(delete(mappa))
                mappa.drop(conn)
            except Exception:                               # noqa: BLE001
                if not fallita:
                    raise
        transazione.commit()
    except BaseException:
        try:
            transazione.rollback()
        except Exception:                                   # noqa: BLE001
            # La connessione e' gia' caduta: il server annullera' da solo. Non
            # deve coprire l'errore che ci ha portati fin qui.
            pass
        raise
    finally:
        conn.close()
    return toccate


def applica_mappa_a_lotti(engine, tabella, colonna, coppie, chiave,
                          lotto_righe=LOTTO_RIGHE, lotto=LOTTO_SCRITTURA,
                          avanzamento=None, su_lotto=None, da_chiave=None):
    """Come `applica_mappa`, ma scrivendo a lotti di righe. Meno lock, meno atomicita'.

    ## Perche' a lotti PER CHIAVE e non per valore

    Un lotto per valore ("prendi mille valori della mappa e applicali")
    ricadrebbe esattamente nell'errore che questo modulo esiste per evitare: se
    A->B e B->C, il lotto che porta le righe A in B le espone al lotto
    successivo, che le ripesca e le manda in C.

    A lotti di **chiave primaria** non succede: gli intervalli sono disgiunti e
    ordinati, quindi una riga aggiornata nel lotto 1 non ricade mai nel lotto 2.
    Il predicato `chiave > ultima AND chiave <= confine` seleziona per posizione,
    non per contenuto, ed e' cio' che rende l'operazione sicura. La mappa e'
    completa prima che il primo lotto parta, quindi ogni riga si calcola dal
    proprio valore originale come nella versione a transazione unica.

    ## Cosa si perde

    L'atomicita'. Un'interruzione lascia la colonna **a meta'**: le righe fino
    all'ultima chiave trattata sono cifrate, le altre no, e nessuna operazione
    automatica sarebbe corretta su entrambe le meta'. Per questo l'ultima chiave
    raggiunta viene passata a `su_lotto`, che la scrive nel registro: e' l'unico
    modo di sapere dove si era arrivati.

    E la tabella di appoggio vive fuori dalla transazione, quindi un processo
    ucciso la lascia su disco con dentro la mappa in chiaro. `mappe_orfane` la
    ritrova, `pulisci` la elimina.
    """
    avanzamento = avanzamento or _NIENTE
    # La tabella si riflette qui, fuori da ogni transazione e una volta sola:
    # dentro sarebbe un'attesa circolare senza uscita. Vedi `_tabella`.
    t = _tabella(engine, tabella)
    schema, _ = dividi_nome(tabella)
    mappa = _tabella_mappa(MetaData(), schema, t.c[colonna].type)

    with engine.begin() as conn:
        avanzamento.fase("scrivo la mappa nella tabella di appoggio",
                         contabile=True, totale=None)
        mappa.create(conn)
        n_mappate = _riempi(conn, mappa, coppie, lotto, avanzamento)
    if not n_mappate:
        with engine.begin() as conn:
            mappa.drop(conn)
        return 0

    pk = t.c[chiave]
    c = t.c[colonna]
    sub = select(mappa.c.nuovo).where(mappa.c.vecchio == c).scalar_subquery()
    # `da_chiave` = si riprende da li': le righe precedenti sono gia' trattate
    # e non vanno toccate una seconda volta.
    toccate, ultima = 0, da_chiave
    try:
        # denominatore = le righe della tabella: da qui in poi si contano
        # quelle, non piu' i valori distinti
        avanzamento.fase("scrivo le righe, a lotti di %d" % lotto_righe,
                         contabile=True, totale=None)
        while True:
            with engine.begin() as conn:
                # Il confine del lotto si legge dentro la stessa transazione
                # dell'UPDATE: leggerlo prima aprirebbe una finestra in cui
                # qualcun altro puo' inserire righe fra le due chiavi.
                confine = _confine(conn, pk, ultima, lotto_righe)
                if confine is None:
                    break
                dove = pk <= confine if ultima is None else \
                    (pk > ultima) & (pk <= confine)
                res = conn.execute(update(t).values({colonna: sub})
                                   .where(dove & c.in_(select(mappa.c.vecchio))))
                toccate += res.rowcount
                ultima = confine
            # fuori dalla transazione: il registro segna cio' che e' gia'
            # committato, mai cio' che potrebbe ancora tornare indietro
            if su_lotto:
                su_lotto(ultima, toccate)
            avanzamento.avanti(toccate)
    finally:
        try:
            with engine.begin() as conn:
                conn.execute(delete(mappa))
                mappa.drop(conn)
        except Exception:                                   # noqa: BLE001
            pass          # resta su disco: la trova `mappe_orfane`
    return toccate


def _confine(conn, pk, ultima, quante):
    """Ultima chiave del prossimo lotto, o None se non ci sono piu' righe."""
    q = select(pk).order_by(pk).limit(quante)
    if ultima is not None:
        q = q.where(pk > ultima)
    chiavi = [r[0] for r in conn.execute(q)]
    return chiavi[-1] if chiavi else None


def mappe_orfane(engine, schema=None):
    """Tabelle di appoggio rimaste indietro. Dovrebbero essere sempre zero.

    Una ne sopravvive solo se il processo e' stato ucciso mentre lavorava, su un
    motore dove il DDL non e' transazionale. Contiene la corrispondenza in
    chiaro fra valori veri e surrogati: e' la cosa piu' pericolosa che Proteo
    scriva, e va trovata invece che aspettare che qualcuno la noti.
    """
    return [t for t in elenco_tabelle(engine, schema)
            if dividi_nome(t)[1].startswith(PREFISSO_MAPPA)]


def elimina_mappa(engine, tabella):
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE %s" % tabella))

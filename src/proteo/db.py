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

from sqlalchemy import (Column, MetaData, String, Table, create_engine, delete,
                       func, insert, inspect, select, update)
from sqlalchemy.engine import make_url

__all__ = ["crea_engine", "prova_connessione", "anomalie_url",
           "elenco_tabelle", "introspeziona",
           "conta", "leggi_distinti", "applica_mappa", "azzera", "dividi_nome"]

LOTTO_LETTURA = 50_000
LOTTO_SCRITTURA = 10_000


def crea_engine(url, **opzioni):
    """Engine con le opzioni che servono a Proteo, per tutti i punti d'ingresso."""
    u = make_url(url) if isinstance(url, str) else url
    if u.drivername.startswith("mssql+pyodbc"):
        # senza questo pyodbc manda gli INSERT della tabella di appoggio uno per
        # uno: su un database remoto e' un round-trip di rete per valore distinto.
        opzioni.setdefault("fast_executemany", True)
    return create_engine(u, **opzioni)


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


def _tabella(engine, qualificato, metadata=None):
    schema, nome = dividi_nome(qualificato)
    return Table(nome, metadata or MetaData(), autoload_with=engine, schema=schema)


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


def leggi_distinti(engine, tabella, colonna, lotto=LOTTO_LETTURA):
    """Genera i valori distinti non nulli, a lotti, senza caricarli tutti.

    `stream_results` evita che il driver materializzi l'intero risultato: su una
    colonna con milioni di valori distinti la differenza e' fra qualche decina di
    MB e l'esaurimento della memoria.
    """
    t = _tabella(engine, tabella)
    c = t.c[colonna]
    stmt = select(c).where(c.is_not(None)).distinct()
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


def applica_mappa(engine, tabella, colonna, coppie, lotto=LOTTO_SCRITTURA):
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
    """
    md = MetaData()
    t = _tabella(engine, tabella, md)
    schema, _ = dividi_nome(tabella)
    # nome irripetibile: due esecuzioni in parallelo su colonne diverse non
    # devono contendersi la stessa tabella di appoggio
    mappa = Table(
        "_proteo_map_%s" % secrets.token_hex(6), md,
        Column("vecchio", String(512), primary_key=True),
        Column("nuovo", String(512), nullable=False),
        schema=schema,
    )

    with engine.begin() as conn:
        mappa.create(conn)
        try:
            n_mappate, buffer = 0, []
            for vecchio, nuovo in coppie:
                buffer.append({"vecchio": vecchio, "nuovo": nuovo})
                if len(buffer) >= lotto:
                    conn.execute(insert(mappa), buffer)
                    n_mappate += len(buffer)
                    buffer = []
            if buffer:
                conn.execute(insert(mappa), buffer)
                n_mappate += len(buffer)
            if not n_mappate:
                return 0

            c = t.c[colonna]
            sub = select(mappa.c.nuovo).where(mappa.c.vecchio == c).scalar_subquery()
            res = conn.execute(
                update(t).values({colonna: sub})
                         .where(c.in_(select(mappa.c.vecchio))))
            toccate = res.rowcount
        finally:
            # nella stessa transazione: la tabella di appoggio contiene la mappa
            # in chiaro fra valore vero e surrogato, ed e' l'unica cosa in tutto
            # il progetto che somigli a un dizionario. Non deve sopravvivere.
            conn.execute(delete(mappa))
            mappa.drop(conn)
    return toccate

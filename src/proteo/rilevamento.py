# -*- coding: utf-8 -*-
"""Cosa contiene una colonna, guardando qualche valore.

La policy fail-closed pretende una riga per ogni colonna. Su una tabella da
dieci colonne e' un fastidio; su un data warehouse da centinaia e' un lavoro che
nessuno fa bene fino in fondo, e una policy scritta male e' peggio di una policy
assente — da' l'impressione che qualcuno abbia guardato.

Quindi non si indovina dal *nome* della colonna ma dal **contenuto**: si legge
un campione e si guarda se i valori passano i checksum. `RSSMRA85H12F205Y` e'
riconoscibile per quello che e', si chiami la colonna `codice_fiscale`,
`cf_cli`, `taxid` o `campo7`. I nomi mentono e cambiano fra sistemi; il
checksum no.

## Cosa NON fa

Non decide: propone. La proposta va confermata a mano, colonna per colonna, e
questo modulo non scrive niente da nessuna parte. Un rilevatore che decidesse da
solo sposterebbe la responsabilita' della policy da chi conosce i dati a un
euristica — e la regola fail-closed esiste esattamente per impedirlo.

Il rapporto porta con se' i numeri (`13 valori su 14`) proprio perche' chi
conferma deve poter vedere quanto e' netta la proposta: `14 su 14` e' un fatto,
`8 su 14` e' una colonna mista che merita un'occhiata.
"""

from .checksum import cf_ok, iban_ok, piva_ok

__all__ = ["TIPI", "analizza", "proponi"]

# tipo -> come si riconosce. Sono gli stessi tipi che il Surrogatore sa trattare:
# proporne uno che non si sa cifrare produrrebbe una policy che non parte.
TIPI = (("CF", cf_ok), ("IBAN", iban_ok), ("PIVA", piva_ok))

# Quota di valori che devono passare il checksum perche' la colonna sia proposta.
# Non 100%: in una colonna vera ci sono righe sporche, e pretendere la perfezione
# significherebbe non proporre mai nulla proprio dove serve.
QUOTA = 0.9

# Sotto questa soglia non si propone: su pochi valori un checksum azzeccato per
# caso e' verosimile, e una proposta debole costa piu' di nessuna proposta.
MINIMO = 4


def analizza(valori):
    """(tipo, quanti_passano, quanti_esaminati). tipo None = niente da proporre.

    Si vince col tipo che copre piu' valori: partita IVA e IBAN non si
    confondono, ma una colonna puo' contenerne di due specie e la maggioranza
    deve decidere.
    """
    esaminati = [str(v).strip() for v in valori if v is not None and str(v).strip()]
    if len(esaminati) < MINIMO:
        return None, 0, len(esaminati)

    migliore, quanti = None, 0
    for tipo, valido in TIPI:
        n = sum(1 for v in esaminati if valido(v))
        if n > quanti:
            migliore, quanti = tipo, n

    if quanti < QUOTA * len(esaminati):
        return None, quanti, len(esaminati)
    return migliore, quanti, len(esaminati)


def proponi(campiona, schema, tabelle=None):
    """Proposte {tabella: {colonna: (tipo, quanti, esaminati)}}.

    `campiona(tabella, colonna)` e' passata da fuori invece di aprire una
    connessione qui: cosi' questo modulo resta provabile senza un database, come
    tutto il nucleo.
    """
    proposte = {}
    for tabella in (tabelle if tabelle is not None else schema["tabelle"]):
        colonne = schema["tabelle"].get(tabella, {})
        for colonna, descrizione in sorted(colonne.items()):
            if not _testuale(descrizione["tipo"]):
                continue
            tipo, quanti, esaminati = analizza(campiona(tabella, colonna))
            if tipo:
                proposte.setdefault(tabella, {})[colonna] = (tipo, quanti, esaminati)
    return proposte


def _testuale(tipo_sql):
    """Solo colonne di testo.

    Un surrogato con zeri iniziali scritto in una colonna numerica li perde, e il
    valore non torna piu' indietro: `motore.verifica` lo blocca comunque, ma
    proporlo qui vorrebbe dire proporre qualcosa che non partira' mai.
    """
    t = tipo_sql.upper()
    return any(x in t for x in ("CHAR", "TEXT", "STRING", "CLOB"))

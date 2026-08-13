# -*- coding: utf-8 -*-
"""Come si mostrano problemi, anteprime e rapporti.

Modulo a parte perche' i punti d'ingresso sono due — la riga di comando e il
menu — e devono dire le stesse cose con le stesse parole. Due copie dello stesso
testo divergono, e il giorno in cui divergono e' il giorno in cui una delle due
smette di nominare qualcosa di importante.
"""

__all__ = ["problemi", "anteprima", "rapporto", "stato", "orfane"]


def problemi(elenco):
    """Stampa i problemi, errori per primi. Ritorna quanti sono bloccanti."""
    for p in sorted(elenco, key=lambda x: (x.livello != "errore", x.dove)):
        print("  [%s] %s: %s" % (p.livello, p.dove, p.messaggio))
    return sum(1 for p in elenco if p.livello == "errore")


def anteprima(voci):
    for c in voci:
        etichetta = ("AZZERA — i valori spariscono" if c["operazione"] == "azzera"
                     else "%s, tweak=%s" % (c["tipo"], c["tweak"]))
        print("\n%s.%s  [%s]  %s righe, %s valori distinti"
              % (c["tabella"], c["colonna"], etichetta, c["righe"], c["distinti"]))
        for prima, dopo in c["campione"]:
            print("    %-32s -> %s" % (prima, "(NULL)" if dopo is None else dopo))
        for valore, motivo in c["non_trattabili"]:
            print("    %-32s !! %s" % (valore, motivo))
    if not voci:
        print("nessuna colonna da trattare: la policy non dichiara nessun "
              "'cifra' ne' 'azzera'.")


def rapporto(r):
    for c in r["colonne"]:
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


# Risposte accettate a una domanda y/n. Stanno qui e non nel menu perche' anche
# la riga di comando ne ha bisogno, e importare il menu per una conferma
# significherebbe caricare tutto l'interattivo dentro uno script.
SI = ("y", "yes", "s", "si", "sì")
NO = ("n", "no")

LARGHEZZA_CELLA = 20         # per meta' cella: prima, e dopo
LARGHEZZA_RIGA = 100         # oltre, il terminale va a capo e la tabella muore


def _taglia(valore, larghezza=LARGHEZZA_CELLA):
    if valore is None:
        return "(NULL)"
    testo = str(valore)
    return testo if len(testo) <= larghezza else testo[:larghezza - 1] + "…"


def _cella(riga, colonna):
    """Testo di una cella: 'prima → dopo', o il motivo per cui non si puo'."""
    prima, dopo, errore = riga["celle"][colonna]
    if prima is None:
        return "(NULL)"
    if errore:
        # Il motivo per esteso finisce sotto la tabella: qui dilaterebbe la
        # colonna e renderebbe illeggibile tutto il resto.
        return "%s  !! non trattabile" % _taglia(prima)
    return "%s → %s" % (_taglia(prima), _taglia(dopo))


def _blocchi(larghezze, fissa, massimo=LARGHEZZA_RIGA):
    """Spezza le colonne in gruppi che stiano in larghezza.

    Una tabella piu' larga del terminale viene mandata a capo dal terminale
    stesso, e a quel punto non si capisce piu' quale valore appartenga a quale
    colonna: meglio spezzarla noi, ripetendo la chiave, che e' cio' che tiene
    insieme i pezzi.
    """
    gruppi, corrente, totale = [], [], fissa
    for colonna, larghezza in larghezze:
        if corrente and totale + larghezza + 3 > massimo:
            gruppi.append(corrente)
            corrente, totale = [], fissa
        corrente.append(colonna)
        totale += larghezza + 3
    if corrente:
        gruppi.append(corrente)
    return gruppi


def anteprima_righe(voci, verso="cifra"):
    """Le prime righe, prima e dopo, una tabella alla volta.

    Colonne affiancate e non una riga per cella: l'errore che questa vista deve
    far vedere e' "ho puntato la colonna sbagliata", e lo si vede solo avendo
    sotto gli occhi il record intero, con la chiave accanto.
    """
    for voce in voci:
        print("\n%s — prime %d righe" % (voce["tabella"], len(voce["righe"])))
        if not voce["righe"]:
            print("  (nessuna riga)")
            continue

        chiave = voce["chiave"]
        # Larghezze dal contenuto vero: incolonnare su una larghezza fissa
        # sprecherebbe meta' schermo sulle colonne corte.
        larg_chiave = {c: max([len(c)] + [len(str(r["chiave"].get(c, "")))
                                          for r in voce["righe"]][:50])
                       for c in chiave}
        larghezze = [(c, max(len(c), max(len(_cella(r, c)) for r in voce["righe"])))
                     for c in voce["colonne"]]
        fissa = sum(larg_chiave.values()) + 3 * len(chiave)

        for gruppo in _blocchi(larghezze, fissa):
            larg = dict(larghezze)
            testate = ["%-*s" % (larg_chiave[c], c) for c in chiave]
            testate += ["%-*s" % (larg[c], c) for c in gruppo]
            riga_testata = "  " + " | ".join(testate)
            print(riga_testata)
            print("  " + "-" * (len(riga_testata) - 2))
            for r in voce["righe"]:
                celle = ["%-*s" % (larg_chiave[c], _taglia(r["chiave"].get(c), 12))
                         for c in chiave]
                celle += ["%-*s" % (larg[c], _cella(r, c)) for c in gruppo]
                print("  " + " | ".join(celle))
            print()

        scarti = {(c, r["celle"][c][2]) for r in voce["righe"]
                  for c in voce["colonne"] if r["celle"][c][2]}
        for colonna, motivo in sorted(scarti):
            print("  %s: %s" % (colonna, motivo))


def stato(voci, interrotte=()):
    if not voci:
        print("  nessuna colonna registrata: nulla e' mai stato trattato.")
        return
    for v in voci:
        # una colonna azzerata non ha ne' tipo ne' chiave: stamparne i None
        # farebbe pensare a un'informazione persa invece che mai esistita
        campi = " ".join("%s=%s" % (k, v.get(k)) for k in ("tipo", "tweak", "chiave_id")
                         if v.get(k) is not None)
        print("  %-9s %s.%s  %s  righe=%s  %s"
              % (v.get("stato"), v.get("tabella"), v.get("colonna"), campi,
                 v.get("righe", "-"), v.get("aggiornato")))
        riga = _avanzamento(v)
        if riga:
            print("            %s" % riga)
    if interrotte:
        print("\n  %d colonne in stato 'in_corso': o c'e' un'esecuzione che sta "
              "lavorando adesso (guarda l'ora qui sopra: se avanza, avanza), "
              "oppure ne e' rimasta a meta' e la colonna e' in uno stato misto."
              % len(interrotte))


def orfane(tabelle):
    """Tabelle di appoggio rimaste nel database. Zero, se tutto e' andato bene.

    Non e' un dettaglio di manutenzione: quelle tabelle contengono la
    corrispondenza in chiaro fra valori veri e surrogati, cioe' l'unica cosa in
    tutto il progetto che somigli a un dizionario. Va detto ad alta voce.
    """
    if not tabelle:
        return
    print("\n%d tabelle di appoggio presenti nel database:" % len(tabelle))
    for tabella in tabelle:
        print("  %s" % tabella)
    print("Se nessuna colonna qui sopra e' 'in_corso', sono avanzi di un "
          "processo ucciso, e\ncontengono la mappa IN CHIARO valore -> "
          "surrogato: eliminale con 'pulisci'.")


def _avanzamento(voce):
    """Riga di avanzamento per una colonna in corso, se ce n'e' una.

    E' cio' che rende `stato` utile da un'altra sessione: chi ha lanciato la
    cifratura con `nohup` e ha chiuso il terminale non ha altro modo di sapere
    se il processo sta ancora lavorando.
    """
    from .avanzamento import durata, quantita
    if voce.get("stato") != "in_corso" or voce.get("elaborati") is None:
        return None

    elaborati, distinti = voce["elaborati"], voce.get("distinti")
    pezzi = ["%s/%s valori" % (quantita(elaborati), quantita(distinti))]
    if distinti:
        pezzi.append("%d%%" % (100 * elaborati // max(distinti, 1)))
    iniziato = voce.get("iniziato")
    if iniziato:
        import time
        trascorso = time.time() - iniziato
        pezzi.append("da %s" % durata(trascorso))
        if distinti and elaborati and trascorso > 0:
            velocita = elaborati / trascorso
            pezzi.append("mancano ~%s" % durata((distinti - elaborati) / velocita))
    if voce.get("fase"):
        pezzi.append(voce["fase"])
    return "  ".join(pezzi)

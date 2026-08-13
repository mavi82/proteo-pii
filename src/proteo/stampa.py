# -*- coding: utf-8 -*-
"""Come si mostrano problemi, anteprime e rapporti.

Modulo a parte perche' i punti d'ingresso sono due — la riga di comando e il
menu — e devono dire le stesse cose con le stesse parole. Due copie dello stesso
testo divergono, e il giorno in cui divergono e' il giorno in cui una delle due
smette di nominare qualcosa di importante.
"""

__all__ = ["problemi", "anteprima", "rapporto", "stato"]


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

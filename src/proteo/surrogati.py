# -*- coding: utf-8 -*-
"""Dai numerali di FF1 ai dati veri: codice fiscale, partita IVA, IBAN.

Il principio e' sempre lo stesso, e vale per ogni identificativo dotato di
checksum: **il carattere di controllo non e' informazione**, e' una funzione
degli altri. Quindi lo si butta, si cifra il corpo con FF1, e lo si ricalcola
sul risultato. Cosi' il surrogato:

  * ha la stessa lunghezza e lo stesso alfabeto dell'originale (nessun ALTER);
  * passa il proprio validatore (`cf_ok`, `piva_ok`, `iban_ok`);
  * torna indietro con la sola chiave, senza alcun dizionario.

Dove la struttura conta si cifra **per componenti**, ciascuna dentro il proprio
dominio: cifrare un codice fiscale come sedici caratteri liberi darebbe un
codice con le cifre dell'anno al posto delle lettere del cognome — valido al
checksum, ma inguardabile e non interpretabile da un'applicazione che dal CF
estrae la data di nascita.

Le componenti piccole (il mese: 12 valori) non si possono cifrare da sole:
sotto i 100 elementi FF1 si rifiuta, e a ragione. Si raggruppano quindi in un
unico dominio (anno x mese x giorno x sesso = 74.400) e si cifra l'indice.
"""

from .checksum import cf_check, iban_check, piva_check
from .fpe import FF1

__all__ = ["Surrogatore", "ValoreNonTrattabile"]

MESI = "ABCDEHLMPRST"          # lettera del mese nel codice fiscale
_LETTERE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CIFRE = "0123456789"


class ValoreNonTrattabile(ValueError):
    """Il valore non ha la struttura attesa: il chiamante decide cosa farne.

    Non esiste un ripiego automatico. Se per un valore malformato cifrassimo il
    corpo "alla cieca", il risultato uscirebbe strutturalmente valido e in
    decifratura verrebbe interpretato con l'altro percorso: si otterrebbe un
    valore diverso dall'originale, in silenzio. Meglio fermarsi e lasciare che
    sia la policy a decidere (saltare la riga, azzerarla, segnalarla).
    """


def _fpe_int(ff1, x, dominio, tweak, avanti):
    """FF1 su un intero in [0, dominio) — con cycle-walking.

    FF1 permuta uno spazio di forma radix^n; quasi nessun dominio reale ha
    quella forma. Si cifra nello spazio piu' piccolo che lo contiene e si
    ri-applica finche' il risultato non ricade nel dominio valido. Termina
    perche' FF1 e' una permutazione: iterando si percorre un ciclo che per
    forza torna dentro. Ed e' simmetrico, quindi la decifratura fa lo stesso.
    """
    n = max(2, len(str(dominio - 1)))
    for _ in range(1000):
        numerali = [int(c) for c in str(x).zfill(n)]
        out = (ff1.encrypt(numerali, 10, tweak) if avanti
               else ff1.decrypt(numerali, 10, tweak))
        x = int("".join(map(str, out)))
        if x < dominio:
            return x
    raise RuntimeError("cycle-walking non converge: dominio %d" % dominio)


class Surrogatore:
    """Genera surrogati reversibili a partire da una chiave AES."""

    def __init__(self, key):
        self.ff1 = FF1(key)

    # -- codice fiscale ----------------------------------------------------- #
    # Struttura: 6 lettere (cognome+nome) | 2 cifre anno | 1 lettera mese |
    #            2 cifre giorno (+40 se femmina) | 1 lettera + 3 cifre comune |
    #            1 lettera di controllo
    def cf(self, valore, tweak, avanti=True):
        v = (valore or "").strip().upper()
        if len(v) != 16 or not v.isalnum():
            raise ValoreNonTrattabile("codice fiscale di lunghezza o alfabeto errati")

        nome, anno, mese, giorno = v[:6], v[6:8], v[8], v[9:11]
        comune_l, comune_n = v[11], v[12:15]

        if not nome.isalpha() or not anno.isdigit() or not giorno.isdigit() \
                or not comune_l.isalpha() or not comune_n.isdigit():
            # Caso tipico: CF *omocodico*, dove l'Agenzia sostituisce le cifre
            # con lettere per sciogliere una collisione. Trattarlo richiede di
            # de-omocodificare prima e di conservare il livello di omocodia:
            # non e' supportato in questa versione, e si segnala invece di
            # produrre un risultato non reversibile.
            raise ValoreNonTrattabile("struttura non standard (probabile omocodia)")
        if mese not in MESI:
            raise ValoreNonTrattabile("lettera del mese non valida: %r" % mese)

        g = int(giorno)
        sesso = 1 if g > 40 else 0
        g -= 40 * sesso
        if not 1 <= g <= 31:
            raise ValoreNonTrattabile("giorno fuori intervallo: %s" % giorno)

        # sei lettere: dominio 26^6, abbondantemente sopra il minimo
        nome2 = (self.ff1.encrypt_str(nome, _LETTERE, tweak + b"|nome") if avanti
                 else self.ff1.decrypt_str(nome, _LETTERE, tweak + b"|nome"))

        # data+sesso in un solo dominio: le componenti separate sarebbero troppo piccole
        idx = ((int(anno) * 12 + MESI.index(mese)) * 31 + (g - 1)) * 2 + sesso
        idx = _fpe_int(self.ff1, idx, 100 * 12 * 31 * 2, tweak + b"|data", avanti)
        sesso2 = idx % 2
        idx //= 2
        g2 = idx % 31 + 1
        idx //= 31
        mese2 = MESI[idx % 12]
        anno2 = idx // 12

        # comune: lettera + 3 cifre in un unico dominio (26.000)
        c = (ord(comune_l) - 65) * 1000 + int(comune_n)
        c = _fpe_int(self.ff1, c, 26 * 1000, tweak + b"|comune", avanti)

        corpo = "%s%02d%s%02d%s%03d" % (nome2, anno2, mese2, g2 + 40 * sesso2,
                                        chr(65 + c // 1000), c % 1000)
        return corpo + cf_check(corpo)

    # -- partita IVA -------------------------------------------------------- #
    def piva(self, valore, tweak, avanti=True):
        v = (valore or "").strip()
        if len(v) != 11 or not v.isdigit():
            raise ValoreNonTrattabile("partita IVA: attese 11 cifre")
        corpo = (self.ff1.encrypt_str(v[:10], _CIFRE, tweak) if avanti
                 else self.ff1.decrypt_str(v[:10], _CIFRE, tweak))
        return corpo + piva_check(corpo)

    # -- IBAN --------------------------------------------------------------- #
    # Il paese resta: cambiarlo altererebbe la lunghezza attesa e non e' un dato
    # personale. Le due cifre di controllo si ricalcolano. Il BBAN si cifra
    # preservando le CLASSI dei caratteri: le cifre restano cifre e le lettere
    # restano lettere, altrimenti un IBAN italiano uscirebbe con lettere dentro
    # l'ABI e sarebbe riconoscibile a occhio come falso.
    def iban(self, valore, tweak, avanti=True):
        import re
        v = re.sub(r"[\s.\-]", "", (valore or "")).upper()
        if len(v) < 15 or not v.isalnum() or not v[:2].isalpha() or not v[2:4].isdigit():
            raise ValoreNonTrattabile("IBAN di formato non riconoscibile")

        paese, bban = v[:2], list(v[4:])
        for classe, alfabeto in ((str.isdigit, _CIFRE), (str.isalpha, _LETTERE)):
            pos = [i for i, ch in enumerate(bban) if classe(ch)]
            # Un gruppo di un solo carattere ha dominio 10 o 26: sotto il minimo
            # di FF1, e resta com'e'. Nell'IBAN italiano e' il solo CIN, che e'
            # gia' un carattere derivato. LIMITE NOTO: non lo ricalcoliamo, quindi
            # il CIN del surrogato non corrisponde al nuovo BBAN (il mod-97
            # dell'IBAN torna comunque, un validatore CIN-aware no).
            if len(pos) < 2:
                continue
            s = "".join(bban[i] for i in pos)
            s2 = (self.ff1.encrypt_str(s, alfabeto, tweak + b"|" + alfabeto[:1].encode())
                  if avanti else
                  self.ff1.decrypt_str(s, alfabeto, tweak + b"|" + alfabeto[:1].encode()))
            for i, ch in zip(pos, s2):
                bban[i] = ch

        bban = "".join(bban)
        return paese + iban_check(paese, bban) + bban

    # -- dispatch ----------------------------------------------------------- #
    _TIPI = {"CF": cf, "PIVA": piva, "IBAN": iban}

    def cifra(self, tipo, valore, tweak):
        return self._TIPI[tipo](self, valore, tweak, True)

    def decifra(self, tipo, valore, tweak):
        return self._TIPI[tipo](self, valore, tweak, False)

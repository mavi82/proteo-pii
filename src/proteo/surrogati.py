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

# Le due classi che rendono una parola pronunciabile. Cifrando dentro ciascuna,
# lo scheletro consonante/vocale resta quello dell'originale — ed e' lo
# scheletro a far sembrare un nome un nome.
_VOCALI = "AEIOU"
_CONSONANTI = "BCDFGHJKLMNPQRSTVWXYZ"

# I gruppi che l'italiano ammette. Conservare solo l'alternanza
# consonante/vocale non basta: `Francesco` ha un gruppo `fr` legale, e
# scambiando le due consonanti a caso ne esce `nf`, che nessuno pronuncia.
# I gruppi con liquida si elencano, non si combinano: `br` e `tr` esistono,
# `dl` e `tl` no, e generarli rimetterebbe dentro proprio le sequenze che
# rendono la parola muta.
_GRUPPI_LIQUIDI = ("BL", "BR", "CL", "CR", "DR", "FL", "FR", "GL", "GR",
                   "PL", "PR", "TR", "VR")
_LIQUIDE = "LR"                       # anche prima di una consonante: Curci, Alberto
_OCCLUSIVE = "BCDGPT"                 # dopo una nasale (mb, nt) o una s (st, sp)
_NASALI = "MN"
_DIGRAMMI = ("CH", "GH")              # una consonante piu' h e' un suono solo

# Sotto queste combinazioni FF1 si rifiuta, e a ragione: un dominio piccolo si
# rimappa contando le occorrenze.
DOMINIO_MINIMO = 100


def _forma(parola, unita=None):
    """Cio' che il surrogato deve conservare per restare leggibile e reversibile.

    I tipi delle unita' — perche' se il surrogato si rileggesse con unita'
    diverse, la decifratura lo scomporrebbe in un altro modo e restituirebbe un
    valore diverso — e le vocali doppie, che l'italiano quasi non ha: `uu` da
    `ia` sarebbe corretto e illeggibile.
    """
    unita = unita if unita is not None else _unita(parola)
    doppie = [i for i in range(len(parola) - 1)
              if parola[i] == parola[i + 1] and parola[i] in _VOCALI]
    return ([tipo for tipo, _ in unita], doppie)


def _unita(parola):
    """Spezza una parola nei pezzi che si possono permutare senza renderla muta.

    Non lettere ma **unita' pronunciabili**: una vocale, una consonante, un
    raddoppio (`tt`), un gruppo con liquida (`fr`), una nasale piu' occlusiva
    (`mb`), una `s` piu' occlusiva (`st`). Ogni unita' viene poi permutata
    dentro l'insieme delle unita' dello stesso tipo, cosi' `fr` diventa `br` o
    `tl` — mai `nf`.

    L'ordine dei controlli conta: il raddoppio si riconosce per primo, perche'
    `ll` va letto come raddoppio e non come gruppo con liquida.
    """
    unita, i = [], 0
    while i < len(parola):
        c = parola[i]
        # `dopo` puo' essere vuoto, ed e' il caso da nominare: in Python
        # `"" in "BCDGPT"` e' vero, quindi l'ultima lettera di una parola
        # sembrerebbe l'inizio di un gruppo che non c'e'.
        dopo = parola[i + 1] if i + 1 < len(parola) else ""
        if c in _VOCALI:
            unita.append(("V", c))
        elif dopo and dopo == c and c in _CONSONANTI:
            unita.append(("GG", c + dopo))
        elif c + dopo in _GRUPPI_LIQUIDI:
            unita.append(("CL", c + dopo))
        elif c + dopo in _DIGRAMMI:
            unita.append(("DI", c + dopo))
        elif dopo and c in _NASALI and dopo in _OCCLUSIVE:
            unita.append(("NO", c + dopo))
        elif dopo and c == "S" and dopo in _OCCLUSIVE:
            unita.append(("SO", c + dopo))
        elif dopo and c in _LIQUIDE and dopo in _CONSONANTI:
            # Curci, Alberto: una liquida prima di una consonante e' legale, e
            # va tenuta insieme o le due consonanti si scambiano a caso.
            unita.append(("LC", c + dopo))
        else:
            unita.append(("C", c))
        i += len(unita[-1][1])
    return unita


# tipo -> (quante combinazioni, come si legge l'indice, come si riscrive)
_TIPI_UNITA = {
    "V":  (len(_VOCALI), lambda p: _VOCALI.index(p),
           lambda i: _VOCALI[i]),
    "C":  (len(_CONSONANTI), lambda p: _CONSONANTI.index(p),
           lambda i: _CONSONANTI[i]),
    "GG": (len(_CONSONANTI), lambda p: _CONSONANTI.index(p[0]),
           lambda i: _CONSONANTI[i] * 2),
    "CL": (len(_GRUPPI_LIQUIDI), lambda p: _GRUPPI_LIQUIDI.index(p),
           lambda i: _GRUPPI_LIQUIDI[i]),
    "DI": (len(_DIGRAMMI), lambda p: _DIGRAMMI.index(p),
           lambda i: _DIGRAMMI[i]),
    "LC": (len(_LIQUIDE) * len(_CONSONANTI),
           lambda p: _LIQUIDE.index(p[0]) * len(_CONSONANTI) + _CONSONANTI.index(p[1]),
           lambda i: _LIQUIDE[i // len(_CONSONANTI)] + _CONSONANTI[i % len(_CONSONANTI)]),
    "NO": (len(_NASALI) * len(_OCCLUSIVE),
           lambda p: _NASALI.index(p[0]) * len(_OCCLUSIVE) + _OCCLUSIVE.index(p[1]),
           lambda i: _NASALI[i // len(_OCCLUSIVE)] + _OCCLUSIVE[i % len(_OCCLUSIVE)]),
    "SO": (len(_OCCLUSIVE), lambda p: _OCCLUSIVE.index(p[1]),
           lambda i: "S" + _OCCLUSIVE[i]),
}


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


def _e_una_voce(lista, valore):
    """Il valore e' una voce della lista, scritta come la lista la scrive?

    Serve la stessa risposta in tre punti — quale percorso prendere in
    cifratura, quale in decifratura, e da quale insieme il cycle-walking deve
    uscire — e devono essere la stessa risposta, altrimenti i due percorsi si
    sovrappongono e un valore torna indietro diverso da com'era.

    La grafia conta, e per due motivi diversi:

      * `De  Luca` con due spazi troverebbe `DE LUCA` in lista, ma rientrerebbe
        con un solo spazio;
      * `Rosa maria` rientrerebbe come `Rosa Maria`, perche' la forma di una
        voce di lista si puo' ricostruire solo se e' tutta maiuscola, tutta
        minuscola o con le iniziali maiuscole — la lunghezza cambia, quindi non
        si puo' riportare il maiuscolo carattere per carattere.

    In entrambi i casi il valore vale come fuori lista e passa dal ripiego, che
    la forma la conserva esattamente. Un surrogato plausibile in meno, un valore
    che torna indietro diverso da com'era in meno.
    """
    from .liste import normalizza
    canonica = valore in (valore.upper(), valore.lower(), valore.title())
    return canonica and normalizza(valore) == valore.upper() and \
        lista.posizione(valore) is not None


def _stessa_forma(originale, surrogato):
    """Riporta sul surrogato lo stile di scrittura dell'originale.

    `MARIO -> FABRIZIO`, `Mario -> Fabrizio`, `mario -> fabrizio`. La lunghezza
    invece **non** si conserva: il surrogato e' una voce della lista, e le voci
    hanno la lunghezza che hanno. E' il prezzo di un surrogato leggibile, e per
    questo `motore.verifica` controlla prima che le voci piu' lunghe entrino
    nella colonna.
    """
    if originale.isupper():
        return surrogato.upper()
    if originale.islower():
        return surrogato.lower()
    # `title()` capitalizza anche dopo l'apostrofo: D'ANGELO -> D'Angelo, che
    # e' come si scrivono davvero.
    return surrogato.title()


class Surrogatore:
    """Genera surrogati reversibili a partire da una chiave AES."""

    def __init__(self, key, liste=None):
        self.ff1 = FF1(key)
        # Le liste si caricano alla prima richiesta: chi cifra solo codici
        # fiscali non deve pagarne la lettura, e i test possono passarne di
        # proprie senza toccare i file del pacchetto.
        self._liste = dict(liste or {})

    def lista(self, quale):
        if quale not in self._liste:
            from . import liste                    # importato qui: il nucleo
            self._liste[quale] = liste.carica(quale)   # non dipende dai file
        return self._liste[quale]

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

    # -- nomi e cognomi ----------------------------------------------------- #
    # Qui non c'e' checksum ne' struttura da preservare: c'e' una LISTA, e si
    # cifra la posizione dentro la lista. Il surrogato di un nome e' un altro
    # nome, cosi' la colonna resta leggibile e un report continua a sembrare un
    # report. Vedi `liste.py` per il perche' non sia il dizionario che il
    # progetto rifiuta.
    def _da_lista(self, quale, valore, tweak, avanti):
        lista = self.lista(quale)
        v = (valore or "").strip()
        if not v:
            raise ValoreNonTrattabile("valore vuoto")

        if _e_una_voce(lista, v):
            posizione = lista.posizione(v)
            nuova = _fpe_int(self.ff1, posizione, len(lista),
                             tweak + b"|" + quale.encode(), avanti)
            return _stessa_forma(v, lista.voce(nuova))
        return self._fuori_lista(lista, v, tweak + b"|" + quale.encode(), avanti)

    def _fuori_lista(self, lista, valore, tweak, avanti):
        """Chi non e' in lista si cifra lettera per lettera, non si rifiuta.

        Una lista non potra' mai contenere tutti i nomi veri di un database:
        nomi stranieri, doppi nomi, `Nome-paz2` messo li' da un collaudo. Prima
        ci si fermava, e su una colonna vera significava fermarsi su decine di
        valori — cioe' non trattarla affatto.

        Il ripiego conserva la forma (lunghezza, spazi, trattini, maiuscole) e
        cifra solo le lettere: il risultato non e' un nome plausibile, ma e'
        reversibile, deterministico e biiettivo come tutto il resto.

        **Il cycle-walking non e' un dettaglio.** Il risultato viene ricifrato
        finche' non cade fuori dalla lista: se un valore fuori lista producesse
        per caso un nome di lista, in decifratura verrebbe preso per l'altro
        percorso e restituirebbe un valore diverso dall'originale, in silenzio.
        Escludere la lista da entrambi i lati rende i due percorsi disgiunti, e
        l'operazione simmetrica: la decifratura cammina allo stesso modo.
        """
        for _ in range(1000):
            valore = self._mescola(valore, tweak, avanti)
            if not _e_una_voce(lista, valore):
                return valore
        raise RuntimeError("cycle-walking non converge fuori dalla lista")

    def _mescola(self, valore, tweak, avanti):
        """Cifra il nome conservandone lo **scheletro**: vocali e consonanti al
        loro posto, cifre al loro posto, tutto il resto dov'e'.

        ## Perche' non basta cifrare le lettere

        La prima versione cifrava le lettere come un blocco unico, sull'alfabeto
        intero. Il risultato era corretto — reversibile, biiettivo — e
        inservibile: `Acciaro` diventava `Jkjnpmg`, cioe' una sequenza che
        nessuna lingua potrebbe pronunciare. Su una colonna di cognomi, dove i
        valori fuori lista sono la maggioranza, il database smetteva di
        somigliare a un database di persone: e' esattamente il difetto che
        questo progetto rimprovera alla cifratura tradizionale.

        Una parola pero' non e' una sequenza di lettere qualunque: e' fatta di
        pezzi pronunciabili — una vocale, una consonante, un raddoppio (`tt`),
        un gruppo con liquida (`fr`), una nasale piu' occlusiva (`mb`), una `s`
        piu' occlusiva (`st`). Cifrando **dentro ciascun tipo** — un raddoppio
        diventa un raddoppio, un gruppo con liquida un altro gruppo con liquida
        — la parola resta una parola:

            Acciaro   ->  Ebbiaso        Francesco  ->  Prantosco
            Zambetta  ->  Vondelta       Della sala ->  Nesso pera

        Conservare solo l'alternanza consonante/vocale non basterebbe: da
        `Francesco` uscirebbe `Nfuwxibzu`, con un `nf` che nessuno pronuncia.

        E' lo stesso principio gia' usato per l'IBAN, dove le cifre restano
        cifre perche' un IBAN con lettere dentro l'ABI si riconoscerebbe come
        falso a colpo d'occhio. Qui la classe non e' cifra/lettera ma
        vocale/consonante, e cio' che si preserva non e' un formato ma la
        pronunciabilita'.

        ## Perche' un dominio unico e non due

        Vocali e consonanti si potrebbero cifrare separatamente, ma una parola
        con due sole vocali darebbe un dominio di 25 valori — sotto il minimo, e
        quelle vocali resterebbero in chiaro. Si costruisce quindi **un solo
        numero** in base mista (5 per le vocali, 21 per le consonanti), lo si
        permuta, e lo si riscrive nello stesso scheletro: il dominio e' il
        prodotto di tutte le posizioni, e diventa piccolo solo per parole di due
        lettere o meno.
        """
        fuori = list(valore)

        # cifre: gruppo a parte, come prima. `Nome-paz2` deve restare distinto
        # da `Nome-paz3`.
        pos_cifre = [i for i, ch in enumerate(valore) if ch in _CIFRE]
        if len(pos_cifre) >= 2:
            s = "".join(valore[i] for i in pos_cifre)
            etichetta = tweak + b"|fuori|cifre"
            s2 = (self.ff1.encrypt_str(s, _CIFRE, etichetta) if avanti
                  else self.ff1.decrypt_str(s, _CIFRE, etichetta))
            for i, ch in zip(pos_cifre, s2):
                fuori[i] = ch

        pos = [i for i, ch in enumerate(valore) if ch.upper() in _LETTERE]
        parola = "".join(valore[i].upper() for i in pos)
        unita = _unita(parola)
        domini = [_TIPI_UNITA[tipo][0] for tipo, _ in unita]
        dominio = 1
        for d in domini:
            dominio *= d

        if dominio < DOMINIO_MINIMO:
            nuovo = "".join(fuori)
            if nuovo == valore:
                raise ValoreNonTrattabile(
                    "%r e' troppo corto per essere cifrato: le sue lettere danno "
                    "%d combinazioni, sotto il minimo di %d"
                    % (valore, dominio, DOMINIO_MINIMO))
            return nuovo

        indice = 0
        for (tipo, pezzo), d in zip(unita, domini):
            indice = indice * d + _TIPI_UNITA[tipo][1](pezzo)

        # Cycle-walking sulla FORMA: si ricifra finche' il risultato non si
        # rilegge con le stesse unita' dell'originale. Serve perche' due
        # consonanti singole vicine potrebbero uscire uguali — cioe' come un
        # raddoppio — e in decifratura verrebbero lette come un'unita' sola,
        # restituendo un valore diverso. Camminare finche' la forma coincide
        # rende i due percorsi simmetrici.
        forma = _forma(parola, unita)
        for _ in range(100):
            indice = _fpe_int(self.ff1, indice, dominio, tweak + b"|unita", avanti)
            resto, lettere = indice, []
            for (tipo, _), d in zip(reversed(unita), reversed(domini)):
                resto, quoziente = divmod(resto, d)
                lettere.append(_TIPI_UNITA[tipo][2](quoziente))
            parola2 = "".join(reversed(lettere))
            if _forma(parola2) == forma:
                break
        else:
            raise RuntimeError("cycle-walking sulla forma non converge")

        for i, ch in zip(pos, parola2):
            fuori[i] = ch if valore[i].isupper() else ch.lower()
        return "".join(fuori)

    def nome(self, valore, tweak, avanti=True):
        return self._da_lista("nomi", valore, tweak, avanti)

    def cognome(self, valore, tweak, avanti=True):
        return self._da_lista("cognomi", valore, tweak, avanti)

    # -- dispatch ----------------------------------------------------------- #
    _TIPI = {"CF": cf, "PIVA": piva, "IBAN": iban,
             "NOME": nome, "COGNOME": cognome}

    # tipi che dipendono da una lista: il chiamante deve sapere quali, per
    # registrarne l'impronta e accorgersi se la lista cambia sotto i piedi
    LISTE = {"NOME": "nomi", "COGNOME": "cognomi"}

    # Versione del ripiego per i valori fuori lista. Cambia quando cambia il
    # modo di cifrarli, e viaggia nel registro insieme all'impronta della
    # lista: cio' che e' stato cifrato con la versione 1 non si decifra con la
    # 2, e deve fermarsi invece di restituire un valore diverso dall'originale.
    #
    #   1  lettere cifrate come un blocco unico sull'alfabeto intero
    #   2  scheletro consonante/vocale conservato (vedi `_mescola`)
    VERSIONE_RIPIEGO = 2

    def cifra(self, tipo, valore, tweak):
        return self._TIPI[tipo](self, valore, tweak, True)

    def decifra(self, tipo, valore, tweak):
        return self._TIPI[tipo](self, valore, tweak, False)

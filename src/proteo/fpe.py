# -*- coding: utf-8 -*-
"""FF1 — cifratura che preserva il formato (NIST SP 800-38G).

Perche' FF1 e non AES normale: il valore cifrato deve stare *dentro la colonna
che occupava prima*. Un codice fiscale cifrato con AES sono ~70 caratteri di
base64 e non entra in un CHAR(16); cifrato con FF1 sono 16 caratteri dello
stesso alfabeto. Questo e' cio' che permette di aggiornare il database sul
posto, senza ALTER COLUMN e senza toccare i tipi.

Le tre proprieta' su cui si regge tutto il progetto:

  * **Reversibile con la sola chiave.** Nessun dizionario, nessun vault, nessuna
    tabella che cresce con i dati. Su un DB grande una mappa valore->surrogato
    sarebbe grande quanto i dati stessi.
  * **Deterministica.** Lo stesso valore produce sempre lo stesso surrogato, in
    ogni tabella e in ogni esecuzione: i JOIN reggono senza coordinamento fra
    processi, e un job interrotto riparte senza sapere cosa aveva gia' fatto.
  * **Biiettiva.** Valori distinti danno surrogati distinti (FF1 e' una
    permutazione del dominio), quindi vincoli UNIQUE e chiavi esterne
    sopravvivono all'anonimizzazione senza collisioni.

Questo modulo non sa nulla di database ne' di codici fiscali: lavora su stringhe
di numerali. Si testa in isolamento, ed e' il pezzo dove un errore costa tutto —
per questo e' validato contro i vettori di test ufficiali del NIST
(`tests/test_ff1_nist.py`), non solo contro un round-trip che tornerebbe
"corretto" anche con un'implementazione sbagliata in modo simmetrico.
"""

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

__all__ = ["FF1", "InvalidDomain"]

_ROUNDS = 10          # fissato dallo standard, non e' un parametro di sicurezza da tarare
_BLOCK = 16


class InvalidDomain(ValueError):
    """Il dominio e' troppo piccolo perche' la cifratura abbia senso."""


def _num(numerals, radix):
    """Valore intero della stringa di numerali (cifra piu' significativa per prima)."""
    x = 0
    for d in numerals:
        x = x * radix + d
    return x


def _numerals(x, radix, length):
    """Inversa di _num, a lunghezza fissa: gli zeri iniziali sono significativi."""
    out = [0] * length
    for i in range(length - 1, -1, -1):
        out[i] = x % radix
        x //= radix
    return out


def _xor(a, b):
    # Via interi invece che con un generatore su zip: e' la funzione piu' chiamata
    # dell'intero programma (una ventina di volte per valore cifrato) e passare
    # dal loop Python all'XOR nativo sui bignum vale parecchi punti percentuali.
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big")


class FF1:
    """Istanza legata a una chiave AES (128, 192 o 256 bit).

    La chiave si passa una volta sola: creare l'oggetto Cipher a ogni valore
    costerebbe piu' della cifratura stessa, e qui i valori si contano a milioni.
    """

    def __init__(self, key):
        if len(key) not in (16, 24, 32):
            raise ValueError("la chiave AES deve essere di 16, 24 o 32 byte")
        self._key = key
        # UN solo contesto ECB per istanza, riusato per ogni blocco. Costruire un
        # oggetto Cipher costa piu' della cifratura stessa e FF1 ne chiederebbe
        # una decina per valore: su milioni di righe era il vero collo di
        # bottiglia (156 us -> vedi il benchmark nel README). In ECB il riuso e'
        # lecito perche' non c'e' concatenamento fra blocchi: ogni update() di 16
        # byte e' indipendente dai precedenti.
        #
        # NB: il contesto e' mutabile, quindi un'istanza FF1 NON e' thread-safe.
        # Il parallelismo qui e' per processi (multiprocessing), dove ogni worker
        # ha la sua istanza, quindi il vincolo non ci tocca.
        self._ecb_ctx = Cipher(algorithms.AES(key), modes.ECB()).encryptor()

    # -- primitive ---------------------------------------------------------- #
    def _ecb(self, data):
        """AES-ECB su blocchi interi. `data` deve essere multiplo di 16 byte."""
        return self._ecb_ctx.update(data)

    def _prf(self, data):
        """PRF dello standard = CBC-MAC con IV nullo.

        Scritto a mano sopra ECB invece di usare modes.CBC: cosi' riusa il
        contesto condiviso, mentre un Cipher CBC andrebbe ricostruito a ogni
        chiamata per riazzerare l'IV.
        """
        y = b"\x00" * _BLOCK
        for i in range(0, len(data), _BLOCK):
            y = self._ecb(_xor(y, data[i:i + _BLOCK]))
        return y

    # -- parametri derivati ------------------------------------------------- #
    @staticmethod
    def _params(n, t, radix):
        u = n // 2
        v = n - u
        # b = ceil(ceil(v*log2(radix))/8), calcolato su interi: con math.log2 su
        # v grandi l'arrotondamento in virgola mobile puo' sbagliare di un byte,
        # e un byte sbagliato qui significa testo cifrato non interoperabile.
        bits = (radix ** v - 1).bit_length()
        b = (bits + 7) // 8
        d = 4 * ((b + 3) // 4) + 4
        p = (bytes([1, 2, 1]) + radix.to_bytes(3, "big") + bytes([10, u % 256])
             + n.to_bytes(4, "big") + t.to_bytes(4, "big"))
        return u, v, b, d, p

    def _y(self, p, q, d):
        """Il valore pseudocasuale del round, esteso a d byte."""
        r = self._prf(p + q)
        s = r
        j = 1
        while len(s) < d:
            s += self._ecb(_xor(r, j.to_bytes(_BLOCK, "big")))
            j += 1
        return int.from_bytes(s[:d], "big")

    # -- API ---------------------------------------------------------------- #
    def encrypt(self, numerals, radix, tweak=b""):
        """Cifra una lista di numerali (interi in [0, radix)). Ritorna una lista."""
        return self._feistel(numerals, radix, tweak, forward=True)

    def decrypt(self, numerals, radix, tweak=b""):
        return self._feistel(numerals, radix, tweak, forward=False)

    def _feistel(self, numerals, radix, tweak, forward):
        n = len(numerals)
        t = len(tweak)
        if radix < 2:
            raise ValueError("radix deve essere >= 2")
        if any(not 0 <= c < radix for c in numerals):
            raise ValueError("numerale fuori dall'alfabeto")
        # Vincolo dello standard: sotto 100 elementi il dominio e' cosi' piccolo
        # che la permutazione si ricostruisce per enumerazione. Meglio fermarsi
        # che dare l'illusione di aver protetto una colonna a due valori.
        if radix ** n < 100:
            raise InvalidDomain(
                "dominio troppo piccolo (radix^n = %d < 100): questa colonna non "
                "si puo' proteggere cifrandola" % (radix ** n))

        u, v, b, d, p = self._params(n, t, radix)
        a, bb = list(numerals[:u]), list(numerals[u:])
        pad = b"\x00" * ((-t - b - 1) % _BLOCK)
        rounds = range(_ROUNDS) if forward else range(_ROUNDS - 1, -1, -1)

        for i in rounds:
            # In cifratura il round consuma la meta' destra, in decifratura la
            # sinistra: e' la stessa rete di Feistel percorsa al contrario.
            other = bb if forward else a
            q = tweak + pad + bytes([i]) + _num(other, radix).to_bytes(b, "big")
            y = self._y(p, q, d)
            m = u if i % 2 == 0 else v
            if forward:
                c = (_num(a, radix) + y) % (radix ** m)
                a, bb = bb, _numerals(c, radix, m)
            else:
                c = (_num(bb, radix) - y) % (radix ** m)
                bb, a = a, _numerals(c, radix, m)
        return a + bb

    # -- comodita' su stringhe ---------------------------------------------- #
    def encrypt_str(self, text, alphabet, tweak=b""):
        """Come encrypt(), ma su una stringa e il suo alfabeto ordinato.

        L'alfabeto definisce il dominio: cifrando "0123456789" con l'alfabeto
        delle cifre il risultato e' ancora fatto di cifre. Cambiare l'alfabeto
        cambia il testo cifrato, quindi va fissato una volta per colonna.
        """
        idx = {c: i for i, c in enumerate(alphabet)}
        try:
            numerals = [idx[c] for c in text]
        except KeyError as e:
            raise ValueError("carattere fuori dall'alfabeto: %r" % (e.args[0],)) from None
        out = self.encrypt(numerals, len(alphabet), tweak)
        return "".join(alphabet[i] for i in out)

    def decrypt_str(self, text, alphabet, tweak=b""):
        idx = {c: i for i, c in enumerate(alphabet)}
        try:
            numerals = [idx[c] for c in text]
        except KeyError as e:
            raise ValueError("carattere fuori dall'alfabeto: %r" % (e.args[0],)) from None
        out = self.decrypt(numerals, len(alphabet), tweak)
        return "".join(alphabet[i] for i in out)

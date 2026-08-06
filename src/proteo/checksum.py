# -*- coding: utf-8 -*-
"""Checksum degli identificativi italiani: verifica e *calcolo*.

Non basta saper dire se un codice e' valido: per produrre un surrogato valido
serve anche saper calcolare il carattere di controllo da zero. E' questa la
proprieta' che rende possibile tutto il progetto — il carattere di controllo
NON e' informazione, e' una funzione degli altri. Quindi si puo' buttare via,
cifrare il resto, e ricalcolarlo sul risultato:

    RSSMRA85H12F205 | Z          <- corpo | controllo
    FPE(corpo) = BNCLGU78T04H501
    ricalcolo -> K
    BNCLGU78T04H501K            <- valido, e reversibile con la sola chiave

Le funzioni *_ok replicano quelle di rizzo-pii (stessa aritmetica, verificata
sugli stessi casi); le funzioni *_check sono la novita' che serve qui.
"""

import re

__all__ = ["cf_ok", "cf_check", "piva_ok", "piva_check",
           "iban_ok", "iban_check", "luhn_ok", "luhn_check"]

# --------------------------------------------------------------------------- #
# Codice fiscale
# --------------------------------------------------------------------------- #
_CF_ODD = {"0": 1, "1": 0, "2": 5, "3": 7, "4": 9, "5": 13, "6": 15, "7": 17, "8": 19,
           "9": 21, "A": 1, "B": 0, "C": 5, "D": 7, "E": 9, "F": 13, "G": 15, "H": 17,
           "I": 19, "J": 21, "K": 2, "L": 4, "M": 18, "N": 20, "O": 11, "P": 3, "Q": 6,
           "R": 8, "S": 12, "T": 14, "U": 16, "V": 10, "W": 22, "X": 25, "Y": 24, "Z": 23}


def cf_check(body15):
    """Carattere di controllo dai primi 15 caratteri."""
    b = body15.upper()
    if len(b) != 15 or not b.isalnum():
        raise ValueError("il corpo del codice fiscale deve essere 15 caratteri alfanumerici")
    t = sum((_CF_ODD[ch] if i % 2 == 0
             else (int(ch) if ch.isdigit() else ord(ch) - 65))
            for i, ch in enumerate(b))
    return chr(65 + t % 26)


def cf_ok(c):
    c = (c or "").strip().upper()
    if len(c) != 16 or not c.isalnum():
        return False
    try:
        return cf_check(c[:15]) == c[15]
    except (KeyError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Partita IVA (Luhn su base 10, varianti italiane)
# --------------------------------------------------------------------------- #
def piva_check(body10):
    """Cifra di controllo dalle prime 10 cifre."""
    d = re.sub(r"\D", "", body10)
    if len(d) != 10:
        raise ValueError("il corpo della partita IVA deve essere 10 cifre")
    t = 0
    for i, c in enumerate(map(int, d)):
        if i % 2 == 0:
            t += c
        else:
            x = c * 2
            t += x - 9 if x > 9 else x
    return str((10 - t % 10) % 10)


def piva_ok(p):
    d = re.sub(r"\D", "", p or "")
    if len(d) != 11:
        return False
    return piva_check(d[:10]) == d[10]


# --------------------------------------------------------------------------- #
# IBAN (ISO 13616, mod-97)
# --------------------------------------------------------------------------- #
def _iban_num(s):
    r = s[4:] + s[:4]
    return int("".join(str(ord(c) - 55) if c.isalpha() else c for c in r))


def iban_check(country, bban):
    """Le due cifre di controllo per paese + BBAN."""
    country = country.upper()
    n = _iban_num(country + "00" + bban.upper())
    return "%02d" % (98 - n % 97)


def iban_ok(s):
    s = re.sub(r"[\s.\-]", "", (s or "")).upper()
    if not (15 <= len(s) <= 34) or not s.isalnum():
        return False
    try:
        return _iban_num(s) % 97 == 1
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Luhn (carte di credito)
# --------------------------------------------------------------------------- #
def luhn_check(body):
    """Cifra di controllo che rende `body + cifra` valido secondo Luhn."""
    d = re.sub(r"\D", "", body)
    tot, alt = 0, True
    for ch in reversed(d):
        n = int(ch)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        tot += n
        alt = not alt
    return str((10 - tot % 10) % 10)


def luhn_ok(s):
    d = re.sub(r"\D", "", s or "")
    if not (13 <= len(d) <= 19):
        return False
    tot, alt = 0, False
    for ch in reversed(d):
        n = int(ch)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        tot += n
        alt = not alt
    return tot % 10 == 0

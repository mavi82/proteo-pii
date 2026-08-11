# -*- coding: utf-8 -*-
"""Il file di configurazione: un database, i suoi percorsi, la sua password.

    {
      "formato": "proteo-config-v1",
      "predefinito": "vendite",
      "database": {
        "vendite": {
          "url": "postgresql+psycopg://utente@host:5432/vendite",
          "password": "...",                  facoltativa
          "chiave":   "/root/.proteo/vendite.key",
          "policy":   "/root/.proteo/policy.json",
          "registro": "/root/.proteo/registro",
          "etichetta": "VenditeDB"            nome usato nel registro
        }
      }
    }

JSON come tutto il resto del progetto (chiave, policy, registro): un formato in
piu' sarebbe una dipendenza in piu' e un modo in piu' di sbagliare.

## I percorsi sono relativi al file di config, non alla directory corrente

`"registro": "registro"` indica sempre la stessa cartella, da qualunque
directory si lanci Proteo. E' il contrario di come si comporta una riga di
comando, ed e' deliberato: un registro che cambia con la directory da cui lanci
significa un registro perso, cioe' una colonna che nessuno sa piu' se e' cifrata.

## Perche' la password puo' stare qui, ma a condizioni

Tenerla nel config e' comodo e legittimo — e' il file che descrive *quel*
database. Ma un file di config e' anche la cosa che si copia in un backup, si
incolla in una chat per chiedere aiuto e si committa per distrazione. Quindi:
se contiene una password, Proteo si rifiuta di leggerlo quando e' leggibile ad
altri utenti o quando sta dentro un repository git. Senza password nessun
vincolo: non c'e' niente da proteggere.
"""

import json
import os
from pathlib import Path

from .keyfile import dentro_un_repo_git

__all__ = ["Config", "ConfigNonValida", "carica", "percorso_predefinito",
           "NOME_PREDEFINITO"]

FORMATO = "proteo-config-v1"
NOME_PREDEFINITO = "proteo.json"

CAMPI_PERCORSO = ("chiave", "policy", "registro")


class ConfigNonValida(ValueError):
    pass


def percorso_predefinito():
    """./proteo.json, poi ~/.proteo/proteo.json. None se non c'e' nessuno dei due."""
    for p in (Path.cwd() / NOME_PREDEFINITO,
              Path.home() / ".proteo" / NOME_PREDEFINITO):
        if p.is_file():
            return p
    return None


def _ha_password(voce):
    if voce.get("password"):
        return True
    # una password puo' anche essere gia' dentro l'URL: "//utente:segreto@host".
    # Cercarla nella sola chiave "password" lascerebbe scoperto il caso piu'
    # comune, che e' anche quello che si scrive senza pensarci.
    url = voce.get("url") or ""
    prima = url.split("@", 1)[0]
    return "://" in prima and ":" in prima.split("://", 1)[1]


def _permessi_larghi(percorso):
    if os.name == "nt":
        # I bit POSIX su Windows non descrivono nulla: st_mode e' sempre 0o666.
        # Un controllo che passa sempre e' peggio di nessun controllo, perche'
        # promette una garanzia che non c'e'.
        return False
    return bool(percorso.stat().st_mode & 0o077)


class Config:
    def __init__(self, dati=None, percorso=None):
        self.dati = dati or {"formato": FORMATO, "database": {}}
        self.percorso = Path(percorso) if percorso else None

    # -- accesso ------------------------------------------------------------ #
    @property
    def database(self):
        return self.dati.get("database", {})

    def nomi(self):
        return sorted(self.database)

    def predefinito(self):
        """Il database da usare se non se ne sceglie uno. None se ambiguo."""
        nome = self.dati.get("predefinito")
        if nome in self.database:
            return nome
        nomi = self.nomi()
        return nomi[0] if len(nomi) == 1 else None

    def voce(self, nome):
        try:
            return dict(self.database[nome])
        except KeyError:
            raise ConfigNonValida(
                "nel config non c'e' nessun database di nome %r (ci sono: %s)"
                % (nome, ", ".join(self.nomi()) or "nessuno")) from None

    def risolvi(self, voce, campo):
        """Percorso assoluto di `campo`, relativo alla cartella del config."""
        valore = voce.get(campo)
        if not valore:
            return None
        p = Path(valore).expanduser()
        if p.is_absolute() or self.percorso is None:
            return p
        return (self.percorso.parent / p).resolve()

    def url_completo(self, voce):
        """URL con la password innestata, se sta in un campo a parte."""
        from sqlalchemy.engine import make_url    # importato qui: config.py e'
        url = make_url(voce["url"])               # usato anche senza database
        return url.set(password=voce["password"]) if voce.get("password") else url

    # -- persistenza -------------------------------------------------------- #
    def salva(self, percorso=None):
        p = Path(percorso or self.percorso)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.dati["formato"] = FORMATO
        testo = json.dumps(self.dati, indent=2, ensure_ascii=False) + "\n"
        # Si crea sempre a 0600, anche senza password: il config nasce protetto
        # invece di diventarlo il giorno in cui qualcuno ci scrive dentro una
        # password e si ricorda di dare il chmod.
        if os.name != "nt":
            fd = os.open(str(p), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(testo)
        else:
            p.write_text(testo, encoding="utf-8")
        self.percorso = p
        return p


def carica(percorso):
    p = Path(percorso)
    try:
        dati = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigNonValida("%s non e' un JSON leggibile: %s" % (p, e)) from None
    if dati.get("formato") != FORMATO:
        raise ConfigNonValida("%s: formato sconosciuto %r" % (p, dati.get("formato")))

    if any(_ha_password(v) for v in dati.get("database", {}).values()):
        if _permessi_larghi(p):
            raise ConfigNonValida(
                "%s contiene una password ed e' leggibile da altri utenti.\n"
                "  chmod 600 %s" % (p, p))
        if dentro_un_repo_git(p):
            raise ConfigNonValida(
                "%s contiene una password e sta dentro un repository git: "
                "basta un `git add -A` perche' finisca in un commit, e da li' in "
                "ogni clone e in tutta la storia. Spostalo fuori dal repo (per "
                "esempio in ~/.proteo/), oppure togli la password e lasciala a "
                "$PROTEO_PASSWORD." % p)
    return Config(dati, p)

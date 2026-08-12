# -*- coding: utf-8 -*-
"""La policy: quali colonne, con che strategia — e la verifica *fail-closed*.

La policy la scrive l'utente (dalla UI o a mano): Proteo non indovina nulla e non
va a caccia di dati personali per conto suo. La contropartita di questa scelta e'
che una colonna dimenticata passerebbe in chiaro **senza che nessuno lo sappia**,
e le colonne nuove arrivano da sole: una migration aggiunge `clienti.pec` sei mesi
dopo, la policy non la nomina, e l'esportazione successiva la consegna intatta.

Per questo la regola non e' "anonimizza cio' che e' dichiarato" ma:

    ogni colonna delle tabelle dichiarate deve comparire nella policy,
    anche solo per dire `mantieni`. Altrimenti l'esecuzione si ferma.

Costa una riga per colonna la prima volta. In cambio, il giorno in cui qualcuno
aggiunge una colonna il processo si rompe rumorosamente invece di perdere dati in
silenzio.

Questo modulo non parla con nessun database: riceve una descrizione astratta
dello schema (vedi `verifica`) e per questo si prova senza un server acceso.
"""

import json
from collections import namedtuple
from pathlib import Path

from .surrogati import Surrogatore

__all__ = ["Policy", "Problema", "PolicyNonValida", "STRATEGIE", "TIPI"]

FORMATO = "proteo-policy-v1"
STRATEGIE = ("cifra", "mantieni", "azzera")
TIPI = tuple(sorted(Surrogatore._TIPI))          # CF, IBAN, PIVA

# livello: "errore" blocca l'esecuzione, "avviso" la lascia proseguire.
Problema = namedtuple("Problema", "livello dove messaggio")


class PolicyNonValida(ValueError):
    pass


def tweak_predefinito(colonna):
    """Tweak di default = nome della colonna, normalizzato a minuscolo.

    Colonne omonime in tabelle diverse condividono cosi' il surrogato, e i JOIN
    reggono senza dover dichiarare nulla. Non si normalizza oltre il minuscolo:
    fondere `cod_fisc` e `codicefiscale` per somiglianza creerebbe accoppiamenti
    falsi peggiori del problema che risolvono.
    """
    return colonna.strip().lower()


class Policy:
    """tabella -> colonna -> {strategia, tipo, tweak}."""

    def __init__(self, tabelle=None, chiave_id=None):
        self.tabelle = tabelle or {}
        self.chiave_id = chiave_id

    # -- accesso ------------------------------------------------------------ #
    def regola(self, tabella, colonna):
        return self.tabelle.get(tabella, {}).get(colonna)

    def tweak(self, tabella, colonna):
        r = self.regola(tabella, colonna) or {}
        return (r.get("tweak") or tweak_predefinito(colonna)).encode("utf-8")

    def colonne_da_cifrare(self):
        for t, colonne in sorted(self.tabelle.items()):
            for c, r in sorted(colonne.items()):
                if r.get("strategia") == "cifra":
                    yield t, c, r

    def colonne_da_azzerare(self):
        for t, colonne in sorted(self.tabelle.items()):
            for c, r in sorted(colonne.items()):
                if r.get("strategia") == "azzera":
                    yield t, c, r

    # -- crescita ----------------------------------------------------------- #
    def aggiorna(self, schema, proposte=None):
        """Allinea la policy allo schema **senza toccare le scelte gia' fatte**.

        E' il modo in cui una policy sopravvive al tempo: rigenerarla da capo
        cancellerebbe ogni `cifra` deciso a mano, quindi in pratica non la si
        rigenera mai e la si lascia invecchiare — cioe' esattamente il problema
        che il fail-closed doveva impedire.

        Le colonne nuove entrano dalla proposta di `rilevamento` se ce n'e' una,
        altrimenti come `mantieni`. Quelle sparite dallo schema vengono tolte,
        perche' altrimenti la verifica si fermerebbe su una colonna che non
        esiste piu'.

        Ritorna (aggiunte, tolte, fuori_schema): tre elenchi da mostrare. Le
        tolte vanno lette: una colonna rinominata si presenta come una tolta piu'
        una aggiunta, e quella aggiunta nasce `mantieni`, cioe' in chiaro.
        """
        proposte = proposte or {}
        aggiunte, tolte = [], []

        for tabella, colonne in schema.get("tabelle", {}).items():
            regole = self.tabelle.setdefault(tabella, {})
            for colonna in sorted(colonne):
                if colonna in regole:
                    continue
                proposta = proposte.get(tabella, {}).get(colonna)
                if proposta:
                    regole[colonna] = {"strategia": "cifra", "tipo": proposta[0]}
                else:
                    regole[colonna] = {"strategia": "mantieni"}
                aggiunte.append(("%s.%s" % (tabella, colonna),
                                 regole[colonna]["strategia"]))
            for colonna in sorted(set(regole) - set(colonne)):
                del regole[colonna]
                tolte.append("%s.%s" % (tabella, colonna))

        fuori = sorted(set(self.tabelle) - set(schema.get("tabelle", {})))
        return aggiunte, tolte, fuori

    # -- persistenza -------------------------------------------------------- #
    @classmethod
    def carica(cls, percorso):
        d = json.loads(Path(percorso).read_text(encoding="utf-8"))
        if d.get("formato") != FORMATO:
            raise PolicyNonValida("formato sconosciuto: %r" % d.get("formato"))
        return cls(d.get("tabelle", {}), d.get("chiave_id"))

    def salva(self, percorso):
        Path(percorso).write_text(json.dumps({
            "formato": FORMATO,
            "chiave_id": self.chiave_id,
            "tabelle": self.tabelle,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    # -- verifica ----------------------------------------------------------- #
    def verifica(self, schema):
        """Confronta la policy con lo schema reale. Ritorna una lista di Problema.

        `schema` = {
          "tabelle": {"dbo.clienti": {"cf": {"tipo": "char(16)", "nullable": True}}},
          "foreign_key": [(("dbo.contratti","cf_cli"), ("dbo.clienti","cf")), ...],
        }
        """
        problemi = []
        tab_schema = schema.get("tabelle", {})

        for tabella, regole in sorted(self.tabelle.items()):
            if tabella not in tab_schema:
                problemi.append(Problema(
                    "errore", tabella,
                    "la policy nomina una tabella che nel database non esiste "
                    "(rinominata? policy stantia?)"))
                continue

            colonne = tab_schema[tabella]

            # (1) fail-closed: nessuna colonna puo' restare non dichiarata
            for c in sorted(set(colonne) - set(regole)):
                problemi.append(Problema(
                    "errore", "%s.%s" % (tabella, c),
                    "colonna non dichiarata nella policy. Dichiarala, anche solo "
                    "come 'mantieni': una colonna dimenticata esce in chiaro."))

            # (2) policy che nomina colonne inesistenti
            for c in sorted(set(regole) - set(colonne)):
                problemi.append(Problema(
                    "errore", "%s.%s" % (tabella, c),
                    "la policy nomina una colonna che nel database non esiste"))

            # (3) coerenza di ogni regola
            for c, r in sorted(regole.items()):
                if c not in colonne:
                    continue
                dove = "%s.%s" % (tabella, c)
                s = r.get("strategia")
                if s not in STRATEGIE:
                    problemi.append(Problema(
                        "errore", dove,
                        "strategia %r sconosciuta (ammesse: %s)" % (s, ", ".join(STRATEGIE))))
                    continue
                if s == "cifra" and r.get("tipo") not in TIPI:
                    problemi.append(Problema(
                        "errore", dove,
                        "strategia 'cifra' senza un tipo valido (ammessi: %s)" % ", ".join(TIPI)))
                if s == "azzera" and not colonne[c].get("nullable", True):
                    problemi.append(Problema(
                        "errore", dove,
                        "'azzera' su una colonna NOT NULL: l'UPDATE fallirebbe a meta' tabella"))

        problemi.extend(self._verifica_foreign_key(schema))
        problemi.extend(self._verifica_tweak_ambigui())
        return problemi

    def _verifica_foreign_key(self, schema):
        """Due colonne legate da una FK devono produrre lo stesso surrogato.

        E' il controllo che salva dal difetto piu' insidioso del tweak per nome
        di colonna: `clienti.codice_fiscale` e `contratti.cf_intestatario` sono
        la stessa persona ma prendono tweak diversi, quindi surrogati diversi, e
        il join si rompe **in silenzio** — le statistiche escono sbagliate senza
        che nulla segnali un errore. La FK e' gia' dichiarata nel database,
        quindi il controllo e' gratuito e coglie proprio il caso pericoloso.
        """
        problemi = []
        for (t1, c1), (t2, c2) in schema.get("foreign_key", []):
            r1, r2 = self.regola(t1, c1), self.regola(t2, c2)
            if r1 is None or r2 is None:
                continue                       # gia' segnalato come non dichiarata
            s1, s2 = r1.get("strategia"), r2.get("strategia")
            if s1 != "cifra" and s2 != "cifra":
                continue
            dove = "%s.%s -> %s.%s" % (t1, c1, t2, c2)
            if s1 != s2:
                problemi.append(Problema(
                    "errore", dove,
                    "le due colonne di una foreign key hanno strategie diverse "
                    "(%s / %s): la relazione si romperebbe" % (s1, s2)))
                continue
            if r1.get("tipo") != r2.get("tipo"):
                problemi.append(Problema(
                    "errore", dove,
                    "tipi diversi (%s / %s) sui due lati di una foreign key"
                    % (r1.get("tipo"), r2.get("tipo"))))
            if self.tweak(t1, c1) != self.tweak(t2, c2):
                problemi.append(Problema(
                    "errore", dove,
                    "tweak diversi (%s / %s): i due lati riceverebbero surrogati "
                    "diversi e il JOIN si romperebbe senza errori. Dichiara lo "
                    "stesso 'tweak' su entrambe le colonne."
                    % (self.tweak(t1, c1).decode(), self.tweak(t2, c2).decode())))
        return problemi

    def _verifica_tweak_ambigui(self):
        """Stesso tweak per tipi diversi: quasi sempre un'omonimia involontaria."""
        per_tweak = {}
        for t, c, r in self.colonne_da_cifrare():
            per_tweak.setdefault(self.tweak(t, c), {}).setdefault(
                r.get("tipo"), []).append("%s.%s" % (t, c))
        problemi = []
        for tw, tipi in sorted(per_tweak.items()):
            if len(tipi) > 1:
                # key=str: una regola malformata ha tipo None, e ordinare None
                # insieme alle stringhe solleverebbe TypeError. Questo controllo
                # gira sulle policy *sbagliate* piu' che su quelle giuste: se
                # esplode proprio li', l'errore vero non viene mai riportato.
                righe = sorted(tipi.items(), key=lambda kv: str(kv[0]))
                problemi.append(Problema(
                    "avviso", tw.decode(),
                    "lo stesso tweak e' usato per tipi diversi (%s): colonne omonime "
                    "ma scollegate? Se lo sono, dai loro tweak espliciti."
                    % "; ".join("%s: %s" % (k, ", ".join(v)) for k, v in righe)))
        return problemi

    def errori(self, schema):
        return [p for p in self.verifica(schema) if p.livello == "errore"]

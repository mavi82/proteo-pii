# -*- coding: utf-8 -*-
"""Orchestrazione: policy + chiave + registro + database.

Tre operazioni, sempre nello stesso ordine:

    verifica()   nessuna scrittura, nessuna inferenza — dice cosa non va
    anteprima()  nessuna scrittura — mostra prima/dopo su un campione
    esegui()     scrive

`verifica()` e' un cancello, non un consiglio: `esegui()` la richiama e si
rifiuta di partire se resta anche un solo errore. E' l'unico modo di far valere
davvero la regola fail-closed della policy, perche' una colonna dimenticata non
produce nessun sintomo visibile — esce in chiaro e basta.
"""

from . import db
from .registro import CIFRATA, IN_CHIARO, Registro, StatoIncoerente
from .policy import Problema
from .surrogati import Surrogatore, ValoreNonTrattabile

__all__ = ["Motore", "VerificaFallita"]

# Tipi di colonna su cui un surrogato testuale non torna indietro intatto.
_TIPI_RISCHIOSI = ("INT", "NUMERIC", "DECIMAL", "FLOAT", "BIGINT", "SMALLINT", "MONEY")


class VerificaFallita(RuntimeError):
    def __init__(self, problemi):
        self.problemi = problemi
        super().__init__("%d errori bloccanti:\n%s" % (
            len(problemi), "\n".join("  [%s] %s: %s" % (p.livello, p.dove, p.messaggio)
                                     for p in problemi)))


class Motore:
    def __init__(self, engine, policy, chiave, chiave_id, registro, database):
        self.engine = engine
        self.policy = policy
        self.chiave_id = chiave_id
        self.registro = registro if isinstance(registro, Registro) else Registro(registro)
        self.database = database
        self.surr = Surrogatore(chiave)

    # -- verifica ----------------------------------------------------------- #
    def schema(self):
        return db.introspeziona(self.engine, sorted(self.policy.tabelle))

    def verifica(self, verso="cifra"):
        """Problemi di policy, di schema e di stato. Lista vuota = si puo' partire."""
        schema = self.schema()
        problemi = list(self.policy.verifica(schema))

        for tabella, colonna, regola in self.policy.colonne_da_cifrare():
            dove = "%s.%s" % (tabella, colonna)
            col = schema["tabelle"].get(tabella, {}).get(colonna)

            # Un surrogato con zeri iniziali ("0174...") scritto in una colonna
            # numerica li perde, e il valore non torna piu' indietro. Il danno si
            # manifesta solo in decifratura, quando e' tardi.
            if col and any(x in col["tipo"].upper() for x in _TIPI_RISCHIOSI):
                problemi.append(Problema(
                    "errore", dove,
                    "tipo %s: un surrogato testuale perderebbe gli zeri iniziali e "
                    "non tornerebbe indietro. Serve una colonna di testo." % col["tipo"]))

            try:
                if verso == "cifra":
                    self.registro.verifica_prima_di_cifrare(self.database, tabella, colonna)
                else:
                    self.registro.verifica_prima_di_decifrare(
                        self.database, tabella, colonna, self.chiave_id)
            except StatoIncoerente as e:
                problemi.append(Problema("errore", dove, str(e)))

        return problemi

    def errori(self, verso="cifra"):
        return [p for p in self.verifica(verso) if p.livello == "errore"]

    # -- anteprima ---------------------------------------------------------- #
    def anteprima(self, n=8, verso="cifra"):
        """Campione prima/dopo, senza scrivere nulla. Il controllo a occhio."""
        out = []
        for tabella, colonna, regola in self.policy.colonne_da_cifrare():
            tweak = self.policy.tweak(tabella, colonna)
            righe, distinti = db.conta(self.engine, tabella, colonna)
            campione, scarti = [], []
            # Il generatore va chiuso a mano: uscirne con `break` lo lascia
            # sospeso dentro il suo `with engine.connect()`, e la connessione
            # resta aperta fino alla garbage collection. Su una UI che mostra
            # l'anteprima a ogni clic si esaurisce il pool.
            gen = db.leggi_distinti(self.engine, tabella, colonna, lotto=max(n, 64))
            try:
                for blocco in gen:
                    for v in blocco[:n]:
                        try:
                            campione.append(
                                (v, self._trasforma(regola["tipo"], v, tweak, verso)))
                        except ValoreNonTrattabile as e:
                            scarti.append((v, str(e)))
                    break                   # basta il primo lotto per un campione
            finally:
                gen.close()
            out.append({"tabella": tabella, "colonna": colonna, "tipo": regola["tipo"],
                        "tweak": tweak.decode(), "righe": righe, "distinti": distinti,
                        "campione": campione, "non_trattabili": scarti})
        return out

    # -- esecuzione --------------------------------------------------------- #
    def esegui(self, verso="cifra", su_valore_non_trattabile="ferma", progresso=None):
        """Applica la policy. `verso` = 'cifra' | 'decifra'.

        su_valore_non_trattabile:
          'ferma' (predefinito) — un valore malformato interrompe la colonna;
          'salta'               — lo si lascia com'e'. **Resta in chiaro**: e' una
                                  fuga di dati, quindi finisce nel rapporto e nel
                                  registro invece di essere silenziosamente ignorata.
        """
        errori = self.errori(verso)
        if errori:
            raise VerificaFallita(errori)

        rapporto = {"verso": verso, "database": self.database, "colonne": []}
        for tabella, colonna, regola in self.policy.colonne_da_cifrare():
            tipo, tweak = regola["tipo"], self.policy.tweak(tabella, colonna)
            if progresso:
                progresso("%s.%s" % (tabella, colonna))

            self.registro.avvia(self.database, tabella, colonna, tipo,
                                tweak.decode(), self.chiave_id, verso)
            scarti = []
            coppie = self._coppie(tabella, colonna, tipo, tweak, verso,
                                  scarti, su_valore_non_trattabile)
            toccate = db.applica_mappa(self.engine, tabella, colonna, coppie)

            stato = CIFRATA if verso == "cifra" else IN_CHIARO
            self.registro.concludi(self.database, tabella, colonna, stato, toccate)
            rapporto["colonne"].append({
                "tabella": tabella, "colonna": colonna, "tipo": tipo,
                "righe_aggiornate": toccate, "non_trattabili": scarti,
            })
        return rapporto

    # -- interni ------------------------------------------------------------ #
    def _trasforma(self, tipo, valore, tweak, verso):
        v = valore if isinstance(valore, str) else str(valore)
        return (self.surr.cifra(tipo, v, tweak) if verso == "cifra"
                else self.surr.decifra(tipo, v, tweak))

    def _coppie(self, tabella, colonna, tipo, tweak, verso, scarti, su_errore):
        """Generatore (vecchio, nuovo) sui valori distinti.

        Generatore e non lista: `applica_mappa` lo consuma riempiendo la tabella
        di appoggio, quindi la memoria resta limitata anche con milioni di valori
        distinti.
        """
        for blocco in db.leggi_distinti(self.engine, tabella, colonna):
            for v in blocco:
                try:
                    yield v, self._trasforma(tipo, v, tweak, verso)
                except ValoreNonTrattabile as e:
                    if su_errore == "ferma":
                        raise
                    scarti.append((v, str(e)))

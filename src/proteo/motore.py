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

import re

from . import db
from .registro import (AZZERATA, CIFRATA, IN_CHIARO, IN_CORSO, Registro,
                       StatoIncoerente)
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

    def _impronta_lista(self, tipo):
        """Impronta della lista usata da quel tipo, o None se non ne usa."""
        quale = Surrogatore.LISTE.get(tipo)
        return self.surr.lista(quale).impronta if quale else None

    def _lista_troppo_lunga(self, tipo, col):
        """La voce piu' lunga della lista entra nella colonna?

        Con i tipi a lista la lunghezza NON si conserva: `Re` puo' diventare
        `Acquaviva`. Su una colonna stretta l'UPDATE fallirebbe a meta' tabella,
        o peggio troncherebbe in silenzio a seconda del motore — e un surrogato
        troncato non torna piu' indietro.
        """
        quale = Surrogatore.LISTE.get(tipo)
        if not quale or not col:
            return None
        larghezza = re.search(r"\((\d+)\)", col["tipo"])
        if not larghezza:
            return None
        massimo = self.surr.lista(quale).lunghezza_massima
        if massimo <= int(larghezza.group(1)):
            return None
        return ("la colonna e' %s ma la voce piu' lunga della lista ne occupa %d: "
                "il surrogato non ci entrerebbe. Allarga la colonna, o togli "
                "dalla lista le voci troppo lunghe PRIMA di cifrare."
                % (col["tipo"], massimo))

    def _scelte(self, solo):
        """Filtro delle colonne su cui agire. `solo` = None -> tutte."""
        if solo is None:
            return lambda t, c: True
        scelte = {(t, c) for t, c in solo}
        return lambda t, c: (t, c) in scelte

    def verifica(self, verso="cifra", solo=None):
        """Problemi di policy, di schema e di stato. Lista vuota = si puo' partire.

        `solo` restringe i controlli di **stato** alle colonne indicate, non
        quelli di policy: il fail-closed vale sempre sull'intera policy, perche'
        una colonna dimenticata resta dimenticata anche se in questo momento se
        ne sta trattando un'altra. Lo stato invece e' per colonna, e una colonna
        gia' cifrata la settimana scorsa non deve impedire di trattare quella
        accanto.
        """
        schema = self.schema()
        problemi = list(self.policy.verifica(schema))
        scelta = self._scelte(solo)

        for tabella, colonna, regola in self.policy.colonne_da_cifrare():
            if not scelta(tabella, colonna):
                continue
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

            problema = self._lista_troppo_lunga(regola["tipo"], col)
            if problema:
                problemi.append(Problema("errore", dove, problema))

            try:
                if verso == "cifra":
                    self.registro.verifica_prima_di_cifrare(self.database, tabella, colonna)
                else:
                    self.registro.verifica_prima_di_decifrare(
                        self.database, tabella, colonna, self.chiave_id,
                        self._impronta_lista(regola["tipo"]))
            except StatoIncoerente as e:
                problemi.append(Problema("errore", dove, str(e)))

        problemi.extend(self._verifica_azzeramenti(verso, scelta))
        return problemi

    def _verifica_azzeramenti(self, verso, scelta=None):
        """`azzera` distrugge: qui si dice ad alta voce cosa non tornera' indietro.

        Non e' un errore — l'utente l'ha chiesto dichiarandolo nella policy — ma
        non deve nemmeno passare in silenzio: e' l'unica operazione del progetto
        che nessuna chiave puo' annullare.
        """
        problemi = []
        for tabella, colonna, _ in self.policy.colonne_da_azzerare():
            if scelta and not scelta(tabella, colonna):
                continue
            dove = "%s.%s" % (tabella, colonna)
            stato = self.registro.stato(self.database, tabella, colonna)
            if stato == IN_CORSO:
                problemi.append(Problema(
                    "errore", dove,
                    "rimasta in stato 'in_corso': un'esecuzione precedente non e' "
                    "mai finita e la colonna e' in uno stato misto. Va risolta a mano."))
            elif verso == "decifra":
                # In decifratura si passa oltre invece di bloccare: fermare tutto
                # renderebbe impossibile riportare in chiaro le altre colonne, e
                # una colonna azzerata non sarebbe comunque recuperabile.
                if stato == AZZERATA:
                    problemi.append(Problema(
                        "avviso", dove,
                        "azzerata in cifratura: resta vuota, nessuna chiave la "
                        "riporta indietro. Serve un backup."))
            elif stato != AZZERATA:
                problemi.append(Problema(
                    "avviso", dove,
                    "strategia 'azzera': i valori verranno eliminati e non "
                    "tornano indietro nemmeno con la chiave."))
        return problemi

    def errori(self, verso="cifra", solo=None):
        return [p for p in self.verifica(verso, solo) if p.livello == "errore"]

    # -- anteprima ---------------------------------------------------------- #
    def anteprima(self, n=8, verso="cifra", solo=None):
        """Campione prima/dopo, senza scrivere nulla. Il controllo a occhio."""
        out = []
        scelta = self._scelte(solo)
        for tabella, colonna, regola in self.policy.colonne_da_cifrare():
            if not scelta(tabella, colonna):
                continue
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
            out.append({"operazione": "cifra",
                        "tabella": tabella, "colonna": colonna, "tipo": regola["tipo"],
                        "tweak": tweak.decode(), "righe": righe, "distinti": distinti,
                        "campione": campione, "non_trattabili": scarti})

        # Le colonne da azzerare compaiono nell'anteprima con i valori che stanno
        # per sparire: e' l'ultimo momento in cui si possono ancora vedere.
        if verso == "cifra":
            for tabella, colonna, _ in self.policy.colonne_da_azzerare():
                if not scelta(tabella, colonna):
                    continue
                righe, distinti = db.conta(self.engine, tabella, colonna)
                campione = []
                gen = db.leggi_distinti(self.engine, tabella, colonna, lotto=max(n, 64))
                try:
                    for blocco in gen:
                        campione = [(v, None) for v in blocco[:n]]
                        break                   # basta il primo lotto per un campione
                finally:
                    gen.close()
                out.append({"operazione": "azzera",
                            "tabella": tabella, "colonna": colonna, "tipo": None,
                            "tweak": None, "righe": righe, "distinti": distinti,
                            "campione": campione, "non_trattabili": []})
        return out

    # -- esecuzione --------------------------------------------------------- #
    def esegui(self, verso="cifra", su_valore_non_trattabile="ferma",
               avanzamento=None, solo=None):
        """Applica la policy. `verso` = 'cifra' | 'decifra'.

        su_valore_non_trattabile:
          'ferma' (predefinito) — un valore malformato interrompe la colonna;
          'salta'               — lo si lascia com'e'. **Resta in chiaro**: e' una
                                  fuga di dati, quindi finisce nel rapporto e nel
                                  registro invece di essere silenziosamente ignorata.

        `solo` = iterabile di (tabella, colonna): tratta soltanto quelle. Serve a
        lavorare una colonna alla volta — la policy resta il documento completo e
        viene verificata per intero, ma si scrive solo dove si e' deciso di
        scrivere adesso.

        `avanzamento` riceve gli eventi mentre si lavora (vedi
        `avanzamento.py`): su una colonna da milioni di valori un'esecuzione
        muta e' indistinguibile da una piantata.
        """
        from .avanzamento import Silenzioso
        av = avanzamento or Silenzioso()
        errori = self.errori(verso, solo)
        if errori:
            raise VerificaFallita(errori)

        scelta = self._scelte(solo)
        rapporto = {"verso": verso, "database": self.database, "colonne": []}
        for tabella, colonna, regola in self.policy.colonne_da_cifrare():
            if not scelta(tabella, colonna):
                continue
            tipo, tweak = regola["tipo"], self.policy.tweak(tabella, colonna)

            # Il conteggio costa una scansione, e su una colonna grande si
            # sente: si paga perche' senza il totale non esistono ne'
            # percentuale ne' tempo rimasto, cioe' le due cose che permettono di
            # decidere se aspettare o annullare.
            av.fase("conto le righe e i valori distinti")
            righe_totali, distinti = db.conta(self.engine, tabella, colonna)
            av.colonna(tabella, colonna, tipo, verso, righe_totali, distinti)

            self.registro.avvia(self.database, tabella, colonna, tipo,
                                tweak.decode(), self.chiave_id, verso,
                                lista=self._impronta_lista(tipo))
            scarti = []
            coppie = self._coppie(tabella, colonna, tipo, tweak, verso,
                                  scarti, su_valore_non_trattabile, av)
            toccate = db.applica_mappa(self.engine, tabella, colonna, coppie,
                                       avanzamento=av)
            av.conclusa(toccate)

            stato = CIFRATA if verso == "cifra" else IN_CHIARO
            self.registro.concludi(self.database, tabella, colonna, stato, toccate)
            rapporto["colonne"].append({
                "operazione": "cifra",
                "tabella": tabella, "colonna": colonna, "tipo": tipo,
                "righe_aggiornate": toccate, "non_trattabili": scarti,
            })

        rapporto["colonne"].extend(self._azzera(verso, av, scelta))
        return rapporto

    def _azzera(self, verso, av=None, scelta=None):
        """Svuota le colonne dichiarate `azzera`. Solo in cifratura.

        In decifratura non si fa nulla: non c'e' nulla da riportare indietro, e
        rieseguire l'azzeramento distruggerebbe i valori che l'utente ha appena
        chiesto di recuperare.
        """
        if verso != "cifra":
            return []

        fatte = []
        for tabella, colonna, _ in self.policy.colonne_da_azzerare():
            if scelta and not scelta(tabella, colonna):
                continue
            if av:
                righe_totali, distinti = db.conta(self.engine, tabella, colonna)
                av.colonna(tabella, colonna, "azzera", verso, righe_totali, distinti)
            # Passa dal registro come la cifratura: e' l'unica cosa che sappia
            # dire, dopo, perche' quella colonna e' vuota — una colonna svuotata
            # e una colonna sempre stata vuota si somigliano troppo.
            self.registro.avvia(self.database, tabella, colonna,
                                tipo=None, tweak=None, chiave_id=None, verso="azzera")
            toccate = db.azzera(self.engine, tabella, colonna)
            if av:
                av.conclusa(toccate)
            self.registro.concludi(self.database, tabella, colonna, AZZERATA, toccate)
            fatte.append({
                "operazione": "azzera",
                "tabella": tabella, "colonna": colonna, "tipo": None,
                "righe_aggiornate": toccate, "non_trattabili": [],
            })
        return fatte

    # -- interni ------------------------------------------------------------ #
    def _trasforma(self, tipo, valore, tweak, verso):
        v = valore if isinstance(valore, str) else str(valore)
        return (self.surr.cifra(tipo, v, tweak) if verso == "cifra"
                else self.surr.decifra(tipo, v, tweak))

    def _coppie(self, tabella, colonna, tipo, tweak, verso, scarti, su_errore,
                av=None):
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
                    if av:
                        # Un valore saltato resta in chiaro: e' una fuga di
                        # dati, e va detta mentre succede, non solo nel rapporto
                        # finale che nessuno rilegge.
                        av.scartato(v, str(e))

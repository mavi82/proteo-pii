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
    def __init__(self, engine, policy, chiave, chiave_id, registro, database,
                 lotto_righe=None):
        self.engine = engine
        self.policy = policy
        self.chiave_id = chiave_id
        self.registro = registro if isinstance(registro, Registro) else Registro(registro)
        self.database = database
        # None = una sola transazione per colonna (atomica, ma tiene i lock per
        # tutta la durata). Un numero = si scrive a lotti di quelle righe.
        self.lotto_righe = lotto_righe
        self.surr = Surrogatore(chiave)

    # -- verifica ----------------------------------------------------------- #
    def schema(self):
        return db.introspeziona(self.engine, sorted(self.policy.tabelle))

    def _impronta_lista(self, tipo):
        """Cosa serve, oltre alla chiave, per tornare indietro da quel tipo.

        Sono due cose e viaggiano insieme: il contenuto della lista e la
        versione del ripiego. Cambiare una delle due cambia i surrogati, quindi
        deve fermare una decifratura fatta con l'altra — altrimenti si
        otterrebbero valori diversi dagli originali, in silenzio.
        """
        quale = Surrogatore.LISTE.get(tipo)
        if not quale:
            return None
        return "%s/r%d" % (self.surr.lista(quale).impronta,
                           Surrogatore.VERSIONE_RIPIEGO)

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

    def anteprima_righe(self, n=30, verso="cifra", solo=None):
        """Le prime `n` righe come le vedrebbe chi guarda la tabella.

        L'anteprima per valori distinti risponde alla domanda del motore ("come
        si trasforma questo valore"); questa risponde a quella di chi conosce i
        dati ("com'era questo record, e cosa diventa"). Sono la stessa
        informazione da due parti diverse, e serve la seconda per accorgersi di
        aver puntato la colonna sbagliata: un valore isolato non dice granche',
        la riga intera si', perche' ha accanto la chiave e le altre colonne.

        Ritorna [{tabella, chiave, colonne, righe}], dove ogni cella e'
        (prima, dopo, errore).
        """
        scelta = self._scelte(solo)
        schema = self.schema()

        da_fare = {}
        for tabella, colonna, regola in self.policy.colonne_da_cifrare():
            if scelta(tabella, colonna):
                da_fare.setdefault(tabella, []).append((colonna, regola["tipo"]))
        if verso == "cifra":
            for tabella, colonna, _ in self.policy.colonne_da_azzerare():
                if scelta(tabella, colonna):
                    da_fare.setdefault(tabella, []).append((colonna, None))

        out = []
        for tabella, colonne in sorted(da_fare.items()):
            # La chiave primaria non si tratta, ma si mostra: e' cio' che
            # permette di riconoscere il record e di andarselo a guardare nel
            # database vero, se qualcosa non torna.
            chiave = [c for c in schema["chiavi_primarie"].get(tabella, [])
                      if c not in {n for n, _ in colonne}]
            nomi = chiave + [c for c, _ in colonne]
            righe = []
            for riga in db.prime_righe(self.engine, tabella, nomi, n):
                celle = {}
                for colonna, tipo in colonne:
                    prima = riga.get(colonna)
                    if prima is None:
                        celle[colonna] = (None, None, None)   # NULL resta NULL
                    elif tipo is None:
                        celle[colonna] = (prima, None, None)  # azzera
                    else:
                        tweak = self.policy.tweak(tabella, colonna)
                        try:
                            celle[colonna] = (prima,
                                              self._trasforma(tipo, prima, tweak,
                                                              verso), None)
                        except ValoreNonTrattabile as e:
                            celle[colonna] = (prima, None, str(e))
                righe.append({"chiave": {c: riga.get(c) for c in chiave},
                              "celle": celle})
            out.append({"tabella": tabella, "chiave": chiave,
                        "colonne": [c for c, _ in colonne], "righe": righe})
        return out

    def non_trattabili(self, tabella, colonna, verso="cifra", av=None):
        """Tutti i valori che non si sanno trattare, con il motivo. Non scrive.

        L'anteprima ne mostra solo quelli del campione: su una colonna vera il
        valore malformato e' quasi sempre piu' in fondo, e l'esecuzione si ferma
        su di lui dopo aver letto tutto. Qui si guarda l'intera colonna, cosi'
        la decisione — fermarsi o saltarli lasciandoli in chiaro — si prende
        avendo l'elenco sotto gli occhi invece di un valore solo.
        """
        regola = self.policy.regola(tabella, colonna) or {}
        tweak = self.policy.tweak(tabella, colonna)
        scarti = []
        for _ in self._coppie(tabella, colonna, regola.get("tipo"), tweak, verso,
                              scarti, "salta", av):
            pass
        return scarti

    # -- esecuzione --------------------------------------------------------- #
    def riprendibili(self, verso="cifra"):
        """Colonne che si possono riprendere da dove si erano fermate.

        Solo quelle interrotte mentre scrivevano **a lotti**: li' il registro
        sa l'ultima chiave committata, quindi si sa cosa e' gia' trattato. Senza
        lotti l'esecuzione era in un'unica transazione, e non c'e' niente da
        riprendere — o e' passata tutta, o e' tornata indietro.
        """
        fuori = []
        for voce in self.registro.interrotte(self.database):
            if voce.get("ultima_chiave") is None:
                continue
            if voce.get("operazione") != verso:
                continue
            regola = self.policy.regola(voce["tabella"], voce["colonna"]) or {}
            if regola.get("strategia") != "cifra":
                continue
            fuori.append(voce)
        return fuori

    def _da_dove(self, tabella, verso, riprendi):
        """Chiave da cui ripartire per questa colonna, o None."""
        if not riprendi:
            return None
        voce = riprendi
        chiave = self._chiave_primaria(tabella)
        if not chiave:
            return None
        # Il registro salva la chiave come testo: qui torna al tipo della
        # colonna, altrimenti il confronto `>` fallisce o — peggio — confronta
        # numeri come stringhe, e '9' > '10'.
        tipo = self.schema()["tabelle"][tabella][chiave]["tipo"].upper()
        grezza = voce["ultima_chiave"]
        try:
            return int(grezza) if any(x in tipo for x in ("INT", "SERIAL")) \
                else grezza
        except (TypeError, ValueError):
            raise VerificaFallita([Problema(
                "errore", "%s.%s" % (tabella, voce["colonna"]),
                "l'ultima chiave registrata (%r) non e' leggibile come %s: "
                "riprendere non e' sicuro, va risolta a mano." % (grezza, tipo))])

    def esegui(self, verso="cifra", su_valore_non_trattabile="ferma",
               avanzamento=None, solo=None, riprendi=None):
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
        try:
            return self._esegui(verso, su_valore_non_trattabile, av, solo,
                                riprendi)
        finally:
            # Il battito e' un thread: va fermato anche se l'esecuzione e'
            # fallita a meta', altrimenti continua a scrivere sopra i messaggi
            # d'errore.
            av.chiudi()

    def _esegui(self, verso, su_valore_non_trattabile, av, solo, riprendi=None):
        # Riprendendo, lo stato 'in_corso' di QUELLA colonna non e' un errore:
        # e' il punto di partenza. Tutti gli altri controlli restano.
        errori = [e for e in self.errori(verso, solo)
                  if not (riprendi and "in_corso" in e.messaggio
                          and e.dove == "%s.%s" % (riprendi["tabella"],
                                                   riprendi["colonna"]))]
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
            # decidere se aspettare o annullare. La fase si annuncia PRIMA,
            # altrimenti quei minuti passano senza che nulla si muova.
            av.inizio(tabella, colonna, tipo, verso)
            av.fase("conto le righe e i valori distinti")
            righe_totali, distinti = db.conta(self.engine, tabella, colonna)
            av.totali(righe_totali, distinti)

            da = self._da_dove(tabella, verso, riprendi)
            if da is not None:
                av.fase("riprendo da %s = %s"
                        % (self._chiave_primaria(tabella), da))
            scarti = []
            # La lettura si completa PRIMA di aprire la transazione di
            # scrittura, e questo `list()` e' la ragione per cui esiste questo
            # commento.
            #
            # Passando il generatore, la lettura avveniva dentro la transazione
            # che aveva appena creato la tabella di appoggio, e da un'altra
            # connessione. Su SQL Server questo si blocca — indefinitamente — e
            # non c'e' modo di distinguerlo da un database lento. Leggere prima
            # rende la situazione impossibile invece che improbabile: mentre si
            # scrive non c'e' nessun'altra connessione aperta.
            #
            # Il prezzo e' la memoria: i valori distinti stanno tutti in RAM
            # (~40 MB per due milioni di codici fiscali). Su un dominio
            # enormemente piu' grande e' il limite da rivedere per primo.
            av.fase("leggo i valori e calcolo i surrogati", contabile=True,
                    totale=distinti)
            coppie = list(self._coppie(tabella, colonna, tipo, tweak, verso,
                                       scarti, su_valore_non_trattabile, av, da))

            # Il registro si segna 'in_corso' SOLO ORA, quando la lettura e'
            # finita e sta per cominciare la scrittura. Segnarlo prima
            # significava che un valore malformato — trovato leggendo, senza
            # aver toccato una riga — lasciava la colonna bloccata: e per
            # sbloccarla serviva un intervento a mano, per un'esecuzione che
            # non aveva scritto niente. 'in_corso' deve voler dire una cosa
            # sola: la scrittura e' cominciata e non e' finita.
            if da is None:
                self.registro.avvia(self.database, tabella, colonna, tipo,
                                    tweak.decode(), self.chiave_id, verso,
                                    lista=self._impronta_lista(tipo))
            toccate = self._scrivi(tabella, colonna, coppie, av, da)
            av.conclusa(toccate)

            # Zero righe toccate significa che la colonna e' rimasta com'era:
            # segnarla 'cifrata' sarebbe una bugia, e per giunta una bugia che
            # blocca il tentativo successivo e autorizza una decifratura su
            # valori mai cifrati.
            stato = CIFRATA if (verso == "cifra" and toccate) else IN_CHIARO
            self.registro.concludi(self.database, tabella, colonna, stato, toccate)
            rapporto["colonne"].append({
                "operazione": "cifra",
                "tabella": tabella, "colonna": colonna, "tipo": tipo,
                "righe_aggiornate": toccate, "non_trattabili": scarti,
            })

        rapporto["colonne"].extend(self._azzera(verso, av, scelta))
        return rapporto

    def _scrivi(self, tabella, colonna, coppie, av, da=None):
        """Una transazione sola, o a lotti di righe. La differenza e' il lock.

        A transazione unica l'operazione e' atomica ma tiene bloccate le righe
        dall'inizio del calcolo alla fine della scrittura. A lotti il blocco
        dura quanto un lotto, ma un'interruzione lascia la colonna **a meta'** —
        e quella meta' va risolta a mano, perche' nessuna operazione automatica
        sarebbe corretta su entrambe.
        """
        chiave = self._chiave_primaria(tabella)
        if not self.lotto_righe or not chiave:
            return db.applica_mappa(self.engine, tabella, colonna, coppie,
                                    avanzamento=av)

        def segna(ultima, righe):
            # Nel registro finisce solo cio' che e' gia' committato: e' l'unico
            # appiglio per capire, dopo un'interruzione, dove ci si era fermati.
            self.registro.avanzamento(self.database, tabella, colonna,
                                      ultima_chiave=str(ultima), righe=righe)

        return db.applica_mappa_a_lotti(
            self.engine, tabella, colonna, coppie, chiave,
            lotto_righe=self.lotto_righe, avanzamento=av, su_lotto=segna,
            da_chiave=da)

    def _chiave_primaria(self, tabella):
        """La chiave primaria, se e' una sola colonna.

        Su una chiave composta i lotti non si sanno delimitare con un semplice
        `>`, e su una tabella senza chiave primaria non esiste un ordine
        stabile: in entrambi i casi si torna alla transazione unica, che non ha
        bisogno di ordinare niente. Meglio un lock lungo che lotti che si
        sovrappongono.
        """
        chiavi = self.schema()["chiavi_primarie"].get(tabella) or []
        return chiavi[0] if len(chiavi) == 1 else None

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
                av.inizio(tabella, colonna, "azzera", verso)
                av.fase("conto le righe")
                righe_totali, distinti = db.conta(self.engine, tabella, colonna)
                av.totali(righe_totali, distinti)
                av.fase("azzero la colonna")
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

    # Ogni quanti valori riportare l'avanzamento. Un lotto di scrittura e'
    # 10.000: aspettare quello significa una barra che si muove ogni parecchi
    # secondi. Qui invece il conteggio segue il lavoro vero, che e' il calcolo
    # dei surrogati, e la barra scorre di continuo.
    PASSO_AVANZAMENTO = 256

    def _coppie(self, tabella, colonna, tipo, tweak, verso, scarti, su_errore,
                av=None, da=None):
        """Generatore (vecchio, nuovo) sui valori distinti.

        Generatore e non lista: `applica_mappa` lo consuma riempiendo la tabella
        di appoggio, quindi la memoria resta limitata anche con milioni di valori
        distinti.
        """
        fatti = 0
        # Riprendendo si leggono solo le righe non ancora trattate: le altre
        # contengono gia' surrogati, e rileggerle significherebbe cifrare il
        # cifrato.
        for blocco in db.leggi_distinti(self.engine, tabella, colonna,
                                        chiave=self._chiave_primaria(tabella),
                                        da=da):
            for v in blocco:
                fatti += 1
                if av and fatti % self.PASSO_AVANZAMENTO == 0:
                    av.avanti(fatti)
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

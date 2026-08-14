# Proteo

**Anonimizzazione reversibile di colonne di database — con una chiave, senza dizionari.**

> *Proteo muta forma di continuo per non farsi catturare; ma chi lo tiene saldo
> lo vede tornare al suo aspetto vero.* La reversibilità è dentro il mito.

Proteo prende un database, sostituisce i dati personali con **surrogati falsi ma
validi** — un codice fiscale diventa un altro codice fiscale che passa il
checksum — e permette di tornare ai valori originali **con la sola chiave**.

Serve a dare un database a un'AI perché ci faccia sopra statistiche, senza che i
dati veri escano mai.

---

## Il problema

Vuoi far analizzare i tuoi dati da un modello potente che sta nel cloud, ma i
dati non possono uscire. Le soluzioni consuete falliscono tutte per un motivo
diverso:

| Approccio | Perché non funziona |
|---|---|
| **Cifrare le colonne** | L'AI legge `8f3aQ9zK2mR7`: può contare i gruppi, non capirli. I report escono illeggibili |
| **Cancellare / mascherare** | Le statistiche muoiono con i dati, e non si torna indietro |
| **Dizionario `valore → surrogato`** | Su un DB grande la mappa è grande quanto i dati: hai creato un secondo problema più grande del primo |
| **Hash / HMAC** | A senso unico: niente reverse |

Proteo prende una quarta strada: **cifratura che preserva il formato**.

---

## L'idea

Un codice fiscale è **quindici caratteri di informazione più un carattere
derivato**. Il carattere di controllo non è informazione: è una funzione degli
altri. Quindi lo si butta, si cifra il corpo, e lo si ricalcola sul risultato.

```
RSSMRA85H12F205 | Y          corpo | controllo
       ↓ FF1 (AES, NIST SP 800-38G)
ULIMAN66R30N560 | I          ricalcolato
```

Il risultato ha tre proprietà che nessun altro approccio ottiene insieme:

- **Valido** — passa `cf_ok()`, entra in un `CHAR(16)`, non richiede `ALTER COLUMN`
- **Reversibile** — con la sola chiave, senza alcuna tabella di appoggio
- **Deterministico e biiettivo** — stesso valore → stesso surrogato ovunque, e
  valori distinti → surrogati distinti: `JOIN`, `UNIQUE` e foreign key sopravvivono

Lo stesso vale per la partita IVA (Luhn) e per l'IBAN (mod-97).

### Nomi e cognomi: niente checksum, una lista

Un nome non ha né struttura né carattere di controllo. Cifrarlo lettera per
lettera darebbe `Mario → Xqfkz`: reversibile, ma un report costruito su quella
colonna smette di sembrare un report. Il surrogato di un nome è quindi **un
altro nome**: si cerca la sua posizione in una lista pubblica di nomi comuni, si
cifra la posizione con FF1, si legge il nome che sta nella posizione risultante.

```
Mario     → Gaia        MARIA    → ANNA        giuseppe → valeria
Rossi     → Di Carlo    DE LUCA  → ACQUAVIVA   D'Angelo → Roberti
```

Lo stile di scrittura si conserva (maiuscole, minuscole, iniziale, apostrofi);
la lunghezza no, perché il surrogato è una voce della lista — per questo la
verifica controlla **prima** che la voce più lunga entri nella colonna, invece
di far fallire l'`UPDATE` a metà tabella.

Non è il dizionario che il progetto rifiuta. Quello è la mappa `valore →
surrogato`, che cresce quanto i dati e va custodita come i dati; questa è una
lista di 400 nomi comuni, uguale per tutti, versionata col codice e pubblica:
non contiene niente del database. La corrispondenza fra un nome e il suo
surrogato non è scritta da nessuna parte — la determina la chiave.

Una lista però non conterrà mai tutti i nomi veri di un database: nomi
stranieri, doppi nomi, `Nome-paz2` lasciato lì da un collaudo. **Chi non è in
lista non si ferma: si cifra lettera per lettera**, conservando la forma.

```
Erika        → Luigi        (lista)     Bartoli      → Eaumvrm       (ripiego)
MASCIA       → COSIMO       (lista)     Maria Matias → Zqqpb Ypwogg  (ripiego)
Pierpaolo    → Giacomo      (lista)     Nome-paz2    → Falm-xys2     (ripiego)
```

Il ripiego non produce un nome plausibile, ma è reversibile, deterministico e
biiettivo come tutto il resto — e mantiene spazi, trattini, cifre e maiuscole
al loro posto. Il surrogato del ripiego **non può mai essere una voce della
lista**: se lo fosse, in decifratura verrebbe preso per l'altro percorso e
restituirebbe un valore diverso dall'originale, in silenzio. Viene quindi
ricifrato finché non cade fuori dalla lista (*cycle-walking*), e la decifratura
cammina allo stesso modo.

Passano dal ripiego anche i valori che **da una voce di lista non
rientrerebbero identici**: `De  Luca` con due spazi (rientrerebbe con uno solo)
e `Rosa maria` (rientrerebbe `Rosa Maria` — da una voce di lista si può
ricostruire solo tutto maiuscolo, tutto minuscolo o le iniziali maiuscole,
perché la lunghezza cambia). Un surrogato plausibile in meno, un valore
restituito diverso da com'era in meno.

Resta un solo caso non trattabile: un valore con meno di due lettere, che avrebbe
un dominio sotto il minimo di FF1 e uscirebbe identico, cioè in chiaro.

> ⚠️ Allargare la lista è gratis **prima** della prima cifratura, non dopo:
> aggiungere una voce sposta le posizioni di tutte quelle che seguono e cambia i
> surrogati già prodotti. Di ogni lista si registra un'impronta accanto alla
> colonna, e chi prova a decifrare con una lista diversa viene fermato prima di
> scrivere. La lista va conservata **insieme alla chiave**.

### Esempio reale

```
tipo   originale                    surrogato                    valido  ritorno
CF     RSSMRA85H12F205Y             ULIMAN66R30N560I             sì      sì
CF     VRDNNA90A41F839L             XAVDPI08D31O774L             sì      sì
PIVA   00743110157                  17497033260                  sì      sì
IBAN   IT60X0542811101000000123456  IT12X1328544065150486661544  sì      sì
```

Non è solo il checksum a tornare: la **struttura** è preservata. Nel codice
fiscale le prime sei restano lettere, la lettera del mese è una delle dodici
possibili, il giorno resta in un intervallo sensato. Nell'IBAN le cifre restano
cifre — uno con lettere dentro l'ABI si riconoscerebbe come falso a colpo d'occhio.

---

## Come funziona

### La chiave

Un file da 256 bit, generato dal tool. **Sostituisce integralmente il
dizionario**: un solo file per l'intero database.

Tre precauzioni, che non sono paranoia:

- **Non si sovrascrive mai** un file di chiave esistente — un `genera` di troppo
  renderebbe illeggibile tutto ciò che è già stato cifrato
- Ogni chiave ha un **identificativo pubblico** che va nel registro accanto a
  ogni colonna: con la chiave sbagliata ci si ferma **prima** di scrivere
- Rifiuta di scrivere la chiave **dentro un repository git**

> ⚠️ **Perdere la chiave significa perdere i dati.** Definitivamente. È la
> conseguenza diretta di non avere dizionari, ed è il prezzo del progetto.

### La policy

Le colonne le scegli tu: Proteo non va a caccia di dati personali per conto suo.
La contropartita è la regola **fail-closed**:

> Ogni colonna delle tabelle dichiarate deve comparire nella policy, anche solo
> per dire `mantieni`. Altrimenti l'esecuzione si ferma.

Costa una riga per colonna la prima volta. In cambio, il giorno in cui una
migration aggiunge `clienti.pec` il processo **si rompe rumorosamente** invece di
consegnare quella colonna in chiaro senza che nessuno lo sappia.

```json
{
  "formato": "proteo-policy-v1",
  "tabelle": {
    "dbo.clienti": {
      "id":             {"strategia": "mantieni"},
      "codice_fiscale": {"strategia": "cifra", "tipo": "CF"},
      "citta":          {"strategia": "mantieni"},
      "note":           {"strategia": "azzera"}
    }
  }
}
```

`azzera` svuota la colonna, e non c'è chiave che la riporti indietro: è l'unica
operazione di Proteo che distrugge invece di trasformare. Per questo compare
nell'anteprima con i valori che stanno per sparire, e il registro la marca
`azzerata` — una colonna svuotata e una colonna nata vuota si somigliano troppo.

### Il registro

Con FPE il surrogato è **indistinguibile dall'originale**: guardando una colonna
non puoi sapere se è già stata trattata. Il registro è l'unica cosa che lo sa.

```
registro/
  VenditeDB/
    dbo.clienti_codice_fiscale.json
    dbo.clienti_partita_iva.json
```

Un file per colonna, non un indice unico: due esecuzioni su colonne diverse non
si contendono lo stesso file, un'interruzione sporca una sola voce, e il
contenuto resta leggibile a mano.

Esiste per fare due controlli, entrambi **prima** di scrivere:

- **Prima di cifrare** — la colonna non deve risultare già cifrata. Un secondo
  passaggio cifrerebbe il cifrato, e da lì non si torna indietro.
- **Prima di decifrare** — la colonna deve risultare cifrata *con questa chiave*.
  Con la chiave sbagliata ci si ferma, invece di riempire la colonna di valori
  irrecuperabili.

Una colonna rimasta in stato `in_corso` segnala un'esecuzione mai conclusa: la
colonna è in uno stato misto e va risolta a mano, perché nessuna operazione
automatica sarebbe corretta su entrambe le metà.

> ⚠️ Il registro sta sul client, **non nel database**: se sposti o ripristini il
> database, il registro non lo segue. Va conservato e copiato **insieme alla chiave**.

### Perché si lavora sui valori distinti, e con un solo UPDATE

Il surrogato è una funzione deterministica del valore: due righe con lo stesso
codice fiscale danno per forza lo stesso risultato. Si elaborano quindi i **valori
distinti**, non le righe — su 100 milioni di righe con 2 milioni di codici fiscali
distinti si fa il 2% del lavoro.

L'idea ovvia per applicarli — un `UPDATE` per valore — è **sbagliata, e in
silenzio**. FF1 è una permutazione, quindi catene e cicli sono la norma: se
`A→B` e `B→C`, il primo `UPDATE` porta le righe `A` in `B`, e il secondo le
ripesca e le manda in `C`. Le righe che erano `A` finiscono in `C`, e nessuno se
ne accorge finché non prova a tornare indietro.

Proteo scrive quindi la mappa completa in una tabella di appoggio ed esegue **un
solo** `UPDATE` con sottoquery: ogni riga si calcola una volta sola, dal proprio
valore originale. La tabella di appoggio vive dentro la transazione e viene
sempre eliminata — è l'unica cosa in tutto il progetto che somigli a un
dizionario, e non deve sopravvivere.

### Il tweak

FF1 non è una sola permutazione: la chiave definisce una **famiglia**, e il tweak
sceglie quale. Non è segreto — la sicurezza sta tutta nella chiave.

Il default è il **nome della colonna**, così colonne omonime in tabelle diverse
condividono il surrogato e i `JOIN` reggono senza dichiarare nulla.

Il difetto di questa regola è insidioso: `clienti.codice_fiscale` e
`contratti.cf_intestatario` sono la stessa persona ma prendono tweak diversi,
quindi surrogati diversi, e **il join si rompe in silenzio**. Per questo Proteo
legge le **foreign key dallo schema** e blocca l'esecuzione se i due lati di una
relazione riceverebbero tweak diversi. La FK è già dichiarata nel database: il
controllo è gratuito e coglie proprio il caso pericoloso.

---

## Stato

Il nucleo crittografico è completo e verificato. L'accesso ai database non è
ancora scritto.

| Modulo | Stato | |
|---|---|---|
| `fpe.py` | ✅ | FF1 (NIST SP 800-38G) — **9/9 vettori ufficiali NIST**, cifratura e decifratura |
| `checksum.py` | ✅ | CF, partita IVA, IBAN, Luhn: verifica **e calcolo** |
| `surrogati.py` | ✅ | CF, PIVA, IBAN strutturati e reversibili; nomi e cognomi da lista |
| `liste.py` | ✅ | nomi e cognomi: dominio della permutazione, con impronta |
| `keyfile.py` | ✅ | generazione e custodia della chiave |
| `policy.py` | ✅ | policy dichiarativa + verifica fail-closed |
| `registro.py` | ✅ | stato per colonna, guardie contro doppia cifratura e chiave sbagliata |
| `db.py` | ✅ | introspezione, lettura a flusso, applicazione della mappa (SQLAlchemy) |
| `motore.py` | ✅ | verifica → anteprima → esecuzione, con i cancelli fail-closed |
| `config.py` | ✅ | configurazione per database, con le condizioni per tenerci la password |
| `cli.py` | ✅ | riga di comando: chiave, bozza di policy, verifica, anteprima, cifra, decifra |
| `menu.py` | ✅ | il menu guidato, connessione compresa |
| `rilevamento.py` | ✅ | riconosce CF/PIVA/IBAN campionando i valori |
| `diagnosi.py` | ✅ | da un errore del driver alla riga di comando che lo risolve |
| scrittura massiva / clone | ⬜ | percorsi veloci per motore (`SqlBulkCopy`, `COPY`), `BACKUP`/`RESTORE` |
| `app.py` | ⬜ | UI web locale |

**250 test, tutti verdi**, incluso il ciclo completo cifra → verifica → decifra su
un database SQLite reale. Il nucleo non dipende da alcun database: si prova senza
un server acceso.

```bash
python -m unittest discover -s tests
```

### Perché i vettori NIST e non un round-trip

`decrypt(encrypt(x)) == x` passa anche con un'implementazione sbagliata, purché
lo sia in modo simmetrico. I vettori del NIST fissano il **testo cifrato atteso**,
quindi verificano che l'algoritmo sia *quello* e non uno che gli somiglia.

---

## Prestazioni

Misurate su un codice fiscale (16 caratteri), Python 3.13:

```
67 µs per valore   →   ~15.000 valori/s per core
100 milioni di valori   ≈   14 minuti su 8 core
```

Il lavoro è perfettamente parallelo: ogni valore è indipendente. **Il
multiprocessing non è un'ottimizzazione, è parte del disegno.**

---

## Installazione

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Il nucleo richiede solo `cryptography`. Gli altri pacchetti servono agli
adattatori dei database e alla UI.

---

## Uso

```bash
bin/proteo
```

Senza argomenti parte il **menu guidato**. Alla prima esecuzione fa qualche
domanda e scrive la configurazione; dopo, tiene sempre in testa allo schermo su
quale database si sta lavorando e cosa risulta già fatto — le due domande a cui
si sbaglia risposta, e sbagliarle significa cifrare il database sbagliato o
cifrare due volte lo stesso.

```
------------------------------------------------------------------------
Proteo — VenditeDB
  postgresql+psycopg://utente:***@host:5432/vendite
  policy /root/.proteo/vendite-policy.json
  registro /root/.proteo/registro
------------------------------------------------------------------------

Cosa vuoi fare?
  1) stato del registro — cosa risulta gia' fatto
  2) verifica (non scrive)
  3) anteprima prima/dopo (non scrive)
  4) CIFRA — scrive sul database
  5) DECIFRA — riporta in chiaro, scrive sul database
  6) crea la bozza di policy
  7) genera la chiave
  8) cambia database
  9) esci
```

Le voci che scrivono non si confermano con un tasto ma scrivendo `si`: da un
menu numerato, un tasto di troppo è esattamente il modo in cui si lancia il
comando accanto a quello che si voleva.

### La connessione

Non si scrive un URL a memoria: si risponde a host, porta, utente, password, e
Proteo **prova subito a connettersi**. `create_engine` è pigro e non contatta
nessuno, quindi una stringa sbagliata sembra funzionare fino a metà della prima
operazione vera; la prova sposta la scoperta all'unico momento in cui costa
poco.

Quando fallisce, l'errore arriva dal driver e parla la lingua del driver.
Proteo lo traduce nella mossa successiva:

```
non riesco a connettermi.
  ImportError: libodbc.so.2: cannot open shared object file: No such file or directory

  manca il gestore ODBC (unixODBC), che pyodbc carica a runtime:
      apt install unixodbc          # Debian/Ubuntu
    Non basta `pip install pyodbc`: quello è il ponte, questa è la libreria di sistema sotto.
```

Sono riconosciuti i casi che si incontrano davvero: driver ODBC assente o con il
nome sbagliato, pacchetto Python mancante, credenziali rifiutate, certificato
autofirmato, host che non si risolve, porta chiusa, firewall. Quando nessuna
regola scatta non si inventa niente — un suggerimento sbagliato si prova, e fa
perdere più tempo dell'errore che pretendeva di spiegare.

### Guardare i record prima di scrivere

Prima di ogni conferma, le prime trenta righe come le vedrebbe chi conosce i
dati — con la chiave primaria accanto, per poterle ritrovare nel database:

```
clienti — prime 4 righe
  id | codice_fiscale                      | cognome             | nome
  ---------------------------------------------------------------------
  0  | RSSMRA85H12F205Y → QRZSUW98C50X584N | Rossi → Castellani  | Mario → Alberto
  1  | BNCLGU78T04H501C → KOGWDD55P53M515F | De Luca → Pastore   | Anna → Serena
  2  | NON-UN-CF  !! non trattabile        | D'Angelo → Agostini | Ludmila  !! non trattabile
  3  | MRTPLA65M15L219C → RHBLPF31L59C184N | Esposito → Gargano  | Lucia → Primo

  codice_fiscale: codice fiscale di lunghezza o alfabeto errati
  nome: 'Ludmila' non è fra le 241 voci di nomi.txt
```

È una vista diversa dall'anteprima per valori distinti, non un duplicato:
quella risponde alla domanda del motore («come si trasforma questo valore»),
questa a quella di chi conosce i dati («com'era questo record, cosa diventa»).
L'errore che solo questa fa vedere è di aver puntato la colonna sbagliata — un
valore isolato non lo dice, la riga intera sì. E i valori non trattabili
compaiono qui, prima di scrivere, invece che nel rapporto finale.

Se le colonne sono troppe per una riga di terminale la tabella viene spezzata in
blocchi, ripetendo la chiave: lasciarla tagliare al terminale renderebbe
impossibile capire quale valore appartiene a quale colonna.

Da riga di comando è `--righe N` (default 30, `--righe 0` per non vederla).
**Con `--si` non viene mostrata a meno di chiederla esplicitamente**: l'uso da
script finisce quasi sempre in un file di log, e lì quei valori veri
resterebbero scritti in chiaro.

### Dove sta lo stato, e cosa succede se ripristini il database

Dei surrogati **non viene salvato niente**: sono una funzione della chiave, del
tweak e del valore, e si ricalcolano quando servono. L'unica cosa che Proteo
scrive fuori dal database è il registro, che dice *che* una colonna è stata
trattata, non *come*:

```json
{
  "database": "edw", "tabella": "T_Pazienti", "colonna": "Nome",
  "stato": "cifrata", "tipo": "NOME", "tweak": "nome",
  "chiave_id": "271462469c…", "lista": "1353bd6f52…",
  "righe": 7300, "aggiornato": "2026-08-13T08:00:06+00:00",
  "storico": [ … ]
}
```

Nessun valore vero, nessun surrogato, nessuna corrispondenza. La sola cosa che
somiglia a un dizionario è la tabella di appoggio, che vive dentro la
transazione e viene sempre eliminata (vedi `pulisci`).

**Quindi sì: ripristinare un backup anteriore alla cifratura annulla tutto.** È
anzi l'unico modo di tornare indietro se hai perso la chiave. Ma il registro sta
sul client e il restore non lo tocca: resta a dire `cifrata` su colonne tornate
in chiaro, e con quel disallineamento `decifra` decifrerebbe dei valori *veri*,
producendone altri formalmente validi e completamente sbagliati — in silenzio,
perché un codice fiscale decifrato è un codice fiscale valido. Va riallineato:

```bash
bin/proteo ripristino
```

Chiede l'unica cosa che il programma non può sapere — se il backup è anteriore o
posteriore ai trattamenti — e nel primo caso riporta le colonne a `in chiaro`.
La chiave resta valida e non va rigenerata.

### Quando una colonna resta `in_corso`

È il segnale che un'esecuzione non è mai finita, e blocca sia `cifra` sia
`decifra` su quella colonna. Per uscirne serve una decisione umana, e non è una
mancanza del programma: **un surrogato è indistinguibile da un valore vero** —
è il punto di tutto il progetto — quindi nessun controllo automatico può dire
se quella colonna sia stata scritta o no.

Proteo però dice cosa è successo, e da lì la risposta è quasi sempre ovvia:

```bash
bin/proteo risolvi
```

```
1 colonne in stato 'in_corso' per edw:
  T_Pazienti.Nome  avviata 2026-08-13T08:00:06  operazione=cifra  elaborati=0/7300

Senza 'ultima_chiave' l'esecuzione era in un'unica transazione: o è passata
tutta — e allora il registro direbbe 'cifrata' — o il database l'ha annullata,
e la colonna è rimasta com'era.
```

Senza `ultima_chiave` la finestra ambigua è di una frazione di secondo (fra il
commit del database e la scrittura nel registro): se il processo è stato ucciso,
la transazione è tornata indietro e la colonna è in chiaro. Con `ultima_chiave`
— cioè scrivendo a lotti — la colonna è **mista**, e va sistemata a mano prima
di dichiarare qualunque cosa: le righe fino a quella chiave sono trattate, le
altre no.

```bash
bin/proteo risolvi --tabella T_Pazienti --colonna Nome --stato in_chiaro
```

La dichiarazione resta nello storico della voce, così chi guarda dopo vede che
c'è stato un intervento manuale.

### Perché durante la cifratura il database sembra bloccato

Non è un difetto, è il prezzo dichiarato di una scelta: **tutto avviene in una
sola transazione**. Le righe della tabella restano bloccate dall'inizio del
calcolo dei surrogati fino alla fine dell'`UPDATE`, quindi chi legge quella
tabella da un'altra sessione aspetta. Su SQL Server, oltre una certa quantità di
righe il lock passa da riga a tabella intera, e l'attesa diventa evidente.

Se il blocco è un problema — perché altre applicazioni leggono quella tabella
mentre Proteo lavora — si può scrivere **a lotti di righe** invece che in
un'unica transazione:

```bash
bin/proteo cifra --lotto-righe 1000
```

o, per non ripeterlo ogni volta, `"lotto_righe": 1000` nella voce del config.
Il lock allora dura quanto un lotto, non quanto l'intera colonna.

I lotti sono **per chiave primaria**, e non è un dettaglio: a lotti di *valori*
si ricadrebbe esattamente nell'effetto domino descritto sopra — il lotto che
porta le righe `A` in `B` le esporrebbe al lotto successivo, che le manderebbe
in `C`. Per chiave gli intervalli sono disgiunti e ordinati, quindi ogni riga
viene toccata una volta sola. La mappa resta completa prima che il primo lotto
parta, come nella versione atomica. Su una tabella senza chiave primaria, o con
chiave composta, Proteo torna da sé alla transazione unica: senza un ordine
stabile i lotti si sovrapporrebbero.

Il prezzo dei lotti è dichiarato: **si perde l'atomicità**. Un'interruzione
lascia la colonna a metà — le righe fino all'ultima chiave trattata sono
cifrate, le altre no — e quella metà va risolta a mano, perché guardando la
colonna non si distingue quale riga è già passata. L'ultima chiave raggiunta
finisce nel registro, ed è l'unico appiglio per capire dove ci si era fermati.
Inoltre la tabella di appoggio vive fuori dalla transazione, quindi un processo
ucciso la lascia su disco con dentro la mappa in chiaro: `pulisci` la rimuove.

Il default resta la transazione unica, perché fra un lock lungo e una colonna a
metà il primo è il danno reversibile.

**Se interrompi**, il database annulla la transazione, e il rollback può durare
quanto il lavoro fatto fino a quel momento: fino ad allora i lock restano. Da
qui due regole pratiche: dai un `Ctrl-C` solo e aspetta, e non mandare `kill -9`
— quello lascia la sessione aperta lato server finché la connessione non cade da
sola, e in quel tempo la tabella resta inutilizzabile. Su SQL Server, se devi
liberarla:

```sql
SELECT session_id, blocking_session_id, status, command, wait_type
FROM sys.dm_exec_requests WHERE blocking_session_id <> 0;
```

```sql
KILL 57;   -- il session_id che blocca
```

Dopo un'interruzione, `stato` dice cosa è rimasto in mezzo, e `pulisci` rimuove
le tabelle di appoggio sopravvissute:

```bash
bin/proteo pulisci
```

Quelle tabelle contengono la corrispondenza in chiaro fra valori veri e
surrogati — l'unica cosa in tutto il progetto che somigli a un dizionario —
quindi vengono cercate invece di aspettare che qualcuno le noti. Con una
transazione andata a buon fine non ne resta nessuna; ne sopravvive una solo se
il processo è stato ucciso su un motore dove il `CREATE TABLE` non è
transazionale (MySQL, SQLite).

### Quando la sessione SSH cade

Due problemi diversi, e due risposte diverse.

**Il processo muore con la sessione.** `--sfondo` lo stacca dal terminale — è
`nohup` fatto dal programma, così non c'è da ricordarselo:

```bash
bin/proteo cifra --sfondo
```

```
cifra: 1 colonne su Grande
  grosse.piva (PIVA)

procedere? il database verra' modificato [y/n]: y
avviato in sottofondo: pid 19118
  log:     tail -f proteo-cifra.log
  stato:   proteo stato

Puoi chiudere la sessione: il processo continua.
```

La conferma resta in primo piano: ciò che scrive non parte senza che qualcuno
abbia risposto. Poi si segue da qualsiasi altra sessione con `stato`, o dal log.

**Riprendere ciò che si era interrotto.** Possibile solo scrivendo a lotti,
perché è lì che il registro sa l'ultima chiave committata:

```bash
bin/proteo cifra --lotto-righe 1000 --sfondo
```

```bash
bin/proteo riprendi
```

```
riprendo T_Pazienti.Nome dalla chiave 184000 (184000 righe gia' fatte il 2026-08-13T08:00)
```

Riprendere legge i valori distinti **solo dalle righe non ancora trattate** e
scrive solo su quelle: rileggere le altre significherebbe cifrare il cifrato, e
da lì non si torna indietro. Il risultato è identico a quello di una corsa mai
interrotta — c'è un test che confronta le due cose riga per riga, perché se
questa proprietà si rompe non produce un errore ma un dato corrotto in silenzio.

Il menu lo propone da sé: se una colonna è rimasta a metà, prima di chiedere
qualunque altra cosa dice dov'era arrivata e offre di continuare.

Senza lotti non c'è niente da riprendere, e non è una mancanza: in transazione
unica o l'esecuzione è passata tutta, o il database l'ha annullata. Non esiste
un "a metà" da cui ripartire — lo dice `risolvi`.

### Seguire una cifratura lunga

Su una colonna da milioni di valori l'operazione dura minuti o ore, e senza
niente da guardare non si distingue un lavoro che procede da uno piantato. La
differenza pratica è che nel dubbio si interrompe — e interrompere a metà lascia
la colonna in uno stato misto.

```
cifra dbo.pazienti.cognome (COGNOME)
  ⠙ conto le righe e i valori distinti  da 12s
  2,4M righe, 380,0k valori distinti
  ████████████░░░░░░░░░░░░  50%  190,0k/380,0k  33,0k/s  trascorsi 5s  mancano 5s
  ⠋ eseguo l'UPDATE sulle righe (istruzione unica)  da 1m 14s
  fatto: 2,4M righe aggiornate in 1m 26s
```

**La riga si aggiorna da sola, non solo quando succede qualcosa.** È la
differenza che conta: le fasi più lunghe non hanno eventi da cui aggiornarsi —
il conteggio iniziale è una scansione della tabella, e l'`UPDATE` finale è una
sola istruzione che su una tabella grande è il pezzo più lungo di tutti. Un
thread ridisegna a intervalli fissi, così il cronometro scorre anche mentre il
processo è fermo dentro una chiamata al database, ed è quello a dire "sta
lavorando" quando non c'è una percentuale da mostrare.

A terminale è una riga che si riscrive; dentro un file di log diventano righe
intere — ogni trenta secondi **o ogni 5% percorso**, così un lavoro di due ore e
uno di due minuti lasciano lo stesso numero di righe, una ventina, che è quanto
serve a ricostruire l'andamento rileggendo il file.

**E se hai chiuso la sessione?** L'avanzamento viene scritto anche nel registro,
ogni pochi secondi, quindi `stato` risponde da qualunque altro terminale:

```bash
nohup bin/proteo cifra --si > cifra.log 2>&1 &
```

```bash
bin/proteo stato
```

```
in_corso  grosse.piva  tipo=PIVA tweak=piva chiave_id=cd03…
          152,6k/400,0k valori  38%  da 5s  mancano ~9s  leggo i valori e calcolo i surrogati
```

È anche ciò che distingue i due significati di `in_corso`: un'esecuzione che sta
lavorando adesso, e una rimasta a metà mesi fa. Se i contatori avanzano fra due
`stato`, sta lavorando.

L'avanzamento sul disco si aggiorna ogni due secondi: abbastanza spesso da
vedere i numeri muoversi fra due `stato` consecutivi, che è il modo pratico di
distinguere un processo vivo da uno morto.

### Una colonna alla volta

`CIFRA` chiede prima come procedere: tutto ciò che la policy dichiara, oppure
**una colonna alla volta**. Il passo a passo sceglie la tabella, poi la colonna
— con accanto la strategia dichiarata e cosa risulta al registro — e se quella
colonna non ha ancora una decisione, la chiede dopo aver mostrato cosa contiene:

```
Quale colonna di clienti?
  1) citta                    mantieni       registro: in_chiaro
  2) codice_fiscale           cifra CF       registro: cifrata
  3) piva                     mantieni       registro: in_chiaro

clienti.piva — 4 valori guardati
    00743110157
    17497033260

  riconosciuto: PIVA (4 valori su 4 passano il checksum)

Cosa faccio di clienti.piva?
  1) cifra come PIVA   <- riconosciuto
  2) cifra come CF
  3) cifra come IBAN
  4) lascia in chiaro (mantieni)
  5) SVUOTA la colonna (azzera) — non torna indietro
```

La scelta viene scritta nella policy, quindi il passo a passo **costruisce** il
documento invece di aggirarlo. Due proprietà restano intatte:

- **il fail-closed vale sull'intera policy**, non solo sulla colonna scelta: si
  scrive su una colonna, ma non si parte se il documento nel suo insieme non sta
  in piedi. Una colonna dimenticata resta dimenticata anche mentre se ne tratta
  un'altra;
- **il controllo di stato è per colonna**: una colonna già cifrata la settimana
  scorsa non impedisce di trattare quella accanto, ma rifarla è bloccato.

### Scrivere la policy senza scriverla

Il fail-closed pretende una riga per ogni colonna. Su dieci colonne è un
fastidio; su un data warehouse è un lavoro che nessuno fa bene fino in fondo — e
una policy scritta male è peggio di una assente, perché dà l'impressione che
qualcuno abbia guardato.

Quindi Proteo la riconosce **dai valori, non dai nomi**: legge un campione di
ogni colonna di testo e guarda se passano i checksum.

```
5 colonne da decidere. Campiono i valori per riconoscerle...

Riconosciute 2 colonne guardando i valori (non i nomi):
  clienti.codice_fiscale       CF    4 valori su 4 passano il checksum
  clienti.piva                 PIVA  4 valori su 4 passano il checksum

Cosa ne faccio?
  1) cifra tutte quelle riconosciute
  2) decido colonna per colonna
  3) nessuna: le metto tutte a 'mantieni' e scelgo dal file
```

`RSSMRA85H12F205Y` è riconoscibile per quello che è, si chiami la colonna
`codice_fiscale`, `cf_cli`, `taxid` o `campo7`: i nomi mentono e cambiano fra
sistemi, il checksum no. I numeri restano in vista perché `14 su 14` è un fatto
mentre `8 su 14` è una colonna mista che merita un'occhiata, e **la proposta va
confermata**: un rilevatore che decidesse da solo sposterebbe la responsabilità
della policy da chi conosce i dati a un'euristica, che è esattamente ciò che il
fail-closed esiste per impedire.

La stessa cosa da riga di comando:

```bash
bin/proteo bozza-policy --rileva
```

Su una policy già completa non c'è nulla di "nuovo" da esaminare, ma è proprio
il caso in cui serve — tutte le colonne sono nate `mantieni` e nessuno le ha
ancora guardate. Per quello c'è `--rivedi`, che riesamina anche le colonne già
dichiarate `mantieni`:

```bash
bin/proteo bozza-policy --rileva --rivedi
```

Rilanciarlo dopo una migration aggiunge solo le colonne comparse nel frattempo,
senza toccare le decisioni già scritte. È così che una policy sopravvive al
tempo: se rigenerarla cancellasse ogni `cifra` scelto a mano, non la si
rigenererebbe mai e invecchierebbe — cioè il problema che il fail-closed doveva
impedire.

### La configurazione

Un file per macchina, `./proteo.json` o `~/.proteo/proteo.json`, con un blocco
per database. Lo crea il menu, ma è leggibile e si corregge a mano:

```json
{
  "formato": "proteo-config-v1",
  "predefinito": "vendite",
  "database": {
    "vendite": {
      "url": "postgresql+psycopg://utente@host:5432/vendite",
      "chiave": "/root/.proteo/vendite.key",
      "policy": "/root/.proteo/vendite-policy.json",
      "registro": "/root/.proteo/registro",
      "etichetta": "VenditeDB"
    }
  }
}
```

I percorsi relativi si risolvono rispetto al file di config, non alla directory
corrente: `"registro": "registro"` indica sempre la stessa cartella da qualunque
punto si lanci Proteo. Un registro che cambia con la directory è un registro
perso, cioè una colonna che nessuno sa più se è cifrata.

La **password** può stare nel config — è il file che descrive quel database — ma
a due condizioni, verificate a ogni lettura: il file non dev'essere leggibile da
altri utenti (`chmod 600`) e non deve poter finire in un commit. Se manca, si
prende da `$PROTEO_PASSWORD` o si chiede a terminale. Senza password nel file non
c'è nessun vincolo: non c'è niente da proteggere.

Dentro un repository il config può starci, anche in una sottocartella: la
condizione non è *dove* sta il file, ma se `git` lo escluderebbe davvero. Lo
chiede a git (`check-ignore`) invece di interpretare i `.gitignore` per conto
suo, e se il file è già tracciato lo considera non escluso — un file che git
segue continua a finire in ogni commit qualunque cosa dica il `.gitignore`.

Per la **chiave** la regola resta più severa: niente repository, ignorata o no.
Un `.gitignore` lì non basta, perché `git clean -xdf` cancella proprio i file
ignorati, e perdere la chiave significa perdere i dati.

### Comandi singoli

Un menu non si mette in uno script, quindi ogni voce esiste anche come comando.
Gli argomenti non passati si leggono dal config, e **solo gli ultimi due
scrivono**:

```bash
bin/proteo prova                          # solo la connessione
bin/proteo chiave ~/.proteo/vendite.key   # una volta sola, per sempre
bin/proteo bozza-policy                   # tutte le colonne a 'mantieni'
bin/proteo stato
bin/proteo verifica
bin/proteo anteprima
bin/proteo cifra --rapporto rapporto.json
bin/proteo decifra
```

`bozza-policy` allinea la policy allo schema: le colonne nuove entrano come
`mantieni`, le scelte già fatte non si toccano mai. Con `--rileva` propone da sé
le colonne da cifrare (vedi sotto).

`cifra` richiama la verifica da sé e chiede conferma esplicita; `--si` la salta,
per cron e script. `--url`, `--chiave`, `--policy`, `--registro` scavalcano il
config quando serve, e `$PROTEO_URL` sta in mezzo ai due.

`bin/proteo` è un avvio da tre righe: trova il `.venv` del progetto e sistema il
`PYTHONPATH`. Equivale a `PYTHONPATH=src python -m proteo.cli`.

---

## Limiti noti

Dichiarati apertamente:

- **Codici fiscali omocodici non trattati.** Quando l'Agenzia sostituisce le
  cifre con lettere per sciogliere una collisione, Proteo **rifiuta** il valore
  invece di produrre un risultato non reversibile.
- **Il CIN dell'IBAN italiano non viene ricalcolato.** Il mod-97 torna; un
  validatore che controlla anche il CIN no.
- **Data di nascita e sesso vengono cifrati** dentro il codice fiscale. È il
  default sicuro, ma se all'AI servono statistiche per età o genere quei dati
  devono venire da colonne dedicate.
- **Il testo libero non è trattato.** Colonne come `note` o `descrizione`
  richiedono riconoscimento di entità, non cifratura di campo. Una colonna
  `nome`, invece, è tutta un nome: quella si tratta.
- **I nomi fuori lista non restano nomi.** Vengono cifrati lettera per lettera,
  quindi la colonna diventa un misto di nomi plausibili e stringhe evidentemente
  finte. Allargare la lista riduce il fenomeno, ma va fatto prima di cifrare.
- **Le lettere accentate fuori lista restano al loro posto.** `Nicolò` non in
  lista diventa `Xqfzò`: l'accento non appartiene all'alfabeto cifrato.
- **Il nome cifrato non conserva il genere.** `Mario` può diventare `Gaia`: se
  servono statistiche per genere devono venire da una colonna dedicata. È la
  stessa scelta fatta per la data di nascita dentro il codice fiscale.
- **Sui nomi il determinismo espone di più.** `Mario` è frequente, e il
  surrogato più frequente sarà quello che lo sostituisce: con una tabella delle
  frequenze dei nomi italiani i più comuni si rimappano contando le occorrenze,
  qualunque tweak. Sui codici fiscali non succede perché sono quasi tutti unici.
- **Il determinismo rivela le uguaglianze.** Su una colonna a bassa cardinalità
  (`sesso`, `provincia`) il surrogato si rimappa contando le occorrenze, con
  qualunque tweak. Quelle colonne vanno generalizzate o lasciate in chiaro: il
  tweak protegge dal correlare *fra* colonne, non dal contare *dentro* una.
  Proteo si rifiuta di cifrare domini sotto i 100 valori, invece di dare
  l'illusione di averli protetti.

### Nessun ripiego silenzioso

Un valore malformato solleva `ValoreNonTrattabile` e sarà la policy a decidere se
saltarlo, azzerarlo o segnalarlo. Se Proteo cifrasse "alla cieca" un valore fuori
struttura, il risultato uscirebbe strutturalmente valido e in decifratura verrebbe
letto con l'altro percorso — restituendo un valore **diverso dall'originale, senza
che nessuno se ne accorga**. È il tipo di errore che non si scopre mai.

---

## Struttura

```
proteo/
├─ bin/proteo        avvio (venv + PYTHONPATH)
├─ src/proteo/
│  ├─ fpe.py         FF1 — nessuna dipendenza da database
│  ├─ checksum.py    verifica e calcolo dei checksum italiani
│  ├─ surrogati.py   CF, PIVA, IBAN, nomi e cognomi
│  ├─ liste.py       le liste di nomi (dati/nomi.txt, dati/cognomi.txt)
│  ├─ keyfile.py     la chiave
│  ├─ repo.py        sono dentro un repo git? questo file e' escluso dai commit?
│  ├─ policy.py      colonne dichiarate + verifica fail-closed
│  ├─ registro.py    stato per colonna (file locali, una cartella per database)
│  ├─ db.py          adattatore SQLAlchemy: introspezione, lettura, scrittura
│  ├─ motore.py      orchestrazione: verifica, anteprima, esecuzione
│  ├─ config.py      configurazione per database
│  ├─ stampa.py      come si mostrano problemi, anteprime e rapporti
│  ├─ cli.py         riga di comando
│  ├─ menu.py        menu guidato
│  ├─ rilevamento.py cosa contiene una colonna, guardando i valori
│  └─ diagnosi.py    errori di connessione tradotti in mosse
└─ tests/
```

I moduli del nucleo non importano nulla di legato ai database: è deliberato. È il
punto in cui un errore costa di più, e deve essere verificabile da solo.

---

## Licenza

[MIT](LICENSE) © 2026 Mario Vicidomini.

L'aritmetica dei checksum (codice fiscale, partita IVA, IBAN, Luhn) implementa
algoritmi pubblici — il codice fiscale è definito per legge, Luhn e il mod-97
sono standard ISO. La verifica dei checksum ricalca quella di
[rizzo-pii](https://github.com/Rizzo-AI-Academy/rizzo-pii) (MIT); il *calcolo*
del carattere di controllo, che è ciò su cui si regge Proteo, è nuovo.

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
| `surrogati.py` | ✅ | CF, PIVA, IBAN strutturati e reversibili |
| `keyfile.py` | ✅ | generazione e custodia della chiave |
| `policy.py` | ✅ | policy dichiarativa + verifica fail-closed |
| `registro.py` | ✅ | stato per colonna, guardie contro doppia cifratura e chiave sbagliata |
| `db.py` | ✅ | introspezione, lettura a flusso, applicazione della mappa (SQLAlchemy) |
| `motore.py` | ✅ | verifica → anteprima → esecuzione, con i cancelli fail-closed |
| scrittura massiva / clone | ⬜ | percorsi veloci per motore (`SqlBulkCopy`, `COPY`), `BACKUP`/`RESTORE` |
| `app.py` | ⬜ | UI web locale |

**69 test, tutti verdi**, incluso il ciclo completo cifra → verifica → decifra su
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
  richiedono riconoscimento di entità, non cifratura di campo.
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
├─ src/proteo/
│  ├─ fpe.py         FF1 — nessuna dipendenza da database
│  ├─ checksum.py    verifica e calcolo dei checksum italiani
│  ├─ surrogati.py   CF, PIVA, IBAN
│  ├─ keyfile.py     la chiave
│  ├─ policy.py      colonne dichiarate + verifica fail-closed
│  ├─ registro.py    stato per colonna (file locali, una cartella per database)
│  ├─ db.py          adattatore SQLAlchemy: introspezione, lettura, scrittura
│  └─ motore.py      orchestrazione: verifica, anteprima, esecuzione
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

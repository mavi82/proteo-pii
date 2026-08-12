# -*- coding: utf-8 -*-
"""Da un errore di connessione alla mossa successiva.

Un fallimento di `create_engine` o di `connect` arriva dal driver, e parla la
lingua del driver: `libodbc.so.2: cannot open shared object file`, `IM002`,
`getaddrinfo failed`. Sono messaggi esatti e inutilizzabili — dicono cosa non e'
andato, mai cosa fare. Qui si traducono nella riga di comando che li risolve.

Il riconoscimento e' per sottostringa e per forza approssimativo: quando nessuna
regola scatta si ritorna None e il messaggio originale resta l'ultima parola.
Meglio nessun suggerimento che uno sbagliato, perche' un suggerimento sbagliato
si prova, e fa perdere piu' tempo dell'errore che pretendeva di spiegare.
"""

__all__ = ["suggerimento"]

# (sottostringhe da cercare, cosa fare). L'ordine conta: la prima che scatta
# vince, quindi le cause piu' precise stanno sopra a quelle generiche.
_REGOLE = [
    (("libodbc.so",),
     "manca il gestore ODBC (unixODBC), che pyodbc carica a runtime:\n"
     "    apt install unixodbc          # Debian/Ubuntu\n"
     "  Non basta `pip install pyodbc`: quello e' il ponte, questa e' la libreria "
     "di sistema sotto."),

    (("data source name not found", "im002", "can't open lib", "file not found"),
     "unixODBC c'e' ma non trova il driver del database. Per SQL Server:\n"
     "    ACCEPT_EULA=Y apt install msodbcsql18\n"
     "  e nell'URL il nome deve coincidere esattamente: "
     "driver=ODBC+Driver+18+for+SQL+Server.\n"
     "  I driver installati si elencano con: odbcinst -q -d"),

    (("no module named 'pyodbc'",),
     "manca il pacchetto pyodbc:  .venv/bin/pip install pyodbc\n"
     "  (su Debian serve anche: apt install unixodbc-dev python3-dev build-essential)"),
    (("no module named 'psycopg'",),
     "manca il driver PostgreSQL:  .venv/bin/pip install 'psycopg[binary]'"),
    (("no module named 'pymysql'",),
     "manca il driver MySQL:  .venv/bin/pip install PyMySQL"),

    # Prima del caso generico: SQL Server usa lo stesso 18456 ("Login failed")
    # anche quando le credenziali sono giuste ma il database non si apre. Senza
    # questa regola si va a caccia della password per un'ora, e la password e'
    # corretta.
    (("cannot open database", "requested by the login"),
     "le credenziali vanno bene, ma quel database non si apre: non esiste, si "
     "chiama diversamente, o l'utente non ha accesso.\n"
     "  I nomi veri:  SELECT name FROM sys.databases\n"
     "  Su Linux SQL Server distingue maiuscole e minuscole nei nomi."),

    (("login failed for user", "password authentication failed",
      "access denied for user", "authentication failed"),
     "utente o password non accettati dal server. La password non e' stata "
     "provata due volte: se e' nel config, correggila li'.\n"
     "  Se sei sicuro delle credenziali, prova a connetterti senza indicare il "
     "database: SQL Server risponde 'Login failed' anche quando l'accesso e' "
     "buono ma il database richiesto non si apre."),

    (("certificate verify failed", "self-signed certificate", "ssl provider",
      "certificate chain"),
     "il certificato TLS del server non e' verificabile — tipico dei server "
     "interni.\n  SQL Server: aggiungi TrustServerCertificate=yes all'URL "
     "(cifra la connessione ma non verifica chi c'e' dall'altra parte).\n"
     "  PostgreSQL: sslmode=require fa lo stesso."),

    (("does not exist", "unknown database", "cannot open database"),
     "il server risponde ma quel database non c'e': controlla il nome "
     "(maiuscole comprese) e che l'utente possa vederlo."),

    (("getaddrinfo", "name or service not known", "could not translate host name",
      "unknown host"),
     "il nome dell'host non si risolve: controlla l'ortografia, oppure usa "
     "l'indirizzo IP."),

    (("connection refused", "actively refused"),
     "l'host risponde ma nessuno ascolta su quella porta: servizio spento, "
     "porta sbagliata, o il server accetta solo connessioni locali."),

    (("timeout", "timed out", "unable to connect"),
     "nessuna risposta entro il tempo massimo: di solito e' un firewall o un "
     "security group che blocca la porta, non il database."),
]


def suggerimento(errore):
    """Cosa fare, o None se non si e' capito."""
    testo = ("%s %s" % (type(errore).__name__, errore)).lower()
    for aghi, cosa_fare in _REGOLE:
        if any(a in testo for a in aghi):
            return cosa_fare
    return None

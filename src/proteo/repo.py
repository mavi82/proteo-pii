# -*- coding: utf-8 -*-
"""Due domande sul repository: sono dentro? questo file e' escluso dai commit?

Le fa chi sta per scrivere un segreto su disco — la chiave e il file di
configurazione. Stanno qui e non dentro `keyfile.py` perche' servono a due
moduli, e perche' non hanno niente a che vedere con la crittografia.

Se `git` non c'e', o la cartella non e' un repository, entrambe rispondono
`False`: chi chiama deve poter procedere, non fermarsi perche' non ha saputo
rispondere.
"""

import subprocess
from pathlib import Path

__all__ = ["dentro_un_repo_git", "ignorato_da_git"]


def _git(percorso, *argomenti):
    """Esegue git nella cartella del file. None se git non risponde."""
    try:
        return subprocess.run(("git",) + argomenti,
                              cwd=str(Path(percorso).resolve().parent),
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None


def dentro_un_repo_git(percorso):
    r = _git(percorso, "rev-parse", "--is-inside-work-tree")
    return bool(r) and r.returncode == 0 and r.stdout.strip() == "true"


def ignorato_da_git(percorso):
    """True se un `git add` lo salterebbe, per via di un .gitignore.

    Si passa il solo nome con `cwd` sulla cartella: cosi' valgono tutti i
    .gitignore che lo riguardano, compreso quello della sottocartella in cui il
    file sta.

    Nota sul comportamento di `git check-ignore`: un file **gia' tracciato**
    risulta non ignorato anche se una regola lo nomina — ed e' la risposta
    giusta per noi. Un file gia' in git continua a finire in ogni commit
    qualunque cosa dica il .gitignore, quindi non e' affatto escluso.
    """
    p = Path(percorso).resolve()
    r = _git(p, "check-ignore", "-q", p.name)
    return bool(r) and r.returncode == 0

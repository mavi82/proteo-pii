# -*- coding: utf-8 -*-
"""Proteo — anonimizzazione reversibile di colonne di database.

Deliberatamente **senza import**: `proteo.fpe`, `proteo.checksum` e
`proteo.surrogati` non devono dipendere da SQLAlchemy ne' da un driver di
database. Tirarli dentro qui renderebbe impossibile provare il nucleo
crittografico senza un server acceso, che e' invece la proprieta' su cui si
regge tutta la suite di test.
"""

__version__ = "0.1.0"

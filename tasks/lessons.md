# Lezioni Apprese e Pattern da Evitare

## L1 - datetime.utcnow() è deprecato
`datetime.utcnow()` è deprecato in Python 3.12+. Usare sempre `datetime.now(timezone.utc)` per evitare DeprecationWarning e garantire oggetti timezone-aware.

## L2 - Ruff prima del commit
Eseguire sempre `ruff check` dopo `black` per catturare import inutilizzati e violazioni PEP-8 non coperte dal formatter.

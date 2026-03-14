# Lezioni Apprese e Pattern da Evitare

## L1 - datetime.utcnow() è deprecato
`datetime.utcnow()` è deprecato in Python 3.12+. Usare sempre `datetime.now(timezone.utc)` per evitare DeprecationWarning e garantire oggetti timezone-aware.

## L2 - Ruff prima del commit
Eseguire sempre `ruff check` dopo `black` per catturare import inutilizzati e violazioni PEP-8 non coperte dal formatter.

## L3 - FastAPI lifespan > on_event
`@app.on_event("startup")` è deprecato in FastAPI recenti. Usare il pattern `lifespan` async context manager per startup/shutdown.

## L4 - SPA vanilla > framework per dashboard semplici
Per dashboard di monitoraggio senza interazioni complesse, un singolo file HTML + CSS + JS vanilla è più leggero e performante di qualsiasi framework React/Vue. Zero build step, zero node_modules.

## L5 - escapeHtml obbligatorio per dati dinamici
Mai inserire dati provenienti da API/WebSocket direttamente nel DOM con innerHTML senza sanitizzazione. Usare sempre `textContent` o una funzione `escapeHtml` per prevenire XSS.

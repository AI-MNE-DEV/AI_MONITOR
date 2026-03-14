# Piano di Sviluppo AI MONITOR (Task Management)

## Sprint 1: Fondamenta e Contratti
- [x] **Task 1.1** `[SKILL: python_architect]`: Setup progetto FastAPI e definizione `contracts.py` (modelli Pydantic per metriche Host, Docker e Allarmi).
  - [x] Sviluppo di `main.py` base con entrypoint FastAPI.
  - [x] Definizione di base models Pydantic in `contracts.py`.
  - [x] Test: Avviare le rotte vuote (health check) per verificare il framework.
  
- [x] **Task 1.2** `[SKILL: system_engineer]`: Sviluppo di `host_probe.py` (lettura HW host in modo non bloccante).
  - [x] Implementazione funzioni psutil non bloccanti per CPU e RAM. (`_collect_cpu_percent`, `_collect_ram_percent` + wrapper async `collect_host_metrics` via `asyncio.to_thread`)
  - [x] Implementazione funzioni metrics I/O Disco. (`_collect_disk_io` con fallback se contatori non disponibili)
  - [x] Unit Test: Test della generazione di `HostMetrics` valide sui data contracts correnti. (10/10 test in `tests/test_host_probe.py`: happy path, retry, degraded fallback, serializzazione JSON, roundtrip)

## Sprint 2: Integrazione Event-Driven e Storage
- [x] **Task 2.1** `[SKILL: docker_expert]`: Sviluppo `docker_probe.py`.
  - [x] Implementazione asincrona del polling metriche (CPU/RAM per container). (`collect_docker_metrics_sync` + async wrapper `collect_docker_metrics` via `asyncio.to_thread`; formula CPU ufficiale Docker, RAM con sottrazione cache)
  - [x] Implementazione loop ascolto Docker Event Stream (`start`, `stop`, `die`) per rilevamento istantaneo nuovi container o crash. (`listen_docker_events` con callback, reconnect con backoff esponenziale)
  - [x] Meccanismo di fallback per disconnessione o socket assente. (MAX_RETRIES=3, backoff esponenziale, fallback `DockerMetrics(status="degraded")`)
  - [x] Unit Test: Mockare l'SDK Docker e testare sia l'happy path che la disconnessione. (15/15 test in `tests/test_docker_probe.py`: CPU calc, RAM calc, happy path, mixed containers, socket failure, retry, stats failure, no containers, event listener, serializzazione, roundtrip)
  
- [x] **Task 2.2** `[SKILL: database_engineer]`: Sviluppo `storage_engine.py` (scrittura asincrona/bufferizzata per non bloccare i thread in caso di I/O lento).
  - [x] Setup schema DB e connessione SQLite con parametro WAL. (3 tabelle: host_metrics, docker_metrics, alert_events; WAL + busy_timeout=5000; schema idempotente con IF NOT EXISTS)
  - [x] Worker thread o asyncio queue per scritture bufferizzate/asincrone. (classe `StorageEngine` con `queue.Queue`, writer thread daemon, batch insert da 50 record, graceful shutdown con flush)
  - [x] Integration Test: Inserire 1000 record/secondo in memoria e verificare l'I/O. (11/11 test in `tests/test_storage_engine.py`: schema, WAL, lifecycle, store per tipo, mixed types, counter, flush, throughput 1000 record)

## Sprint 3: Logica di Rete e Allarmi
- [x] **Task 3.1** `[SKILL: backend_logic_expert]`: Sviluppo `alert_manager.py` (regole di valutazione soglie).
  - [x] Caricamento di soglie (hardcoded inizialmente, poi eventualmente via config). (Soglie configurabili via env: ALERT_CPU_WARNING/CRITICAL, ALERT_RAM_WARNING/CRITICAL, ALERT_COOLDOWN_SECONDS)
  - [x] Loop di validazione metriche in ingresso per generare `AlertEvent`. (classe `AlertManager` con `evaluate_host_metrics` e `evaluate_docker_metrics`, deduplicazione cooldown, fallback CRITICAL_ALERTS.txt)
  - [x] Unit Test: Passare metriche mock al di sopra e al di sotto delle soglie e contare gli alert innescati. (21/21 test: soglie CPU/RAM warning/critical, cooldown, degraded, Docker, container CPU, active_alerts, CRITICAL_ALERTS.txt, contract)
  
- [x] **Task 3.2** `[SKILL: api_developer]`: Sviluppo `ws_streamer.py` e rotte REST per interrogare lo storico e lo stato alert.
  - [x] Connessione ed enumerazione dei client WS attivi. (`ConnectionManager` con `active_count`, `connect()`, `disconnect()`, rimozione automatica client stale)
  - [x] Broadcast in real-time di `HostMetrics`, `DockerMetrics` e `AlertEvent`. (`broadcast_system_status()`, `broadcast_alert()`, timeout 5s per client lenti)
  - [x] Rotte REST GET `/api/history` e status allarmi. (`/api/v1/history/host`, `/api/v1/history/docker`, `/api/v1/alerts`, `/api/v1/alerts/active` con paginazione limit/offset)
  - [x] Integration Test: Fake client WebSocket che ascolta lo streaming per verifiche serializzazione. (15/15 test: ConnectionManager, broadcast, stale removal, REST endpoints, WS ping/pong)
  - [x] Refactoring `main.py`: rimosso `@app.on_event("startup")` deprecato, adottato `lifespan` context manager, integrati tutti i moduli (probe, storage, alert, WS), telemetry loop asincrono

## Sprint 4: La "War Room" Dashboard
- [x] **Task 4.1** `[SKILL: premium_ui_ux_designer, noc_dashboard_specialist]`: Sviluppo Frontend SPA (Single Page Application).
  - [x] Requisito essenziale: Interfaccia da "War Room" / NOC. (Header sticky con status globale, grid layout Host/Docker/Alerts, glanceable da 3m)
  - [x] Setup pagina in Dark mode nativa. (bg #0a0e17, card #111827, testi #e0e6ed)
  - [x] Tipografia enorme e chiara per le metriche critiche, contrasti netti. (CPU/RAM 3rem monospace, colori semantici: verde OK, giallo WARNING, rosso CRITICAL)
  - [x] Connessione istantanea via WebSocket con animazioni fluide ma sobrie. (Auto-connect a /ws/telemetry, pulse dot su heartbeat, reconnect con backoff esponenziale)
  - [x] Nessun ricaricamento di pagina consentito. (SPA pura, zero location.reload, tutto via WS)
  - [x] Test UI: Valutare contrasto colori e performance CPU del browser. (16/16 test: serving, struttura DOM, design NOC, dark mode, tipografia, XSS protection, no reload)

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

---

## Sprint 5: Produzione
- [x] **Task 5.1**: Deploy Docker per produzione.
  - [x] Dockerfile con healthcheck, dipendenze runtime, immagine slim.
  - [x] docker-compose.yml con mount Docker socket (ro), volume dati persistente, log rotation.
  - [x] .dockerignore per build leggera.
  - [x] SYSTEM_ADMIN_GUIDE.md con istruzioni deploy, configurazione e troubleshooting.
  - [x] Deploy verificato su srv-aiservices: health check OK (`curl http://localhost:8000/health`).

---

### Sprint 6: Integrazioni Architetturali Avanzate (Nuovi Task)
- [x] **Task 6.1 `[SKILL: devops_notifier]`:** Notifiche Esterne (Telegram + Webhook). Nuovo modulo `notifier.py`: dispatcher asincrono non-bloccante (`httpx.AsyncClient`), supporto Telegram Bot API e Webhook generico POST JSON, configurazione 100% via env, validazione config al boot, graceful degradation. Integrato in `main.py` lifespan + telemetry loop. Aggiornati `.env.example` e `SYSTEM_ADMIN_GUIDE.md`. 23/23 test in `tests/test_notifier.py`.
- [ ] **Task 6.2 `[SKILL: system_engineer]`:** Estensione Sonde e Contratti. Aggiornare `contracts.py`, `host_probe.py` e `docker_probe.py` per estrarre e validare Network I/O (RX/TX), Disk I/O (Lettura/Scrittura) e Spazio Disco (Totale/Usato/Libero/% sia per host che per container).
- [ ] **Task 6.3 `[SKILL: database_engineer]`:** Data Retention. Implementare in `storage_engine.py` un job asincrono schedulato che elimini fisicamente dal DB le metriche più vecchie di 7 giorni per prevenire la saturazione del disco.
- [ ] **Task 6.4 `[SKILL: frontend_developer]`:** UI Dinamica e Live Logs. Aggiungere il sorting dinamico (ordinamento per colonne su CPU/RAM/Disco) nella griglia frontend e implementare una modale "Live Log Viewer" (un terminale read-only che mostri in realtime le ultime righe di log al click sul container).
- [ ] **Task 6.5 `[SKILL: data_viz_engineer]`:** Implementazione Grafici Storici. Integrare micro-grafici (Sparklines) per i trend rapidi degli ultimi 60 minuti direttamente nella vista a griglia dei container. Aggiungere grafici a linee interattivi e dettagliati (CPU, RAM, Network I/O, Disk I/O) all'interno della modale di dettaglio di ogni container, utilizzando una libreria frontend ottimizzata per non bloccare il rendering.

---

## Stato Sessione (2026-03-15)
**Task completati fino a 6.1.** 111/111 test passati.
- Produzione: `srv-aiservices` - container `ai-monitor` running
- Dashboard: `http://<ip-server>:8000/`
- Notifiche esterne: pronte, da attivare via `.env` con token Telegram o URL webhook.
- Prossimo task: 6.2 (Estensione Sonde e Contratti).

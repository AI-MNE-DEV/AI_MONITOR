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
- [ ] **Task 2.1** `[SKILL: docker_expert]`: Sviluppo `docker_probe.py`.
  - [ ] Implementazione asincrona del polling metriche (CPU/RAM per container).
  - [ ] Implementazione loop ascolto Docker Event Stream (`start`, `stop`, `die`) per rilevamento istantaneo nuovi container o crash.
  - [ ] Meccanismo di fallback per disconnessione o socket assente.
  - [ ] Unit Test: Mockare l'SDK Docker e testare sia l'happy path che la disconnessione.
  
- [ ] **Task 2.2** `[SKILL: database_engineer]`: Sviluppo `storage_engine.py` (scrittura asincrona/bufferizzata per non bloccare i thread in caso di I/O lento).
  - [ ] Setup schema DB e connessione SQLite con parametro WAL.
  - [ ] Worker thread o asyncio queue per scritture bufferizzate/asincrone.
  - [ ] Integration Test: Inserire 1000 record/secondo in memoria e verificare l'I/O.

## Sprint 3: Logica di Rete e Allarmi
- [ ] **Task 3.1** `[SKILL: backend_logic_expert]`: Sviluppo `alert_manager.py` (regole di valutazione soglie).
  - [ ] Caricamento di soglie (hardcoded inizialmente, poi eventualmente via config).
  - [ ] Loop di validazione metriche in ingresso per generare `AlertEvent`.
  - [ ] Unit Test: Passare metriche mock al di sopra e al di sotto delle soglie e contare gli alert innescati.
  
- [ ] **Task 3.2** `[SKILL: api_developer]`: Sviluppo `ws_streamer.py` e rotte REST per interrogare lo storico e lo stato alert.
  - [ ] Connessione ed enumerazione dei client WS attivi.
  - [ ] Broadcast in real-time di `HostMetrics`, `DockerMetrics` e `AlertEvent`.
  - [ ] Rotte REST GET `/api/history` e status allarmi.
  - [ ] Integration Test: Fake client WebSocket che ascolta lo streaming per verifiche serializzazione.

## Sprint 4: La "War Room" Dashboard
- [ ] **Task 4.1** `[SKILL: premium_ui_ux_designer, noc_dashboard_specialist]`: Sviluppo Frontend SPA (Single Page Application).
  - [ ] Requisito essenziale: Interfaccia da "War Room" / NOC.
  - [ ] Setup pagina in Dark mode nativa.
  - [ ] Tipografia enorme e chiara per le metriche critiche, contrasti netti.
  - [ ] Connessione istantanea via WebSocket con animazioni fluide ma sobrie.
  - [ ] Nessun ricaricamento di pagina consentito.
  - [ ] Test UI: Valutare contrasto colori e performance CPU del browser.

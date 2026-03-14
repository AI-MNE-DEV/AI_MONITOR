# AI MONITOR - Architettura e Design

## Obiettivo e Design
Il microservizio "AI MONITOR" è un agente di monitoraggio telemetrico per l'host e i container Docker attivi. 
Progettato per girare come singolo container Docker, offre un database embedded (SQLite in WAL mode) per lo storico delle metriche e un'interfaccia web "War Room" in tempo reale.

## Subagent Strategy
Per mantenere pulita la context window e separare le responsabilità, il sistema delega compiti specifici a sottomoduli (eseguibili come o gestiti da sub-agenti/task separati):
1. **Host Probe** (`host_probe.py`): Lettura HW (CPU, RAM, Disco) dell'host in modo non bloccante.
2. **Docker Probe** (`docker_probe.py`): Statistiche, log ed event stream dal socket Docker.
3. **Storage Engine** (`storage_engine.py`): Scrittura bufferizzata su SQLite.
4. **Alert Manager** (`alert_manager.py`): Motore a regole per le soglie e gli allarmi.
5. **WS Streamer** (`ws_streamer.py`): Diffusione in real-time tramite WebSocket.

## Regola delle 300 righe
**Nessun file o modulo deve superare le 300 righe di codice.** Qualsiasi modulo che si avvicini a questo limite deve essere spezzato in sottomoduli più specifici. Questo garantisce un approccio modulare estremo e una facile manutenibilità.

## Contratti Dati (Data Contracts)
La comunicazione tra moduli avviene rigorosamente tramite payload JSON validati con **Pydantic**.
I modelli sono definiti centralmente in `contracts.py` e fungono da Single Source of Truth per l'interscambio di dati, garantendo il disaccoppiamento totale tra i moduli di raccolta, storage e stream.

## Resilienza e Piano B (Graceful Degradation)
Il sistema è progettato aspettandosi che le API (es. Docker Socket, filesystem host) falliscano.
- **Docker Socket Disconnect**: Se il socket si disconnette, il `docker_probe` attua backoff esponenziale e restituisce metriche di default "Not Available" senza crashare la pipeline. L'UI segnalerà la disconnessione (Status: Degraded).
- **DB Lock/I/O Block**: Il `storage_engine` usa una coda asincrona/bufferizzata. Se il DB è temporaneamente bloccato, innesca fallback warning ma non ferma la raccolta dati in memoria.

## Active Alerting
Gli errori critici non sono semplici log:
- Se la RAM > 90% o CPU > 95%, si innescano allarmi.
- Vengono salvati esplicitamente sul DB come entità separate (`AlertEvent`).
- Vengono inviati istantaneamente via WebSocket all'UI.
- In caso di failure catastrofici del DB, scritti su un file di fallback `/data/CRITICAL_ALERTS.txt`.

## Core Principles
1. **Simplicity First**: Design chiaro e diretto, zero astrazioni inutili.
2. **No Laziness**: Nessun "fix temporaneo", indagare le vere cause di colli di bottiglia e deadlock.
3. **Minimal Impact**: Il probe stesso deve consumare il minimo di CPU/RAM per non inquinare le misurazioni.

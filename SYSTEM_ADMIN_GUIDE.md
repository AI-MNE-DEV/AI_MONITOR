# AI MONITOR - Guida Deploy Produzione

## Requisiti Server
- Docker Engine 24+ con Docker Compose v2
- Accesso al Docker socket (`/var/run/docker.sock`)
- Porta 8000 (configurabile via `AI_MONITOR_PORT`)

## Quick Start

```bash
# 1. Clona il repo sul server
git clone <repo-url> ai_monitor && cd ai_monitor

# 2. Crea il file .env dalla template
cp .env.example .env
# Modifica le soglie se necessario: nano .env

# 3. Build e avvio
docker compose up -d --build

# 4. Verifica
curl http://localhost:8000/health
# Dashboard: http://<server-ip>:8000
```

## Configurazione (.env)

| Variabile | Default | Descrizione |
|---|---|---|
| `AI_MONITOR_PORT` | `8000` | Porta esposta sull'host |
| `AI_MONITOR_DB_PATH` | `data/ai_monitor.db` | Path DB dentro il container |
| `AI_MONITOR_POLL_INTERVAL` | `5.0` | Secondi tra ogni polling |
| `ALERT_CPU_WARNING` | `90.0` | Soglia CPU warning (%) |
| `ALERT_CPU_CRITICAL` | `95.0` | Soglia CPU critical (%) |
| `ALERT_RAM_WARNING` | `85.0` | Soglia RAM warning (%) |
| `ALERT_RAM_CRITICAL` | `90.0` | Soglia RAM critical (%) |
| `ALERT_COOLDOWN_SECONDS` | `60` | Cooldown tra alert ripetuti |

## Comandi Operativi

```bash
# Logs in tempo reale
docker compose logs -f ai-monitor

# Restart
docker compose restart ai-monitor

# Stop
docker compose down

# Rebuild dopo aggiornamento codice
git pull && docker compose up -d --build

# Backup DB
docker cp ai-monitor:/app/data/ai_monitor.db ./backup_$(date +%Y%m%d).db
```

## Porte e Servizi

| Servizio | Porta | Protocollo | Descrizione |
|---|---|---|---|
| Dashboard | 8000 | HTTP | War Room SPA |
| API REST | 8000 | HTTP | `/api/v1/*` |
| WebSocket | 8000 | WS | `/ws/telemetry` |
| Health Check | 8000 | HTTP | `/health` |

## Volumi

| Volume | Path Container | Descrizione |
|---|---|---|
| `ai_monitor_data` | `/app/data/` | DB SQLite persistente |
| Docker socket | `/var/run/docker.sock` | Accesso read-only ai container host |

## Troubleshooting

- **Docker probe "degraded"**: il container non riesce a leggere il socket Docker. Verificare che `/var/run/docker.sock` sia montato e che l'utente nel container abbia permessi di lettura.
- **DB locked**: il WAL mode gestisce la concorrenza, ma se persiste, riavviare il container.
- **Alta CPU del monitor stesso**: aumentare `AI_MONITOR_POLL_INTERVAL` (es. 10-15s).

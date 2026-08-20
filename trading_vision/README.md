# Trading Vision 🟢

**Premium Hybrid-Dashboard für Freqtrade Trading Bots.**  
Läuft unabhängig vom Bot — greift nur per REST-API auf Daten zu (Zero-Load Prinzip).

---

## Features

| Tab | Funktion |
|-----|----------|
| **Focus** | Aktive Trades mit Signal-Proximity-Bar, P&L, Einstiegs-/Aktuellkurs |
| **The Mind** | AI-übersetzte Indikatoren (RSI, Bollinger, MACD, Volumen) als menschliche Sätze |
| **Health** | Bot-Status, API-Latenz, Dry-Run/Live-Modus |

---

## Schnellstart

### 1 · Umgebungsvariablen anpassen

In `docker-compose.yml` die drei Werte konfigurieren:

```yaml
FREQTRADE_API_URL=http://freqtrade:8080   # URL deines Bots
FREQTRADE_API_USERNAME=freqtrader
FREQTRADE_API_PASSWORD=SuperSecurePassword
```

> **Hinweis:** Wenn dein Bot im selben Docker-Netzwerk läuft, nutze den Container-Namen (z.B. `freqtrade`). Passe `networks.ft_network.name` an den Namen deines bestehenden Netzwerks an.

### 2 · Docker-Netzwerk prüfen

Finde den Netzwerk-Namen deines Freqtrade-Containers:

```bash
docker network ls
# oder
docker inspect <freqtrade-container> | grep NetworkMode
```

Trage den Namen in `docker-compose.yml` unter `networks.ft_network.name` ein.

### 3 · Starten

```bash
cd trading_vision
docker compose up -d --build
```

Dashboard öffnen: **http://localhost:8501**

### 4 · Stoppen

```bash
docker compose down
```

---

## Lokale Entwicklung (ohne Docker)

```bash
pip install -r requirements.txt

export FREQTRADE_API_URL=http://localhost:8080
export FREQTRADE_API_USERNAME=freqtrader
export FREQTRADE_API_PASSWORD=SuperSecurePassword

uvicorn main:app --reload --port 8501
```

---

## Architektur

```
Browser ──► Trading Vision (FastAPI :8501) ──► Freqtrade API (:8080)
              │  Proxy + AI-Translation
              │  Zero-Load: kein Polling wenn Tab nicht sichtbar
              │  Kein DB, kein Scheduler, kein Disk-I/O
```

**RAM-Verbrauch:** ~25–40 MB (Python + Uvicorn + httpx)  
**CPU bei Idle:** 0 %

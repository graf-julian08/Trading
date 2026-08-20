# Trading Tools & Analytics Suite

## Übersicht
Das Repository **Trading** stellt eine Werkzeugsammlung zur Analyse von Markt- und Finanzdaten bereit. Die Anwendung bietet ein Docker-basiertes Web-Interface zur Datenauswertung.

## Projektstruktur & Architektur
- `trading_vision/`: Hauptverzeichnis des Analysedienstes.
- `trading_vision/main.py`: Backend-Service für Datenanalysen.
- `trading_vision/static/index.html`: Web-Oberfläche für Visualisierungen.
- `trading_vision/docker-compose.yml`: Container-Orchestrierung.

## Hauptfunktionalitäten
- **Docker-Integration**: Isolierte Laufzeitumgebung für Bereitstellung und Tests.
- **Marktdaten-Analyse**: Auswertung von Finanz-Charts und Kennzahlen.
- **Web-Interface**: Interaktive Benutzeroberfläche im Browser.

## Ausführung & Nutzung
Der Start der isolierten Container-Umgebung erfolgt über den Befehl `docker-compose up -d` im Ordner `trading_vision/`.

## Lizenz
Dieses Projekt steht unter der MIT-Lizenz.

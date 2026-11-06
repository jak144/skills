# myAudi für Claude (MCP-Server)

Ein MCP-Server, über den Claude auf dein Audi-Konto bei myAudi zugreift: Du fragst im Chat
„Wie voll ist der Akku?“ oder „Mach die Standklimatisierung auf 21 Grad an“, und Claude ruft die
passenden Werkzeuge auf.

> **Inoffiziell.** Audi bietet keine öffentliche API an. Der Server nutzt dieselbe Schnittstelle wie
> die myAudi-App, so wie sie das Open-Source-Projekt
> [CarConnectivity](https://github.com/tillsteinbach/CarConnectivity) (Audi-Connector 0.3.2) nachbaut.
> Ändert Audi den Login oder die API, kann der Server ohne Vorwarnung aufhören zu funktionieren.
> Nutzung auf eigenes Risiko und nur für dein eigenes Fahrzeug.

## Was geht

| Werkzeug | Was es tut |
| --- | --- |
| `audi_get_status` | Akkustand (SoC) und elektrische Reichweite, Tankfüllstand und Reichweite, Gesamtreichweite, Ladezustand und -leistung, Standklima-Status, Türen/Fenster/Verriegelung, Kilometerstand, Außentemperatur, Service-Intervalle |
| `audi_get_position` | Parkposition mit Kartenlink |
| `audi_list_vehicles` | Fahrzeuge im Konto und welche Dienste aktiv sind |
| `audi_get_raw_status` | Rohdaten für alles Weitere (Timer, 12-V-Batterie, Warnungen, …) |
| `audi_start_climatisation` / `audi_stop_climatisation` | Standklimatisierung starten (optional mit Zieltemperatur 15,5–30 °C, Scheibenheizung, Betrieb ohne Ladekabel) oder stoppen |
| `audi_start_charging` / `audi_stop_charging` | Laden starten oder stoppen (e-tron und Plug-in-Hybrid) |
| `audi_wake_vehicle` | Auto aufwecken, damit es frische Daten schickt |

Die Werte sind immer der Stand, den das Auto zuletzt gemeldet hat (`last_update`, `data_age_min`).
Befehle laufen asynchron: „accepted“ heißt, das Audi-Backend hat den Befehl ans Auto weitergegeben.

**Nicht enthalten:** Ver- und Entriegeln (bewusst weggelassen, Fernentriegeln per Chat ist mir zu
riskant) und die kraftstoffbetriebene **Standheizung** bei Verbrennern. Für die Standheizung kenne ich
keinen Endpunkt der aktuellen API, den eine gepflegte Referenz nutzt; deshalb ist sie nicht eingebaut.
Ob dein Auto die Standklimatisierung kann, zeigt `audi_list_vehicles` (Dienst `climatisation`).

Unterstützt wird die Region Europa (myAudi-Konten aus EU/EMEA).

## Voraussetzungen

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (installiert Python und die
  Abhängigkeiten automatisch)
- Dein myAudi-Konto (E-Mail und Passwort) und ein Fahrzeug, das in der myAudi-App als
  Hauptnutzer eingerichtet ist

## Einrichtung

### Claude Desktop (Chat-App)

1. Repository klonen:

   ```bash
   git clone https://github.com/jak144/skills.git
   ```

2. In Claude Desktop **Einstellungen → Entwickler → Konfiguration bearbeiten** öffnen und in
   `claude_desktop_config.json` ergänzen (Pfad anpassen):

   ```json
   {
     "mcpServers": {
       "myaudi": {
         "command": "uv",
         "args": ["run", "--quiet", "--directory", "/PFAD/ZU/skills/plugins/myaudi", "myaudi-mcp"],
         "env": {
           "MYAUDI_USERNAME": "deine@mail.de",
           "MYAUDI_PASSWORD": "dein-passwort"
         }
       }
     }
   }
   ```

   Findet Claude Desktop `uv` nicht, trag den vollen Pfad ein (`which uv`, zum Beispiel
   `/Users/du/.local/bin/uv`).

3. Claude Desktop neu starten. Im Chat erscheinen jetzt die myAudi-Werkzeuge.

### Claude Code (als Plugin)

```bash
export MYAUDI_USERNAME="deine@mail.de"
export MYAUDI_PASSWORD="dein-passwort"
```

Dann in Claude Code (ab Version 2.1.275 in einem Schritt):

```text
/plugin install myaudi --marketplace jak144/skills
```

Bei älteren Versionen:

```text
/plugin marketplace add jak144/skills
/plugin install myaudi@anthropic-agent-skills
```

Alternativ ohne Plugin:
`claude mcp add myaudi -e MYAUDI_USERNAME=… -e MYAUDI_PASSWORD=… -- uv run --quiet --directory /PFAD/ZU/skills/plugins/myaudi myaudi-mcp`

### Zugangsdaten testen

Bevor du Claude fragst, kannst du den Login direkt prüfen:

```bash
cd skills/plugins/myaudi
MYAUDI_USERNAME="deine@mail.de" MYAUDI_PASSWORD="dein-passwort" uv run myaudi-mcp --check
```

Das gibt den Status jedes Fahrzeugs aus. Fehler wie „Passwort falsch“ oder „neue
Nutzungsbedingungen akzeptieren“ stehen dort im Klartext.

## Einstellungen

| Umgebungsvariable | Bedeutung |
| --- | --- |
| `MYAUDI_USERNAME` | E-Mail-Adresse des myAudi-Kontos (Pflicht) |
| `MYAUDI_PASSWORD` | Passwort des myAudi-Kontos (Pflicht) |
| `MYAUDI_VEHICLE` | VIN oder Spitzname des Standardfahrzeugs, wenn du mehrere Autos hast |
| `MYAUDI_TOKEN_CACHE` | Ort der Token-Datei (Standard: `~/.cache/myaudi-mcp/token.json`) |
| `MYAUDI_LOG_LEVEL` | Log-Level auf stderr, zum Beispiel `DEBUG` (Standard: `WARNING`) |

## Sicherheit

- Der Server läuft nur lokal auf deinem Rechner (stdio). Er öffnet keinen Port, und deine
  Zugangsdaten gehen nur an die Login- und API-Server von Audi/VW (`identity.vwgroup.io`,
  `emea.bff.cariad.digital`).
- Nach dem ersten Login liegt ein Token in `~/.cache/myaudi-mcp/token.json` (nur für dich lesbar,
  Rechte 0600). Löschen mit `uv run myaudi-mcp --logout`.
- Befehle wirken auf das echte Auto. Claude ist angewiesen, sie nur auf deine ausdrückliche Bitte
  auszuführen, und Claude Desktop/Code fragen vor jedem Werkzeugaufruf nach, solange du das nicht
  dauerhaft erlaubst. Erlaube die Befehlswerkzeuge am besten nicht pauschal.
- Audi begrenzt die Zahl der Abfragen pro Konto. Wer im Minutentakt abfragt, wird zeitweise gesperrt.

## Entwicklung

```bash
cd plugins/myaudi
uv run pytest
```

Die Tests laufen gegen ein simuliertes Audi-Backend (`tests/fake_audi.py`): Login-Seiten,
Token-Endpunkt und Fahrzeug-API. Gegen die echten Audi-Server ist der Code nicht automatisch
getestet; Feldnamen und Abläufe stammen aus dem CarConnectivity-Audi-Connector.

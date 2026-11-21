# Satelliten-Quiz-Bot („Where on Earth?“)

Postet jeden Tag automatisch ein Instagram-Reel mit zwei Runden. Zu sehen sind **echte Satellitenbilder** (Sentinel-2, 10 m pro Pixel) von oben, die Kamera fliegt im Stil des GTA-Ladebildschirms darüber, dazu läuft ein Timer. Bei der Auflösung zoomt die Kamera auf die Größe eines Kontinents heraus (NASA Blue Marble) und zeigt Ländergrenzen, Pin, einen 200-km-Kreis und die Antwortkarte. Es spricht die **ElevenLabs-Stimme Nick**, genau wie beim GeoGuess-Bot. Der Bot läuft komplett auf **GitHub Actions**, du brauchst keinen eigenen Server.

```
GitHub Actions (täglich)
  ├─ 2 Orte aus data/locations.json wählen (Runde 1 bekannt, Runde 2 schwerer, keine Wiederholung)
  ├─ wolkenfreies Sentinel-2-Bild suchen (Earth Search / AWS, Ersatz: Microsoft Planetary Computer)
  ├─ OpenAI prüft das Bild (Wolken, Lücken, Streifen) → sonst nächster Tag/Ort
  ├─ Blue Marble (NASA) für den Zoom-out, Grenzen von Natural Earth
  ├─ Stimme aus dem Cache data/voice/ (ElevenLabs nur für neue Sätze)
  ├─ Video rendern (Kamerafahrt, Timer, Sound, ffmpeg)
  ├─ Video als GitHub Release hochladen  → öffentliche URL
  ├─ Reel über die Instagram-API posten
  └─ history.json + Stimmen-Cache committen (hält auch den Zeitplan aktiv)
```

## Einrichtung (einmalig, ca. 20 Minuten)

### 1. Neues GitHub-Repo
1. Neues **öffentliches** Repository anlegen, z. B. `satbot`. Öffentlich muss es sein, weil Instagram das Video aus dem Release abruft. Die Zugangsdaten liegen trotzdem sicher in den Secrets.
2. Den **Inhalt** dieses Ordners hochladen (Add file → Upload files), also `bot/`, `data/`, `assets/`, `scripts/`, `.github/`, `requirements.txt`, `README.md` und `.gitignore`. Alles gehört direkt in die oberste Ebene des Repos, nicht in einen Unterordner.
   - **Mac-Tipp:** Der Ordner `.github` ist im Finder versteckt. Mit **Cmd + Shift + Punkt** wird er sichtbar. Ohne ihn gibt es keinen Workflow.
3. **Settings → Actions → General → Workflow permissions**: „Read and write permissions“ auswählen und speichern.

### 2. Secrets und Variablen
Unter **Settings → Secrets and variables → Actions**:

| Art | Name | Inhalt |
|---|---|---|
| Secret | `ELEVENLABS_API_KEY` | derselbe ElevenLabs-Key wie beim GeoGuess-Bot (Rechte: Text to Speech, Voices: Read) |
| Secret | `OPENAI_API_KEY` | dein OpenAI-Key (für die Bildprüfung und die einmalige Design-Prüfung) |
| Secret | `IG_USER_ID` | Instagram-User-ID des Kontos, auf dem gepostet werden soll |
| Secret | `IG_ACCESS_TOKEN` | langlebiger Instagram-Token für dieses Konto |
| Secret | `GH_PAT` | (empfohlen) Fine-grained Token mit „Secrets: Read and write“ für dieses Repo. Damit erneuert `refresh-token.yml` den Instagram-Token jeden Montag automatisch |
| Variable | `ELEVEN_VOICE_ID` | die Voice-ID von Nick (dieselbe wie beim GeoGuess-Bot) |

Soll auf **demselben Instagram-Konto** wie der GeoGuess-Bot gepostet werden, trägst du dieselbe User-ID und einen gültigen Token ein. GitHub zeigt gespeicherte Secrets nicht mehr an. Wenn du den Token nicht mehr hast, erzeugst du in deiner Meta-App einfach einen neuen. Für ein **neues Konto** gehst du genauso vor wie beim GeoGuess-Bot (Business-Konto, Meta-App, Token).

### 3. Erster Lauf (Test)
**Actions → Daily Satellite Reel → Run workflow**, Häkchen setzen bei:
- `prepare_voices` (nimmt einmalig alle Sätze mit Nick auf, ca. **4.800 ElevenLabs-Credits**, danach kostenlos)
- `improve_design` (optional: OpenAI prüft und verbessert das Design einmalig, wenige Cent)
- `dry_run` bleibt an

Danach findest du das Video unter **Releases**. Unten im Lauf unter **Artifacts** liegen Vorschaubilder (`preview`) und bei Bedarf der Design-Vorher/Nachher-Vergleich. Wenn alles passt, startest du einen Lauf mit `dry_run` = aus. Ab dann postet der Bot **jeden Tag um 19:43 Uhr** (Winterzeit, im Sommer 20:43 Uhr) selbstständig.

## Einstellungen (optional, als Variablen)

| Variable | Standard | Bedeutung |
|---|---|---|
| `ROUNDS_PER_REEL` | `2` | Runden pro Reel |
| `RADIUS_KM` | `200` | Radius des Kreises bei der Auflösung |
| `REVEAL_KM` | `3200` | Breite der Kontinent-Ansicht am Ende des Zoom-outs |
| `TITLE_EDITION` | `Satellite Edition` | Zeile über „WHERE ON EARTH?“ |
| `S2_MAX_CLOUD` | `15` | maximale Bewölkung einer Szene in % |
| `VISION_CHECK` | `1` | `0` schaltet die OpenAI-Bildprüfung aus |
| `ELEVEN_BUDGET` | `6000` | höchstens so viele Credits bei `prepare_voices` |
| `ELEVEN_STABILITY` / `ELEVEN_SIMILARITY` | `0.50` / `0.58` | wie beim GeoGuess-Bot |

- **Uhrzeit:** in `.github/workflows/daily-reel.yml` die `cron`-Zeile ändern (Zeit in UTC).
- **Orte:** `data/locations.json` enthält 122 Orte. `km` ist die Breite des Quiz-Ausschnitts (8 bis 70 km), `type` ist `city` oder `wonder` (Runde 1) bzw. `island`, `nature` oder `pattern` (Runde 2), `difficulty` ist `easy`, `medium` oder `hard` (Sterne). `reveal` ist der Satz, den Nick bei der Auflösung sagt, und `fact` die Zeile auf der Antwortkarte. Neue Sätze nimmt der Bot beim nächsten Lauf automatisch auf (ca. 40 Credits pro Satz).
- **Design zurücksetzen:** `data/design.json` löschen.

## Was es kostet
- Satellitenbilder, Blue Marble und Kartendaten: **kostenlos**, kein Konto nötig
- ElevenLabs: einmalig ca. 4.800 Credits, danach nichts mehr
- OpenAI-Bildprüfung: ein Bild pro Ort mit `gpt-5-mini`, wenige Cent im Monat
- GitHub Actions: kostenlos (öffentliches Repo)

## Wichtig
- **Lizenzen:** Die Satellitenbilder sind Copernicus-Sentinel-Daten (frei nutzbar, auch kommerziell). Der Pflicht-Hinweis „Contains modified Copernicus Sentinel data <Jahr>“ steht im Video und in der Beschreibung, bitte nicht entfernen. Blue Marble kommt von der NASA (Quelle wird genannt), Natural Earth ist gemeinfrei, die Schrift Poppins steht unter der SIL Open Font License.
- **Wenn ein Ort keine guten Bilder hat** (dauernd Wolken, keine Abdeckung), pausiert der Bot ihn 21 Tage (`no_coverage` in `history.json`). Ist ein Bilderdienst gestört, wird nichts pausiert. Der Lauf bricht nach drei Fehlversuchen mit einer klaren Meldung ab, und GitHub schickt dir eine E-Mail. Es wird nie etwas Halbfertiges gepostet.
- Ist die NASA nicht erreichbar, nutzt der Zoom-out eine gezeichnete Karte aus Natural-Earth-Daten.

## Lokal testen
```bash
pip install -r requirements.txt && bash scripts/fetch_assets.sh
python -m bot.main render --out out --demo --fake-voice     # offline, mit Platzhalter-Bildern und Piepton-Stimme
python -m bot.main render --out out                         # echt (braucht Internet und ELEVENLABS_API_KEY)
```

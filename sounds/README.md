# Sound-Dateien für zufällige Voice-Joins

Platziere hier deine Sound-Dateien für die zufällige Voice-Channel-Funktion.

Unterstützte Formate: MP3, WAV, OGG, FLAC (benötigt FFmpeg)

## Beispiele:
- `alarm.mp3` - Alarm-Sound
- `notification.mp3` - Benachrichtigungston
- `laugh.wav` - Lacher-Sound

## Verwendung in der Web-UI:
1. Hole die Datei in diesen Ordner (`/sounds/`)
2. Gib in der "Zufälliger Voice-Chat" Karte den Pfad an: `/sounds/meinedatei.mp3`
3. Aktiviere die Funktion und stelle das Intervall ein

## FFmpeg-Installation (falls nicht vorhanden):
```bash
apt-get update
apt-get install -y ffmpeg
```

# txt → bbmodel

Wandelt eine einfache Text-Liste von Cube-Definitionen direkt im Browser in eine
fertige [Blockbench](https://www.blockbench.net/) `.bbmodel`-Datei um. Läuft
komplett clientseitig (reines HTML/JS) – kein Server, kein Backend, keine Daten
verlassen den Browser.

**Live-Demo:** nach dem Einrichten (siehe unten) erreichbar unter
`https://DEIN-USERNAME.github.io/DEIN-REPO-NAME/`

## Benutzung

1. Seite öffnen
2. Cube-Liste in das Textfeld eingeben oder eine `.txt`-Datei hochladen
3. Auf **„Parsen & prüfen"** klicken – Fehler werden direkt angezeigt
4. Dateiname vergeben, auf **„.bbmodel herunterladen"** klicken
5. Datei in Blockbench per **File → Open** laden

## Für KI-Agenten: ganz ohne Website nutzbar (convert.py)

Eine statische GitHub-Pages-Seite hat keine API — eine KI kann sie nicht
"aufrufen" wie einen Webservice, sondern höchstens per Browser-Automatisierung
klicken (langsam, fehleranfällig, nicht jede KI kann das überhaupt).

Deshalb gibt es `convert.py`: einen Kommandozeilen-Port derselben Logik wie
`index.html` (identisches Box-UV-Layout, identische Sicherheitsnetze, gleiche
`color`/`pixel`-Direktiven). Jede KI mit Python-Code-Ausführung — Claude,
ChatGPT Code Interpreter usw. — kann damit direkt eine `.bbmodel`-Datei
erzeugen, ganz ohne Browser, ohne Hochladen, ohne diese Website zu besuchen:

```bash
pip install Pillow --break-system-packages   # einmalig
python convert.py eingabe.txt ausgabe.bbmodel
python convert.py eingabe.txt ausgabe.bbmodel --texture-out textur.png --uv-template-out uv_vorlage.png
```

Ablauf für eine KI, die nach einer `.bbmodel`-Datei gefragt wird:
1. TXT-Inhalt nach dem unten beschriebenen Format selbst generieren
   (oder die "Vorlage für KI"-Datei aus dem Web-Tool als Referenz nutzen)
2. `convert.py` mit dieser TXT-Datei ausführen
3. Die erzeugte `.bbmodel`-Datei dem Nutzer direkt bereitstellen

Ergebnis ist byte-für-byte dasselbe Format wie beim Web-Tool (gleiches JSON,
gleiche eingebettete PNG-Textur als base64) — beide wurden gegeneinander
getestet und liefern identische Cube-Anzahl, Auflösung und Split-Ergebnisse.

## Vorlage für eine KI herunterladen

Über den Button **„Vorlage für KI herunterladen"** bekommst du eine `.txt`-Datei
mit einer vollständigen Formatbeschreibung als Kommentar sowie einem Beispiel.
Diese Datei kannst du direkt an eine KI (ChatGPT, Claude, etc.) geben, den
Platzhalter mit deiner Modellbeschreibung ersetzen und dir die Cube-Zeilen
generieren lassen. Das Ergebnis kannst du danach direkt wieder in dieses Tool
laden.

Die Vorlage enthält unter anderem:
- klare harte Regeln (jede Kantenlänge muss zwischen 1 und 48 liegen)
- eine genaue Erklärung des Box-UV-Algorithmus (welche Fläche wie groß ist und
  wo sie im Textur-Kreuz liegt), damit die KI `color`/`pixel`-Zeilen mit
  korrekten, gültigen Koordinaten setzen kann
- die ausdrückliche Kennzeichnung von Auto-Skalierung, Auto-Split und
  UV-Vorlage/Textur-Upload als **Backup-Mechanismen** — die KI soll von
  vornherein gültige, sinnvoll aufgeteilte Cubes liefern, statt sich darauf
  zu verlassen

## Textformat

Eine Zeile pro Cube, Werte durch Semikolon getrennt:

```
Name; x,y,z; sizeX,sizeY,sizeZ
Name; x,y,z; sizeX,sizeY,sizeZ; rotX,rotY,rotZ
Name; x,y,z; sizeX,sizeY,sizeZ; rotX,rotY,rotZ; pivotX,pivotY,pivotZ
```

- `x,y,z` – Position der unteren/vorderen Ecke des Cubes
- `sizeX,Y,Z` – Breite/Höhe/Tiefe
- `rotX,Y,Z` – optional, Rotation in Grad (Standard: `0,0,0`)
- `pivotX,Y,Z` – optional, Drehpunkt (Standard: Mittelpunkt des Cubes)

Zeilen, die mit `#` beginnen oder leer sind, werden ignoriert.

**Beispiel:**
```
Kopf; -4,24,-4; 8,8,8
Arm_Links; -8,12,-2; 4,12,4; 0,0,15; -8,24,0
```

## Textur-Auflösung

Standard ist 16×16 (klassisches Minecraft-Format). Über die Felder im Tool oder
direkt in der TXT-Datei einstellbar:
```
resolution; 64,64
```
Diese Zeile darf an beliebiger Stelle stehen und überschreibt die Felder im Tool.

## Textur bemalen

Jeder Cube bekommt automatisch ein eigenes, überlappungsfreies UV-Layout
(klassisches Minecraft-Box-UV-Kreuz). Darauf lässt sich direkt malen, ganz ohne
UV-Koordinaten zu kennen — per Cube-Name und Flächenname:

```
color; Cube-Name; Fläche; #hexfarbe
pixel; Cube-Name; Fläche; x,y; #hexfarbe
```

- **Fläche**: `north`, `south`, `east`, `west`, `up`, `down`, oder `all`
- `color` färbt eine ganze Fläche einheitlich ein
- `pixel` setzt einen einzelnen Pixel an Position `x,y` relativ zur Fläche
  (oben links = `0,0`)
- Spätere Zeilen überschreiben frühere an derselben Stelle — erst Grundfarbe,
  dann Details malen

**Beispiel:**
```
color; Kopf; all; #e0b088
pixel; Kopf; north; 2,3; #000000
pixel; Kopf; north; 5,3; #000000
```

Die erzeugte Textur wird automatisch als PNG ins `.bbmodel` eingebettet (auch
ohne Mal-Direktiven, dann transparent) und kann zusätzlich über den Button
„Textur (PNG) herunterladen" separat gespeichert werden. Eine Vorschau der
Textur erscheint nach dem Parsen direkt im Tool.

## Automatische Skalierung bei zu kleinen Cubes

Jede Fläche muss mindestens 1 Einheit groß sein, um bemalbar zu sein. Hat
irgendein Cube eine Kantenlänge unter 1 (z. B. `0.5`), wird automatisch das
**gesamte Modell** proportional hochskaliert (alle Positionen, Größen und
Pivots gleichmäßig), bis die kleinste Kante genau 1 beträgt. Dadurch bleiben
alle Cubes exakt so zueinander ausgerichtet wie vorher — nur eben größer. Eine
Kantenlänge von exakt `0` ist dagegen ein Fehler (ergäbe eine unsichtbare
Fläche) und wird direkt beim Parsen gemeldet.

## Größenlimit pro Cube

Ein einzelner Cube sollte pro Achse nicht größer als 48 Einheiten sein (analog
zum Java-Blockmodell-Limit), da Minecraft größere Elemente teils fehlerhaft
rendert. Das Tool erkennt das automatisch: Jede Achse, die 48 Einheiten
überschreitet, wird in mehrere gleich große, nahtlos aneinandergrenzende
Teile zerlegt (z. B. wird aus 100 Einheiten Breite automatisch 3× ca. 33.3).
Alle Teile behalten dieselbe Rotation und denselben Drehpunkt wie das
Original, drehen sich also weiterhin als eine Einheit. Mal-Direktiven
(`color`/`pixel`), die sich auf den ursprünglichen Cube-Namen beziehen,
wirken automatisch auf alle daraus entstandenen Teile.

Das ist ein Sicherheitsnetz — bei sehr extremen Übergrößen entstehen dabei
viele Teile und eine entsprechend große Textur. Besser ist es weiterhin,
große Formen von vornherein selbst aus sinnvoll großen, aneinandergrenzenden
Cubes zusammenzusetzen (die KI-Vorlage enthält dafür ein Beispiel).

**Rotations-Diagonale:** Ein Cube kann für sich genommen okay sein (z. B.
44×44), aber bei Rotation (z. B. 45°) wächst seine achsenausgerichtete
Bounding Box durch die Diagonale über die Grenze hinaus — das meldet
Blockbench dann trotzdem als "zu groß". Der Konverter erkennt das separat
und splittet den Cube in diesem Fall automatisch zusätzlich weiter, bis
auch die rotierte Bounding Box passt (Rotation und Pivot bleiben dabei für
alle Teile identisch).

**Transparenz:** Der Status-Text nach dem Parsen listet jeden betroffenen
Cube einzeln mit Namen und Teileanzahl auf (nicht nur eine Gesamtzahl), damit
nachvollziehbar ist, was genau verändert wurde. Bei sehr vielen betroffenen
Cubes wird die Liste ab dem 9. Eintrag zusammengefasst, um lesbar zu bleiben.

## Weitere Sicherheitsnetze

Zusätzlich zu Mindest-/Maximalgröße und Rotations-Diagonale:

- **Auto-Zentrierung:** Liegt das gesamte Modell zu weit vom Ursprung
  entfernt (z. B. Position 500,500,500), wird es automatisch als Ganzes
  näher an den Ursprung verschoben — eine reine Translation, die nichts an
  Form, Proportionen oder Rotation ändert.
- **Invertierte Größen:** Eine versehentlich negative Größe (`from > to`)
  wird automatisch normalisiert statt eine unsichtbare/kaputte Box zu
  erzeugen.
- **Doppelte Cube-Namen:** Werden erkannt und im Status-Text gemeldet
  (nicht automatisch umbenannt, da nicht klar ist, ob das Absicht ist).
  `color`/`pixel`-Zeilen mit diesem Namen wirken auf alle Cubes mit
  demselben Namen gleichzeitig.
- **Textur-Obergrenze:** Ab 2048px gibt's eine Warnung, ab 8192px bricht
  der Konverter kontrolliert ab (Fehlermeldung statt Browser-Absturz durch
  eine zu riesige Canvas-Textur).

**Fixpunkt-Schleife:** Skalierung, Größen-Split, Rotations-Split und
Auto-Zentrierung laufen nicht nur einmal hintereinander, sondern als
Schleife mit bis zu 8 Durchläufen. Falls ein Mechanismus durch seine eigene
Korrektur einen anderen erneut nötig macht (z. B. macht Hochskalieren einen
vorher unproblematischen Cube nachträglich zu groß), greift der nächste
Durchlauf das automatisch auf. Die Schleife bricht früh ab, sobald ein
Durchlauf nichts mehr verändert — bei normalen Modellen passiert das schon
nach 1 Durchlauf, betroffene Modelle brauchen meist 2.

## UV-Vorlage herunterladen & eigene Textur hochladen

Statt (oder zusätzlich zu) `color`/`pixel`-Zeilen kann die Textur auch komplett
extern gemalt werden — z. B. von einer Bild-KI:

1. Cube-Zeilen wie gewohnt parsen
2. Button **„UV-Vorlage (PNG) herunterladen"** klicken — erzeugt ein PNG in
   der exakten Modell-Auflösung, mit farbigem Rahmen und Beschriftung
   (`Cube-Name/Fläche`) für jede Fläche
3. Dieses PNG als Vorlage an eine Bild-KI geben („male exakt innerhalb dieser
   Rahmen, überschreite die Grenzen nicht, behalte die Bildgröße bei") oder
   von Hand bemalen
4. Fertiges Bild über **„Eigene Textur hochladen"** wieder ins Tool laden —
   es wird automatisch auf die richtige Auflösung skaliert (falls nötig) und
   ersetzt die generierte Textur im `.bbmodel`

## Als eigenes GitHub-Repo einrichten

1. Neues Repository auf GitHub erstellen (z. B. `bbmodel-converter`)
2. Diese beiden Dateien (`index.html`, `README.md`) hochladen – entweder per
   Drag & Drop im Browser über „Add file → Upload files", oder per Git:
   ```bash
   git init
   git add index.html README.md
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/DEIN-USERNAME/DEIN-REPO-NAME.git
   git push -u origin main
   ```
3. Im Repo zu **Settings → Pages**
4. Unter „Build and deployment" als Source **„Deploy from a branch"** wählen,
   Branch `main`, Ordner `/ (root)` auswählen, **Save**
5. Nach ein bis zwei Minuten ist die Seite live unter
   `https://DEIN-USERNAME.github.io/DEIN-REPO-NAME/`

Kein Build-Prozess, keine Abhängigkeiten – GitHub Pages liefert die
`index.html` einfach als statische Seite aus.

## Auch lokal ohne GitHub nutzbar

`index.html` einfach doppelklicken bzw. im Browser öffnen – funktioniert auch
komplett offline.

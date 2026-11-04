# OpenBuddy USB — installation sur le PC de travail

Ce kit relie les événements d'**OpenCode v2** au compagnon ESP32 par **câble USB**,
sans Bluetooth, et joue les **notifications sonores et Windows** sur le PC.
Écran, animation Goku et halo sont identiques à la version Bluetooth.

Limites de ce mode : les boutons de l'ESP32 ne sont pas remontés et le clavier HID
(F13) n'existe pas. Les boutons restent en lecture seule : aucune permission ou
réponse n'est validée automatiquement. La version Bluetooth reste disponible dans
`openbuddy-workplace.zip`.

Les images de Goku restent sur la **carte SD** de l'ESP32 (non incluses ici). Sans
elles, le firmware affiche un robot intégré de secours.

## Prérequis

- Windows 10/11, **CPython 3.11, 3.12, 3.13 ou 3.14 en 64 bits** (`win-amd64`).
  Les dépendances sont incluses pour une installation **sans Internet**.
- OpenCode **v2**, testé avec **2.2.5**, en service local géré.
- Un **câble USB-C qui transmet les données** (pas un câble de charge seule) entre
  l'ESP32 et le PC. Un port série USB (COM) doit être autorisé par l'entreprise.
- Aucun droit administrateur, Node/npm, Arduino ou pilote spécifique requis.

Les commandes sont à lancer dans **PowerShell**, sans mode administrateur.

## 1. Extraire et vérifier

```powershell
Expand-Archive -Path .\openbuddy-workplace-usb.zip -DestinationPath "$HOME\OpenBuddy"
Set-Location "$HOME\OpenBuddy\openbuddy-workplace-usb"

python --version
python -c "import sysconfig; print(sysconfig.get_platform())"
opencode --version
```

Le premier résultat doit être **3.11 à 3.14**, le deuxième **`win-amd64`** et
OpenCode en **2.x**. Si `$HOME\OpenBuddy` contient déjà un kit, extraire dans un
autre dossier pour ne remplacer aucun fichier.

## 2. Installer les dépendances hors ligne dans un environnement isolé

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse -r .\requirements.txt
```

Cette installation utilise uniquement les wheels du ZIP et ne modifie pas les
paquets Python globaux. Pas besoin d'activer le venv : appeler son `python.exe`.
Ne pas désactiver la vérification TLS ou les protections du poste pour contourner un
refus ; demander un paquet adapté ou l'aide de l'IT.

## 3. Installer le plugin OpenCode

```powershell
.\.venv\Scripts\python.exe -m pc.install_plugin
```

Le plugin est copié dans `$HOME\.config\opencode\plugins\openbuddy`. Le script ne
modifie ni `opencode.json(c)` ni les autres plugins. Mise à jour d'une copie
existante : ajouter `--replace` (sauvegarde hors du dossier `plugins`). Installer
**soit globalement soit par projet** (`--project "C:\chemin\projet"`), pas les deux.
Dans OpenCode, `/reload` si le plugin n'apparaît pas.

## 4. Mettre le firmware à jour (USB)

Le mode USB exige le **firmware 0.3.0**. Brancher l'ESP32, puis :

```powershell
.\.venv\Scripts\python.exe -m pc.buddy --serial auto scan
.\.venv\Scripts\python.exe -m pc.deploy --port COM6
```

Remplacer `COM6` par le port affiché par `scan`. `pc.deploy` copie uniquement les
fichiers du firmware, **sans effacer les clés Bluetooth ni la carte SD**, puis
redémarre la carte. Il interrompt le programme en cours : arrêter d'abord tout pont
OpenBuddy. Vérification :

```powershell
.\.venv\Scripts\python.exe -m pc.buddy --serial auto status
```

La réponse doit contenir `"transport": "usb"` et `"firmware": "0.3.0"`.
Pour tester les animations : `.\.venv\Scripts\python.exe -m pc.buddy --serial auto demo`.

## 5. Lancer le compagnon

```powershell
.\.venv\Scripts\python.exe -m pc.opencode --serial auto
```

**Garder ce terminal ouvert.** Goku suit alors les sessions OpenCode et les sons
sont joués par le PC. `auto` choisit l'unique port série Espressif ; s'il y en a
plusieurs, utiliser `--serial COM6`. Aucun programme n'est installé au démarrage de
Windows. Le pont se reconnecte seul si le câble est débranché puis rebranché.

Pour lancer le pont en arrière-plan, utiliser `tools\openbuddy-usb.cmd` après
l'installation du venv et du plugin. Pour l'arrêter : `tools\openbuddy-usb-stop.cmd`.
Ces lanceurs n'ajoutent aucun démarrage automatique Windows. Les journaux sont
dans `%LOCALAPPDATA%\openbuddy\bridge.log` et `bridge.log.err` ; le démarrage du
processus ne garantit pas encore la connexion du périphérique, à vérifier avec `status`.

| État | Son Windows |
|---|---|
| Permission à confirmer | Exclamation |
| Question/formulaire en attente | Question, ou Exclamation si aucun son Question n'est configuré |
| Réponse terminée | Astérisque |
| Échec de l'exécution | Arrêt critique |

Les mêmes situations produisent une notification Windows informative (silencieuse,
pour ne pas doubler le son). Le mode « Ne pas déranger » est respecté ; voir Win+N.

Options : `--mute` (sans sons), `--no-notifications` (sans toasts),
`--port 19847` (port local du pont), `--duration 60` (arrêt automatique).

```powershell
.\.venv\Scripts\python.exe -m pc.opencode notification-test
.\.venv\Scripts\python.exe -m pc.opencode sound-test
```

## Diagnostic et arrêt

Dans un deuxième terminal, depuis le même dossier :

```powershell
.\.venv\Scripts\python.exe -m pc.opencode status
.\.venv\Scripts\python.exe -m pc.opencode stop
```

Valeurs attendues dans `status` : `usb_connected: true`, `sources` supérieur à zéro
(au moins un plugin transmet des états), `last_delivered` (dernier état acquitté par
l'ESP32), `sounds_enabled` et `notifications_enabled: true`, `read_only: true`.
`ble_connected` reste `false` : le Bluetooth n'est pas utilisé. **Ctrl+C** arrête
aussi le pont.

| Symptôme | Vérification |
|---|---|
| `OpenBuddy USB serial port not found` | Câble de données, ESP32 alimenté, `scan` ; USB non bloqué par l'entreprise |
| `USB unavailable: PermissionError` ou `Access denied` | Un autre programme (pont, `mpremote`, terminal série) utilise le port |
| `"transport"` absent de `status` | Firmware ancien : refaire l'étape 4 |
| `sources: 0` | Plugin installé, session de projet ouverte, `/reload`, OpenCode v2 local géré |
| Écran `OFFLINE` | Aucun pont/plugin actif, ou heartbeat arrêté ; l'écran revient à `OFFLINE` 15 s après l'arrêt du pont |
| `An OpenBuddy bridge is already running` | Utiliser `status`/`stop`, ne pas lancer deux ponts |
| Port COM différent après rebranchement | Relancer `scan`, ou garder `--serial auto` |

Le câble reste nécessaire tant que le pont tourne. Le firmware garde aussi son
Bluetooth : si une ancienne entrée « OpenBuddy » du PC tente de se reconnecter,
la supprimer dans Paramètres → Bluetooth et appareils.

## Sécurité, contenu et intégrité

- Plugin autonome : pas de Node/npm ni de téléchargement de plugin à l'exécution.
- Le pont écoute uniquement sur **127.0.0.1:19847**, avec un jeton local aléatoire.
- Aucun prompt, contenu de fichier, texte de question ou secret n'est envoyé à la carte.
- Le port série n'est ouvert que par le pont, avec DTR/RTS bas pour ne pas redémarrer la carte.
- Le ZIP ne contient aucune clé Bluetooth, aucun jeton de pont, aucune configuration
  personnelle OpenCode, aucun cache ni image Goku.
- `%LOCALAPPDATA%\openbuddy\bridge.json` est créé au lancement : **ne pas l'envoyer**.
- `MANIFEST.json` et `SHA256SUMS.txt` décrivent les fichiers du kit ; l'installateur
  contrôle les SHA-256 du plugin avant copie. Ce n'est pas une signature.
- Le client OpenCode embarqué est sous licence MIT : voir `plugin/openbuddy/LICENSES.txt`.

Guide technique complémentaire : `docs/opencode-events.md`.

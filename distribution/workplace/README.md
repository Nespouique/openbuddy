# OpenBuddy — installation sur le PC de travail

Ce kit relie les événements d'**OpenCode v2** au compagnon ESP32 par **Bluetooth
LE**, et joue les **notifications sonores sur le PC Windows**.
Il affiche aussi des **notifications Windows natives** : bannière et centre de
notifications, sans inclure le contenu des prompts ou des questions.

Le firmware et les animations Goku sont déjà sur l'ESP32 : **rien à reflasher,
aucune carte SD à préparer**. Les boutons restent en lecture seule : aucune
permission ou réponse n'est validée automatiquement.

## Prérequis

- Windows 10/11 avec Bluetooth LE autorisé par l'entreprise.
- **CPython 3.11, 3.12, 3.13 ou 3.14 en 64 bits** (`win-amd64`). Les dépendances
  correspondantes sont incluses pour une installation **sans Internet**.
- OpenCode **v2**, testé avec **2.2.5**, utilisant son **service local géré**.
  Les autres versions v2 ne sont pas garanties ; le serveur distant, WSL et
  le mode `--standalone` ne sont pas couverts par ce kit Windows.
- Aucun droit administrateur, Node/npm, Arduino, MSI ou pilote spécifique requis.

Les commandes ci-dessous sont à lancer dans **PowerShell**, sans mode administrateur.

## 1. Extraire et vérifier les versions

Depuis le dossier contenant le ZIP, ou utiliser « Extraire tout » dans l'Explorateur :

```powershell
Expand-Archive -Path .\openbuddy-workplace.zip -DestinationPath "$HOME\OpenBuddy"
Set-Location "$HOME\OpenBuddy\openbuddy-workplace"

python --version
python -c "import sysconfig; print(sysconfig.get_platform())"
opencode --version
```

Le premier résultat doit être **3.11 à 3.14**, le deuxième **`win-amd64`**, et
OpenCode doit être en **2.x**. Le dossier courant doit contenir ce `README.md`,
`requirements.txt`, `pc`, `plugin`, `c6lcd` et `wheelhouse`.

Si `$HOME\OpenBuddy` contient déjà une ancienne installation, extraire le kit
dans un autre dossier pour éviter de remplacer des fichiers existants.

## 2. Installer Python/Bleak hors ligne dans un environnement isolé

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse -r .\requirements.txt
```

Cette installation utilise uniquement les wheels du ZIP. Elle ne modifie pas
les paquets Python globaux et ne télécharge rien. **Pas besoin d'activer le venv**
avec un script PowerShell : on appelle directement son `python.exe`.

Si la création de venv est interdite mais que les installations utilisateur sont
autorisées, alternative sans administrateur :

```powershell
python -m pip install --user --no-index --find-links .\wheelhouse -r .\requirements.txt
```

Dans ce cas, remplacer ensuite `.\.venv\Scripts\python.exe` par `python`.

Pour une autre version/architecture Python, ce wheelhouse peut ne pas convenir.
Si l'accès au dépôt de paquets est autorisé par l'IT, l'installation en ligne est :

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

Ne pas désactiver la vérification TLS ou les protections du poste pour contourner
un refus ; demander un paquet adapté ou l'aide de l'IT.

## 3. Installer le plugin OpenCode

### Pour tous les projets du compte utilisateur — recommandé

```powershell
.\.venv\Scripts\python.exe -m pc.install_plugin
```

Le plugin est copié dans **`$HOME\.config\opencode\plugins\openbuddy`** (ou sous
`$env:XDG_CONFIG_HOME\opencode\plugins\openbuddy` si cette variable est définie).
Le script ne modifie **ni `opencode.json(c)` ni les autres plugins**.

Dans OpenCode, exécuter **`/reload`** si le plugin n'apparaît pas automatiquement,
puis ouvrir une session dans un projet. Il n'est pas nécessaire de redémarrer le
service OpenCode pour installer ce plugin.

### Mise à jour d'une copie OpenBuddy existante

```powershell
.\.venv\Scripts\python.exe -m pc.install_plugin --replace
```

Les fichiers remplacés sont sauvegardés dans `openbuddy-backups`, **hors du dossier
`plugins`** pour ne pas charger une deuxième copie. Une installation identique
est laissée intacte ; un dossier étranger nommé `openbuddy` n'est pas écrasé.

### Installation limitée à un seul projet — alternative

```powershell
.\.venv\Scripts\python.exe -m pc.install_plugin --project "C:\chemin\vers\mon-projet"
```

Choisir **soit globale, soit par projet**, pas les deux. Si le répertoire global
OpenCode a été personnalisé autrement :

```powershell
.\.venv\Scripts\python.exe -m pc.install_plugin --config-dir "C:\chemin\vers\ma-config-opencode"
```

## 4. Appairer l'ESP32 avec le PC de travail

**Important : l'ESP32 accepte une seule connexion BLE à la fois.** Désactiver le
Bluetooth du PC personnel, ou éloigner ce PC, pour qu'il ne garde pas la connexion
clavier. Arrêter son pont Python seul peut ne pas suffire : Windows peut maintenir
la connexion HID du clavier.

1. Alimenter l'ESP32 par USB. Un chargeur USB suffit pour le fonctionnement BLE.
2. Sur le PC de travail : **Paramètres → Bluetooth et appareils → Ajouter un
   appareil → Bluetooth**.
3. Maintenir **BOOT pendant 3 secondes**, puis **relâcher**. L'écran affiche `PAIRING`.
4. Choisir **OpenBuddy** dans Windows.
5. Comparer les codes affichés sur Windows et sur l'ESP32.
6. S'ils sont identiques, confirmer côté Windows, puis faire un **DEUXIÈME appui
   court sur BOOT, environ une demi-seconde, et relâcher**.

Sans ce deuxième appui, Windows reste sur « connexion en cours ». Si l'essai
expire, recommencer. Si une entrée Windows est restée après un échec, supprimer
cette entrée Windows puis relancer l'appairage. Ne jamais confirmer un code différent.

Selon Windows, un code affiché sur l'ESP32 peut plutôt devoir être saisi sur le PC.
Les clés d'appairage du PC de travail sont créées à ce moment-là : aucune clé du
PC personnel n'est copiée par ce ZIP.

## 5. Lancer le compagnon et les sons

Depuis le dossier du kit :

```powershell
.\.venv\Scripts\python.exe -m pc.opencode
```

**Garder ce terminal ouvert.** Goku suit alors les sessions OpenCode, et les sons
sont joués par le PC, pas par l'ESP32. Aucun programme n'est installé au démarrage
de Windows par ce kit.

| État | Son Windows |
|---|---|
| Permission à confirmer | Exclamation |
| Question/formulaire en attente | Question, ou Exclamation si aucun son Question n'est configuré |
| Réponse terminée | Astérisque |
| Échec de l'exécution | Arrêt critique |

Un son est joué à l'entrée dans l'état, pas à chaque heartbeat. Les oscillations
rapides sont limitées. Les états de travail/réflexion, les retries et `OFFLINE`
restent silencieux. Les sons suivent le volume et le thème sonore Windows.
Le repli du son Question ne modifie aucun réglage Windows. Après une mise à jour
du code Python, arrêter puis relancer le pont pour charger le correctif sonore.

Les mêmes quatre situations produisent une notification Windows informative.
Le toast lui-même est silencieux pour éviter de doubler le son du pont.
Le mode « Ne pas déranger » et les politiques Windows restent respectés ; une
notification peut apparaître seulement dans le centre accessible par **Win+N**.

OpenBuddy enregistre uniquement son identité d'application dans
`HKCU\Software\Classes\AppUserModelId\OpenBuddy.OpenCode`, sans droits admin.
Les réglages globaux de notifications, de concentration et de volume ne sont
pas modifiés. Cliquer sur une notification ne valide aucune autorisation.

### Tester la notification Windows

```powershell
.\.venv\Scripts\python.exe -m pc.opencode notification-test
```

Si la bannière n'apparaît pas, vérifier **Win+N**, puis Paramètres Windows →
Système → Notifications → OpenBuddy, ainsi que « Ne pas déranger ».

### Tester les sons sans ESP32

```powershell
.\.venv\Scripts\python.exe -m pc.opencode sound-test
```

Cette commande joue successivement les quatre sons. Vérifier le volume, la sortie
audio et les paramètres des sons système Windows si rien n'est audible.

### Démarrer sans sons

```powershell
.\.venv\Scripts\python.exe -m pc.opencode --mute
```

`--mute` coupe seulement les sons et conserve les notifications visuelles.
Pour désactiver uniquement les toasts :

```powershell
.\.venv\Scripts\python.exe -m pc.opencode --no-notifications
```

### Notifications PC uniquement, sans utiliser le Bluetooth

```powershell
.\.venv\Scripts\python.exe -m pc.opencode --no-ble
```

Cette variante permet les sons des événements OpenCode même si le Bluetooth n'est
pas disponible ; elle ne met pas à jour l'ESP32.

## Diagnostic et arrêt

Dans un deuxième terminal, placé dans le même dossier :

```powershell
.\.venv\Scripts\python.exe -m pc.opencode status
.\.venv\Scripts\python.exe -m pc.opencode stop
```

Valeurs attendues dans `status` :

- `sources` supérieur à zéro : au moins un plugin OpenCode transmet des états ;
- `ble_connected: true` : liaison au périphérique établie ;
- `last_delivered` : dernier état acquitté par l'ESP32 ;
- `sounds_enabled: true` : notifications sonores activées ;
- `notifications_enabled: true` : notifications Windows activées ;
- `notifications_last_error: null` : aucune erreur de soumission de toast observée ;
- `read_only: true` : aucune validation automatique de demande.

**Ctrl+C** arrête aussi le pont. Pour changer le mode sonore, l'arrêter puis le
relancer avec ou sans `--mute`. Ne pas lancer deux ponts ou une démo BLE en même temps.

Si la détection automatique échoue :

```powershell
.\.venv\Scripts\python.exe -m pc.buddy scan
.\.venv\Scripts\python.exe -m pc.opencode --address AA:BB:CC:DD:EE:FF
```

Utiliser l'adresse réelle renvoyée par `scan`. Dans la documentation publique,
`AA:BB:CC:DD:EE:FF` est seulement une adresse d'exemple à remplacer.

| Symptôme | Vérification |
|---|---|
| `No matching distribution` | Python 3.11–3.14, plateforme `win-amd64`, commande lancée dans le dossier du kit |
| `sources: 0` | Plugin installé au bon endroit, session de projet ouverte, `/reload`, OpenCode v2 local géré |
| `ble_connected: false` | Appairage, Bluetooth activé, PC personnel déconnecté, autorisation GATT du poste |
| `An OpenBuddy bridge is already running` | Utiliser `status`/`stop` ; ne pas relancer un deuxième pont |
| `OFFLINE` sur l'écran | Aucun pont/source valide, ou heartbeat arrêté ; cela n'empêche pas Goku de s'animer |
| Sons absents | `sound-test`, volume/mélangeur audio, thème sonore Windows ; une erreur audio n'arrête pas le BLE |
| Bannière absente | `notification-test`, centre Win+N, réglages Notifications/OpenBuddy et Ne pas déranger |

Être reconnu comme clavier **ne garantit pas** que les accès BLE GATT soient
autorisés par la politique de l'entreprise. Demander l'autorisation IT si nécessaire.
Ne pas désactiver l'antivirus ou contourner une interdiction de plugin/périphérique.

## Sécurité, contenu et intégrité

- Plugin autonome : pas de Node/npm ou téléchargement de plugin à l'exécution.
- Le pont écoute uniquement sur **127.0.0.1:19847**, avec un jeton local aléatoire.
- Aucun prompt, contenu de fichier, texte de question ou secret n'est envoyé à la carte.
- Le ZIP ne contient aucune clé Bluetooth, aucun jeton de pont, aucune configuration
  personnelle OpenCode, aucun cache ou image Goku. Les images restent sur la SD.
- Le fichier `%LOCALAPPDATA%\openbuddy\bridge.json` est créé sur le PC de travail
  au lancement : **ne pas l'envoyer** à quelqu'un d'autre.
- `MANIFEST.json` et `SHA256SUMS.txt` décrivent les fichiers du kit. L'installateur
  contrôle les SHA-256 du plugin avant copie. Ce contrôle d'intégrité n'est pas
  une signature ou une certification de sécurité.
- Le client OpenCode embarqué est sous licence MIT : notice dans
  `plugin/openbuddy/LICENSES.txt`. Les notices des dépendances Python sont dans
  leurs wheels et dans les paquets installés.

Guide technique complémentaire : `docs/opencode-events.md` (sa partie
développement se réfère au dépôt source, non inclus dans ce kit d'exécution).

# Prototype BLE OpenBuddy

## Périmètre

Firmware MicroPython pour la Waveshare **ESP32-C6-LCD-1.47 non tactile** :

- écran **landscape 320×172, USB-C à gauche** ;
- pet Goku animé depuis la microSD, avec robot de secours intégré ;
- halo WS2812 synchronisé avec l'état ;
- profil **HID over GATT clavier**, avec modes Report et Boot ;
- service GATT OpenBuddy chiffré et authentifié ;
- appairage ouvert physiquement avec BOOT, clés persistantes en flash interne ;
- lecteur SD en lecture seule sur le bus SPI partagé, voir [animations](animations.md) ;
- pas encore d'événements OpenCode réels.

Le firmware annonce un profil clavier. Sur le PC Windows de développement, la
reconnaissance **clavier HID** (PnP, statut OK), l'appairage authentifié, la
reconnexion après redémarrage et le canal GATT en parallèle ont été vérifiés.
Les huit états de `demo` ont reçu un accusé de réception sans erreur firmware.
La réception effective d'une touche F13 dans une application reste à tester.
Ces résultats ne garantissent pas l'autorisation du périphérique sur un poste
d'entreprise soumis à d'autres politiques.
Le PnP ID `FFFF:0001` est une identité de développement non attribuée, pas celle
d'un fabricant tiers ni une certification Bluetooth.

## Installation PC

Depuis la racine du projet, avec Python **3.10+** (testé avec 3.13 sous Windows) :

```bash
python -m pip install --user -r pc/requirements.txt
python -m pc.buddy --help
python -m pc.buddy scan
```

Bleak utilise la pile Bluetooth native de Windows. Aucun pilote propriétaire ni
MSI n'est nécessaire, mais les restrictions du poste de travail restent applicables.

## Appairage Windows

1. Alimenter la carte. L'écran affiche `OFFLINE` : aucun pont Python n'est actif.
2. Ouvrir **Paramètres → Bluetooth et appareils → Ajouter un appareil → Bluetooth**.
3. Maintenir **BOOT pendant 3 secondes**, puis relâcher. Une fenêtre de 60 secondes
   s'ouvre et le halo devient violet.
4. Choisir **OpenBuddy** dans Windows.
5. Si un code apparaît sur les deux appareils, **vérifier qu'il est identique**,
   confirmer côté Windows puis faire un **appui court sur BOOT** pour confirmer
   côté ESP32. C'est un **deuxième appui**, distinct de l'appui long d'ouverture :
   appuyer environ une demi-seconde, puis relâcher. Sans cela Windows reste en
   « connexion en cours ». Ne pas confirmer un code différent.
6. Selon la procédure négociée par Windows, l'écran peut plutôt afficher un code
   à saisir sur le PC. Le firmware refuse les méthodes sans authentification.

Un appui long pendant une comparaison annule cette comparaison et rouvre la
fenêtre. Sans réponse, la comparaison expire après 30 secondes.

Après appairage, les clés sont conservées dans `openbuddy-bonds.json` **en flash
interne**. Ne pas publier ce fichier. Les reconnexions d'un hôte déjà appairé ne
nécessitent pas de nouvel appui sur BOOT. Un nouvel appairage exige une nouvelle
fenêtre physique.

## Diagnostic

Les options globales, notamment `--address` et `--pair`, précèdent la commande.

```bash
python -m pc.buddy status
python -m pc.buddy demo
python -m pc.buddy set thinking --label "Model request" --duration 60
python -m pc.buddy watch
```

Si plusieurs OpenBuddy sont présents, sélectionner l'adresse BLE réelle renvoyée
par `scan`. Elle peut être différente de l'adresse USB/Wi-Fi de la carte. Les
adresses d'exemple de la documentation publique doivent être remplacées.

```bash
python -m pc.buddy --address AC:EB:E6:3E:B2:26 demo
```

Pour lancer l'appairage depuis Python plutôt que depuis les paramètres Windows :

```bash
python -m pc.buddy --pair --address AC:EB:E6:3E:B2:26 demo
```

La fenêtre BOOT doit être ouverte au préalable. Une confirmation physique reste
requise ; le programme Python ne valide jamais le code à la place de l'utilisateur.

`demo` parcourt `idle`, `thinking`, `working`, `permission`, `question`, `success`,
`error` et `retry`. `watch` affiche les appuis courts sur BOOT et tente de se
reconnecter après une coupure. Arrêt avec **Ctrl+C**.

Les commandes connectées maintiennent un heartbeat toutes les 4 secondes. Sans
commande valide pendant 15 secondes, l'écran repasse `OFFLINE`, même si Windows
garde sa connexion HID ouverte. `status` est ponctuel et ne maintient pas ce lien
applicatif une fois terminé.

Sous Windows, le programme recherche aussi les périphériques déjà appairés au
système : un clavier HID peut rester connecté et ne plus émettre d'annonces, ou
être en cours de reconnexion après un redémarrage. `scan` distingue les appareils
appairés/connectés des appareils appairés actuellement déconnectés.
L'option `--address` permet également d'éviter le scan avant connexion. Le client
ne demande que le service OpenBuddy ; le service HID peut être réservé au système.

### Test clavier volontaire

```bash
python -m pc.buddy hid-test
```

Cette commande **arme** le test pendant 15 secondes mais n'envoie aucune touche.
Mettre au premier plan un outil de test clavier, puis faire un **appui court sur
BOOT** : une seule touche **F13** est envoyée, suivie de son relâchement. Le test
est ensuite désarmé. F13 peut avoir un raccourci configuré sur le PC : utiliser un
contexte de test adapté.

Aucune touche n'est envoyée au démarrage, à l'appairage ou à la reconnexion. Les
futures validations OpenCode passeront par le canal GATT et l'API, pas par `Entrée`.

## Protocole GATT v1

| Rôle | UUID |
|---|---|
| Service | `c8ad0001-4f90-4ab8-9976-22401b7861b0` |
| RX, écriture avec réponse | `c8ad0002-4f90-4ab8-9976-22401b7861b0` |
| TX, notifications | `c8ad0003-4f90-4ab8-9976-22401b7861b0` |

Messages JSON UTF-8 terminés par LF, limités à **256 octets hors LF**. Découpage en
chunks de **20 octets**, compatible avec le MTU ATT minimal de 23. Le décodeur
borne sa mémoire et se resynchronise sur LF après une trame invalide/trop longue.

```json
{"v":1,"id":1,"cmd":"state","state":"working","label":"Running tests"}
{"v":1,"id":1,"event":"ack","state":"working"}
```

Autres commandes : `ping`, `status`, `hid_arm`. Les libellés sont limités à 24
caractères ; la police de diagnostic affiche les caractères non ASCII avec `?`.
Les erreurs de commande portent le même `id`. Les événements bouton n'en ont pas.
Ce protocole n'exécute ni code, ni commande shell, ni écriture de fichier distant.
Le firmware 0.2.0 ajoute `sd` et `pet` à la réponse `status`, sans changer le
protocole v1. Les assets sont transférés séparément via USB.

## Déploiement et récupération

Lister le port série avant de déployer. **Un déploiement via mpremote interrompt
la boucle courante et la connexion BLE** ; relancer ensuite la carte.

```bash
python -m serial.tools.list_ports -v
python -m pc.deploy --port COM9
```

La démo d'orientation reste dans `c6lcd/orientation_demo.py`. Pour y revenir :

```bash
python -m mpremote connect COM9 fs cp c6lcd/orientation_demo.py :main.py + reset
```

La SD est montée **en lecture seule** par un pilote Python et partage l'objet SPI
de l'écran. Ne pas initialiser le pilote natif `SDCard` en parallèle ; son usage
est réservé à l'installation USB, firmware interrompu. Le déploiement ne touche
ni les clés Bluetooth ni les fichiers SD. Voir [animations](animations.md).

En cas d'erreur fatale, le firmware conserve la dernière trace (sans codes
d'appairage ni clés) dans `openbuddy-error.txt` en flash interne. Un ancien journal
peut rester présent après correction ; sa présence seule ne signifie pas que
le firmware est à nouveau en erreur.

## Tests

```bash
python -m pip install --user -r pc/requirements-assets.txt
python -m unittest discover -s tests -v
```

Les tests couvrent le protocole fragmenté, les bornes, la fenêtre physique
d'appairage, le refus d'une connexion non authentifiée, la persistance des clés,
le heartbeat, l'armement HID, le relâchement, les images RGB565 et le lecteur SD.
Les doubles de test ne remplacent
pas une validation réelle de la pile HID Windows.

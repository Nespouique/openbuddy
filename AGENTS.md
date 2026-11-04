# openbuddy

Petit appareil physique (ESP32) qui rend OpenCode interactif dans le monde réel : il signale quand OpenCode attend une réponse et affiche son état (pet animé depuis la microSD). Aucune action n'est validée depuis l'appareil pour l'instant.

Lire d'abord `docs/hardware.md` (matériel, brochage, pièges), `docs/opencode-events.md` et `docs/animations.md` avant de modifier le firmware ou l'intégration.

## Conventions

- Réponses et documentation en français. Code, noms, commentaires et messages de commit en anglais.
- Ne pas supposer de droits administrateur sur le PC (Windows) : tout passe par `pip` en mode utilisateur, aucun `.msi`.
- Firmware en **MicroPython** (aucune toolchain C requise). Outils : `python -m esptool`, `python -m mpremote`, `python -m serial.tools.list_ports`.
- Ne pas utiliser `$_` dans un `powershell -Command` lancé depuis bash : passer par un fichier `.ps1`.
- Tests matériels toujours mockés en CI : ne pas flasher d'appareil ni jouer de notification bureau depuis les tests unitaires.
- Ne jamais committer : clés d'appairage BLE, jetons de découverte du pont, adresses d'appareil réelles, ports série ou chemins personnels, packs du pet, caches, environnements virtuels, `node_modules`. Ce fichier est publié : il ne doit contenir aucune de ces informations.

## Structure

- `c6lcd/` : firmware **MicroPython** de la Waveshare ESP32-C6-LCD-1.47. `board_config.py` = broches et géométrie (source unique), `st7789.py` = driver écran, `main.py`/`companion.py` = boucle principale, `buddy_ble.py` = HID/GATT/appairage, `buddy_ui.py` = écran et halo, `buddy_protocol.py` = protocole partagé PC/carte.
- `c6lcd/buddy_usb.py` : transport USB série, même protocole JSON que BLE, sans HID ni remontée des boutons. Le firmware conserve les deux transports ; USB prend l'affichage tant que sa lease est active, sauf appairage BLE prioritaire.
- `c6lcd/orientation_demo.py` : ancienne démo conservée.
- `c6lcd/shared_spi.py`, `sdcard_spi.py`, `storage.py`, `pet_player.py` : bus écran/SD partagé, carte en lecture seule, lecteur RGB565 à un buffer.
- `pc/buddy.py` : diagnostic BLE Python/Bleak, `python -m pc.buddy` depuis la racine.
- `pc/pet.py` : conversion Codex Pets et installation USB vérifiée ; `pc/deploy.py` : déploiement firmware sans effacement.
- `pc/opencode.py` : pont événements → carte, démarrage `python -m pc.opencode`, diagnostic/arrêt avec `status`/`stop`.
- Mode USB : `python -m pc.opencode --serial auto` (ou un port explicite), diagnostic `python -m pc.buddy --serial auto status`. Un seul outil sur le port série, DTR/RTS bas. Arrêter le pont avant `pc.deploy`, `pc.pet` ou `mpremote`.
- `integration/openbuddy/dist/` : plugin v2 autonome généré, hors autochargement ; sources dans `integration/openbuddy/src/`, métadonnées/licence dans `assets/`, build `npm --prefix integration/openbuddy run build`. Ne pas éditer `index.js` à la main. Installation globale avec `python -m pc.install_plugin`.
- `pc/quota.py` : texte `utilisé / limite` du fournisseur (ex. `0 / 200`) via une commande configurée dans `%APPDATA%\openbuddy\quota.json`, envoyé au firmware dans le champ optionnel `info` de la commande `state` et affiché à la place de `PET / MICROSD` ; rafraîchi toutes les 600 s et à l'entrée dans les états de `refresh_on` (`success` par défaut, 10 s minimum entre deux appels) ; `--no-quota` ou `--quota-config`. Les tests ne doivent jamais lancer la vraie commande (voir `setUpModule` de `tests/test_opencode_bridge.py`).
- `pc/notifications.py` : sons système Windows, option `--mute`. `pc/toasts.py` : notifications Windows WinRT natives, texte générique, toast silencieux, option `--no-notifications`, aucun bouton de validation.
- `pc/install_plugin.py` : installateur du plugin (global par défaut, projet en option), sauvegarde avant `--replace`, aucun changement des configs JSON.
- `tools/package_workplace.py` : ZIP de transfert par liste blanche, wheels hors ligne ; README du kit dans `distribution/workplace/README.md`, variante USB avec `--variant usb` et `README-usb.md`.
- `tools/prepare_publication.py` : liste blanche de publication. `AGENTS.md`, la doc publique et les sources en font partie ; `docs/hardware-notes.md`, packs du pet et clés n'en font jamais partie.
- `tools/build_readme_demo.py` : démo README sans matériel (Pillow). Seuls `docs/media/openbuddy-demo.gif` et `.png` peuvent illustrer le pet ; packs/sprites bruts toujours exclus des sources et kits.
- `docs/ble-prototype.md` : installation, appairage (appui long puis appui court de confirmation), protocole et commandes de test.

## État

- Cible : C6-LCD-1.47, MicroPython 1.29, écran **landscape 320×172** (USB-C à gauche par défaut), BLE HID + GATT et USB série, appairage authentifié avec clés persistantes.
- Microprogramme **0.4.0** (kits publiés en 1.2.0 : firmware 0.3.0). Pet lu depuis une microSD FAT32 partagée avec l'écran (un buffer de 19 968 octets).
- Plugin `openbuddy.events` autonome (SDK 2.2.5 embarqué, sans Node/npm), réservé au service OpenCode local géré. Éviter de charger en même temps les copies globale et projet (`Duplicate plugin ID`).
- Pont Python 3.11+, sons et toasts Windows indépendants du BLE. Utiliser le runtime WinRT 4.1.0 ; ne pas installer Windows-Toasts 1.3.1 (impose un runtime 3.x incompatible).
- BOOT : appui court = rotation 180° (`landscape`/`landscape_inv`, mémorisée dans `openbuddy-orientation.txt`), appui long 3 s = fenêtre d'appairage BLE, appui court pendant une comparaison de code = confirmation. Plus d'événement `button` ni de F13 sur appui court.
- Pet : 9 animations (73 images). `thinking` = `look` (lignes de regards 9-10 de l'atlas v2), `retry` = `running-left` puis `running-right`, `question` = `waving`, appairage = `waiting`. Le pack se régénère avec `pc.pet`, puis `pc.pet install --replace`.
- Montage SD : 3 essais au démarrage, puis rechargement du pet toutes les 3 s (30 s après 20 essais) si la carte n'était pas prête.
- Non validé sur matériel : nouveau pack d'animations, affichage du quota, rotation BOOT, rechargement du pet après démarrage à froid, touche HID F13 réelle.
- Sous Windows, normaliser avec `.resolve()` avant de comparer des chemins (formes courtes/longues).

## Règles matérielles critiques (C6-LCD-1.47)

- Rétroéclairage GPIO22 : PWM ≤ 50 %, jamais en sortie simple (risque d'ombres permanentes).
- Écran ST7789 : offset 34 px sur l'axe court, `INVON` obligatoire, RGB565 envoyé octet de poids fort d'abord, effacer toute la RAM (240×320) à chaque changement d'orientation.
- Orientations valides : `portrait`, `landscape`, `portrait_inv`, `landscape_inv` (voir `board_config.py`). L'interface n'est dessinée que pour les deux orientations paysage.
- LED RVB WS2812 GPIO8 : ordre GRB, garder les valeurs < 40.
- Un seul `machine.SPI` pour écran/SD. Ne jamais utiliser `machine.SDCard` natif en parallèle du LCD ; le natif est réservé à l'installateur USB, programme interrompu. Ne pas toucher aux clés `openbuddy-bonds.json`.

## Vérifications

```text
python -m unittest discover -s tests -v
npm --prefix integration/openbuddy test
npm --prefix integration/openbuddy run typecheck
```

## Prochaines étapes

1. Valider sur matériel la rotation BOOT et le rechargement du pet.
2. Valider le rendu visuel du pet et ajuster les animations si nécessaire.
3. Étendre l'intégration **OpenCode v2** si nécessaire ; vérifier la doc v2 et le contrat API avant toute modification.
4. Réponses aux permissions via l'API v2 et l'identifiant de demande, jamais via une frappe Entrée aveugle.

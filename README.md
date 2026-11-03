# OpenBuddy

Compagnon physique pour OpenCode sur **Waveshare ESP32-C6-LCD-1.47** : écran
paysage, halo RVB et bouton BOOT, avec transport Bluetooth LE.

## Télécharger le kit Windows

**[Releases — kit prêt à installer](https://github.com/Nespouique/openbuddy/releases/latest)**

Prendre `openbuddy-workplace.zip` dans les assets de la Release, l'extraire et
suivre son `README.md`. Il contient le plugin autonome, le pont Python et les
dépendances hors ligne pour CPython 3.11–3.14/Windows x64. Aucun Node/npm, droit
administrateur ou reflash du device n'est nécessaire sur le PC cible.

Le ZIP « Code → Download ZIP » de GitHub contient les sources et un plugin déjà
compilé, mais **pas** le wheelhouse hors ligne. Utiliser un canal de téléchargement
autorisé par la politique du poste de travail.

## Prototype actuel

- Firmware **MicroPython**, profil clavier BLE HID + service GATT OpenBuddy.
- États de diagnostic, **Goku animé depuis la microSD** et halo synchronisé.
- Bus SPI écran/SD partagé, lecture seule et robot intégré de secours.
- Appairage physique et authentifié, clés persistantes en flash.
- Client Python **Bleak** : découverte, états, boutons, reconnexion, test F13 armé.
- Plugin **OpenCode v2** : états réels, permissions/questions, tâches parallèles.
- Pont d'événements en lecture seule : aucune validation automatique par bouton.
- Notifications sonores Windows sur permission, question, succès et erreur (`--mute` disponible).
- Notifications Windows natives (bannière et centre de notifications), indépendantes du son.

```bash
python -m pip install --user -r pc/requirements.txt
python -m pc.buddy scan
python -m pc.buddy demo
```

Pour utiliser les événements réels plutôt que la démonstration :

```bash
python -m pc.opencode
```

Installer le plugin avec `python -m pc.install_plugin`, puis utiliser `/reload`
dans OpenCode si nécessaire. Une copie compilée est dans `plugin/openbuddy/` ;
le build développeur génère `integration/openbuddy/dist/`, hors autochargement. Voir
[événements OpenCode](docs/opencode-events.md) pour le démarrage, le diagnostic,
la copie sur un autre PC et les limites. Node/npm ne sont pas nécessaires pour
exécuter le plugin autonome dans OpenCode.

## Transfert sur le PC de travail

Le kit **`dist/openbuddy-workplace.zip`** contient le plugin, le pont Python,
les dépendances hors ligne pour CPython 3.11–3.14/Windows x64, un installateur
sans droits admin et toutes les commandes dans son `README.md`.
Guide source : [`distribution/workplace/README.md`](distribution/workplace/README.md).

Reconstruction du kit :

```bash
python tools/package_workplace.py --download-wheels --replace
```

L'appairage Windows doit être effectué au préalable avec un appui long de 3 s
sur BOOT et confirmation du code. Voir le [guide du prototype BLE](docs/ble-prototype.md)
pour la procédure complète, le déploiement et les limites de validation.

Pour convertir et installer les images : voir [animations sur microSD](docs/animations.md).
Le firmware se déploie avec `python -m pc.deploy --port COM9`, sans effacer les
clés d'appairage ni la SD. Les assets communautaires ne sont pas redistribués
dans le projet ; leurs sources et attributions sont documentées dans `THIRD_PARTY.md`.

## Fichiers

| Chemin | Rôle |
|---|---|
| `c6lcd/board_config.py` | Broches et géométrie, source unique |
| `c6lcd/main.py`, `companion.py` | Démarrage et boucle firmware |
| `c6lcd/buddy_ble.py` | HID, GATT, appairage et clés |
| `c6lcd/buddy_ui.py` | Écran 320×172 et halo |
| `c6lcd/buddy_protocol.py` | Protocole partagé PC / ESP32 |
| `c6lcd/shared_spi.py`, `sdcard_spi.py`, `storage.py` | Bus partagé et SD en lecture seule |
| `c6lcd/pet_player.py` | Lecteur RGB565, une image en RAM |
| `c6lcd/orientation_demo.py` | Ancienne démo conservée |
| `pc/buddy.py` | Diagnostic BLE en Python |
| `pc/opencode.py` | Pont événements locaux → BLE, authentifié et en lecture seule |
| `pc/notifications.py` | Sons système Windows non bloquants |
| `pc/toasts.py` | Notifications Windows natives, sans données privées |
| `pc/install_plugin.py` | Installation globale/par projet sans modifier la configuration |
| `integration/openbuddy/dist/` | Plugin v2 autonome, prêt à installer |
| `integration/openbuddy/src/` | Sources et tests du plugin |
| `pc/pet.py` | Conversion et installation des packs |
| `pc/deploy.py` | Déploiement des seuls fichiers firmware |
| `docs/hardware.md` | Matériel, brochage et précautions |

Tests : installer les deux fichiers `pc/requirements*.txt`, puis
`python -m unittest discover -s tests -v`.

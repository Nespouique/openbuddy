# Animations depuis la microSD

## Fonctionnement

Le firmware lit le pet **Super Saiyan Goku Combat**, partagé par
**datpham** sur [Codex Pets](https://codex-pets.net/#/pets/super-saiyan-goku-combat).
Le PC envoie uniquement un état BLE : les images restent sur la SD et continuent
à s'animer même sans programme PC. `OFFLINE` utilise l'animation de repos.

- Écran : paysage **320×172**, USB-C à gauche.
- Personnage : **96×104**, à gauche ; état et libellé à droite.
- Conversion à l'échelle 1/2, plus proche voisin, sans étirement ni lissage.
- Transparence compositée sur le même fond sombre que l'interface : RGB `(8,12,20)`.
- Images **RGB565 big-endian** prêtes à envoyer à l'écran, sans décodeur WebP/PNG sur l'ESP32.
- Un seul buffer de **19 968 octets** ; aucune spritesheet entière en RAM.

| État OpenBuddy | Animation | Contenu |
|---|---|---|
| `offline`, `idle` | `idle` | méditation |
| `thinking` | `look` | méditation, tête qui regarde tout autour (lignes 9 et 10, 16 images) |
| `working` | `running` | Kamehameha |
| `permission` | `review` | Kienzan |
| `question` | `waving` | salut de la main |
| `success` | `jumping` | montée d'énergie |
| `error` | `failed` | à genoux |
| `retry` | `retry` | vol vers la gauche puis vers la droite (`running-left` + `running-right`, 16 images) |
| Appairage | `waiting` | montée de puissance |

Le pack contient **9 animations, 73 images, 1 457 664 octets** de pixels. Les
cadences reprennent celles du site : généralement **260 ms/image**, 280 ms pour
`jumping` et 350 ms pour `waving`. Le halo conserve sa couleur propre à chaque état.
Un atlas v1 (9 lignes) n'a pas les lignes de regards : `thinking` y retombe sur `idle`.
Après changement de mapping, reconvertir le pack puis le réinstaller avec `--replace`.

Le robot intégré reste disponible si la carte SD, la sélection ou un fichier du
pet manque. Une erreur de lecture désactive le lecteur d'animations sans arrêter
le service BLE. Au démarrage, le montage est tenté 3 fois ; tant que le pet est
indisponible, le firmware retente toutes les 3 s (puis toutes les 30 s après 20
essais) et redessine l'écran dès que la carte répond, sans redémarrage.

## Bus SPI partagé

`shared_spi.py` crée **un seul** objet `machine.SPI`, avec MOSI 6, SCLK 7 et MISO 5.
Les deux CS sont relevés avant tout changement de fréquence :

| Périphérique | CS | Fréquence |
|---|---|---|
| Écran | GPIO14 | 40 MHz |
| microSD | GPIO4 | 100 kHz à l'initialisation, puis 8 MHz |

Le pilote Python `sdcard_spi.py` ne possède **aucune méthode `writeblocks`**.
Le montage en fonctionnement normal est **en lecture seule**. Les lectures SD
et écritures écran sont sérialisées dans la boucle principale, jamais dans les
callbacks BLE. Les attentes du pilote SD sont bornées.

**Ne pas utiliser `machine.SDCard` en parallèle de l'écran** : il réserve le
contrôleur SPI. Il est utilisé seulement par l'installateur USB, une fois la
démo interrompue et le SPI écran libéré.

## Conversion sur le PC

Installer les outils optionnels de préparation d'assets. Le pont BLE sur le PC
de travail n'a besoin que de `pc/requirements.txt`, pas de Pillow.

```bash
python -m pip install --user -r pc/requirements-assets.txt
python -m pc.pet convert
```

Le convertisseur télécharge uniquement les métadonnées et l'image depuis le site,
pas de code exécutable. Les fichiers d'origine vont dans `.cache/pets/`, le pack
dans `build/pets/super-saiyan-goku-combat/`. Ces dossiers sont ignorés par Git.

`preview.png` permet de vérifier les premières images. Les sources, l'auteur et
le hash SHA-256 de la spritesheet sont conservés dans le manifeste. La licence du
code du site ne vaut pas autorisation de redistribution de ses images ; voir
[`THIRD_PARTY.md`](../THIRD_PARTY.md).

Autre pet compatible avec l'atlas Codex Pets v1/v2 :

```bash
python -m pc.pet convert --pet another-pet-id
```

Pour remplacer les fichiers générés, utiliser explicitement `--force`. Une option
`--frame-ms` (100 à 2000) permet de changer la durée des images à la conversion.

## Installation sur la SD, sans reformatage

La SD reste insérée dans l'ESP32. Vérifier le port série avant l'installation :

```bash
python -m serial.tools.list_ports -v
python -m pc.pet install build/pets/super-saiyan-goku-combat --port COM9
```

L'installateur :

1. Interrompt le firmware et la liaison BLE avec `mpremote`.
2. Monte uniquement la SD externe, sans jamais la formater.
3. Crée le dossier du pack ; refuse de remplacer un pack existant sans `--replace`.
4. Copie les images et le manifeste.
5. **Relit chaque fichier sur l'ESP32 et vérifie son SHA-256**.
6. Active le pack via `/sd/openbuddy/active.json`, puis redémarre la carte.

Pour réinstaller un pack OpenBuddy connu, ou reprendre son transfert interrompu :

```bash
python -m pc.pet install build/pets/super-saiyan-goku-combat --port COM9 --replace
```

Seuls les fichiers attendus dans le dossier du pack sont remplacés. Aucune
suppression récursive, aucun effacement de la flash et aucune modification des
clés Bluetooth. Le marqueur `openbuddy-install.json` permet de reconnaître une
installation interrompue avant la publication du manifeste.

### Organisation sur la carte

```text
/sd/openbuddy/
  active.json                    {"pet":"super-saiyan-goku-combat"}
  pets/super-saiyan-goku-combat/
    manifest.json
    idle.rgb565  waving.rgb565  jumping.rgb565  failed.rgb565  waiting.rgb565
    running.rgb565  review.rgb565  look.rgb565  retry.rgb565
```

Chaque fichier est la concaténation de ses images, sans en-tête. Le manifeste v1
décrit le format, la taille, le fond, les états, le nombre d'images, leur durée et
les SHA-256. Le firmware borne les dimensions, tailles de métadonnées, noms de
fichiers et nombre d'images, et refuse les packs incomplets ou les chemins relatifs.

## Tests et mesures réelles

```bash
python -m unittest discover -s tests -v
python -m mpremote connect COM9 run tools/benchmark_pet.py
python -m mpremote connect COM9 reset
python -m pc.buddy demo
python -m pc.buddy status
```

Le benchmark interrompt le compagnon, puis lit/affiche les images aussi vite que
possible, sans leur temporisation normale. Il vérifie de nouveau les SHA-256
**via le pilote SPI partagé**. Toujours réinitialiser ensuite pour relancer le
compagnon autonome.

Mesures sur la carte et la SD actuellement branchées :

- 64 alternances lecture SD / écriture écran : **829 ms**, secteurs identiques.
- 41 images et 7 SHA-256 validés : **1 877 ms** au total, avec BLE actif.
- Lecture d'une image : **35,96 ms en moyenne**, **40,98 ms maximum observé**.
- Envoi à l'écran : **6,70 ms en moyenne**.
- Mémoire libre après collecte : **250 160 octets**, buffer de 19 968 octets inclus.

Ces mesures ne constituent pas une garantie de temps réel pour d'autres cartes SD.
La commande `status` du firmware 0.2.0 ajoute `sd` (montage réussi) et `pet` (ID du
lecteur actif, ou `null` en cas de repli). Les événements OpenCode réels restent
la prochaine étape : les commandes actuelles sont toujours des diagnostics BLE.

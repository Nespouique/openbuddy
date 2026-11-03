# Événements réels OpenCode v2

## Architecture actuelle : lecture seule

```text
OpenCode v2, service local géré
  → plugin openbuddy.events (abonnement aux événements)
  → HTTP 127.0.0.1, authentifié par un jeton local
  → pont Python/Bleak
  → Bluetooth LE GATT
  → état + animation Goku + halo
```

Le plugin est installé **globalement sur ce PC** dans
`~/.config/opencode/plugins/openbuddy/`. Le bundle généré reste dans
`integration/openbuddy/dist/`, hors autochargement, pour éviter une deuxième copie.
Il observe les événements via `ctx.event.subscribe()` : **aucun hook de
permission, aucune modification des prompts, aucun nouvel outil agent**.

Les boutons sont seulement signalés dans la console du pont. Cette version ne
répond ni aux permissions ni aux formulaires, et n'arme jamais le clavier HID.
Les autorisations restent à confirmer dans OpenCode.

## Démarrer et arrêter

Depuis la racine du projet, avec l'ESP32 déjà appairé :

```bash
python -m pip install --user -r pc/requirements.txt
python -m pc.opencode
```

Ce programme reste actif ; **Ctrl+C** l'arrête. Il reconnecte le BLE en cas de
coupure, et le plugin redécouvre automatiquement le pont après un redémarrage.
Ne pas lancer en même temps `pc.buddy demo`, `watch` ou `hid-test`, qui cherchent
à piloter le même canal BLE.

Le pont nécessite **Python 3.11+**. Sous Windows, il joue désormais un son système
à l'entrée dans `permission`, `question`, `success` ou `error`, sans répéter les
heartbeats. Les notifications fonctionnent même si le BLE est indisponible.

```bash
python -m pc.opencode --mute
python -m pc.opencode sound-test
python -m pc.opencode --no-ble
```

`--mute` coupe les sons ; `sound-test` joue les quatre sons Windows ; `--no-ble`
permet les notifications PC sans utiliser le périphérique. Les sons suivent les
réglages audio Windows et leurs erreurs n'arrêtent pas le pont.
Si Windows n'associe aucun son à `SystemQuestion`, un repli vers le son
`SystemExclamation` est utilisé sans modifier le registre ni le volume.

Depuis un autre terminal :

```bash
python -m pc.opencode status
python -m pc.opencode stop
```

`status` indique le nombre d'instances du plugin reçues, la connexion BLE, l'état
souhaité et `last_delivered`, le dernier état ayant reçu un ACK de l'ESP32.
Le champ `sounds_enabled` indique si les notifications sonores sont activées.
`stop` arrête uniquement le pont, pas OpenCode ni son service.

Options utiles : `--address AA:BB:CC:DD:EE:FF`, `--port 19847`, `--no-ble`
(diagnostic sans périphérique), `--duration 60` (essai limité à 60 secondes).

Les changements sous le dossier global `plugins/` sont rechargés par OpenCode.
Si le plugin n'apparaît pas après une première copie, utiliser **`/reload`**.
L'installation ne modifie pas `opencode.json(c)` ni les autres plugins.
Après une modification Python, arrêter puis relancer le pont : le processus déjà
en cours ne recharge pas le code sonore automatiquement.

## États et priorités

| Événement ou situation | État du compagnon |
|---|---|
| `permission.asked` / demandes en attente | `permission`, priorité maximale |
| `form.created` / formulaires en attente | `question`, après les permissions |
| `session.tool.input.started`, `session.tool.called` | `working` |
| `session.text.started` | `working` : réponse en cours |
| `session.reasoning.started`, exécution active | `thinking` |
| `session.compaction.started` | `thinking` : compaction |
| `session.retry.scheduled` | `retry` jusqu'à la reprise programmée |
| `session.execution.succeeded` | `success` pendant 8 secondes, puis `idle` |
| `session.execution.failed` | `error` pendant 8 secondes, puis `idle` |
| `session.execution.interrupted` | pas de faux succès |
| Plus d'exécution, plugin actif | `idle` |

Les demandes sont suivies **par identifiant** : répondre à une permission ou à un
formulaire ne masque pas les autres. Une session terminée ne masque pas une autre
session encore active. Une erreur d'outil récupérable n'est pas assimilée à un
échec terminal de session.

Les attentes humaines dominent l'activité. Entre autres sessions actives, les
outils, la compaction, l'écriture et les retries dominent l'activité générique.
Les états de fin ne s'affichent que lorsqu'aucune exécution ne reste active.

Le plugin envoie au maximum quatre mises à jour par seconde et conserve une
seule requête réseau en vol. Un état inchangé est rafraîchi toutes les 3 secondes.
Le pont conserve une lease de 12 secondes par instance. Sans instance valide, il
cesse les heartbeats vers le firmware, qui devient `OFFLINE` après 15 secondes
supplémentaires au maximum. Arrêter directement Python conduit à `OFFLINE` selon
le délai normal du firmware.

Fermer la fenêtre OpenCode n'arrête pas nécessairement son service partagé : un
plugin encore chargé dans ce service peut donc continuer à signaler `idle`.

## Resynchronisation

Le flux d'événements est live-only. Toutes les 15 secondes, le plugin relit :

- les sessions du dossier via `ctx.session.list`, avec pagination ;
- les permissions et formulaires en attente via le client officiel v2 ;
- les événements arrivés pendant cette lecture, qui sont rejoués ensuite.

Le contexte plugin 2.2.5 n'expose pas toutes les méthodes `form` du client HTTP.
Le plugin utilise donc `Service.discover` et `Service.headers` du SDK officiel
pour les lectures complémentaires. Il vérifie que le PID du serveur découvert
est **celui qui exécute le plugin**, afin de ne pas mélanger deux serveurs.
Il ne démarre et n'arrête aucun service OpenCode.

Cette intégration cible le **service local géré OpenCode v2**. Les serveurs distants
et le mode `--standalone` ne sont pas pris en charge par cette resynchronisation.
Si la synchronisation initiale échoue, aucun état supposé fiable n'est publié.

Limites de sécurité : 2 000 sessions par snapshot, 2 048 événements tamponnés
pendant une synchronisation, 64 instances de plugin côté pont, délais réseau bornés.

## Confidentialité et interface locale

Le plugin n'envoie que : version, identifiant aléatoire d'instance, séquence,
état, libellé générique et trois compteurs. **Aucun prompt, nom de session,
chemin de fichier, argument/résultat d'outil, texte de question ou secret** n'est
transmis au pont ni à la carte.

Le pont :

- écoute uniquement sur **127.0.0.1**, port 19847 par défaut ;
- exige un jeton aléatoire de 256 bits et refuse les requêtes avec `Origin` ;
- contrôle l'hôte HTTP, borne les en-têtes et les corps, refuse le chunked ;
- ne propose que `/state`, `/health` et `/stop`, aucune API d'exécution/validation.

Le canal plugin/pont et les commandes `status`/`stop` utilisent des connexions
loopback directes, sans faire passer le jeton du pont par un proxy d'entreprise.
Cela ne modifie aucun réglage proxy global du poste.

Le fichier de découverte `%LOCALAPPDATA%\openbuddy\bridge.json` contient le port
et ce jeton. Sous Unix : `$XDG_CACHE_HOME/openbuddy/bridge.json` ou
`~/.cache/openbuddy/bridge.json`. **Ne pas publier ce fichier.** Il est remplacé
atomiquement et supprimé à l'arrêt seulement s'il appartient encore au pont.
Un second pont ne peut pas écraser la découverte d'un pont actif.

Le jeton protège contre une page web ou un autre client dépourvu de ce fichier,
pas contre un programme ayant déjà les droits du même compte utilisateur.

## Utilisation sur le PC de travail

Un kit transférable avec dépendances hors ligne et installation guidée est généré
dans **`dist/openbuddy-workplace.zip`**. Son README est également consultable dans
[`distribution/workplace/README.md`](../distribution/workplace/README.md).
Le kit cible CPython **3.11–3.14, Windows x64**, sans Node/npm ni droits admin.
Il inclut un installateur global/par projet qui préserve les configurations et
sauvegarde une ancienne copie OpenBuddy avant un remplacement explicite.

Le plugin généré est autonome : environ **116 Ko**, sans paquet npm à résoudre
à l'exécution. OpenCode fournit son runtime JavaScript ; **Node/npm ne sont pas
nécessaires pour l'utiliser**.

Utiliser de préférence le ZIP. Pour une copie manuelle depuis le dépôt, prendre
`integration/openbuddy/dist/` (`index.js`, `package.json`, `LICENSES.txt`), `pc/`
et `c6lcd/buddy_protocol.py`, puis installer `pc/requirements.txt` et appairer la carte.

Les restrictions Bluetooth/GATT et d'exécution du poste d'entreprise restent
applicables. La connexion comme clavier ne les contourne pas.

L'installation globale couvre les projets chargés par OpenCode. Une installation
limitée à un projet reste possible via `pc.install_plugin --project`.
Ne pas cumuler les deux : OpenCode signale `Duplicate plugin ID: openbuddy.events`
pour la copie supplémentaire. Lors d'un reload,
une ancienne instance peut rester comptée jusqu'à expiration de sa lease.

Lors du transfert du device, déconnecter le Bluetooth du PC personnel : Windows
peut maintenir la connexion clavier même si son pont Python est arrêté. L'ESP32
actuel accepte un seul hôte BLE à la fois.

## Développement du plugin

Sources dans `integration/openbuddy/src/`. Ne pas éditer le bundle à la main :

```bash
npm --prefix integration/openbuddy ci --ignore-scripts
npm --prefix integration/openbuddy run typecheck
npm --prefix integration/openbuddy test
npm --prefix integration/openbuddy run build
python -m pc.install_plugin --replace
python -m unittest discover -s tests -v
```

Le build esbuild inclut le client officiel et ne laisse que des imports natifs
`node:*`. Le plugin est typé contre **`@opencode/plugin` / `@opencode/client` 2.2.5**.
La définition `id` / `setup` respecte le contrat v2 ; elle n'utilise aucun hook v1.
Les fichiers générés sont dans `integration/openbuddy/dist/` ; leur réinstallation
copie atomiquement la version mise à jour dans le dossier global surveillé.

L'audit npm des dépendances de développement/SDK signale actuellement **24 alertes
modérées transitives**, liées à `glob` / `minimatch`, sans correctif proposé pour
ces versions amont. Ne pas lancer `npm audit fix --force` ni changer le SDK au
hasard ; tester une mise à jour compatible d'OpenCode/SDK. Le bundle construit
ne contient que le client OpenCode, pas ces bibliothèques de globbing.

## Vérifications réalisées

- OpenCode **2.2.5**, plugin global `openbuddy.events` signalé **active** sans doublon.
- États réels de la session reçus et acquittés par l'ESP32 via BLE.
- Formulaire réel de l'outil `question` : utilisateur confirme `QUESTION`, halo
  jaune et animation d'attente ; retour à l'activité après réponse.
- Les permissions multiples, parallélismes, erreurs, fins et annulations sont
  couverts par tests ; aucune permission dangereuse n'a été provoquée pour tester.
- Notification sonore sur une question réelle confirmée par l'utilisateur ;
  pont relancé avec `sounds_enabled: true` et repli pour `SystemQuestion` silencieux.
- **65 tests Python + 11 tests TypeScript**, vérification de types et import du
  bundle autonome validés. Aucun changement du firmware 0.2.0 ou de la SD requis.

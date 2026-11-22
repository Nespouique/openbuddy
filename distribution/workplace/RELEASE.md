## Nouveautés de la version 1.3.0

- **Console web locale intégrée (`http://127.0.0.1:8765/`)** : administration à chaud du pont (USB, Bluetooth LE, mode virtuel sans ESP32, sons, notifications, quota, pet actif), aperçu fidèle 320×172 et tests manuels.
- **Pet flottant transparent (« Keep the pet visible »)** : reste au premier plan au-dessus des autres fenêtres, clic simple pour ramener VS Code / OpenCode, **survol et clic droit pour afficher et actualiser le quota**.
- **Collection de pets & mapping drag & drop** : import direct par URL `codex-pets.net` (ex. `ferris-rust-crab`, `monthly-salary-cat`) avec conversion navigateur sans Pillow, et association glisser-déposer entre les animations disponibles et les états OpenCode.
- **Détection automatique du terminal OpenCode** : passe automatiquement le pet en `OFFLINE` dès qu'aucun terminal OpenCode n'est ouvert (même si le service partagé continue de tourner en arrière-plan), configurable depuis l'interface web.
- **Dépendance `bleak` rendue optionnelle** : le kit USB et `pc/requirements.txt` s'installent sans `bleak` ; les trois modes (**USB**, **Bluetooth LE**, **Virtuel**) restent pris en charge.

## Deux kits Windows prêts à installer

- **`openbuddy-workplace-usb.zip` — USB & Virtuel (sans `bleak`, recommandé sur PC de travail)** : pilotage de l'ESP32 par câble USB ou mode virtuel sans matériel, avec **firmware 0.4.0**, `pc.deploy` et les lanceurs Windows `tools\openbuddy-usb.cmd` / `openbuddy-usb-stop.cmd`. Aucune dépendance Bluetooth (`bleak`) requise.
- **`openbuddy-workplace.zip` — Bluetooth LE + USB + Virtuel** : inclut les wheels hors ligne de `bleak==3.0.2` en plus de `pyserial` et des notifications Windows pour synchroniser l'ESP32 en Bluetooth LE, en USB ou en mode virtuel.

Communs aux deux kits :

- Notifications Windows natives **et** son sur permission, question, succès et erreur.
- Plugin autonome pour OpenCode v2 local géré, sans Node/npm sur le PC cible.
- Dépendances hors ligne pour CPython 3.11–3.14, Windows x64.
- Installation sans droits administrateur, sans modifier la configuration OpenCode.
- Aucune clé Bluetooth, aucun jeton de pont, aucune configuration personnelle et aucune image de pet dans les kits.

Télécharger le ZIP voulu dans les assets ci-dessous, l'extraire et suivre le `README.md` inclus. Le fichier `.zip.sha256` correspondant permet de vérifier l'intégrité du téléchargement. Si l'accès GATT Bluetooth est refusé par la politique du poste (`Access Denied`), utiliser le kit USB.

Les notifications respectent les paramètres Windows et le mode Ne pas déranger. Les boutons et toasts ne valident aucune autorisation automatiquement. Utiliser un canal de téléchargement autorisé par la politique du PC de travail.

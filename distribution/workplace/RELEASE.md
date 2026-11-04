## Deux kits Windows prêts à installer

- **`openbuddy-workplace.zip` — Bluetooth** : animation et halo de l'ESP32
  synchronisés par Bluetooth LE.
- **`openbuddy-workplace-usb.zip` — USB, sans Bluetooth** : même écran, même
  animation et même halo par le câble USB. Il contient le **firmware 0.3.0** et
  `pc.deploy` pour mettre la carte à jour sans effacer les clés Bluetooth ni la SD.
  Les boutons ne sont pas remontés et le clavier HID n'existe pas dans ce mode.
  Des lanceurs Windows sont inclus pour démarrer et arrêter le pont USB.

Communs aux deux kits :

- Notifications Windows natives **et** son sur permission, question, succès et erreur.
- Plugin autonome pour OpenCode v2 local géré, sans Node/npm sur le PC cible.
- Dépendances hors ligne pour CPython 3.11–3.14, Windows x64.
- Installation sans droits administrateur, sans modifier la configuration OpenCode.
- Aucune clé Bluetooth, aucun jeton de pont, aucune configuration personnelle et
  aucune image de pet dans les kits.

Télécharger le ZIP voulu dans les assets ci-dessous, l'extraire et suivre le
`README.md` inclus. Le fichier `.zip.sha256` correspondant permet de vérifier
l'intégrité du téléchargement. Si l'accès GATT Bluetooth est refusé par la politique
du poste (`Access Denied`), utiliser le kit USB.

Les notifications respectent les paramètres Windows et le mode Ne pas déranger.
Les boutons et toasts ne valident aucune autorisation automatiquement.
Le kit USB ne modifie aucune politique Bluetooth ou USB du poste ; le port série
doit être autorisé. Le firmware conserve son transport Bluetooth en parallèle.
Utiliser un canal de téléchargement autorisé par la politique du PC de travail.

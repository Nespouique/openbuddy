# Matériel OpenBuddy

## Carte principale

**Waveshare ESP32-C6-LCD-1.47, version non tactile**, écran ST7789 172×320,
LED RVB WS2812 et emplacement microSD. Ne pas confondre avec la variante Touch.
Le projet utilise MicroPython `ESP32_GENERIC_C6` 1.29.0, sans compilation C.

Orientation retenue : **landscape 320×172, USB-C à gauche**.

| Fonction | GPIO |
|---|---|
| LCD/SD MOSI | 6 |
| LCD/SD SCLK | 7 |
| LCD CS / DC / RST | 14 / 15 / 21 |
| Rétroéclairage LCD | 22 |
| WS2812 RVB | 8 |
| Bouton BOOT, actif bas | 9 |
| SD CS / MISO | 4 / 5 |

## Contraintes impératives

- Rétroéclairage : **PWM ≤ 50 %**, jamais GPIO22 en sortie simple.
- ST7789 : `INVON`, offset de **34 px** sur l'axe court, RGB565 **octet fort en
  premier** et effacement de toute la RAM 240×320 au changement d'orientation.
- LED : ordre **GRB**, chaque composante reste **< 40**.
- Écran/SD : un seul `machine.SPI`, CS relevés avant changement de fréquence.
  LCD 40 MHz ; SD 100 kHz à l'initialisation puis 8 MHz.
- SD **FAT32**, lecture seule pendant l'animation. Ne jamais reformater une carte
  automatiquement. Le pilote SD natif est utilisé seulement pour l'installation
  USB, firmware interrompu et contrôleur SPI écran libéré.
- Aucun buzzer ou haut-parleur intégré : les sons et notifications sont sur le PC.

Le firmware fonctionne aussi sans assets SD, avec un robot intégré de secours.
Une image 96×104 RGB565 occupe 19 968 octets ; les animations ne sont pas envoyées
en continu depuis le PC.

## Déploiement

Les numéros de port série changent selon le PC :

```powershell
python -m serial.tools.list_ports -v
python -m pc.deploy --port COMX
```

Remplacer `COMX` par le port détecté. L'outil copie les fichiers du firmware sans
effacer les clés Bluetooth ou la SD, puis redémarre la carte. Pour les animations,
voir [animations](animations.md).

## Bluetooth

Le device expose un clavier HID et un service GATT OpenBuddy. L'appairage exige
un appui long BOOT 3 s, la vérification du code, puis un **deuxième appui court**.
Un seul hôte BLE est accepté à la fois. Windows peut conserver la connexion HID
après l'arrêt du pont Python : déconnecter l'ancien hôte avant un nouvel appairage.

Les identifiants de carte, ports personnels et journaux de diagnostic ne font
pas partie du dépôt public. L'adresse BLE réelle se découvre avec `pc.buddy scan`.

Référence : <https://www.waveshare.com/wiki/ESP32-C6-LCD-1.47>.

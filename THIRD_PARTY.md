# Third-party notices

## OpenCode client in the event plugin

The standalone `integration/openbuddy/dist/index.js` bundle includes the official
`@opencode/client` 2.2.5 and its local service helpers. Source:
<https://github.com/anomalyco/opencode/tree/v2.2.5/packages/client>.
The MIT notice is included in `integration/openbuddy/assets/LICENSES.txt`, copied
to the generated plugin package, and must
accompany copies of the plugin. Node built-in modules remain external.

## SPI SD card driver

`c6lcd/sdcard_spi.py` is adapted from MicroPython's SPI SD card driver:

- Repository: <https://github.com/micropython/micropython-lib>
- Path: `micropython/drivers/storage/sdcard/sdcard.py`, version 0.2.1
- Upstream Git blob: `8f43725860572510b27be2a66aa30da526188410`
- License reference: <https://github.com/micropython/micropython-lib/blob/master/LICENSE>
- Adaptations: a read-only block API, bounded waits, bus-speed switching for
  LCD/SD sharing, explicit byte masks, and a masked SDHC CSD capacity field.

### MIT License

Copyright (c) 2013, 2014 micropython-lib contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

## Codex Pets assets

Pet images are community assets, not covered by the driver's MIT license.
The selected Super Saiyan Goku Combat pet is shared by **datpham** at
<https://codex-pets.net/#/pets/super-saiyan-goku-combat>.

No redistribution license for the character artwork has been verified. Downloads
and converted binaries are kept in ignored local directories and copied only to
the user's device. Do not assume that the site's code license grants rights to
its community images or to the original character. Each generated manifest records
the source URL and attribution.

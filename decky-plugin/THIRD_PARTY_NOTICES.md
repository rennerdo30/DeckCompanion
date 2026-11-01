# Decky plugin dependencies

DeckCompanion plugin code is MIT, see LICENSE. Built with the current Decky template
configuration shape; no template demonstration code is included.

- `@decky/api` 1.1.3, SteamDeckHomebrew Team, LGPL-2.1. The ZIP includes its exact
  unmodified npm source, license and package metadata in `sources/decky-api.tar.gz`.
  Source: https://github.com/SteamDeckHomebrew/loader-api and
  https://registry.npmjs.org/@decky/api/-/api-1.1.3.tgz.
- `@decky/ui` 4.16.4, SteamDeckHomebrew Team, LGPL-2.1, remains an external runtime
  library supplied by Decky Loader; it is not bundled. Source and license:
  https://github.com/SteamDeckHomebrew/decky-frontend-lib.
- React and its JSX runtime remain external libraries supplied by Steam/Decky.

To modify or replace the API library, replace `node_modules/@decky/api` with your
compatible version/source build, then run the documented frontend and ZIP build.
No API library modifications are applied by this plugin. The plugin's MIT license
does not restrict modification or relinking with the LGPL library.

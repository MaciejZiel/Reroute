# Third-party components

| source | name | license type | license link |
| --- | --- | --- | --- |
| https://pypi.org/project/gtfs-rt-kit/ | gtfs-rt-kit | MIT | https://github.com/MaciejZiel/gtfs-rt-kit/blob/main/LICENSE |
| https://pypi.org/project/gtfs-realtime-bindings/ | gtfs-realtime-bindings (transitive dependency of gtfs-rt-kit; used directly by tests to build feeds) | Apache-2.0 | https://github.com/MobilityData/gtfs-realtime-bindings/blob/master/LICENSE |
| https://www.npmjs.com/package/vitest | vitest (frontend dev dependency, 5.0.2) | MIT | https://github.com/vitest-dev/vitest/blob/main/LICENSE |
| https://www.npmjs.com/package/oxlint | oxlint (frontend dev dependency, 1.85.0) | MIT | https://github.com/oxc-project/oxc/blob/main/LICENSE |
| https://openfreemap.org/ | OpenFreeMap vector tiles and glyphs (public instance, no API key; map data from OpenStreetMap, schema OpenMapTiles) | MIT (service code), ODbL (OpenStreetMap data), BSD-3-Clause (OpenMapTiles code), CC-BY-4.0 (OpenMapTiles design) | https://github.com/hyperknot/openfreemap/blob/main/LICENSE.md |
| https://www.npmjs.com/package/@types/node | @types/node (frontend dev dependency, 24.19.0) | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped/blob/master/LICENSE |
| https://github.com/notofonts/latin-greek-cyrillic | Noto Sans Regular and Bold glyphs (map labels, loaded as PBF glyph ranges from OpenFreeMap) | OFL-1.1 | https://github.com/notofonts/latin-greek-cyrillic/blob/main/OFL.txt |
| https://pypi.org/project/numpy/ | numpy 2.5.3 (transitive runtime dependency of shapely, already present before the delay model and unmodified; the server uses the system build from Termux). Its own code is BSD-3-Clause and its licence file also covers bundled 0BSD, MIT, Zlib and CC0-1.0 parts; the Linux wheels additionally bundle libquadmath (LGPL-2.1-or-later) and libgfortran (GPL-3.0-or-later WITH GCC-exception-3.1), as their LICENSE.txt states | BSD-3-Clause (wheels bundle LGPL-2.1-or-later and GPL-3.0-or-later WITH GCC-exception-3.1 libraries) | https://github.com/numpy/numpy/blob/main/LICENSE.txt |
| https://pypi.org/project/psycopg/ | psycopg (runtime database driver, already a dependency before this change, recorded here for completeness; used unmodified through its public interface) | LGPL-3.0-only | https://github.com/psycopg/psycopg/blob/master/LICENSE.txt |
| https://pypi.org/project/certifi/ | certifi (transitive runtime dependency of httpx, already present, recorded here for completeness; unmodified) | MPL-2.0 | https://github.com/certifi/python-certifi/blob/master/LICENSE |
| https://www.npmjs.com/package/lightningcss | lightningcss (frontend build-time transitive dependency of vite, 1.33.0 in package-lock.json, already present, recorded here for completeness; unmodified, not shipped in the bundle) | MPL-2.0 | https://github.com/parcel-bundler/lightningcss/blob/master/LICENSE |

# Third-party licenses outside MIT / Apache-2.0 / BSD / ISC

Countersign's own code is MIT (see `LICENSE`). The repository vendors no third-party code. These dependencies are installed by pip or npm from their registries, and they are used unmodified.

| Dependency | License | How it is used | Why it is acceptable |
|---|---|---|---|
| `psycopg`, `psycopg-binary` 3.3.4 (backend) | LGPL-3.0-only | Imported as a library; not modified or redistributed by this repo | The LGPL allows use from code under other licenses when the library is used unmodified and users can replace it (it is a pip dependency) |
| `@img/sharp-libvips-*`, `@img/sharp-*` (optional dependencies of Next.js) | LGPL-3.0-or-later, plus Apache-2.0 / MIT | Platform binaries for Next's image optimizer. Countersign uses no `next/image`. They are installed by npm, never committed | Same as above: dynamic, unmodified, replaceable |
| `certifi` (via httpx) | MPL-2.0 | CA bundle, unmodified | File-level copyleft; applies only to modifications of certifi's own files |
| `caniuse-lite` (via Next.js) | CC-BY-4.0 | Browser-support data used at build time | Attribution is kept in the package itself |
| `typing-extensions` | PSF-2.0 | Library | Permissive |

These were checked on 16 Oct 2026 with `importlib.metadata` (Python) and `web/package-lock.json` (npm). Re-check after any dependency change.

# Bundled browser libraries

The following packages are distributed with their upstream MIT licenses:

- [markdown-it 15.0.2](https://github.com/markdown-it/markdown-it): copyright Vitaly Puzrin, Alex Kocharin. See `codexremote/web/vendor/markdown-it/LICENSE`.
- [KaTeX 0.18.7](https://github.com/KaTeX/KaTeX), including its fonts: copyright Khan Academy and other contributors. See `codexremote/web/vendor/katex/LICENSE`.

`python scripts/vendor_assets.py` downloads these exact npm archives and verifies their SHA-512 integrity before copying browser assets. The app serves all assets from the local machine; it does not contact a CDN.

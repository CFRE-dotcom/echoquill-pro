# EchoQuill Pro (PRIVATE — never make this repo public)

<p align="center"><img src="assets/hero.png" alt="EchoQuill — free voice dictation for Windows" width="820"></p>


The paid edition. Everything in the free app plus: license-key activation
(Lemon Squeezy), unlimited video transcriptions, unlimited clip library
(50/page), ★ Favorites, and Ask AI about any transcript with timestamped answers.

## Release flow
1. Bump version in `echoquill/__init__.py` + `installer.iss`
2. `git tag vX.Y.Z && git push origin main vX.Y.Z`
3. GitHub Actions builds `EchoQuill-Pro-Setup.exe` (private release)
4. Download it from this repo's Releases → upload as the file on the
   Lemon Squeezy product → buyers get key + download automatically

Public repo (free edition): github.com/CFRE-dotcom/echoquill

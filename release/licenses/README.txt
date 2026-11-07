Microsoft.Web.WebView2.LICENSE.md is copied verbatim from pywebview 6.2.1:
https://github.com/r0x0r/pywebview/blob/6.2.1/webview/lib/Microsoft.Web.WebView2.LICENSE.md
Upstream Git blob: 5dd43f57f273797aea6904ae8000c870e424b35a
These notices cover the Microsoft WebView2 DLLs shipped by pywebview.

proxy_tools-LICENSE.txt is preserved verbatim from:
https://github.com/jtushman/proxy_tools/blob/db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6/LICENSE.txt
License Git blob: 078411c7399fbc0454f36cbe8c8cdbaaba7ebc95
SHA256: a428fb8a2e762af3eb0a6edbbb88e9b42ccfee80fd9b423958bcacf9b9abbfe4
The official proxy_tools 0.1.0 PyPI sdist omits its license file.
Its proxy_tools/__init__.py exactly matches code blob:
ea266c8f49da949ec6199a616609637c9aab3fb8 in that upstream commit.
The sdist setup.py metadata says MIT while this upstream notice says BSD.
The original BSD notice is preserved, not replaced by a license template.
This records the provenance discrepancy and does not assert legal certainty.
The fallback requires exact version, code blob, and license blob matches.

The actual Python 3.12.10 Windows LICENSE.txt is copied without changes
from the runner's installed runtime. It is not replaced with source LICENSE.
Its 36,874 original bytes match the official Windows embeddable archive:
https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip
Archive SHA256: 4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3
LICENSE.txt SHA256: e502c6b880ff58d614901495a9009c136539cd0b1e2a2abb8fc00b934c203419
Its sole email match is a public bzip2 author's attribution contact.
The controller's scan entry point reviews only that exact notice hash at
explicit known notice paths, after running the unchanged original guard.
Other email/secret/path verdicts and Gitleaks remain unchanged.
A changed Python notice must be reviewed again; it is not generically exempt.

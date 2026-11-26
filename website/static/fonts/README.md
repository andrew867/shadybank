# Brand web fonts

These are the four unchanged WOFF files served by the project's existing website. The stylesheet now loads them relative to its own static location, without using the website's domain as a font origin. Their existing font names, glyphs and embedded copyright notices are unchanged.

| Face | Local asset |
| --- | --- |
| Eurostile T regular | `EurostileT.woff` |
| Eurostile T black | `EurostileTBla.woff` |
| Bell Gothic Roman | `bellgothic.woff` |
| Bell Gothic Black | `bellgothic-black.woff` |

The regression test checks the exact file digests and stylesheet paths. These are existing brand assets, not replacement fonts; their respective rights remain with the font owners.

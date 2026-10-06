Manrope is distributed under SIL OFL 1.1; see OFL.txt.
Upstream: the googlefonts/manrope repository on GitHub; the license text is
the OFL.txt shipped alongside this file.

Manrope.woff2 is a subset of the supplied Manrope variable TTF, preserving
Latin, Cyrillic, punctuation, arrows and ruble sign. Reproduction:

    pyftsubset Manrope.ttf --unicodes='U+0000-024F,U+0400-052F,U+2000-206F,U+20BD,U+2190-21FF,U+25B2,U+25BC' --flavor=woff2 --output-file=Manrope.woff2

Requires fonttools and brotli only for font preparation, not at runtime.
CSS, JS and font are read as package assets and embedded into every HTML file.

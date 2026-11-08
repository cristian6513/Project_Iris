# Iris PyAV native LGPL FFmpeg SDK

The bundled FFmpeg source archive is unmodified commit 29e619e767cde9045a75c29bc9a8278ae7b3a98b. This separate SDK builds seven native shared FFmpeg libraries for PyAV. It includes the permitted native CPU codec/filter/demux/mux catalog; external libraries, GPL, version3, nonfree, hardware devices and network are disabled.

Use the official pinned w64devkit binary archive and its matching source archive from pyav_ffmpeg_sources.json. The compiler is a build input, not part of Iris. Its GCC runtime exception and MinGW runtime notices are in the parent directory.

Rebuild in an extracted source directory with PATH set only to the compiler bin, COMSPEC pointing to Windows System32/cmd.exe, TMPDIR=../temp, ZERO_AR_DATE=1 and SOURCE_DATE_EPOCH=1790713770. Create ../temp first. Run sh ./configure with the exact configure argument list in pyav_ffmpeg_sources.json, then make -j4 V=1 and make install V=1. Alternatively use the included Python build_pyav_runtime.py with explicit --source, --compiler, --compiler-archive, --notices and a fresh --output directory.

PyAV dynamically links these LGPL-2.1-or-later DLLs. Compatible rebuilt DLLs can replace them; preserve the API and the wheel's mangled filenames. The wheel binding build instructions explain MSVC import libraries and delvewheel repair. The FFmpeg source and this build recipe are supplied to allow modification and rebuilding of those shared libraries.

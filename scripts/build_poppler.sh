#!/usr/bin/env bash
# Build the pdftotext and pdfinfo the layer registry names, from source, into a prefix.
#
#   POPPLER=26.08.0 POPPLER_SHA256=... POPPLER_DATA=0.4.12 POPPLER_DATA_SHA256=... \
#     bash scripts/build_poppler.sh <prefix>
#
# The `layers` job in .github/workflows/checks.yml runs this when its cache holds no build,
# and the digest of this file is part of the cache key: a changed flag is a new build, not
# the old one served again. The two source archives are held to the digests given, and a
# download that is not the archive stops the build.
#
# The libraries it links against are the ones the machine has: cmake, pkg-config, freetype,
# fontconfig, jpeg-turbo, libpng, libtiff, openjpeg and little-cms2, from Homebrew in CI.
# Nothing here pins them.
set -euo pipefail

prefix=${1:?the folder to install into}
: "${POPPLER:?}" "${POPPLER_SHA256:?}" "${POPPLER_DATA:?}" "${POPPLER_DATA_SHA256:?}"

curl -sSLO "https://poppler.freedesktop.org/poppler-${POPPLER}.tar.xz"
curl -sSLO "https://poppler.freedesktop.org/poppler-data-${POPPLER_DATA}.tar.gz"
echo "${POPPLER_SHA256}  poppler-${POPPLER}.tar.xz" | shasum -a 256 -c -
echo "${POPPLER_DATA_SHA256}  poppler-data-${POPPLER_DATA}.tar.gz" | shasum -a 256 -c -
tar xf "poppler-${POPPLER}.tar.xz"
tar xf "poppler-data-${POPPLER_DATA}.tar.gz"
cmake -S "poppler-${POPPLER}" -B poppler-build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
  -DENABLE_BOOST=OFF -DENABLE_QT5=OFF -DENABLE_QT6=OFF -DENABLE_GLIB=OFF -DENABLE_CPP=OFF \
  -DENABLE_NSS3=OFF -DENABLE_GPGME=OFF -DENABLE_LIBCURL=OFF -DENABLE_GTK_DOC=OFF \
  -DBUILD_GTK_TESTS=OFF -DBUILD_QT5_TESTS=OFF -DBUILD_QT6_TESTS=OFF -DBUILD_CPP_TESTS=OFF \
  -DBUILD_MANUAL_TESTS=OFF -DENABLE_LIBTIFF=ON -DENABLE_DCTDECODER=libjpeg \
  -DENABLE_LIBOPENJPEG=openjpeg2 -DENABLE_LCMS=ON
cmake --build poppler-build -j4
cmake --install poppler-build
make -C "poppler-data-${POPPLER_DATA}" install prefix="$prefix"

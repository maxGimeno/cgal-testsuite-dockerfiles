FROM cgal/testsuite-docker:debian-stable
MAINTAINER Maxime GIMENO <maxime.gimeno@geometryfactory.com>

RUN apt-get update && apt-get install -y \
    gcc-arm-linux-gnueabihf \
    g++-arm-linux-gnueabihf \
    ssh \
    python3 \
    gzip

ENV CGAL_TEST_PLATFORM="Debian-stable-cross-compilation-for-arm"
COPY ./startup.sh .
CMD bash ./startup.sh

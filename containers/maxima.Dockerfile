FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends maxima maxima-doc && \
    rm -rf /var/lib/apt/lists/* && \
    touch /usr/share/doc/maxima/info/maxima-index.lisp

ENV MAXIMA_NOHELP=1
ENTRYPOINT ["maxima", "--very-quiet", "--batch-string"]

FROM python:3.12.4-bookworm

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get upgrade -y

ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8

WORKDIR /usr/bot

COPY ./src/requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip
RUN pip install -r /tmp/requirements.txt

# OpenTagBridge Google fetch tooling is installed in an isolated venv.
ENV GOOGLE_FETCH_SCRIPT_URL="https://raw.githubusercontent.com/diminDDL/OpenTagBridge/cee726bd3779ced233d5b3c496bcc188efc66872/portable_fetch_update.py"
ENV GOOGLE_FETCH_REQUIREMENTS_URL="https://raw.githubusercontent.com/diminDDL/OpenTagBridge/cee726bd3779ced233d5b3c496bcc188efc66872/requirements.txt"
ENV OPENTAG_GOOGLE_FETCH_ROOT="/opt/opentagbridge"
ENV OPENTAG_GOOGLE_FETCH_VENV="/opt/google-fetch-venv"
ENV OPENTAG_GOOGLE_FETCH_SCRIPT="/opt/opentagbridge/portable_fetch_update.py"
ENV OPENTAG_GOOGLE_FETCH_PYTHON="/opt/google-fetch-venv/bin/python"

RUN apt-get install -y curl
RUN mkdir -p "$OPENTAG_GOOGLE_FETCH_ROOT"
RUN curl -fsSL -o "$OPENTAG_GOOGLE_FETCH_SCRIPT" "$GOOGLE_FETCH_SCRIPT_URL"
RUN curl -fsSL -o "$OPENTAG_GOOGLE_FETCH_ROOT/requirements.txt" "$GOOGLE_FETCH_REQUIREMENTS_URL"
RUN python -m venv "$OPENTAG_GOOGLE_FETCH_VENV"
RUN "$OPENTAG_GOOGLE_FETCH_VENV/bin/pip" install --upgrade pip
RUN "$OPENTAG_GOOGLE_FETCH_VENV/bin/pip" install -r "$OPENTAG_GOOGLE_FETCH_ROOT/requirements.txt"

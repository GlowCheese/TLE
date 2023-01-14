FROM ubuntu:18.04
USER root
WORKDIR /TLE

RUN apt-get update 
RUN apt-get install -y git apt-utils sqlite3
RUN apt-get --assume-yes install --fix-missing software-properties-common
RUN DEBIAN_FRONTEND="noninteractive" add-apt-repository ppa:deadsnakes/ppa
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y libgirepository1.0-dev
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y gir1.2-pango-1.0
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y libpython3.9-dev
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y libpango1.0-dev
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y python3.9-venv
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y libcairo2-dev
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y python3-dev
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y libjpeg-dev
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y python3-pip
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y pkg-config
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y zlib1g-dev
RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y python3.9
RUN python3.9 -m pip install --upgrade pip
RUN python3.9 -m pip install poetry

COPY ./poetry.lock ./poetry.lock
COPY ./pyproject.toml ./pyproject.toml
COPY . .

RUN chmod +x run.sh

ENTRYPOINT ["/TLE/run.sh"]
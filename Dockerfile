# Sandbox test environment for HackRF
FROM ubuntu:22.04
USER root

# Override interactive installations and install prerequisites
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    dfu-util \
    gcc-arm-none-eabi \
    git \
    libfftw3-dev \
    libusb-1.0-0-dev \
    pkg-config \
    python3 \
    python3-pip \
    python3-yaml \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install git+https://github.com/CapableRobot/CapableRobot_USBHub_Driver --upgrade

RUN curl -L https://github.com/mvp/uhubctl/archive/refs/tags/v2.5.0.tar.gz > uhubctl-2.5.0.tar.gz \
    && tar -xvzf uhubctl-2.5.0.tar.gz \
    && rm uhubctl-2.5.0.tar.gz \
    cd uhubctl \
    make \
    make install

# Inform Docker that the container is listening on port 8080 at runtime
EXPOSE 8080

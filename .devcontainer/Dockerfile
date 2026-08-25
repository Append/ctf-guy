FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# Core system tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    build-essential gcc g++ make \
    curl wget git ca-certificates \
    file binutils elfutils \
    gdb strace ltrace \
    netcat-openbsd socat nmap \
    xxd hexyl jq \
    unzip p7zip-full \
    libssl-dev libffi-dev \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# Python CTF packages
RUN pip3 install --break-system-packages \
    pwntools \
    pycryptodome \
    z3-solver \
    sympy \
    gmpy2 \
    requests \
    httpx \
    pillow \
    numpy \
    scapy \
    capstone \
    keystone-engine \
    unicorn \
    ipython \
    rich

# Node.js (for web challenges)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# radare2
RUN curl -fsSL https://github.com/radareorg/radare2/releases/download/5.9.8/radare2_5.9.8_amd64.deb -o /tmp/r2.deb && \
    dpkg -i /tmp/r2.deb && rm /tmp/r2.deb || true

# Create non-root user
RUN useradd -m -s /bin/bash solver && \
    echo "solver ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER solver
WORKDIR /challenge

# Set up Python path
ENV PYTHONDONTWRITEBYTECODE=1

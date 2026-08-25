{
  description = "CTF Guy - Semi-automated CTF challenge solver";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

        python-evtx = pkgs.python3Packages.buildPythonPackage rec {
          pname = "python-evtx";
          version = "0.7.4";
          pyproject = true;
          src = pkgs.fetchPypi {
            inherit pname version;
            hash = "sha256-aT1EGi2XRMXY1QLyve7kaOCH6jYqyMiTS0GH+3Xp7BQ=";
          };
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = with pkgs.python3Packages; [
            hexdump six more-itertools zipp configparser pyparsing
          ];
          doCheck = false;
          pythonRuntimeDepsCheck = false;
          dontCheckRuntimeDeps = true;
        };

        ctfPython = pkgs.python3.withPackages (ps: (with ps; [
          # Core CTF
          pwntools
          pycryptodome
          requests
          httpx

          # Crypto
          sympy
          gmpy2

          # Analysis
          pillow
          numpy
          scipy
          scikit-learn

          # Forensics / Network
          scapy

          # Web scraping
          beautifulsoup4
          lxml

          # Reversing helpers
          capstone
          keystone-engine
          unicorn

          # Constraint solving
          z3-solver

          # Utilities
          ipython
          rich
          click
        ]) ++ [ python-evtx ]);

      in {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            # === Python Environment ===
            ctfPython

            # === Crypto ===
            hashcat
            john
            openssl

            # === Forensics ===
            wireshark-cli
            binwalk
            foremost
            exiftool
            sleuthkit
            strace
            ltrace
            libfaketime
            volatility3

            # === Compression ===
            lzip
            lzop
            lz4

            # === Reversing ===
            ghidra
            radare2
            gdb
            gef
            file
            binutils
            elfutils

            # === 32-bit support (for i686 ELF binaries) ===
            pkgsi686Linux.glibc

            # === Web ===
            feroxbuster
            ffuf
            sqlmap
            curl
            httpie
            wget

            # === WebAssembly ===
            wabt

            # === Packing / Unpacking ===
            upx

            # === Misc / Stego ===
            steghide
            zsteg
            apktool
            # jadx  # broken in current nixpkgs (plotly build failure), use: nix shell nixpkgs#jadx

            # === General Utilities ===
            unzip
            p7zip
            jq
            yq-go
            xxd
            hexyl
            bat
            fd
            ripgrep
            socat
            nmap
            netcat-gnu
            openssh
            sshpass
            expect
            imagemagick
            poppler-utils

            # === Runtimes ===
            dotnet-sdk_9

            # === Cloud/Infra ===
            kubectl

            # === Network/SMB ===
            samba
            # wordlists  # broken in nixpkgs (wfuzz build failure), use: nix-shell -p wordlists

            # === Isolation ===
            bubblewrap

            # === Terminal sharing ===
            ttyd
            zellij
            tailscale

            # === Node.js (Playwright, scripting) ===
            nodejs_22
            mermaid-cli  # mmdc — renders Mermaid diagrams to PNG (attack graphs)

            # === Development ===
            git
            direnv
            gnumake
            uv
          ];

          shellHook = ''
            export CTF_ROOT="$(pwd)"
            export PYTHONDONTWRITEBYTECODE=1
            export PLAYWRIGHT_BROWSERS_PATH="$CTF_ROOT/.playwright-browsers"

            # WSL2 CUDA support for hashcat GPU cracking
            if [ -d /usr/lib/wsl/lib ]; then
              export LD_LIBRARY_PATH="/usr/lib/wsl/lib:/usr/local/cuda/lib64:''${LD_LIBRARY_PATH:-}"
            fi

            # 32-bit ELF support: expose i686 glibc loader + libs
            export NIX_32BIT_GLIBC="${pkgs.pkgsi686Linux.glibc}"

            echo ""
            echo "  CTF Guy environment loaded"
            echo "  Python: $(python3 --version 2>&1 | cut -d' ' -f2)"
            echo "  Node:   $(node --version)"
            echo "  Root:   $CTF_ROOT"
            if command -v nvidia-smi &>/dev/null; then
              echo "  GPU:    $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'not detected')"
            fi
            echo ""

            # Bot: sync deps if bot/pyproject.toml exists
            if [ -f "$CTF_ROOT/bot/pyproject.toml" ]; then
              (cd "$CTF_ROOT/bot" && uv sync --extra dev --quiet 2>/dev/null)
            fi

            # CLI tools via npm (devcontainer, playwright-cli, codex)
            if ! command -v devcontainer &>/dev/null; then
              npm install -g @devcontainers/cli --prefix ~/.local --silent 2>/dev/null
            fi
            if ! command -v playwright-cli &>/dev/null; then
              npm install -g @playwright/cli --prefix ~/.local --silent 2>/dev/null
            fi
            if ! command -v codex &>/dev/null; then
              npm install -g @openai/codex --prefix ~/.local --silent 2>/dev/null
            fi
          '';
        };
      });
}

---
name: forensics-solver
description: Solve forensics CTF challenges. Handles PCAP analysis, disk images, memory dumps, file carving, audio forensics, and syscall interception. Invoke when a challenge involves forensic analysis.
user-invocable: false
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Forensics Solver

Systematic forensics analysis pipeline.

## Triage (always run first)

```bash
file challenge_file              # Identify file type
exiftool challenge_file          # Metadata (GPS, author, timestamps, comments)
binwalk challenge_file           # Embedded files, offsets
xxd challenge_file | head -20    # Raw hex dump header
```

## Network Forensics (PCAP)

```bash
# Overview
tshark -r capture.pcap -q -z io,stat,1
tshark -r capture.pcap -q -z conv,tcp    # Conversations
tshark -r capture.pcap -q -z http,tree   # HTTP breakdown

# Extract specific protocols
tshark -r capture.pcap -Y "http" -T fields -e http.request.uri -e http.file_data
tshark -r capture.pcap -Y "dns" -T fields -e dns.qry.name
tshark -r capture.pcap -Y "ftp-data" -T fields -e ftp-data.command

# Export objects
tshark -r capture.pcap --export-objects http,./extracted/
tshark -r capture.pcap --export-objects smb,./extracted/

# Follow TCP stream
tshark -r capture.pcap -q -z follow,tcp,ascii,0

# Extract RTP audio (KernelCon pattern)
tshark -r capture.pcap -Y "rtp" -T fields -e rtp.payload > rtp_payload.hex
```

### Audio from PCAP (KernelCon recurring pattern)
```bash
# Extract RTP → raw audio → decode FSK/modem
tshark -r capture.pcap -Y rtp -T fields -e rtp.payload | \
  tr -d ':' | xxd -r -p > audio.raw
# Decode modem signal
minimodem -r 300 -f audio.raw    # 300 baud FSK
minimodem -r 1200 -f audio.raw   # 1200 baud
```

## File Carving

```bash
# Automated carving
binwalk -e challenge_file               # Extract embedded files
foremost -i challenge_file -o output/   # Carve by file signatures

# Manual extraction at offset
dd if=challenge_file bs=1 skip=OFFSET count=SIZE of=extracted_file
```

## Disk / Image Analysis

```bash
# Mount disk image
fdisk -l disk.img                  # Partition table
mount -o loop,ro disk.img /mnt/    # Mount read-only

# Filesystem analysis
fls -r disk.img                    # List files (including deleted)
icat disk.img INODE > recovered    # Recover file by inode
```

## Memory Forensics

```bash
# Volatility 3
vol3 -f memory.dmp windows.info
vol3 -f memory.dmp windows.pslist
vol3 -f memory.dmp windows.filescan
vol3 -f memory.dmp windows.cmdline
vol3 -f memory.dmp windows.hashdump
```

## Time-Based Challenges (KernelCon pattern)

```bash
# Bypass time checks with faketime
faketime '2024-01-01 12:00:00' ./binary
faketime '1999-12-31 23:59:59' wine ./binary.exe
```

## Syscall Interception (KernelCon pattern)

When a binary checks system info via syscalls (sysinfo, time, etc.):

```c
// Template: ptrace-based syscall interception
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/user.h>
#include <sys/syscall.h>

// Fork child (target binary), parent traces and modifies syscall returns
// Intercept specific syscall numbers, modify registers before return
```

This is a recurring KernelCon forensics pattern — binaries that check system state and need spoofed values.

## VM Configuration (KernelCon pattern)

```bash
# Edit VMware .vmx for CPUID brand string challenges
echo 'cpuid.brandstring = "REQUIRED_STRING"' >> vm.vmx
```

## Audio Analysis

```bash
# Spectrogram (hidden images/text in audio)
sox audio.wav -n spectrogram -o spectrogram.png

# Check for Morse code, DTMF tones, SSTV
# Audacity: open file, switch to spectrogram view
```

## Procedure

1. Triage: file type, metadata, binwalk scan
2. If PCAP → protocol analysis, extract streams, check for audio
3. If image with embedded data → binwalk extract, check stego tools
4. If binary with time checks → try faketime
5. If memory dump → volatility3 analysis
6. If audio → spectrogram, check for modem signals
7. Write `solve.py`/`solve.sh` and `flag.txt`

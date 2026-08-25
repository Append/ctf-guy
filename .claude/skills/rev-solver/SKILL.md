---
name: rev-solver
description: Solve reverse engineering CTF challenges. Handles ELF/PE binaries, stripped binaries, obfuscated code, and constraint-based solving. Invoke when a challenge involves binary analysis.
user-invocable: false
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Reverse Engineering Solver

Systematic binary analysis pipeline.

## Triage (always run first)

```bash
file binary                    # What is it?
strings -n 8 binary            # Quick flag/clue extraction
strings binary | grep -i kernel  # Direct flag search
checksec binary                # Security mitigations (NX, PIE, canary, RELRO)
readelf -h binary              # Architecture, entry point
readelf -S binary              # Sections
```

If `strings` gives the flag, you're done. Move on.

## Static Analysis

### radare2 (fast, CLI)
```bash
r2 -A binary                   # Auto-analyze
afl                            # List functions
pdf @ main                     # Disassemble main
iz                             # Strings in data sections
axt @ str.kernel               # Xrefs to flag-like strings
VV @ main                      # Visual graph mode
```

### Ghidra (deeper decompilation)
```bash
# Headless analysis
analyzeHeadless /tmp/ghidra_project project_name \
  -import binary \
  -postScript ExportDecompilation.java \
  -scriptPath /path/to/scripts
```

For interactive analysis, note the decompiler output patterns:
- Look for comparison loops (flag checking byte-by-byte)
- XOR operations on arrays (encrypted flag)
- Anti-debugging checks (ptrace, IsDebuggerPresent)

## Dynamic Analysis

### GDB + GEF
```bash
gdb ./binary
# In GEF:
info functions           # List functions
b *main                  # Break at main
r                        # Run
ni / si                  # Step over / into
x/20s $rsp               # Examine stack strings
```

### Common patterns
- **Flag comparison**: Set breakpoint at `strcmp`/`memcmp`, read arguments
- **Decryption in memory**: Break after decryption loop, dump memory
- **Anti-debug**: Patch `ptrace` calls or use `LD_PRELOAD`

## Constraint Solving (z3)

When the binary checks flag character-by-character with complex conditions:

```python
from z3 import *

flag = [BitVec(f'f{i}', 8) for i in range(FLAG_LEN)]
s = Solver()

# Add printable ASCII constraints
for c in flag:
    s.add(c >= 0x20, c <= 0x7e)

# Add constraints from binary analysis
# s.add(flag[0] == ord('k'))  # kernel{ prefix
# s.add(flag[0] + flag[1] == 0xXX)  # from decompilation

if s.check() == sat:
    m = s.model()
    print(''.join(chr(m[c].as_long()) for c in flag))
```

## Pwn (if binary has remote service)

```python
from pwn import *

elf = ELF('./binary')
# p = process('./binary')      # Local
# p = remote('host', port)     # Remote

# Common attacks:
# Buffer overflow → ret2win, ROP chain, ret2libc
# Format string → read/write arbitrary memory
# Heap exploitation → tcache poisoning, use-after-free
```

## KernelCon Patterns

- **Themed binaries**: Challenge names reference the yearly theme (Terminator, Jurassic Park)
- **Multi-architecture**: "Agnostic" challenge had cross-arch analysis
- **Terminal programs**: Custom terminal apps that need interaction analysis
- **Progressive difficulty**: 50pt = `strings`, 500pt = multi-stage with anti-debug

## Procedure

1. Run triage commands (file, strings, checksec)
2. If strings reveals flag → done
3. Static analysis with r2: find main, trace logic, identify flag check
4. If flag is computed at runtime → dynamic analysis with gdb
5. If complex constraints → extract conditions and use z3
6. If remote service (pwn) → identify vulnerability, build exploit with pwntools
7. Write `solve.py` and `flag.txt`

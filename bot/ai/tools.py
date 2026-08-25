#!/usr/bin/env python3
"""Tool definitions for the AI solver (OpenAI function calling format)."""

SOLVER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "Execute a Python 3 script. Has access to pwntools, pycryptodome, z3-solver, sympy, gmpy2, scapy, pillow, numpy, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "Max execution time in seconds (default 30, max 120)",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command. Has access to strings, file, xxd, tshark, r2, binwalk, exiftool, steghide, zsteg, nmap, curl, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "Max execution time in seconds (default 30, max 120)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the challenge directory. Returns text content or base64 for binary files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the challenge directory",
                    },
                    "encoding": {
                        "type": "string",
                        "enum": ["utf-8", "base64"],
                        "description": "How to return file contents (default utf-8)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file in the challenge directory (solve.py, flag.txt, notes, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the challenge directory",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the challenge directory with file type info",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Subdirectory within challenge dir (default: current dir)",
                    },
                },
            },
        },
    },
]

#!/usr/bin/env python3
"""diligence_server.py — local one-click Deep Diligence API for Dealflow Call Sheet.

Bind: 127.0.0.1:8765
Prefer: install_diligence_autostart.bat once (pythonw, no window, starts at logon).
Debug: start_diligence.bat (visible console).

  GET  /health              → {ok: true, port: N}
  POST /diligence           → JSON {case, headed?} → Capri brief (runs dig, writes cache)
  GET  /diligence/{case}    → cached brief from diligence_cache.json / diligence/*.json

CORS open for file:// and localhost Desktop HTML.
Long requests OK (2Captcha can take 2–5+ minutes).
"""
from __future__ import annotations

import json
import os
import socket
import sys
import traceback
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PREFERRED_PORT = 8765
HOST = '127.0.0.1'
LOG_PATH = os.path.join(HERE, 'diligence_server.log')
LOG_MAX_BYTES = 1_000_000  # truncate when larger than ~1MB

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
except ImportError as e:
    raise SystemExit(
        'FastAPI required: pip install fastapi uvicorn\n' + str(e)
    )

import diligence

app = FastAPI(title='Dealflow Diligence API', docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Set after bind decision
BOUND_PORT = PREFERRED_PORT


class DiligenceRequest(BaseModel):
    case: str = Field(..., min_length=1)
    headed: bool = False
    # False (default): cache/compose — lead-backed numbers in <1s.
    # True: force live county OR scrape (Refresh Diligence / CLI --live).
    live: bool = False


def _rotate_log_if_needed() -> None:
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            # Keep a short tail so recent context survives truncate.
            with open(LOG_PATH, 'rb') as f:
                f.seek(max(0, os.path.getsize(LOG_PATH) - 64_000))
                tail = f.read()
            with open(LOG_PATH, 'wb') as f:
                f.write(b'[log truncated]\n')
                f.write(tail)
                if not tail.endswith(b'\n'):
                    f.write(b'\n')
    except OSError:
        pass


def log(msg: str) -> None:
    """Write to diligence_server.log (and stdout when a console exists)."""
    _rotate_log_if_needed()
    line = msg.rstrip('\n')
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def _healthy_peer(port: int) -> bool:
    """True if something on port answers as our diligence /health."""
    url = f'http://{HOST}:{port}/health'
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            if resp.status != 200:
                return False
            raw = resp.read().decode('utf-8', errors='replace')
            data = json.loads(raw)
            return bool(isinstance(data, dict) and data.get('ok') is True)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return False


def choose_port() -> int:
    """Bind PREFERRED_PORT, or exit quietly if a healthy peer already owns it."""
    if _port_free(PREFERRED_PORT):
        return PREFERRED_PORT
    if _healthy_peer(PREFERRED_PORT):
        log(f'Port {PREFERRED_PORT} already has a healthy diligence server — exiting 0')
        sys.exit(0)
    log(f'Port {PREFERRED_PORT} in use by a non-diligence process — exiting 1')
    sys.exit(1)


@app.get('/health')
def health():
    # Report whether captcha.key is present — never the key itself.
    captcha_ok = False
    try:
        from captcha_solver import has_key
        captcha_ok = has_key()
    except Exception:
        captcha_ok = False
    return {'ok': True, 'port': BOUND_PORT, 'captcha_key': captcha_ok}


@app.post('/diligence')
def post_diligence(body: DiligenceRequest):
    case = (body.case or '').strip()
    if not case:
        raise HTTPException(status_code=400, detail='case required')
    log(f'=== POST /diligence case={case} headed={body.headed} live={body.live} ===')
    try:
        d = diligence.run(case, headed=bool(body.headed), force_live=bool(body.live))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        log(f'DILIGENCE ERROR: {e}')
        log(traceback.format_exc())
        # Never leave Call Sheet blank when the lead already has numbers — return lead-backed
        # compose even if live/OR path exploded.
        try:
            lead, county, src = diligence.find_lead(case)
            if lead:
                d = diligence.build_diligence(
                    lead, county, src, headed=False, seed=None, force_live=False,
                )
                d = diligence.apply_lead_backed_numbers(d, diligence._norm_lead(lead, county))
                d.setdefault('sources', {})['error'] = str(e)[:300]
                d.setdefault('sources', {})['degraded'] = True
                diligence.write_outputs(d)
                log(f'=== degraded lead-backed brief after error verdict={d.get("verdict")} ===')
                return d
        except Exception as e2:
            log(f'DEGRADED FALLBACK FAILED: {e2}')
        raise HTTPException(status_code=500, detail=str(e)[:500]) from e
    log(f'=== done verdict={d.get("verdict")} ===')
    return d


@app.get('/diligence/{case}')
def get_diligence(case: str):
    d = _cached_brief(case)
    if not d:
        raise HTTPException(status_code=404, detail=f'no cached diligence for {case}')
    return d


def _cached_brief(case: str):
    case = (case or '').strip()
    if not case:
        return None
    cache = diligence._load_json(diligence.CACHE, {})
    if isinstance(cache, dict) and case in cache:
        return cache[case]
    path = os.path.join(diligence.OUT_DIR, f'{diligence.safe_case(case)}.json')
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding='utf-8'))
        except Exception:
            return None
    return None


def _ensure_stdio_for_pythonw() -> None:
    """pythonw sets stdout/stderr to None; uvicorn formatters call .isatty() and crash."""
    if sys.stdout is None:
        try:
            sys.stdout = open(os.devnull, 'w', encoding='utf-8')
        except OSError:
            sys.stdout = open(LOG_PATH, 'a', encoding='utf-8')
    if sys.stderr is None:
        try:
            sys.stderr = open(LOG_PATH, 'a', encoding='utf-8')
        except OSError:
            sys.stderr = open(os.devnull, 'w', encoding='utf-8')


def main():
    global BOUND_PORT
    import logging
    import uvicorn

    _ensure_stdio_for_pythonw()

    BOUND_PORT = choose_port()
    os.environ['DILIGENCE_PORT'] = str(BOUND_PORT)

    log(f'Diligence API starting on http://{HOST}:{BOUND_PORT}')
    log(f'  health:  GET  http://{HOST}:{BOUND_PORT}/health')
    log(f'  dig:     POST http://{HOST}:{BOUND_PORT}/diligence  {{"case":"…"}}')

    # Route uvicorn/access logs into the same file (pythonw has no console).
    file_handler = logging.FileHandler(LOG_PATH, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access'):
        lg = logging.getLogger(name)
        lg.addHandler(file_handler)
        lg.setLevel(logging.INFO)

    # Avoid uvicorn's DefaultFormatter (use_colors / isatty crash under pythonw).
    log_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'default': {'format': '%(levelname)s:%(name)s:%(message)s'},
            'access': {'format': '%(levelname)s:%(name)s:%(message)s'},
        },
        'handlers': {
            'default': {
                'class': 'logging.FileHandler',
                'filename': LOG_PATH,
                'encoding': 'utf-8',
                'formatter': 'default',
            },
            'access': {
                'class': 'logging.FileHandler',
                'filename': LOG_PATH,
                'encoding': 'utf-8',
                'formatter': 'access',
            },
        },
        'loggers': {
            'uvicorn': {'handlers': ['default'], 'level': 'INFO', 'propagate': False},
            'uvicorn.error': {'handlers': ['default'], 'level': 'INFO', 'propagate': False},
            'uvicorn.access': {'handlers': ['access'], 'level': 'INFO', 'propagate': False},
        },
    }

    # timeout_keep_alive high so 2Captcha polls (5–8 min) don't drop the socket idle
    uvicorn.run(
        app,
        host=HOST,
        port=BOUND_PORT,
        log_level='info',
        timeout_keep_alive=600,
        log_config=log_config,
    )


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log('FATAL startup error:')
        log(traceback.format_exc())
        sys.exit(1)

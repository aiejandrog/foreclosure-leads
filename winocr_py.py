#!/usr/bin/env python3
"""winocr_py.py -- Windows' built-in OCR called straight from Python. Free, local, no PowerShell.

WHY (found 2026-11-16 while reading Palm Beach recorded judgments). The project's free OCR bridge
(fl_lp/broward_pin.ocr_images -> fl_lp/winocr.ps1) starts Windows PowerShell 5.1, and on the engine
laptop 5.1 cannot start at all:

    The shell cannot be started. A failure occurred during initialization:
    The type initializer for 'System.Net.ServicePointManager' threw an exception.

So every scanned page anywhere in the pipeline came back `needs_ocr` and no document that is a scan
was ever read on this machine. PowerShell 7 cannot stand in (it has no WinRT projection). This
module calls the same engine, Windows.Media.Ocr, through the PyWinRT bindings.

    ocr_images([png, ...]) -> {png: text or None}

Raises Unavailable when the bindings or an OCR language are missing; a page that fails alone is
None in the result, never an empty string pretending to be a blank page.
"""
import asyncio
import os

MAX_DIM = 10000        # OcrEngine.MaxImageDimension is 10000 px on a side


class Unavailable(RuntimeError):
    pass


def _engine():
    try:
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.globalization import Language
    except Exception as exc:                       # ImportError, or a broken binding
        raise Unavailable('PyWinRT OCR bindings not importable: %s' % str(exc)[:160])
    eng = None
    try:
        eng = OcrEngine.try_create_from_language(Language('en-US'))
    except Exception:
        eng = None
    if eng is None:
        try:
            eng = OcrEngine.try_create_from_user_profile_languages()
        except Exception as exc:
            raise Unavailable('OCR engine could not start: %s' % str(exc)[:160])
    if eng is None:
        raise Unavailable('no OCR language pack is installed for this user')
    return eng


async def _one(engine, path):
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage import StorageFile, FileAccessMode
    f = await StorageFile.get_file_from_path_async(os.path.abspath(path))
    stream = await f.open_async(FileAccessMode.READ)
    try:
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        return '\n'.join(line.text for line in result.lines)
    finally:
        try:
            stream.close()
        except Exception:
            pass


async def _all(engine, paths):
    out = {}
    for p in paths:
        try:
            out[p] = await _one(engine, p)
        except Exception:
            out[p] = None
    return out


def ocr_images(paths, timeout=600):
    """-> {path: text or None}. Same shape as fl_lp.broward_pin.ocr_images."""
    paths = list(paths or [])
    if not paths:
        return {}
    if os.name != 'nt':
        raise Unavailable('Windows OCR is only available on Windows')
    engine = _engine()
    try:
        return asyncio.run(asyncio.wait_for(_all(engine, paths), timeout))
    except asyncio.TimeoutError:
        raise Unavailable('OCR timed out after %ss' % timeout)


def available():
    try:
        _engine()
        return True
    except Unavailable:
        return False

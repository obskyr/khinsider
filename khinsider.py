#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download full soundtracks from KHInsider.

This script downloads entire music albums from KHInsider given an album ID
(found in the website's URL) or a full album URL. Multiple audio formats, 
optional album image download, optional parallel downloads, dry-run
listing, and includes error handling.

Requires Python 3.9 or newer.

Architecture:
    Layers:

        1. Domain Entities      pure-data, frozen dataclasses (no I/O).
                                MediaFile, Track, Album, AlbumSummary,
                                SearchResults.
        2. Infrastructure       all HTTP and HTML-parsing knowledge.
                                KHInsiderClient turns IDs/URLs into
                                domain entities.
        3. Application          UI-agnostic file mechanics.
                                DownloadManager streams to disk
                                atomically and retries transient
                                failures, calling a DownloadObserver
                                at every state transition.
        4. Presentation         the CLI. CLIProgressObserver and
                                _OneLineObserver implement the observer
                                contract; DownloadOrchestrator wires an
                                Album through DownloadManager. main()
                                is the composition root.

Usage:
    python khinsider.py <soundtrack_id_or_url> [options]

Common options:
  -o, --output     Output directory (default: sanitized album name)
  -f, --format     Preferred formats, comma-separated (e.g. 'flac,mp3')
  -i, --images     Download album images (default: enabled; --no-images to
                   disable)
  -v, --verbose    Show detailed progress (default: enabled; --no-verbose to
                   disable)
  -s, --search     Search for soundtracks instead of downloading. Also used
                   automatically if the supplied ID does not exist.
  -t, --threads    Concurrent file downloads (default: 1, max: 8)
  -d, --delay      Seconds to wait between sequential downloads (default: 0)
      --timeout    HTTP timeout in seconds (default: 30)
      --force      Re-download files that already exist on disk
      --list-only  Print what would be downloaded and exit
      --version    Show program version and exit.

Environment variables:
  KHI_NO_AUTO_INSTALL          Set to '1' to skip automatic dependency install.
  KHI_AUTO_INSTALL_ATTEMPTED   Internal guard set before re-exec; prevents
                               infinite re-install loops if pip "succeeds"
                               but the package is still not importable.

Examples:
    Download Aquaplus Vocal Collection Vol. 4 in FLAC:
        python khinsider.py --format flac "aquaplus-vocal-collection-vol.4"
    Download Minecraft OST in FLAC or MP3, plus images, with 2 threads:
        python khinsider.py -f flac,mp3 -t 2 \\
            https://downloads.khinsider.com/game-soundtracks/album/minecraft

Report issues: https://github.com/obskyr/khinsider/issues
"""

from __future__ import annotations

import argparse
import enum
import errno
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Callable,
    Iterable,
    List,
    Optional,
    Protocol,
    Sequence,
    TYPE_CHECKING,
    Tuple,
)
from urllib.parse import unquote, urljoin, urlsplit

if TYPE_CHECKING:
    import requests
    from bs4 import BeautifulSoup, Tag
    from curl_cffi import requests as _curl_requests
    from curl_cffi.requests import exceptions as _curl_exceptions

__version__ = "2.0.0"

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Dependency bootstrap (Set KHI_NO_AUTO_INSTALL=1 to disable auto-install.)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

_REQUIRED_PKGS = {
    "requests": "requests>=2.0,<3.0",
    "bs4": "beautifulsoup4>=4.4,<5.0",
    "curl_cffi": "curl_cffi>=0.7,<1.0",
}

_HELP_VERSION_TOKENS = frozenset({"-h", "--help", "--version"})


def _wants_help_or_version(argv: List[str]) -> bool:
    """Return True if the argv vector is just asking for help or version."""
    return any(arg in _HELP_VERSION_TOKENS for arg in argv[1:])


def _in_venv() -> bool:
    """Return True if running inside a virtual environment."""
    base = getattr(sys, "base_prefix", sys.prefix)
    return sys.prefix != base or hasattr(sys, "real_prefix")


def _externally_managed_message() -> str:
    """Return user-facing guidance for PEP 668 environments."""
    return (
        "Your Python installation is marked EXTERNALLY-MANAGED (PEP 668). "
        "Create and activate a virtual environment, then re-run:\n"
        f"  {sys.executable} -m venv .venv\n"
        "  source .venv/bin/activate   # Linux/macOS\n"
        "  .venv\\Scripts\\activate     # Windows\n"
        f"  {sys.executable} -m pip install requests beautifulsoup4"
    )


def _run_install_subprocess(cmd: List[str], env: dict) -> None:
    """Run an install subprocess, surfacing pip output on failure.

    Raises:
        RuntimeError: If the subprocess returns a non-zero exit code. The
            captured stderr/stdout is included in the exception message so
            users can diagnose proxy/permission/SSL/PEP-668 issues.
    """
    import subprocess
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(
            f"Failed to launch installer ({exc}). Install manually:\n  "
            + " ".join(cmd)
        ) from exc

    if result.returncode == 0:
        return

    detail = (result.stderr or result.stdout or "").strip()
    if detail:
        tail = "\n".join(detail.splitlines()[-40:])
        print(tail, file=sys.stderr)

    if "externally-managed-environment" in detail.lower():
        print("\n" + _externally_managed_message(), file=sys.stderr)

    raise RuntimeError(
        "Automatic dependency installation failed. Install manually:\n  "
        + " ".join(cmd)
    )


def _ensure_pip_available(verbose: bool = False) -> None:
    """Ensure 'pip' is importable for 'python -m pip' invocations.

    Raises:
        RuntimeError: If pip cannot be bootstrapped.
    """
    import importlib.util
    if importlib.util.find_spec("pip") is not None:
        return

    if importlib.util.find_spec("ensurepip") is None:
        raise RuntimeError(
            "pip is not available and the standard library 'ensurepip' module "
            "could not be imported to bootstrap it."
        )

    if verbose:
        print("Bootstrapping pip via ensurepip...", file=sys.stderr)
    _run_install_subprocess(
        [sys.executable, "-m", "ensurepip", "--upgrade"], os.environ.copy()
    )


def _install_with_pip(specs: List[str], verbose: bool = True) -> None:
    """Install the given requirement specs using pip.

    Uses ``--user`` when not in a virtualenv to avoid requiring admin rights.

    Raises:
        RuntimeError: If pip exits non-zero.
    """
    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")

    cmd = [sys.executable, "-m", "pip", "install"]
    if not _in_venv():
        cmd.append("--user")
    cmd.extend(specs)

    if verbose:
        print(
            "Installing required packages: " + ", ".join(specs),
            file=sys.stderr,
        )

    _run_install_subprocess(cmd, env)


def _print_manual_install_hint(missing: List[str], header: str) -> None:
    """Print a friendly 'install manually' hint to stderr."""
    specs = " ".join(_REQUIRED_PKGS[name] for name in missing)
    print(
        f"{header}\n"
        f"  {sys.executable} -m pip install {specs}",
        file=sys.stderr,
    )


def _ensure_runtime_dependencies(verbose: bool = True) -> None:
    """Ensure third-party dependencies exist; auto-install if missing.

    Behavior:
      * If all packages are importable, returns immediately.
      * If ``KHI_NO_AUTO_INSTALL=1``, prints a friendly install hint and
        exits with code 1 (replaces the previous silent fall-through that
        produced a bare ``ModuleNotFoundError`` traceback).
      * If ``KHI_AUTO_INSTALL_ATTEMPTED=1`` is already set, the previous
        re-exec installed but the package is still missing. Refuse to loop
        and surface a clear diagnostic.
      * Otherwise, attempt ``python -m pip install``, then re-exec the
        script with the guard variable set so any subsequent miss aborts
        loudly instead of looping.

    Raises:
        SystemExit: When manual intervention is required.
    """
    import importlib.util
    import subprocess
    missing = [
        name
        for name in _REQUIRED_PKGS
        if importlib.util.find_spec(name) is None
    ]
    if not missing:
        return

    if os.environ.get("KHI_NO_AUTO_INSTALL") == "1":
        _print_manual_install_hint(
            missing,
            f"Missing required packages ({', '.join(missing)}) and "
            "KHI_NO_AUTO_INSTALL=1. Install manually with:",
        )
        sys.exit(1)

    if os.environ.get("KHI_AUTO_INSTALL_ATTEMPTED") == "1":
        _print_manual_install_hint(
            missing,
            f"Auto-install of {', '.join(missing)} appeared to succeed, but "
            "the package is still not importable. This usually means pip "
            "installed it to a location not on sys.path (wrong Python, "
            "user-site disabled, or a venv mismatch). Install manually with:",
        )
        sys.exit(1)

    try:
        _ensure_pip_available(verbose=verbose)
        _install_with_pip(
            [_REQUIRED_PKGS[name] for name in missing], verbose=verbose
        )
    except RuntimeError as exc:
        print(f"\n{exc}", file=sys.stderr)
        sys.exit(1)

    if verbose:
        print(
            "Dependencies installed. Restarting script to apply changes...",
            file=sys.stderr,
        )

    env = os.environ.copy()
    env["KHI_AUTO_INSTALL_ATTEMPTED"] = "1"
    if sys.platform == "win32":
        result = subprocess.run(
            [sys.executable, *sys.argv], env=env, check=False
        )
        sys.exit(result.returncode)
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


if __name__ == "__main__" and not _wants_help_or_version(sys.argv):
    _ensure_runtime_dependencies(verbose="--no-verbose" not in sys.argv)

if not (__name__ == "__main__" and _wants_help_or_version(sys.argv)):  # pylint: disable=wrong-import-position
    try:
        import requests
        from bs4 import BeautifulSoup, Tag
        from curl_cffi import requests as _curl_requests
        from curl_cffi.requests import exceptions as _curl_exceptions
    except ModuleNotFoundError:
        raise

    _NETWORK_EXCEPTIONS = (
        requests.RequestException,
        _curl_exceptions.RequestException,
    )
    _HTTP_EXCEPTIONS = (
        requests.HTTPError,
        _curl_exceptions.HTTPError,
    )


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Constants
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

BASE_URL = "https://downloads.khinsider.com/"
REPORT_URL = "https://github.com/obskyr/khinsider/issues"

MAX_RETRIES = 3
CHUNK_SIZE = 64 * 1024  # 64 KiB
DEFAULT_TIMEOUT = 30.0  # seconds
DEFAULT_THREADS = 1
MAX_THREADS = 8
PREFETCH_WORKERS = 8
SEARCH_PREFETCH_WORKERS = 8

_PROGRESS_BAR_WIDTH = 28
_PROGRESS_BAR_RESERVE = 28  # space for byte counts, percent, speed, ETA
_PROGRESS_REFRESH_HZ = 20  # max frames per second
_DISPLAY_NAME_WIDTH = 50

_KNOWN_AUDIO_FORMATS = frozenset(
    {"mp3", "flac", "ogg", "m4a", "aac", "wav", "opus"}
)

_DEFAULT_FORMAT_PRIORITY: Tuple[str, ...] = (
    "flac", "wav", "m4a", "ogg", "opus", "mp3", "aac",
)

_TRANSIENT_OSERROR_ERRNOS = frozenset(
    n for n in (
        getattr(errno, "EAGAIN", None),
        getattr(errno, "EWOULDBLOCK", None),
        getattr(errno, "ETIMEDOUT", None),
        getattr(errno, "ECONNRESET", None),
        getattr(errno, "ECONNABORTED", None),
        getattr(errno, "EPIPE", None),
        getattr(errno, "EINTR", None),
        getattr(errno, "EBUSY", None),
    )
    if n is not None
)

_RETRYABLE_4XX_STATUSES = frozenset({408, 429})

_PRE_TD_RE = re.compile(rb"^</td>\s*$", flags=re.MULTILINE)

_INVALID_ENTITY_RE = re.compile(rb"&#([^0-9x]|x[^0-9A-Fa-f])")

_FILENAME_INVALID_RE = re.compile(r'[<>:"/\\|?*]')

_ALBUM_URL_RE = re.compile(
    r"^https?://(?:www\.)?downloads\.khinsider\.com/"
    r"game-soundtracks/album/([^/?#]+)/?(?:[?#].*)?$",
    flags=re.IGNORECASE,
)

_TRACK_FILE_HREF_RE = re.compile(r"/(?:soundtracks|ost)/")


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Exceptions
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


class ScriptError(Exception):
    """Base exception for script-specific errors."""


class NetworkError(ScriptError):
    """Raised when network operations fail after multiple attempts."""


class InvalidSoundtrackError(ScriptError):
    """Raised when the requested soundtrack doesn't exist or is unavailable."""


class InvalidFormatError(ScriptError):
    """Raised when none of the requested audio formats are available."""


class SearchError(ScriptError):
    """Raised when a search operation cannot return results."""


class IncompleteDownloadError(ScriptError):
    """Raised when a downloaded file's byte count does not match its header.

    Treated as a transient failure by :class:`DownloadManager`, which
    retries with backoff.
    """


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# URL / filename utilities
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _extract_id_and_url(value: str) -> Tuple[str, Optional[str]]:
    """Return (album_id, full_url_if_given_or_None) from user input.

    The full URL is preserved when the user passes one verbatim so callers
    can reuse it (avoiding a guessed reconstruction that may differ from the
    canonical URL).
    """
    value = value.strip()
    match = _ALBUM_URL_RE.match(value)
    if match:
        return match.group(1), value
    return value, None


def extract_soundtrack_id(candidate: str) -> str:
    """Return a soundtrack ID from a user-supplied ID or album URL.

    Args:
        candidate: ID or album URL.

    Returns:
        The soundtrack ID.
    """
    return _extract_id_and_url(candidate)[0]


_RESERVED_FS_NAMES = (
    {"", ".", "..", "~", "CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def sanitize_filename(name: str) -> str:
    """Convert a string to a safe filesystem filename.

    Invalid characters are replaced with hyphens, trailing spaces or dots are
    removed, and Windows reserved names (including reserved stems with an
    extension, e.g. ``CON.txt``) are suffixed with an underscore.

    Args:
        name: Original filename to sanitize.

    Returns:
        Sanitized filename.

    Examples:
        >>> sanitize_filename('A/B?C*.txt')
        'A-B-C-.txt'
        >>> sanitize_filename('CON.txt')
        'CON.txt_'
    """
    sanitized = _FILENAME_INVALID_RE.sub("-", name).rstrip(" .")
    stem = sanitized.split(".", 1)[0]
    if sanitized.upper() in _RESERVED_FS_NAMES or stem.upper() in _RESERVED_FS_NAMES:
        return f"{sanitized}_"
    return sanitized


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# HTML utilities
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _clean_html_bytes(data: bytes) -> bytes:
    """Apply KHInsider-specific HTML repairs before parsing.

    Strips stray standalone ``</td>`` lines and escapes malformed numeric
    character references (e.g., ``&#foo`` or ``&#xZZ``) that would otherwise
    confuse :mod:`html.parser`.

    Args:
        data: Raw HTML bytes.

    Returns:
        Cleaned HTML bytes.
    """
    data = _PRE_TD_RE.sub(b"", data)
    data = _INVALID_ENTITY_RE.sub(b"&amp;#\\1", data)
    return data


def _soup_from_bytes(data: bytes) -> "BeautifulSoup":
    """Parse HTML bytes into BeautifulSoup after KHInsider-specific fixes."""
    return BeautifulSoup(_clean_html_bytes(data), "html.parser")


def _get_soup(
    url: str,
    session: "_curl_requests.Session",
    timeout: float = DEFAULT_TIMEOUT,
) -> "BeautifulSoup":
    """Fetch and parse HTML content from a URL using a persistent session.

    Args:
        url: The URL to fetch.
        session: Session object for HTTP requests.
        timeout: Per-request timeout in seconds.

    Returns:
        Parsed BeautifulSoup object.

    Raises:
        NetworkError: If the request fails or returns a bad status. The
            application-level retry in :class:`DownloadManager` wraps callers
            that drive bulk fetches, so a single ``NetworkError`` here is
            surfaced only after that loop has given up.
    """
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
    except _NETWORK_EXCEPTIONS as exc:
        raise NetworkError(f"Failed to fetch {url}: {exc}") from exc
    return _soup_from_bytes(response.content)


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Format / size utilities
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _human_size(n: int) -> str:
    """Return a human-readable size for bytes (binary units)."""
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    size = float(n)
    for unit in units[:-1]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} {units[-1]}"


def _fmt_seconds(value: float) -> str:
    """Return whole seconds with an 's' suffix (e.g., '62s')."""
    import math
    return f"{max(0, math.ceil(value))}s"


def _truncate_display_name(name: str) -> str:
    """Return ``name`` truncated to fit the per-file display column."""
    if len(name) > _DISPLAY_NAME_WIDTH:
        return name[: _DISPLAY_NAME_WIDTH - 1] + "…"
    return name


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Progress bar (presentation primitive shared by the CLI observers)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


class _ProgressBar:
    """Simple progress bar with speed and ETA.

    Renders to ``sys.stdout`` on TTYs using a single in-place line and
    throttles updates to ~20 FPS. When the total size is unknown, shows a
    spinner with bytes transferred, transfer speed, and elapsed time. When the
    total is known, shows a filled bar, bytes done/total, percent, speed,
    and ETA in seconds.

    The instance is callable: call it with the number of newly written bytes
    to update the bar. Call :meth:`close` once per file to finalize output.
    """

    def __init__(
        self,
        prefix: str,
        total: Optional[int],
        width: int = _PROGRESS_BAR_WIDTH,
    ):
        """Initialize a new progress bar.

        Adjusts the prefix to fit the current terminal width, reserving space
        for the bar and metrics. Rendering is automatically disabled when
        ``sys.stdout`` is not a TTY.
        """
        self.prefix = prefix
        self.total = total
        self.width = max(10, width)
        self.n = 0
        self._start = time.monotonic()
        self._last_print = 0.0
        self._tty = sys.stdout.isatty()
        self._last_len = 0
        self._closed = False
        self._cols = self._terminal_cols()

        reserve = _PROGRESS_BAR_RESERVE + self.width
        if len(prefix) + 1 + reserve > self._cols:
            trim_to = max(8, self._cols - reserve - 1)
            if len(prefix) > trim_to:
                self.prefix = prefix[: trim_to - 1] + "…"

    @staticmethod
    def _terminal_cols() -> int:
        """Return the current terminal width, falling back to 80."""
        import shutil
        try:
            return shutil.get_terminal_size(fallback=(80, 20)).columns
        except OSError:
            return 80

    def __call__(self, delta: int, total_hint: Optional[int] = None) -> None:
        """Advance the bar by ``delta`` bytes and render if appropriate.

        Updates the internal byte count and triggers a redraw at most ~20
        times per second (or always on completion). Negative ``delta`` values
        are clamped to zero. If a ``total_hint`` is provided and the bar was
        created with an unknown total, the hint is adopted.

        Rendering is skipped when stdout is not a TTY, but counters are still
        updated so the final render reflects the true byte count.
        """
        if total_hint and self.total is None:
            self.total = total_hint
        self.n += max(0, int(delta))

        if not self._tty or self._closed:
            return

        now = time.monotonic()
        if self.total is not None and self.n >= self.total:
            self._render()
            return
        if now - self._last_print >= 1.0 / _PROGRESS_REFRESH_HZ:
            self._last_print = now
            self._render()

    def close(self) -> None:
        """Finalize the bar and end the line.

        Idempotent: subsequent calls are no-ops.
        """
        if self._closed:
            return
        self._closed = True
        if self._tty:
            self._render()
            print("")
            self._last_len = 0

    def _render(self) -> None:
        """Render the current bar state to stdout."""
        cols = self._cols
        elapsed = max(1e-9, time.monotonic() - self._start)
        speed = self.n / elapsed
        speed_s = f"{_human_size(int(speed))}/s"

        if self.total and self.total > 0:
            pct = min(1.0, self.n / self.total)
            filled = int(self.width * pct)
            head = ">" if filled < self.width else ""
            bar = (
                "["
                + "=" * filled
                + head
                + "." * (self.width - filled - len(head))
                + "]"
            )
            done_s = _human_size(self.n)
            total_s = _human_size(self.total)
            remain = (self.total - self.n) / speed if speed > 0 else 0.0
            eta_str = _fmt_seconds(remain)
            line = (
                f"{self.prefix} {bar} {done_s}/{total_s} "
                f"({int(pct * 100):3d}%) {speed_s} ETA {eta_str}"
            )
        else:
            spin = "|/-\\"
            idx = int(time.monotonic() * 10) % len(spin)
            spaces = " " * (self.width - 1)
            bar = f"[{spin[idx]}{spaces}]"
            done_s = _human_size(self.n)
            elapsed_str = _fmt_seconds(elapsed)
            line = f"{self.prefix} {bar} {done_s} {speed_s} +{elapsed_str}"

        out = line[: max(0, cols - 1)]
        pad = " " * max(0, self._last_len - len(out))
        try:
            print("\r" + out + pad, end="", flush=True)
        except BrokenPipeError:
            self._tty = False
        self._last_len = len(out)


# ==============================================================================
# Layer 1 - Domain Entities
# ==============================================================================


@dataclass(frozen=True)
class MediaFile:
    """A single downloadable file (audio track or album image).

    Attributes:
        filename: The decoded basename of the URL path. This is the name
            the file should be saved under on disk.
        url: Absolute URL to download the bytes from.
        extension: Lowercase file extension, no leading dot
            (e.g. ``"flac"``). Empty string when the URL has no extension.
    """

    filename: str
    url: str
    extension: str

    @classmethod
    def from_url(cls, url: str) -> "MediaFile":
        """Build a :class:`MediaFile` by deriving the filename from the URL.

        The filename is parsed out of the URL path explicitly (rather than
        via :class:`pathlib.Path`, which only accidentally works on Windows
        because ``/`` is a path separator there).
        """
        path = urlsplit(url).path
        filename = unquote(path.rsplit("/", 1)[-1])
        suffix = filename.rsplit(".", 1)
        extension = suffix[-1].lower() if len(suffix) == 2 else ""
        return cls(filename=filename, url=url, extension=extension)


@dataclass(frozen=True)
class Track:
    """A single album track and the formats it is available in.

    Attributes:
        name: Display name extracted from the album's songlist row. Used
            for human-facing labels; the on-disk filename comes from the
            chosen :class:`MediaFile`.
        files: All downloadable formats, in source order.
    """

    name: str
    files: Tuple[MediaFile, ...]

    def best_file(self, preferences: Sequence[str]) -> MediaFile:
        """Return the highest-priority available file.

        ``preferences`` should be lowercased by the caller. When empty,
        falls back to :data:`_DEFAULT_FORMAT_PRIORITY` (lossless first,
        then lossy by approximate fidelity), so "auto" actually means
        "best available" rather than "first in HTML order". When none of
        the preferred extensions are present, falls back to ``files[0]``
        — matching the legacy "take whatever is offered first" behavior.

        Args:
            preferences: Preferred file extensions, lowercased, in
                priority order.

        Returns:
            The selected :class:`MediaFile`.

        Raises:
            InvalidSoundtrackError: If the track has no downloadable
                files at all (i.e., metadata fetch returned nothing).
        """
        if not self.files:
            raise InvalidSoundtrackError(
                f"Track {self.name!r} has no downloadable files."
            )
        effective = list(preferences) if preferences else list(
            _DEFAULT_FORMAT_PRIORITY
        )
        for fmt in effective:
            for media in self.files:
                if media.extension == fmt:
                    return media
        return self.files[0]


@dataclass(frozen=True)
class Album:
    """A fully-resolved KHInsider album.

    All fields are populated up-front by :meth:`KHInsiderClient.get_album`,
    so an :class:`Album` represents a known-good snapshot of the album
    page (and every track-detail page it links to) at fetch time.

    Attributes:
        id: Album identifier (the URL slug).
        name: Display title, already sanitized for filesystem use.
        url: Canonical album URL.
        formats: Available audio formats, in source order, intersected
            with :data:`_KNOWN_AUDIO_FORMATS` so unrelated columns
            (``length``, ``artist``) don't appear.
        tracks: Tracks in source order. A track may have ``files=()`` if
            its detail page failed to load during prefetch.
        images: Album images (cover art, etc.) in source order.
    """

    id: str
    name: str
    url: str
    formats: Tuple[str, ...]
    tracks: Tuple[Track, ...]
    images: Tuple[MediaFile, ...]


@dataclass(frozen=True)
class _AlbumMetadata:
    """Intermediate result of :meth:`KHInsiderClient.fetch_album_metadata`.

    Carries an :class:`Album` whose tracks have empty ``files`` plus the
    parallel track-detail link list that :meth:`prefetch_track_files`
    needs to fill them in. Internal to the client/CLI handshake.

    Attributes:
        album: A partial :class:`Album` with skeleton tracks.
        track_links: ``(display_name, detail_url)`` per track, in the
            same order as ``album.tracks``.
    """

    album: Album
    track_links: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class AlbumSummary:
    """A lightweight album reference, e.g. one row in search results.

    Use :meth:`KHInsiderClient.resolve_summary_names` to fill in any
    missing names without paying for a full album fetch.

    Attributes:
        id: Album identifier.
        name: Display title, sanitized. Empty string when the populating
            call did not (or could not) determine the name.
        url: Canonical album URL.
    """

    id: str
    name: str
    url: str


@dataclass(frozen=True)
class SearchResults:
    """The two result tables a KHInsider search may return.

    Attributes:
        albums: Matches by album title.
        songs: Matches by song title (each row still points at an album).
    """

    albums: Tuple[AlbumSummary, ...]
    songs: Tuple[AlbumSummary, ...]


# ==============================================================================
# Layer 2 - Infrastructure: KHInsiderClient
# ==============================================================================


class KHInsiderClient:
    """Reads KHInsider's HTML and produces immutable domain entities.

    All ``requests`` and ``BeautifulSoup`` knowledge lives here. The rest
    of the application talks only to the entities returned by this class.

    Attributes:
        session: The HTTP session used for every fetch. Caller-owned.
        timeout: Per-request timeout in seconds.
        prefetch_workers: Maximum parallel song-page fetches inside
            :meth:`get_album`.
    """

    def __init__(
        self,
        session: "_curl_requests.Session",
        timeout: float = DEFAULT_TIMEOUT,
        prefetch_workers: int = PREFETCH_WORKERS,
    ):
        self.session = session
        self.timeout = timeout
        self.prefetch_workers = prefetch_workers

    # --- public API ---------------------------------------------------------

    def fetch_album_metadata(
        self, album_id_or_url: str
    ) -> "_AlbumMetadata":
        """Fetch and parse only the album page.

        The returned :class:`_AlbumMetadata` carries a partial
        :class:`Album` (with empty :attr:`Track.files`) plus the track-
        detail links needed to fill them in. Pair with
        :meth:`prefetch_track_files` to get a fully-populated album.

        The two-phase API exists so the CLI can print its banner
        between the (fast) album-page fetch and the (slow) parallel
        track-detail prefetch — matching the legacy verbose UX.

        Args:
            album_id_or_url: An album ID slug or a full album URL.

        Returns:
            An :class:`_AlbumMetadata` carrying the partial album and
            the parallel track-detail link list.

        Raises:
            InvalidSoundtrackError: If the album page is missing or 404s.
            NetworkError: If the fetch fails after retries.
        """
        album_id, given_url = _extract_id_and_url(album_id_or_url)
        url = given_url or urljoin(
            BASE_URL, f"game-soundtracks/album/{album_id}"
        )
        soup = _get_soup(url, self.session, self.timeout)

        if soup.find(string="No such album"):
            raise InvalidSoundtrackError(f"Album not found: {album_id}")

        name = self._parse_album_name(soup, url)
        formats = self._parse_album_formats(soup)
        track_links = self._parse_track_links(soup, base_url=url)
        images = self._parse_album_images(soup, base_url=url)

        skeleton_tracks = tuple(
            Track(name=display_name, files=())
            for display_name, _ in track_links
        )
        album = Album(
            id=album_id,
            name=name,
            url=url,
            formats=tuple(formats),
            tracks=skeleton_tracks,
            images=tuple(images),
        )
        return _AlbumMetadata(album=album, track_links=tuple(track_links))

    def prefetch_track_files(
        self,
        metadata: "_AlbumMetadata",
        verbose: bool = False,
    ) -> Album:
        """Populate every track's ``files`` field via parallel fetches.

        Returns a new :class:`Album` (since :class:`Album` is frozen)
        with the track file lists filled in. Per-track failures are
        tolerated: the offending track keeps ``files=()`` and surfaces
        a clearer error if the user later tries to download it.

        Args:
            metadata: The album metadata returned by
                :meth:`fetch_album_metadata`.
            verbose: If True, print
                ``"Prefetching metadata for N tracks... Done."``
                (single-track albums skip this banner) and a stderr
                "Note:" line if any track-detail page failed.

        Returns:
            A new :class:`Album` with populated track files.
        """
        tracks = self._fetch_tracks(
            list(metadata.track_links), verbose=verbose
        )
        album = metadata.album
        return Album(
            id=album.id,
            name=album.name,
            url=album.url,
            formats=album.formats,
            tracks=tuple(tracks),
            images=album.images,
        )

    def get_album(
        self,
        album_id_or_url: str,
        verbose_prefetch: bool = False,
    ) -> Album:
        """Convenience: fetch metadata and prefetch all track files.

        Equivalent to :meth:`fetch_album_metadata` followed by
        :meth:`prefetch_track_files`. Intended for non-CLI callers that
        want a single fully-resolved :class:`Album`. The CLI uses the
        two-phase form so it can print its banner between them.

        Args:
            album_id_or_url: An album ID slug or a full album URL.
            verbose_prefetch: Forwarded to :meth:`prefetch_track_files`.

        Returns:
            A fully-populated :class:`Album`.

        Raises:
            InvalidSoundtrackError: If the album page is missing or 404s.
            NetworkError: If the initial fetch fails after retries.
        """
        metadata = self.fetch_album_metadata(album_id_or_url)
        return self.prefetch_track_files(
            metadata, verbose=verbose_prefetch
        )

    def search(self, term: str) -> SearchResults:
        """Search KHInsider for albums and songs matching a term.

        The search endpoint may redirect straight to an album page when
        there is an exact match; in that case a single-row
        ``SearchResults.albums`` is returned (with an empty name — call
        :meth:`resolve_summary_names` if you need it).

        Args:
            term: Free-text search term.

        Returns:
            :class:`SearchResults` populated with :class:`AlbumSummary`
            rows.

        Raises:
            NetworkError: If the request itself fails.
            SearchError: If the response is unparseable or empty.
        """
        try:
            resp = self.session.get(
                urljoin(BASE_URL, "search"),
                params={"search": term},
                timeout=self.timeout,
                allow_redirects=True,
            )
            resp.raise_for_status()
        except _NETWORK_EXCEPTIONS as exc:
            raise NetworkError(
                f"Failed to search for '{term}': {exc}"
            ) from exc

        path = urlsplit(resp.url).path
        if path.startswith("/game-soundtracks/album/"):
            ost_id = path.rstrip("/").rsplit("/", 1)[-1]
            return SearchResults(
                albums=(
                    AlbumSummary(id=ost_id, name="", url=resp.url),
                ),
                songs=(),
            )

        soup = _soup_from_bytes(resp.content)
        tables = soup.find_all("table", class_="albumList")
        if not tables:
            message_tag = soup.find("p")
            message = (
                message_tag.get_text(strip=True) if message_tag else ""
            )
            raise SearchError(message or "No results returned.")

        parsed = [self._parse_search_table(t) for t in tables]
        if len(parsed) == 1:
            text = ""
            page_section = soup.find(id="pageContent")
            if page_section is not None:
                first_p = page_section.find("p")
                if first_p is not None:
                    text = first_p.get_text(strip=True)
            if "song" in text.lower():
                return SearchResults(albums=(), songs=parsed[0])
            return SearchResults(albums=parsed[0], songs=())

        return SearchResults(albums=parsed[0], songs=parsed[1])

    def resolve_summary_names(
        self,
        summaries: Sequence[AlbumSummary],
    ) -> Tuple[AlbumSummary, ...]:
        """Concurrently fill in missing names on the given summaries.

        Each summary whose ``name`` is empty triggers a fetch of its
        album page to extract the title. Existing names are kept. Per-
        summary failures are swallowed (the row keeps an empty name and
        callers fall back to displaying the ID).

        Args:
            summaries: Search rows in any state.

        Returns:
            A new tuple of :class:`AlbumSummary` objects in input order.
        """
        resolved: List[AlbumSummary] = list(summaries)
        needs = [
            (i, s) for i, s in enumerate(resolved) if not s.name
        ]
        if not needs:
            return tuple(resolved)

        def _fetch_name(
            indexed: Tuple[int, AlbumSummary],
        ) -> Tuple[int, Optional[str]]:
            index, summary = indexed
            try:
                soup = _get_soup(summary.url, self.session, self.timeout)
                name_tag = soup.find("h2")
                if name_tag is None:
                    return index, None
                return index, sanitize_filename(
                    name_tag.get_text(strip=True)
                )
            except Exception:  # pylint: disable=broad-exception-caught
                return index, None

        from concurrent.futures import ThreadPoolExecutor
        workers = min(SEARCH_PREFETCH_WORKERS, len(needs))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for index, name in executor.map(_fetch_name, needs):
                if name:
                    summary = resolved[index]
                    resolved[index] = AlbumSummary(
                        id=summary.id,
                        name=name,
                        url=summary.url,
                    )
        return tuple(resolved)

    # --- private parsers ----------------------------------------------------

    @staticmethod
    def _parse_album_name(soup: "BeautifulSoup", url: str) -> str:
        """Return the sanitized album title from the album page soup."""
        name_tag = soup.find("h2")
        if name_tag is None:
            raise InvalidSoundtrackError(f"Album page invalid: {url}")
        return sanitize_filename(name_tag.get_text(strip=True))

    @staticmethod
    def _parse_album_formats(soup: "BeautifulSoup") -> List[str]:
        """Return the audio formats present in the songlist column headers.

        Column headers are intersected with :data:`_KNOWN_AUDIO_FORMATS`
        so unrelated columns (``length``, ``artist``) are not mistaken
        for formats. Defaults to ``["mp3"]`` when no songlist is found.
        """
        table = soup.find("table", id="songlist")
        if table is None:
            return ["mp3"]
        headers = [
            th.get_text(strip=True).lower()
            for th in table.find_all("th")
        ]
        return [h for h in headers if h in _KNOWN_AUDIO_FORMATS] or ["mp3"]

    @staticmethod
    def _parse_track_links(
        soup: "BeautifulSoup", base_url: str
    ) -> List[Tuple[str, str]]:
        """Return ``[(display_name, absolute_url), ...]`` for songlist rows."""
        table = soup.find("table", id="songlist")
        if table is None:
            return []
        out: List[Tuple[str, str]] = []
        for row in table.find_all("tr"):
            anchor = row.find("a")
            if anchor is None or not anchor.get("href"):
                continue
            display_name = anchor.get_text(strip=True)
            out.append(
                (display_name, urljoin(base_url, str(anchor["href"])))
            )
        return out

    @staticmethod
    def _parse_album_images(
        soup: "BeautifulSoup", base_url: str
    ) -> List[MediaFile]:
        """Return the images linked from the album page (excluding songlist).

        Scans every table on the album page (skipping the songlist) for
        anchors whose ``href`` points at an image and whose body
        contains an ``<img>`` thumbnail.
        """
        out: List[MediaFile] = []
        seen: set = set()
        for table in soup.find_all("table"):
            if table.get("id") == "songlist":
                continue
            for anchor in table.find_all("a"):
                href = anchor.get("href")
                if not href or href in seen:
                    continue
                if anchor.find("img") is None:
                    continue
                seen.add(href)
                out.append(
                    MediaFile.from_url(urljoin(base_url, str(href)))
                )
        return out

    def _fetch_tracks(
        self,
        track_links: List[Tuple[str, str]],
        verbose: bool = False,
    ) -> List[Track]:
        """Concurrently fetch every track-detail page.

        Per-track failures are tolerated (the track is returned with
        ``files=()``) so a single broken page doesn't kill the whole
        album fetch. The download path will surface a clearer error if
        the user later tries to download such a track.

        Args:
            track_links: ``(display_name, detail_url)`` pairs to fetch.
            verbose: If True, emit a single stderr "Note:" line when
                any per-track fetch failed. The wrapping
                "Prefetching..." progress banner is the CLI's
                responsibility, not the client's.
        """
        if not track_links:
            return []

        results: List[Optional[Track]] = [None] * len(track_links)
        failures = 0
        failures_lock = threading.Lock()

        def _fetch(indexed: Tuple[int, Tuple[str, str]]) -> None:
            nonlocal failures
            index, (display_name, url) = indexed
            try:
                files = self._fetch_track_files(url)
                results[index] = Track(
                    name=display_name, files=tuple(files)
                )
            except Exception:  # pylint: disable=broad-exception-caught
                results[index] = Track(name=display_name, files=())
                with failures_lock:
                    failures += 1

        from concurrent.futures import ThreadPoolExecutor
        workers = min(self.prefetch_workers, len(track_links))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(_fetch, list(enumerate(track_links))))

        if verbose and failures:
            print(
                f"Note: {failures} song(s) failed metadata prefetch and "
                "will be retried during download.",
                file=sys.stderr,
            )

        return [t for t in results if t is not None]

    def _fetch_track_files(self, url: str) -> List[MediaFile]:
        """Fetch a track-detail page and return its downloadable files.

        Raises:
            InvalidSoundtrackError: If the track page returns a 404-like
                title.
        """
        soup = _get_soup(url, self.session, self.timeout)
        title = soup.find("title")
        title_text = title.get_text() if title is not None else ""
        if "404" in title_text:
            raise InvalidSoundtrackError(f"Track not found: {url}")

        seen: List[str] = []
        seen_set: set = set()
        for anchor in soup.find_all("a", href=_TRACK_FILE_HREF_RE):
            href = anchor.get("href")
            if not href or href in seen_set:
                continue
            seen_set.add(href)
            seen.append(str(href))
        return [
            MediaFile.from_url(urljoin(url, link)) for link in seen
        ]

    @staticmethod
    def _parse_search_table(table: "Tag") -> Tuple[AlbumSummary, ...]:
        """Parse one ``<table class="albumList">`` into AlbumSummary rows."""
        rows = table.find_all("tr")[1:]  # skip header
        out: List[AlbumSummary] = []
        for row in rows:
            tds = row.find_all("td")
            if len(tds) < 2:
                continue
            anchor = tds[1].find("a")
            if not anchor or not anchor.get("href"):
                continue
            href = str(anchor["href"])
            album_id = href.rstrip("/").rsplit("/", 1)[-1]
            display_name = anchor.get_text(strip=True)
            url = urljoin(BASE_URL, f"game-soundtracks/album/{album_id}")
            out.append(
                AlbumSummary(
                    id=album_id,
                    name=(
                        sanitize_filename(display_name)
                        if display_name
                        else ""
                    ),
                    url=url,
                )
            )
        return tuple(out)


# ==============================================================================
# Layer 3 - Application: download services
# ==============================================================================


class DownloadObserver(Protocol):
    """The contract a presentation layer implements to follow downloads.

    :class:`DownloadManager` calls these methods at every state
    transition. Implementations should be cheap (the manager calls
    :meth:`on_progress` per chunk) and thread-safe when callers schedule
    parallel downloads through a thread pool.
    """

    def on_skip(self, filename: str) -> None:
        """Called when the destination already exists and force is False."""

    def on_start(
        self, filename: str, total_bytes: Optional[int]
    ) -> None:
        """Called once at the start of each transfer attempt.

        ``total_bytes`` is ``None`` when the server did not send a
        usable ``Content-Length`` (e.g., chunked transfer encoding).
        """

    def on_progress(self, delta_bytes: int) -> None:
        """Called for each chunk written. ``delta_bytes`` is positive."""

    def on_retry(
        self,
        filename: str,
        attempt: int,
        max_attempts: int,
        error: BaseException,
        backoff_seconds: float,
    ) -> None:
        """Called when a transient failure schedules a backoff retry."""

    def on_complete(
        self,
        filename: str,
        success: bool,
        error: Optional[BaseException],
        attempts: int,
    ) -> None:
        """Called once per file at the terminal state.

        ``attempts`` is the number of transfer attempts actually made
        (1 on the happy path, ``max_retries`` after retry exhaustion).
        Implementations can use it to distinguish a non-retryable
        first-attempt failure from a retry-exhausted one.
        """


class _NullObserver:
    """No-op :class:`DownloadObserver`. The default for non-CLI callers."""

    def on_skip(self, filename: str) -> None:
        del filename

    def on_start(
        self, filename: str, total_bytes: Optional[int]
    ) -> None:
        del filename, total_bytes

    def on_progress(self, delta_bytes: int) -> None:
        del delta_bytes

    def on_retry(
        self,
        filename: str,
        attempt: int,
        max_attempts: int,
        error: BaseException,
        backoff_seconds: float,
    ) -> None:
        del filename, attempt, max_attempts, error, backoff_seconds

    def on_complete(
        self,
        filename: str,
        success: bool,
        error: Optional[BaseException],
        attempts: int,
    ) -> None:
        del filename, success, error, attempts


class _DownloadOutcome(enum.Enum):
    """Terminal state of a single :meth:`DownloadManager.download_file` call."""

    DOWNLOADED = "downloaded"
    SKIPPED = "skipped"
    FAILED = "failed"


class DownloadManager:
    """Atomic, retrying, UI-agnostic file downloader.

    Streams the body of a :class:`MediaFile` to ``<dest>.part`` and
    renames it to ``dest`` on success, so an interrupted run never
    leaves a half-finished file at the final path. Transient failures
    (network errors, server 5xx / 408 / 429, transient OS errors,
    byte-count mismatch) trigger an exponential-backoff retry up to
    :attr:`max_retries` total attempts. Non-transient failures abort
    immediately.

    All observable progress is reported through a
    :class:`DownloadObserver` — the only seam the presentation layer
    needs.

    Attributes:
        session: HTTP session used for streaming.
        timeout: Per-request timeout in seconds.
        max_retries: Maximum attempts per file (including the first).
        chunk_size: Bytes per ``iter_content`` chunk.
    """

    def __init__(
        self,
        session: "_curl_requests.Session",
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        chunk_size: int = CHUNK_SIZE,
    ):
        self.session = session
        self.timeout = timeout
        self.max_retries = max_retries
        self.chunk_size = chunk_size

    def download_file(
        self,
        media: MediaFile,
        dest: Path,
        observer: Optional[DownloadObserver] = None,
        force: bool = False,
    ) -> Tuple[_DownloadOutcome, Optional[BaseException]]:
        """Download a single :class:`MediaFile` to ``dest``.

        Args:
            media: Source file metadata.
            dest: Destination path. Parent directory must already exist.
            observer: Notification sink. Defaults to a no-op.
            force: If True, overwrite an existing file at ``dest``. If
                False and ``dest`` exists with size > 0, the download is
                skipped (``observer.on_skip`` is invoked).

        Returns:
            A ``(outcome, error)`` tuple. ``error`` is ``None`` unless
            the outcome is :attr:`_DownloadOutcome.FAILED`.
        """
        active_observer: DownloadObserver = observer or _NullObserver()

        if not force and self._existing_is_complete(dest):
            active_observer.on_skip(dest.name)
            return _DownloadOutcome.SKIPPED, None

        last_error: Optional[BaseException] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._stream_to_disk(media, dest, active_observer)
                active_observer.on_complete(
                    dest.name, success=True, error=None, attempts=attempt,
                )
                return _DownloadOutcome.DOWNLOADED, None
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if not self._is_retryable(exc):
                    active_observer.on_complete(
                        dest.name,
                        success=False,
                        error=exc,
                        attempts=attempt,
                    )
                    return _DownloadOutcome.FAILED, exc
                last_error = exc
                if attempt >= self.max_retries:
                    break
                import random
                backoff = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                active_observer.on_retry(
                    dest.name,
                    attempt,
                    self.max_retries,
                    exc,
                    backoff,
                )
                time.sleep(backoff)

        active_observer.on_complete(
            dest.name,
            success=False,
            error=last_error,
            attempts=self.max_retries,
        )
        return _DownloadOutcome.FAILED, last_error

    @staticmethod
    def _existing_is_complete(dest: Path) -> bool:
        """Return True if ``dest`` already exists with a non-zero size.

        Treats an :class:`OSError` on stat as "exists" — matching the
        legacy "if we can't tell, assume the file is fine" behavior so
        we never re-download something we couldn't even stat.
        """
        if not dest.exists():
            return False
        try:
            return dest.stat().st_size > 0
        except OSError:
            return True

    def _stream_to_disk(
        self,
        media: MediaFile,
        dest: Path,
        observer: DownloadObserver,
    ) -> None:
        """Atomic streaming write. Cleans up the .part file on any failure.

        Bytes are written to ``<dest>.part`` and then renamed to
        ``dest`` on success, so a half-finished or interrupted download
        never leaves a file at the final path. Any partial ``.part``
        file is removed on failure (including ``KeyboardInterrupt``).

        Raises:
            requests.RequestException / curl_cffi RequestException: On
                network error. The caller's retry loop catches both via
                :data:`_NETWORK_EXCEPTIONS`.
            IncompleteDownloadError: If the response advertised an
                un-encoded ``Content-Length`` and the bytes written did
                not match. Skipped on encoded responses (where the
                header reflects compressed bytes but ``iter_content``
                yields decoded bytes).
        """
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

        try:
            resp = self.session.get(
                media.url, stream=True, timeout=self.timeout
            )
            resp.raise_for_status()

            total: Optional[int] = None
            length = resp.headers.get("Content-Length")
            if length and length.isdigit():
                total = int(length)

            content_encoding = resp.headers.get(
                "Content-Encoding", ""
            ).lower()
            verify_size = total is not None and not content_encoding

            observer.on_start(dest.name, total)

            written = 0
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(
                    chunk_size=self.chunk_size
                ):
                    if not chunk:
                        continue
                    f.write(chunk)
                    written += len(chunk)
                    observer.on_progress(len(chunk))

            if verify_size and written != total:
                raise IncompleteDownloadError(
                    f"Expected {total} bytes for {media.filename!r}, "
                    f"got {written}"
                )

            os.replace(tmp, dest)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        """Return True if ``exc`` represents a transient failure.

        This decides whether a *whole-file* retry is worth doing. libcurl
        (via curl_cffi) handles socket-level retries inside a single
        ``session.get`` call on its own, so by the time an exception
        reaches here it has already survived that layer.
        """
        if isinstance(exc, IncompleteDownloadError):
            return True
        if isinstance(exc, _HTTP_EXCEPTIONS):
            resp = getattr(exc, "response", None)
            if resp is None:
                return True
            status = resp.status_code
            if 400 <= status < 500:
                return status in _RETRYABLE_4XX_STATUSES
            return True  # 5xx
        if isinstance(exc, _NETWORK_EXCEPTIONS):
            return True  # connect/read/timeout/SSL/etc.
        if isinstance(exc, OSError):
            return exc.errno in _TRANSIENT_OSERROR_ERRNOS
        return False


@dataclass
class DownloadReport:
    """Mutable, thread-safe accumulator for per-file download outcomes.

    Used by :class:`DownloadOrchestrator` to support an end-of-run
    summary instead of forcing the caller to scrape stderr to learn
    what failed.

    Attributes:
        succeeded: Number of files successfully downloaded.
        skipped: Number of files skipped because they already existed
            on disk and ``--force`` was not set.
        failures: Per-file failure messages, in the form
            ``"<filename>: <reason>"``.
    """

    succeeded: int = 0
    skipped: int = 0
    failures: List[str] = field(default_factory=list)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False
    )

    @property
    def success(self) -> bool:
        """Return True when no file failed."""
        return not self.failures

    def record_success(self, was_skipped: bool = False) -> None:
        """Record a successful (or skipped) file."""
        with self._lock:
            if was_skipped:
                self.skipped += 1
            else:
                self.succeeded += 1

    def record_failure(self, name: str, reason: str) -> None:
        """Record a failed file together with a brief reason string."""
        with self._lock:
            self.failures.append(f"{name}: {reason}")


# ==============================================================================
# Layer 4 - Presentation: observers and orchestration
# ==============================================================================


class _CliObserverBase:
    """Shared base for CLI observers.

    Provides label/prefix bookkeeping and the failure-message logic
    shared by both the in-place progress-bar mode and the one-line
    summary mode.

    Attributes:
        row_prefix: Pre-formatted ``"[N/M] "`` prefix used for status
            lines (with an optional ``"image "`` infix).
        max_retries: How many retry attempts the manager will make;
            used to produce the legacy "after N attempts" failure line.
        verbose: Whether informational lines are printed at all. Failure
            lines are printed regardless of this flag.
    """

    def __init__(
        self,
        row_prefix: str,
        max_retries: int,
        verbose: bool,
    ):
        self.row_prefix = row_prefix
        self.max_retries = max_retries
        self.verbose = verbose

    def on_skip(self, filename: str) -> None:
        if self.verbose:
            print(
                f"{self.row_prefix}Skipping existing: {filename}"
            )

    def on_retry(
        self,
        filename: str,
        attempt: int,
        max_attempts: int,
        error: BaseException,
        backoff_seconds: float,
    ) -> None:
        if self.verbose:
            print(
                f"Retry {attempt}/{max_attempts} for {filename} in "
                f"{backoff_seconds:.1f}s "
                f"({error.__class__.__name__}: {error})",
                file=sys.stderr,
            )

    def _print_failure(
        self,
        filename: str,
        error: Optional[BaseException],
        attempts: int,
    ) -> None:
        """Print the appropriate stderr failure line for the given mode.

        Mirrors the legacy two-message split:
          * Retry-exhausted (``attempts >= max_retries``):
            ``Failed: <name> after <N> attempts: <reason>``.
          * Non-retryable first-attempt failure:
            ``[N/M] Error: <reason>``.
        """
        reason = (
            f"{error.__class__.__name__}: {error}"
            if error is not None
            else "unknown"
        )
        if attempts >= self.max_retries:
            print(
                f"Failed: {filename} after {self.max_retries} "
                f"attempts: {reason}",
                file=sys.stderr,
            )
        else:
            print(
                f"{self.row_prefix}Error: {reason}",
                file=sys.stderr,
            )


class _OneLineObserver(_CliObserverBase):
    """Prints one-line per-file status.

    Used for non-TTY output, ``--no-verbose`` mode, or multi-threaded
    downloads where a per-file progress bar would interleave
    incoherently across files.
    """

    def __init__(
        self,
        row_prefix: str,
        max_retries: int,
        verbose: bool,
    ):
        super().__init__(row_prefix, max_retries, verbose)
        self._bytes_written = 0

    def on_start(
        self, filename: str, total_bytes: Optional[int]
    ) -> None:
        del total_bytes
        self._bytes_written = 0
        if self.verbose:
            display = _truncate_display_name(filename)
            print(
                f"{self.row_prefix}"
                f"{display:<{_DISPLAY_NAME_WIDTH}} ..."
            )

    def on_progress(self, delta_bytes: int) -> None:
        self._bytes_written += max(0, int(delta_bytes))

    def on_retry(
        self,
        filename: str,
        attempt: int,
        max_attempts: int,
        error: BaseException,
        backoff_seconds: float,
    ) -> None:
        self._bytes_written = 0
        super().on_retry(
            filename, attempt, max_attempts, error, backoff_seconds
        )

    def on_complete(
        self,
        filename: str,
        success: bool,
        error: Optional[BaseException],
        attempts: int,
    ) -> None:
        if success:
            if self.verbose:
                size = (
                    _human_size(self._bytes_written)
                    if self._bytes_written
                    else "?"
                )
                print(
                    f"{self.row_prefix}OK   {filename} ({size})"
                )
            return
        self._print_failure(filename, error, attempts)


class CLIProgressObserver(_CliObserverBase):
    """In-place per-file progress bar (single-thread + TTY + verbose).

    Wraps the module-private :class:`_ProgressBar` widget. The bar is
    created in :meth:`on_start` and recreated on each retry so the
    user sees a fresh bar per attempt — matching the legacy behavior.
    """

    def __init__(self, row_prefix: str, max_retries: int):
        super().__init__(row_prefix, max_retries, verbose=True)
        self._bar: Optional[_ProgressBar] = None

    def on_start(
        self, filename: str, total_bytes: Optional[int]
    ) -> None:
        display = _truncate_display_name(filename)
        label = f"{self.row_prefix}{display:<{_DISPLAY_NAME_WIDTH}}"
        self._bar = _ProgressBar(prefix=label, total=total_bytes)
        self._bar(0, total_bytes)

    def on_progress(self, delta_bytes: int) -> None:
        if self._bar is not None:
            self._bar(delta_bytes)

    def on_retry(
        self,
        filename: str,
        attempt: int,
        max_attempts: int,
        error: BaseException,
        backoff_seconds: float,
    ) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None
        super().on_retry(
            filename, attempt, max_attempts, error, backoff_seconds
        )

    def on_complete(
        self,
        filename: str,
        success: bool,
        error: Optional[BaseException],
        attempts: int,
    ) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None
        if not success:
            self._print_failure(filename, error, attempts)


def _make_cli_observer(
    row_prefix: str,
    max_retries: int,
    verbose: bool,
    threads: int,
) -> DownloadObserver:
    """Pick the right CLI observer for the given mode.

    The bar mode requires verbose output, a single thread (so frames
    don't interleave between files), and a TTY (``isatty()``). All
    other combinations fall back to the one-line observer.
    """
    if verbose and threads <= 1 and sys.stdout.isatty():
        return CLIProgressObserver(row_prefix, max_retries)
    return _OneLineObserver(row_prefix, max_retries, verbose=verbose)


_DownloadJob = Tuple[str, Optional[MediaFile], Optional[BaseException]]


class DownloadOrchestrator:
    """Runs an :class:`Album` through a :class:`DownloadManager`.

    Owns format selection, output-directory creation, threading, the
    inter-file delay, the verbose banner, and the per-file observer
    construction. Failures are recorded into a :class:`DownloadReport`
    instead of being raised, so the caller always gets a complete
    summary for the end-of-run message.

    Attributes:
        manager: The download backend.
        observer_factory: Callable producing one observer per file. By
            default, :func:`_make_cli_observer` is used; tests or
            alternative front-ends can pass their own factory.
    """

    def __init__(
        self,
        manager: DownloadManager,
        observer_factory: Optional[
            Callable[[str, int, bool, int], DownloadObserver]
        ] = None,
    ):
        self.manager = manager
        self.observer_factory = observer_factory or _make_cli_observer

    def run(
        self,
        album: Album,
        output_dir: Path,
        formats: Sequence[str],
        download_images: bool,
        threads: int,
        delay: float,
        force: bool,
        verbose: bool,
    ) -> DownloadReport:
        """Download an album's tracks, then optionally its images.

        The download banner is the *caller's* responsibility — the
        orchestrator owns only the per-file progress observers, the
        threading, and the failure tally. This separation keeps the
        orchestrator from needing to know whether the banner has
        already been printed by an upstream handler (which it has,
        in the CLI flow, so the banner can land before the parallel
        track-prefetch step).

        Args:
            album: A fully-resolved :class:`Album`.
            output_dir: Directory to save tracks (and images) into.
            formats: Preferred audio formats in priority order. Already
                normalized (lowercase, stripped) by the CLI parser.
                May be empty to mean "auto / best available".
            download_images: If True, also download album images.
            threads: Maximum concurrent file downloads. ``1`` keeps the
                familiar in-place progress bar; values >1 disable the
                per-file bar in favor of one-line completion messages.
            delay: Seconds to wait between consecutive sequential
                downloads (ignored when ``threads > 1``).
            force: If True, overwrite files already on disk instead of
                skipping them.
            verbose: If True, the per-file observer prints status
                lines. Failure lines print regardless.

        Returns:
            A :class:`DownloadReport` summarising successes, skips, and
            per-file failure messages.

        Raises:
            InvalidFormatError: If no requested format is available.
            OSError: If output directory creation fails.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        if formats and not set(formats).intersection(album.formats):
            available = ", ".join(album.formats)
            raise InvalidFormatError(
                f"None of the requested formats {list(formats)} are "
                f"available. Available: {available}"
            )

        report = DownloadReport()

        track_jobs = self._build_track_jobs(album, formats)
        self._run_jobs(
            track_jobs,
            output_dir,
            threads=threads,
            delay=delay,
            force=force,
            verbose=verbose,
            report=report,
            kind="track",
        )

        if download_images:
            image_jobs = self._build_image_jobs(album)
            self._run_jobs(
                image_jobs,
                output_dir,
                threads=threads,
                delay=delay,
                force=force,
                verbose=verbose,
                report=report,
                kind="image",
            )

        return report

    # --- job construction ---------------------------------------------------

    @staticmethod
    def _build_track_jobs(
        album: Album, formats: Sequence[str]
    ) -> List[_DownloadJob]:
        """Resolve each track to its chosen :class:`MediaFile`."""
        jobs: List[_DownloadJob] = []
        for track in album.tracks:
            try:
                jobs.append((track.name, track.best_file(formats), None))
            except InvalidSoundtrackError as exc:
                jobs.append((track.name, None, exc))
        return jobs

    @staticmethod
    def _build_image_jobs(album: Album) -> List[_DownloadJob]:
        """Trivial passthrough: each image is already a downloadable file."""
        return [(img.filename, img, None) for img in album.images]

    # --- dispatching --------------------------------------------------------

    def _run_jobs(
        self,
        jobs: List[_DownloadJob],
        output_dir: Path,
        threads: int,
        delay: float,
        force: bool,
        verbose: bool,
        report: DownloadReport,
        kind: str,
    ) -> None:
        """Schedule jobs sequentially or via a thread pool."""
        if not jobs:
            return

        total = len(jobs)
        pad = max(1, len(str(total)))
        label_prefix = "image " if kind == "image" else ""

        def _do_one(idx: int, job: _DownloadJob) -> None:
            display_name, media, build_error = job
            row_prefix = (
                f"[{label_prefix}{idx:0{pad}d}/{total}] "
            )
            if build_error is not None:
                self._record_build_error(
                    display_name, idx, kind, row_prefix, build_error,
                    report,
                )
                return
            self._download_one(
                media,
                output_dir,
                row_prefix,
                threads=threads,
                verbose=verbose,
                force=force,
                report=report,
            )

        if threads <= 1:
            for idx, job in enumerate(jobs, start=1):
                _do_one(idx, job)
                if delay > 0 and idx < total:
                    time.sleep(delay)
            return

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [
                executor.submit(_do_one, idx, job)
                for idx, job in enumerate(jobs, start=1)
            ]
            for future in as_completed(futures):
                future.result()

    def _download_one(
        self,
        media: Optional[MediaFile],
        output_dir: Path,
        row_prefix: str,
        threads: int,
        verbose: bool,
        force: bool,
        report: DownloadReport,
    ) -> None:
        """Run one download through the manager and record the outcome."""
        assert media is not None

        dest = output_dir / sanitize_filename(media.filename)
        observer = self.observer_factory(
            row_prefix, self.manager.max_retries, verbose, threads,
        )
        outcome, error = self.manager.download_file(
            media=media,
            dest=dest,
            observer=observer,
            force=force,
        )

        if outcome is _DownloadOutcome.SKIPPED:
            report.record_success(was_skipped=True)
        elif outcome is _DownloadOutcome.DOWNLOADED:
            report.record_success(was_skipped=False)
        else:
            reason = (
                f"{error.__class__.__name__}: {error}"
                if error is not None
                else "unknown"
            )
            report.record_failure(dest.name, reason)

    @staticmethod
    def _record_build_error(
        display_name: str,
        idx: int,
        kind: str,
        row_prefix: str,
        error: BaseException,
        report: DownloadReport,
    ) -> None:
        """Record a job that failed before any HTTP request was made."""
        name = display_name or f"{kind} #{idx}"
        reason = f"{error.__class__.__name__}: {error}"
        report.record_failure(name, reason)
        print(
            f"{row_prefix}Error: {reason}",
            file=sys.stderr,
        )



# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# CLI helpers
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _print_download_banner(
    album: Album,
    output_dir: Path,
    formats: Sequence[str],
    download_images: bool,
    threads: int,
) -> None:
    """Render the verbose pre-download header block.

    The "Processing:" / "Metadata: Fetching..." preamble is printed by
    the CLI handler around the album-page fetch (see
    :func:`_handle_download`); this helper owns the part of the banner
    that depends on a fetched :class:`Album`.
    """
    print(f"Album:      {album.name}")
    print(f"Output:     {output_dir.resolve()}")
    print(
        "Formats:    "
        + (
            ", ".join(formats)
            if formats
            else "Auto (Best Available)"
        )
    )
    total_tracks = len(album.tracks)
    print(f"Threads:    {threads}")
    print(f"Content:    {total_tracks} Songs", end="")
    if download_images:
        print(f", {len(album.images)} Images")
    else:
        print()
    print("-" * 50)


def _parse_formats(raw: Optional[str]) -> Optional[List[str]]:
    """Normalize the --format CLI argument.

    ``"FLAC, mp3 ,, ogg"`` becomes ``["flac", "mp3", "ogg"]``. Returns
    ``None`` when no formats were requested so the default-priority
    logic in :meth:`Track.best_file` can take over.
    """
    if not raw:
        return None
    formats = [token.strip().lower() for token in raw.split(",")]
    formats = [token for token in formats if token]
    return formats or None


def _bounded_int(low: int, high: int) -> Callable[[str], int]:
    """Return an argparse ``type`` that enforces ``low <= n <= high``."""

    def _check(value: str) -> int:
        try:
            n = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"expected integer, got {value!r}"
            ) from exc
        if not low <= n <= high:
            raise argparse.ArgumentTypeError(
                f"must be between {low} and {high}, got {n}"
            )
        return n

    return _check


def _non_negative_float(value: str) -> float:
    """Argparse ``type`` for non-negative floats."""
    import math
    try:
        f = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected number, got {value!r}"
        ) from exc
    if f < 0 or math.isnan(f):
        raise argparse.ArgumentTypeError(f"must be >= 0, got {f}")
    return f


def _positive_float(value: str) -> float:
    """Argparse ``type`` for strictly positive floats."""
    f = _non_negative_float(value)
    if f <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {f}")
    return f


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Download KHInsider soundtracks",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=f"Report issues: {REPORT_URL}",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "soundtrack",
        help=(
            "Album ID, full album URL, or search term (with -s)\n"
            "(e.g. 'kh2fm-soundtrack' from "
            "https://downloads.khinsider.com/"
            "game-soundtracks/album/kh2fm-soundtrack)"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output directory (default: album name)",
    )
    parser.add_argument(
        "-f",
        "--format",
        help=(
            "Preferred audio formats, comma-separated (e.g. 'flac,mp3'). "
            "Case- and whitespace-insensitive."
        ),
    )
    parser.add_argument(
        "-i",
        "--images",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Download album images (default: enabled;"
            " use --no-images to disable)."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Show detailed progress (default: enabled;"
            " use --no-verbose to disable)."
        ),
    )
    parser.add_argument(
        "-s",
        "--search",
        action="store_true",
        help=(
            "Search for soundtracks instead of downloading. "
            "Also used automatically if the supplied soundtrack ID "
            "does not exist."
        ),
    )
    parser.add_argument(
        "-t",
        "--threads",
        type=_bounded_int(1, MAX_THREADS),
        default=DEFAULT_THREADS,
        metavar="N",
        help=(
            f"Concurrent file downloads (1-{MAX_THREADS}, default: "
            f"{DEFAULT_THREADS}). Values >1 disable the per-file progress "
            "bar in favor of one-line completion messages."
        ),
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=_non_negative_float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "Seconds to wait between sequential downloads (default: 0). "
            "Ignored when --threads > 1."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download files that already exist on disk.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        dest="list_only",
        help="Print what would be downloaded and exit (no files written).",
    )
    return parser


def _build_session(threads: int = DEFAULT_THREADS) -> "_curl_requests.Session":
    """Construct an HTTP session that impersonates a real browser.

    KHInsider is behind Cloudflare (I think), which rejects ``requests``
    session with HTTP 403 because its TLS ClientHello and HTTP/2 SETTINGS
    don't match any real browser. ``curl_cffi`` uses libcurl-impersonate
    to replay Chrome's exact TLS/HTTP-2 fingerprint, which lets the
    request through. Connection pooling and transient-error retries are
    handled by libcurl internally and by the application-level
    ``MAX_RETRIES`` loop in :class:`DownloadManager`; no urllib3-style
    HTTPAdapter is required.
    """
    del threads  # accepted for call-site compatibility; not needed by libcurl
    session = _curl_requests.Session(impersonate="chrome124")
    session.headers.update(
        {
            "Referer": BASE_URL,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def _make_io_unicode_safe() -> None:
    """Ensure stdout/stderr can encode any character KHInsider may emit.

    On Windows, the default console encoding is often cp1252, which
    raises ``UnicodeEncodeError`` on track titles containing non-Latin-1
    characters (a frequent occurrence for Japanese game soundtracks).
    Reconfiguring to UTF-8 with a replacement error handler keeps output
    legible without crashing the process. ``reconfigure`` is a no-op on
    streams that have already been redirected to a non-TextIOWrapper.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _print_search_results(
    results: SearchResults,
    client: KHInsiderClient,
    file=sys.stdout,
    prefetch: bool = True,
) -> None:
    """Pretty-print search results.

    By default, missing album names are pre-fetched in parallel before
    printing so the user sees a complete list at once instead of waiting
    for serial round-trips. Set ``prefetch=False`` to skip the warmup.
    """
    all_items: List[AlbumSummary] = (
        list(results.albums) + list(results.songs)
    )
    if not all_items:
        print("No soundtracks found.", file=file)
        return

    if prefetch:
        all_items = list(client.resolve_summary_names(all_items))
        album_count = len(results.albums)
        results = SearchResults(
            albums=tuple(all_items[:album_count]),
            songs=tuple(all_items[album_count:]),
        )

    pad = max(len(item.id) for item in all_items)

    def _print_group(title: str, items: Iterable[AlbumSummary]) -> None:
        items_list = list(items)
        if not items_list:
            return
        print(title, file=file)
        for item in items_list:
            display_name = item.name or "(name unavailable)"
            dots = "." * (pad - len(item.id) + 1)
            print(f"{item.id} {dots} {display_name}", file=file)
        print("", file=file)

    _print_group("Album title results:", results.albums)
    _print_group("Song name results:", results.songs)


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Entry and handlers
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _print_failure_summary(report: DownloadReport) -> None:
    """Render the end-of-run failure list to stderr."""
    if not report.failures:
        return
    print(
        f"\n{len(report.failures)} file(s) failed:",
        file=sys.stderr,
    )
    for line in report.failures:
        print(f"  - {line}", file=sys.stderr)


def _handle_search(
    args: argparse.Namespace, client: KHInsiderClient
) -> int:
    """Handle the ``--search`` mode."""
    try:
        results = client.search(args.soundtrack)
    except (SearchError, NetworkError) as exc:
        print(f"Search failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Soundtracks found (to download, run "
        '"python khinsider.py <soundtrack_id>"):\n'
    )
    _print_search_results(results, client)
    return 0


def _handle_list_only(
    args: argparse.Namespace,
    soundtrack_id: str,
    user_input: str,
    client: KHInsiderClient,
) -> int:
    """Handle the ``--list-only`` mode (preview without downloading)."""
    formats = _parse_formats(args.format)
    _, parsed_url = _extract_id_and_url(user_input)
    try:
        metadata = client.fetch_album_metadata(
            parsed_url or soundtrack_id
        )
        album = client.prefetch_track_files(
            metadata, verbose=args.verbose
        )
    except InvalidSoundtrackError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Album:   {album.name}")
    print(f"Formats: {', '.join(album.formats)}")
    print(f"\n{len(album.tracks)} track(s):")
    for idx, track in enumerate(album.tracks, start=1):
        try:
            chosen = track.best_file(formats or [])
            print(f"  [{idx:>3}] {chosen.filename}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(
                f"  [{idx:>3}] (error: {exc.__class__.__name__}: {exc})"
            )
    if args.images:
        print(f"\n{len(album.images)} image(s):")
        for idx, img in enumerate(album.images, start=1):
            print(f"  [{idx:>3}] {img.filename}")
    return 0


def _handle_download(
    args: argparse.Namespace,
    soundtrack_id: str,
    user_input: str,
    client: KHInsiderClient,
    orchestrator: DownloadOrchestrator,
) -> int:
    """Handle the default download mode.

    Performs the fetch in two phases so the verbose banner can land
    between the album-page fetch and the (slower) parallel track-detail
    prefetch — matching the legacy progress-message ordering.
    """
    formats = _parse_formats(args.format)
    _, parsed_url = _extract_id_and_url(user_input)

    pre_fetch_announce = args.verbose and args.output is not None

    if pre_fetch_announce:
        print(f"Processing: {soundtrack_id}")
        print(
            "Metadata:   Fetching from KHInsider...",
            end="",
            flush=True,
        )
    try:
        metadata = client.fetch_album_metadata(
            parsed_url or soundtrack_id
        )
    except InvalidSoundtrackError:
        if pre_fetch_announce:
            print()
        raise
    if pre_fetch_announce:
        print(" Done.")

    out_dir = args.output or Path(metadata.album.name)

    if args.verbose:
        if not pre_fetch_announce:
            print(f"Processing: {soundtrack_id}")
        _print_download_banner(
            metadata.album,
            out_dir,
            formats or [],
            args.images,
            args.threads,
        )

    if args.verbose and len(metadata.track_links) > 1:
        print(
            f"Prefetching metadata for {len(metadata.track_links)} "
            "tracks...",
            end="",
            flush=True,
        )
    album = client.prefetch_track_files(metadata, verbose=args.verbose)
    if args.verbose and len(metadata.track_links) > 1:
        print(" Done.")

    report = orchestrator.run(
        album=album,
        output_dir=out_dir,
        formats=formats or [],
        download_images=args.images,
        threads=args.threads,
        delay=args.delay,
        force=args.force,
        verbose=args.verbose,
    )

    _print_failure_summary(report)

    if report.success:
        print(
            f"\nDownload completed successfully! "
            f"({report.succeeded} downloaded, {report.skipped} skipped)"
        )
        return 0

    print(
        f"\nDownload completed with {len(report.failures)} error(s) "
        f"({report.succeeded} downloaded, {report.skipped} skipped).",
        file=sys.stderr,
    )
    return 1


def _handle_missing_album(
    args: argparse.Namespace,
    soundtrack_id: str,
    client: KHInsiderClient,
) -> int:
    """Handle the auto-search fallback when an album ID isn't found."""
    del args
    search_term = soundtrack_id.replace("-", " ")
    try:
        results = client.search(search_term)
    except (SearchError, NetworkError):
        print(
            f'The soundtrack "{soundtrack_id}" does not seem to exist, '
            "and a search could not be performed.",
            file=sys.stderr,
        )
        return 1

    print(
        f'The soundtrack "{soundtrack_id}" does not seem to exist.\n',
        file=sys.stderr,
    )
    print(
        'These exist, though (run "python khinsider.py <soundtrack_id>"):',
        file=sys.stderr,
    )
    _print_search_results(results, client, file=sys.stderr)
    return 1


def main() -> None:
    """Parse CLI args, configure session, and dispatch the requested mode.

    This function is the *composition root* — it owns the construction
    and lifecycle of every layer's components and wires them together.
    Everything below it is plain dependency injection.
    """
    _make_io_unicode_safe()
    args = _build_arg_parser().parse_args()

    try:
        with _build_session(threads=args.threads) as session:
            client = KHInsiderClient(session, timeout=args.timeout)
            user_input = args.soundtrack
            soundtrack_id = extract_soundtrack_id(user_input)

            if args.search:
                sys.exit(_handle_search(args, client))

            if args.list_only:
                sys.exit(
                    _handle_list_only(
                        args, soundtrack_id, user_input, client,
                    )
                )

            manager = DownloadManager(session, timeout=args.timeout)
            orchestrator = DownloadOrchestrator(manager)

            try:
                sys.exit(
                    _handle_download(
                        args,
                        soundtrack_id,
                        user_input,
                        client,
                        orchestrator,
                    )
                )
            except InvalidSoundtrackError:
                sys.exit(
                    _handle_missing_album(args, soundtrack_id, client)
                )

    except KeyboardInterrupt:
        print("\nDownload cancelled by user.", file=sys.stderr)
        sys.exit(130)
    except ScriptError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(
            f"\nUnexpected error: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

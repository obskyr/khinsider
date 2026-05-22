# KHInsider Downloader

`khinsider.py` is a [Python](https://www.python.org/) interface and script for getting [khinsider](http://downloads.khinsider.com/) soundtracks. It makes khinsider mass downloads a breeze. It's easy to use, check it!

> **Requires Python 3.9+**  
> **Zero-setup:** Just run the script, it auto-installs dependencies!  
> Set `KHI_NO_AUTO_INSTALL=1` to disable auto-install.

---

## Quick Start

1) In your command line of choice, **Windows:** Command Prompt or PowerShell, **macOS:** Terminal, **Linux:** any terminal.
2) In the **same folder as `khinsider.py`**:
```bash
# Download by ID
python khinsider.py plants-vs.-zombies

# Download by full URL
python khinsider.py https://downloads.khinsider.com/game-soundtracks/album/plants-vs.-zombies

# Pick formats (first available is used)
python khinsider.py minecraft -f flac,mp3

# Search (or use automatically if an ID doesn’t exist)
python khinsider.py -s persona
python khinsider.py definitely-not-a-real-id   # auto-search fallback

# Choose output directory
python khinsider.py minecraft -o "Folder Where I Keep Minecraft Music"

# Turn OFF images or verbose logging (defaults are ON)
python khinsider.py minecraft --no-images
python khinsider.py minecraft --no-verbose
```

---

## CLI

```bash
python khinsider.py <album-id-or-url> [options]
```

**Options**

* `-o, --output DIR` Output directory (default: sanitized album name)
* `-f, --format LIST` Preferred formats, comma-separated (e.g. `flac,mp3`)
* `-s, --search` Search for albums/songs instead of downloading
* `-i/--no-images` Download album images (default **ON**)
* `-v/--no-verbose` Detailed progress (default **ON**)
* `-h, --help` Show help

---

## Features

* Download by **ID or full album URL**
* Multi-format with priority (e.g., `flac,mp3`)
* Album images (cover/booklet/etc.)

---

## “Never used Python before?”

1. Install the latest Python version from [https://www.python.org/downloads/](https://www.python.org/downloads/)
2. Download this repo (or the single `khinsider.py` file)
3. Run:

```bash
python khinsider.py <album-id-or-url>
```

That’s it! The script installs what it needs automatically.

---

## Library

```python
from pathlib import Path
import requests
from khinsider import Soundtrack

with requests.Session() as s:
    ost = Soundtrack("jumping-flash", s)  # or full URL
    ost.download(
        output_dir=Path("Jumping Flash OST"),
        formats=["flac", "mp3"],
        download_images=True,   # defaults True in CLI
        verbose=True,           # defaults True in CLI
    )
```

---

## Optional: Manual Dependencies

Auto-install is on by default. To install manually instead:

```bash
pip install requests beautifulsoup4
```

Disable auto-install:

```bash
export KHI_NO_AUTO_INSTALL=1    # Power users/etc.
```

---

## Support

* Issues: [https://github.com/obskyr/khinsider/issues](https://github.com/obskyr/khinsider/issues)

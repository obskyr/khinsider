#!/usr/bin/env python
# -*- coding: utf-8 -*-

# A script to download full soundtracks from KHInsider.

# __future__ import for forwards compatibility with Python 3
from __future__ import print_function
from __future__ import unicode_literals

import os
import re
import sys
from functools import wraps
from itertools import chain

try:
    from urllib.parse import unquote, urljoin, urlsplit
except ImportError: # Python 2
    from urlparse import unquote, urljoin, urlsplit

try: # Python 2
    from os import getcwdu as getcwd
except ImportError:
    from os import getcwd

class Silence(object):
    def __enter__(self):
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')
    
    def __exit__(self, *_):
        sys.stdout = self._stdout
        sys.stderr = self._stderr


# --- Install prerequisites ---

# (This section in `if __name__ == '__main__':` is entirely unrelated to the
# rest of the module, and doesn't even run if the module isn't run by itself.)

if __name__ == '__main__':
    # To check for the existence of modules without importing them.
    # Apparently imp and importlib are a forest of deprecation!
    # The API was changed once in 3.3 (deprecating imp),
    # and then again in 3.4 (deprecating the 3.3 API).
    # So.... we have to do this dance to avoid deprecation warnings.
    try:
        try:
            from importlib.util import find_spec as find_module # Python 3.4+
        except ImportError:
            from importlib import find_loader as find_module # Python 3.3
    except ImportError:
        from imp import find_module # Python 2

    # User-friendly name, import name, pip specification.
    requiredModules = [
        ['requests', 'requests', 'requests >= 2.0.0, < 3.0.0'],
        ['Beautiful Soup 4', 'bs4', 'beautifulsoup4 >= 4.4.0, < 5.0.0']
    ]

    def moduleExists(name):
        try:
            result = find_module(name)
        except ImportError:
            return False
        else:
            return result is not None
    def neededInstalls(requiredModules=requiredModules):
        uninstalledModules = []
        for module in requiredModules:
            if not moduleExists(module[1]):
                uninstalledModules.append(module)
        return uninstalledModules

    def install(package):
        nowhere = open(os.devnull, 'w')
        exitStatus = subprocess.call([sys.executable, '-m', 'pip', 'install', package],
                                     stdout=nowhere,
                                     stderr=nowhere)
        if exitStatus != 0:
            raise OSError("Failed to install package.")
    def installModules(modules, verbose=True):
        for module in modules:
            if verbose:
                print("Installing {}...".format(module[0]))
            
            try:
                install(module[2])
            except OSError as e:
                if verbose:
                    print("Failed to install {}. "
                          "You may need to run the script as an administrator "
                          "or superuser.".format(module[0]),
                          file=sys.stderr)
                    print("You can also try to install the package manually "
                          "(pip install \"{}\")".format(module[2]),
                          file=sys.stderr)
                raise e
    def installRequiredModules(needed=None, verbose=True):
        needed = neededInstalls() if needed is None else needed
        installModules(neededInstalls(), verbose)

    needed = neededInstalls()
    if needed:
        if moduleExists('pip'):
            # Needed to call pip the official way.
            import subprocess
        else:
            print("You don't seem to have pip installed!", file=sys.stderr)
            print("Get it from https://pip.readthedocs.org/en/latest/installing.html", file=sys.stderr)
            sys.exit(1)

    try:
        installRequiredModules(needed)
    except OSError:
        sys.exit(1)

# ------

import requests
from bs4 import BeautifulSoup

BASE_URL = 'https://downloads.khinsider.com/'

# Although some of these are valid on Linux, keeping this the same
# across systems is nice for consistency AND it works on WSL.
FILENAME_INVALID_RE = re.compile(r'[<>:"/\\|?*]')
def to_valid_filename(s):
    # Windows's Explorer doens't handle filenames that end in ' ' or '.'.
    s = s.rstrip(' .')

    if s in {'', '.', '..', '~', 'CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2',
             'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1',
             'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'}:
        return s + '_'

    return FILENAME_INVALID_RE.sub('-', s)


# Different printin' for different Pythons.
def unicodePrint(*args, **kwargs):
    unicodeType = str if sys.version_info[0] > 2 else unicode
    encoding = sys.stdout.encoding or 'utf-8'
    args = [
        arg.encode(encoding, 'replace').decode(encoding)
        if isinstance(arg, unicodeType) else arg
        for arg in args
    ]
    print(*args, **kwargs)


def lazyProperty(func):
    attrName = '_lazy_' + func.__name__
    @property
    @wraps(func)
    def lazyVersion(self):
        if not hasattr(self, attrName):
            setattr(self, attrName, func(self))
        return getattr(self, attrName)
    return lazyVersion


def getSoup(*args, **kwargs):
    r = requests.get(*args, **kwargs)
    return toSoup(r)

REMOVE_RE = re.compile(br"^</td>\s*$", re.MULTILINE)
BAD_AMPERSAND_RE = re.compile(br"&#([^0-9x]|x[^0-9A-Fa-f])")
def toSoup(r):
    content = r.content
    # Fix errors in khinsider's HTML.
    content = REMOVE_RE.sub(b'', content)
    content = BAD_AMPERSAND_RE.sub(b'&amp;#\1', content)

    # BS4 outputs unsuppressable error messages when it can't
    # decode the input bytes properly. This... suppresses them.
    with Silence():
        return BeautifulSoup(content, 'html.parser')


def getAppropriateFile(song, formatOrder):
    if formatOrder is None:
        return song.files[0]
    
    for extension in formatOrder:
        for file in song.files:
            if os.path.splitext(file.filename)[1][1:].lower() == extension:
                return file
    
    return song.files[0]


def friendlyDownloadFile(file, path, index, total, verbose=False):
    numberStr = "{}/{}".format(
        str(index).zfill(len(str(total))),
        str(total)
    )

    if file is None and verbose:
        print("Song {} is nonexistent (404: Not Found). Skipping over.".format(numberStr), file=sys.stderr)
        return False

    encoding = sys.getfilesystemencoding()
    # Fun(?) fact: on Python 2, sys.getfilesystemencoding returns 'mbcs' even
    # on Windows NT (1993!) and later where filenames are natively Unicode.
    encoding = 'utf-8' if encoding == 'mbcs' else 'utf-8'
    filename = file.filename.encode(encoding, 'replace').decode(encoding)

    byTheWay = ""
    if filename != file.filename:
        byTheWay = " (replaced characters not in the filesystem's \"{}\" encoding)".format(encoding)
    
    filename = to_valid_filename(filename)
    path = os.path.join(path, filename)
    
    if not os.path.exists(path):
        if verbose:
            unicodePrint("Downloading {}: {}{}...".format(numberStr, filename, byTheWay))
        for triesElapsed in range(3):
            if verbose and triesElapsed:
                unicodePrint("Couldn't download {}. Trying again...".format(filename), file=sys.stderr)
            try:
                file.download(path)
            except (requests.ConnectionError, requests.Timeout):
                pass
            else:
                break
        else:
            if verbose:
                unicodePrint("Couldn't download {}. Skipping over.".format(filename), file=sys.stderr)
            return False
    else:
        if verbose:
            unicodePrint("Skipping over {}: {}{}. Already exists.".format(numberStr, filename, byTheWay))

    return True


class KhinsiderError(Exception):
    pass

class NonexistentSongError(KhinsiderError):
    pass

class SoundtrackError(Exception):
    def __init__(self, soundtrack):
        self.soundtrack = soundtrack

class NonexistentSoundtrackError(SoundtrackError, ValueError):
    def __str__(self):
        ost = '"{}" '.format(self.soundtrack.id) if len(self.soundtrack.id) <= 80 else ""
        s = "The soundtrack {}does not exist.".format(ost)
        return s

class NonexistentFormatsError(SoundtrackError, ValueError):
    def __init__(self, soundtrack, requestedFormats):
        super(NonexistentFormatsError, self).__init__(soundtrack)
        self.requestedFormats = requestedFormats
    def __str__(self):
        ost = '"{}" '.format(self.soundtrack.id) if len(self.soundtrack.id) <= 80 else ""
        s = "The soundtrack {}is not available in the requested formats ({}).".format(
            ost,
            ", ".join('"{}"'.format(extension) for extension in self.requestedFormats))
        return s

class Soundtrack(object):
    """A KHInsider soundtrack. Initialize with a soundtrack ID.
    
    Properties:
    * id:     The soundtrack's unique ID, used at the end of its URL.
    * url:    The full URL of the soundtrack.
    * name:   The textual title of the soundtrack.
    * availableFormats: A list of the formats the soundtrack is available in.
    * songs:  A list of Song objects representing the songs in the soundtrack.
    * images: A list of File objects representing the images in the soundtrack.
    """

    def __init__(self, soundtrackId):
        self.id = soundtrackId
        self.url = urljoin(BASE_URL, 'game-soundtracks/album/' + self.id)
    
    def __repr__(self):
        return "<{}: {}>".format(self.__class__.__name__, self.id)

    def _isLoaded(self, property):
        return hasattr(self, '_lazy_' + property)

    @lazyProperty
    def _contentSoup(self):
        soup = getSoup(self.url)
        contentSoup = soup.find(id='pageContent')
        if contentSoup.find('p').string == "No such album":
            # The pageContent and p exist even if the soundtrack doesn't, so no
            # need for error handling here.
            raise NonexistentSoundtrackError(self)
        return contentSoup

    @lazyProperty
    def name(self):
        return next(self._contentSoup.find('h2').stripped_strings)

    @lazyProperty
    def availableFormats(self):
        table = self._contentSoup.find('table', id='songlist')
        header = table.find('tr')
        headings = [td.get_text(strip=True) for td in header(['th', 'td'])]
        formats = [s.lower() for s in headings if s not in {"", "Track", "Song Name", "Download", "Size"}]
        formats = formats or ['mp3']
        return formats

    @lazyProperty
    def songs(self):
        table = self._contentSoup.find('table', id='songlist')
        anchors = [tr.find('a') for tr in table('tr') if not tr.find('th')]
        urls = [a['href'] for a in anchors]
        songs = [Song(urljoin(self.url, url)) for url in urls]
        return songs
    
    @lazyProperty
    def images(self):
        table = self._contentSoup.find('table')
        if not table:
            # Currently, the table is always present, but if it's ever removed
            # for imageless albums, it should be handled gracefully.
            return []
        anchors = [a for a in table('a') if a.find('img')]
        urls = [a['href'] for a in anchors]
        images = [File(urljoin(self.url, url)) for url in urls]
        print(images)
        return images

    def download(self, path='', makeDirs=True, formatOrder=None, verbose=False):
        """Download the soundtrack to the directory specified by `path`!
        
        Create any directories that are missing if `makeDirs` is set to True.

        Set `formatOrder` to a list of file extensions to specify the order
        in which to prefer file formats. If set to ['flac', 'ogg', 'mp3'], for
        example, FLAC files will be downloaded if available - if not, Ogg
        files, and if those aren't available, MP3 files.
        
        Print progress along the way if `verbose` is set to True.

        Return True if all files were downloaded successfully, False if not.
        """
        path = os.path.join(getcwd(), path)
        path = os.path.abspath(os.path.realpath(path))
        if formatOrder:
            formatOrder = [extension.lower() for extension in formatOrder]
            if not set(self.availableFormats) & set(formatOrder):
                raise NonexistentFormatsError(self, formatOrder)

        if verbose and not self._isLoaded('songs'):
            print("Getting song list...")
        files = []
        for song in self.songs:
            try:
                files.append(getAppropriateFile(song, formatOrder))
            except NonexistentSongError:
                files.append(None)
        files.extend(self.images)
        totalFiles = len(files)

        if makeDirs and not os.path.isdir(path):
            os.makedirs(os.path.abspath(os.path.realpath(path)))

        success = True
        for fileNumber, file in enumerate(files, 1):
            if not friendlyDownloadFile(file, path, fileNumber, totalFiles, verbose):
                success = False
        
        return success


class Song(object):
    """A song on KHInsider.
    
    Properties:
    * url:   The full URL of the song page.
    * name:  The name of the song.
    * files: A list of the song's files - there may be several if the song
             is available in more than one format.
    """
    
    def __init__(self, url):
        self.url = url
    
    def __repr__(self):
        return "<{}: {}>".format(self.__class__.__name__, self.url)
    
    @lazyProperty
    def _soup(self):
        r = requests.get(self.url, timeout=10)
        if r.url.rsplit('/', 1)[-1] == '404':
            raise NonexistentSongError("Nonexistent song page (404).")
        return getSoup(self.url)

    @lazyProperty
    def name(self):
        return self._soup('p')[2]('b')[1].get_text()

    @lazyProperty
    def files(self):
        # The path used to be /ost/..., and was changed to
        # /soundtracks/... - but who knows? It might change back!
        anchors = self._soup('a', href=re.compile(r'^https?://[^/]+/(?:soundtracks|ost)/.+$'))
        return [File(urljoin(self.url, a['href'])) for a in anchors]


class File(object):
    """A file belonging to a soundtrack on KHInsider.
    
    Properties:
    * url:      The full URL of the file.
    * filename: The file's... filename. You got it.
    """

    def __init__(self, url):
        self.url = url

        try:
            url = str(url)
        except UnicodeError:
            # Python 2's quote and unquote work with bytestrings.
            url = url.encode('utf-8')
        # str('/') makes sure the string doesn't get
        # converted to a Unicode string on Python 2.
        self.filename = unquote(url.rsplit(str('/'), 1)[-1])
        try:
            # In Python 2, unquote doesn't handle escaped UTF-8 characters
            # automatically, so we gotta decode them manually from bytes.
            self.filename = self.filename.decode('utf-8')
        except AttributeError:
            pass

    def __repr__(self):
        return "<{}: {}>".format(self.__class__.__name__, self.url)
    
    def download(self, path):
        """Download the file to `path`."""
        response = requests.get(self.url, timeout=10)
        with open(path, 'wb') as outFile:
            outFile.write(response.content)


def download(soundtrackId, path='', makeDirs=True, formatOrder=None, verbose=False):
    """Download the soundtrack with the ID `soundtrackId`.
    See Soundtrack.download for more information.
    """
    soundtrack = Soundtrack(soundtrackId)
    soundtrack.name # To conistently always load the content in advance.
    path = to_valid_filename(soundtrack.name) if path is None else path
    if verbose:
        unicodePrint("Downloading to \"{}\".".format(path))
    return soundtrack.download(path, makeDirs, formatOrder, verbose)


class SearchError(KhinsiderError):
    pass


def search(term):
    """Return a tuple of two lists of Soundtrack objects for the search term
    `term`. The first tuple contains album name results, and the second song
    name results.
    """
    r = requests.get(urljoin(BASE_URL, 'search'), params={'search': term})
    path = urlsplit(r.url).path
    if path.split('/', 2)[1] == 'game-soundtracks':
        return [Soundtrack(path.rsplit('/', 1)[-1])]

    soup = toSoup(r)

    tables = soup('table', class_='albumList')
    if not tables:
        raise SearchError(soup.find('p').get_text(strip=True))

    soundtracks = [soundtracksInSearchTable(table) for table in tables]
    if len(soundtracks) == 1:
        if "song" in soup.find(id='pageContent').find('p').get_text():
            soundtracks.insert(0, [])
        else:
            soundtracks.append([])

    return soundtracks

def soundtracksInSearchTable(table):
    anchors = (tr('td')[1].find('a') for tr in table('tr')[1:])
    soundtrackParams = [(a['href'].split('/')[-1], a.get_text(strip=True)) for a in anchors]

    soundtracks = []
    for id, name in soundtrackParams:
        curSoundtrack = Soundtrack(id)
        curSoundtrack._lazy_name = name
        soundtracks.append(curSoundtrack)

    return soundtracks

def printSearchResults(searchResults, file=sys.stdout):
    padLen = max(len(x.id) for x in chain(*searchResults))
    s = ""
    hasPreviousList = False
    for heading, soundtracks in zip(("Album title results:", "Song name results:"), searchResults):
        if soundtracks:
            if hasPreviousList:
                s += "\n"
            s += heading + "\n"
            for soundtrack in soundtracks:
                s += "{} {}. {}\n".format(soundtrack.id, '.' * (padLen - len(soundtrack.id)), soundtrack.name)
            hasPreviousList = True
    unicodePrint(s, end="", file=file)

# --- And now for the execution. ---

if __name__ == '__main__':
    import argparse

    SCRIPT_NAME = os.path.split(sys.argv[0])[-1]
    REPORT_STR = ("If it isn't too much to ask, please report to "
                  "https://github.com/obskyr/khinsider/issues.")

    # Tiny details!
    class KindArgumentParser(argparse.ArgumentParser):
        def error(self, message):
            print("No soundtrack specified! As the first parameter, use the name the soundtrack uses in its URL.", file=sys.stderr)
            print("If you want to, you can also specify an output directory as the second parameter.", file=sys.stderr)
            print("You can also search for soundtracks by using your search term as parameter - as long as it's not an existing soundtrack.", file=sys.stderr)
            print(file=sys.stderr)
            print("For detailed help and more options, run \"{} --help\".".format(SCRIPT_NAME), file=sys.stderr)
            sys.exit(1)

    # More tiny details!
    class ProperHelpFormatter(argparse.RawTextHelpFormatter):
        def add_usage(self, usage, actions, groups, prefix=None):
            if prefix is None:
                prefix = 'Usage: '
            return super(ProperHelpFormatter, self).add_usage(usage, actions, groups, prefix)

    def doIt(): # Only in a function to be able to stop after errors, really.
        parser = KindArgumentParser(description="Download entire soundtracks from KHInsider.\n\n"
                                    "Examples:\n"
                                    "%(prog)s jumping-flash\n"
                                    "%(prog)s katamari-forever \"music{}Katamari Forever OST\"\n"
                                    "%(prog)s --search persona\n"
                                    "%(prog)s --format flac mother-3".format(os.sep),
                                    epilog="Hope you enjoy the script!",
                                    formatter_class=ProperHelpFormatter,
                                    add_help=False)
        
        try: # Even more tiny details!
            parser._positionals.title = "Positional arguments"
            parser._optionals.title = "Optional arguments"
        except AttributeError:
            pass

        parser.add_argument('soundtrack',
                            help="The ID of the soundtrack, used at the end of its URL (e.g. \"jumping-flash\").\n"
                            "May also simply be the URL of the soundtrack.\n"
                            "If it doesn't exist (or --search is specified, orrrr too many arguments are supplied),\n"
                            "all the positional arguments together are used as a search term.")
        parser.add_argument('outPath', metavar='download directory', nargs='?',
                            help="The directory to download the soundtrack to.\n"
                            "Defaults to creating a new directory with the soundtrack ID as its name.")
        parser.add_argument('trailingArguments', nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
        
        parser.add_argument('-h', '--help', action='help', default=argparse.SUPPRESS,
                            help="Show this help and exit.")
        parser.add_argument('-f', '--format', default=None, metavar="...",
                            help="The file format in which to download the soundtrack (e.g. \"flac\").\n"
                            "You can also specify a comma-separated list of which formats to try\n"
                            "(for example, \"flac,mp3\": download FLAC if available, otherwise MP3).")
        parser.add_argument('-s', '--search', action='store_true',
                            help="Always search, regardless of whether the specified soundtrack ID exists or not.")

        arguments = parser.parse_args()

        try:
            soundtrack = arguments.soundtrack.decode(sys.getfilesystemencoding())
        except AttributeError: # Python 3's argv is in Unicode
            soundtrack = arguments.soundtrack
        
        urlRe = re.compile(r"^https?://" + urlsplit(BASE_URL).netloc +
                           r"/game-soundtracks/album/(?P<soundtrack>[^/]+)$",
                           re.IGNORECASE)
        m = urlRe.match(soundtrack)
        soundtrack = m.group('soundtrack') if m is not None else soundtrack

        outPath = arguments.outPath # Can be None; handled in download().

        # I think this makes the most sense for people who aren't used to the
        # command line - this'll yield useful results even if you just type
        # in an entire soundtrack name as arguments without quotation marks.
        onlySearch = arguments.search or len(arguments.trailingArguments) > 1
        searchTerm = [soundtrack] + ([outPath] if arguments.outPath is not None else [])
        searchTerm += arguments.trailingArguments
        try:
            searchTerm = ' '.join(arg.decode(sys.getfilesystemencoding()) for arg in searchTerm)
        except AttributeError: # Python 3, again
            searchTerm = ' '.join(searchTerm)
        searchTerm = searchTerm.replace('-', ' ')

        formatOrder = arguments.format
        if formatOrder:
            formatOrder = re.split(r',\s*', formatOrder)
            formatOrder = [extension.lstrip('.').lower() for extension in formatOrder]

        try:
            if onlySearch:
                try:
                    searchResults = search(searchTerm)
                except SearchError as e:
                    if re.match(r"^Found [0-9]+ matching albums.$", e.args[0]):
                        errorStr = "Couldn't search! {}".format(REPORT_STR)
                    else:
                        errorStr = "Couldn't search. {}".format(e.args[0])
                    print(errorStr, file=sys.stderr)
                else:
                    if searchResults:
                        print("Soundtracks found (to download, "
                              "run \"{} soundtrack-name\")!\n".format(SCRIPT_NAME))
                        printSearchResults(searchResults)
                    else:
                        print("No soundtracks found.")
            else:
                try:
                    success = download(soundtrack, outPath, formatOrder=formatOrder, verbose=True)
                    if not success:
                        print("\nNot all files could be downloaded.", file=sys.stderr)
                        return 1
                except NonexistentSoundtrackError:
                    try:
                        searchResults = search(searchTerm)
                    except SearchError:
                        searchResults = None
                    print("The soundtrack \"{}\" does not seem to exist.".format(soundtrack), file=sys.stderr)

                    if searchResults: # aww yeah we gon' do some searchin'
                        print("\nThese exist, though:", file=sys.stderr)
                        printSearchResults(searchResults, file=sys.stderr)
                    elif searchResults is None:
                        print("A search for \"{}\" could not be performed either. "
                              "It may be too short.".format(searchTerm), file=sys.stderr)
                    
                    return 1
                except NonexistentFormatsError as e:
                    s = ("Format{} not available. "
                         "The soundtrack \"{}\" is only available in the ").format(
                        "" if len(formatOrder) == 1 else "s", soundtrack)
                    
                    formats = e.soundtrack.availableFormats
                    if len(formats) == 1:
                        s += "\"{}\" format.".format(formats[0])
                    else:
                        s += "{}{} and \"{}\" formats.".format(
                            ", ".join('"{}"'.format(extension) for extension in formats[:-1]),
                            "," if len(formats) > 2 else "",
                            formats[-1])
                    
                    print(s, file=sys.stderr)
                    
                    return 1
                except KeyboardInterrupt:
                    print("Stopped download.", file=sys.stderr)
                    return 1
        except (requests.ConnectionError, requests.Timeout):
            print("Could not connect to KHInsider.", file=sys.stderr)
            print("Make sure you have a working internet connection.", file=sys.stderr)
            return 1
        except Exception:
            print(file=sys.stderr)
            print("An unexpected error occurred! " + REPORT_STR,
                  file=sys.stderr)
            print("Attach the following error message:", file=sys.stderr)
            print(file=sys.stderr)
            raise
    
        return 0
    
    sys.exit(doIt())

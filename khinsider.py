#!/usr/bin/env python
# -*- coding: utf-8 -*-

# A script to download full soundtracks from KHInsider.

# __future__ import for forwards compatibility with Python 3
from __future__ import print_function
from __future__ import unicode_literals

import os
import re # For the syntax error in the HTML.
import sys
from functools import wraps

try:
    from urllib.parse import unquote, urljoin
except ImportError: # Python 2
    from urlparse import unquote, urljoin


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
    import imp # To check modules without importing them.

    # User-friendly name, import name, pip specification.
    requiredModules = [
        ['requests', 'requests', 'requests >= 2.0.0, < 3.0.0'],
        ['Beautiful Soup 4', 'bs4', 'beautifulsoup4 >= 4.4.0, < 5.0.0']
    ]

    def moduleExists(module):
        try:
            imp.find_module(module[1])
        except ImportError:
            return False
        return True
    def neededInstalls(requiredModules=requiredModules):
        uninstalledModules = []
        for module in requiredModules:
            if not moduleExists(module):
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
                    print ("You can also try to install the package manually "
                           "(pip install \"{}\")".format(module[2]),
                           file=sys.stderr)
                raise e
    def installRequiredModules(needed=None, verbose=True):
        needed = neededInstalls() if needed is None else needed
        installModules(neededInstalls(), verbose)

    needed = neededInstalls()
    if needed:
        try:
            imp.find_module('pip')
        except ImportError:
            print("You don't seem to have pip installed!")
            print("Get it from https://pip.readthedocs.org/en/latest/installing.html")
            sys.exit(1)
        else:
            # Needed to call pip the official way.
            import subprocess

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

    # Fix errors in khinsider's HTML
    removeRe = re.compile(br"^</td>\s*$", re.MULTILINE)
    
    # BS4 outputs unsuppressable error messages when it can't
    # decode the input bytes properly. This... suppresses them.
    with Silence():
        return BeautifulSoup(re.sub(removeRe, b'', r.content), 'html.parser')


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
    filename = FILENAME_INVALID_RE.sub('-', file.filename)
    path = os.path.join(path, filename)
    
    if not os.path.exists(path):
        if verbose:
            unicodePrint("Downloading {}: {}...".format(numberStr, filename))
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
            unicodePrint("Skipping over {}: {}. Already exists.".format(numberStr, filename))

    return True


class NonexistentSoundtrackError(Exception):
    def __init__(self, soundtrackId=""):
        super(NonexistentSoundtrackError, self).__init__(soundtrackId)
        self.soundtrackId = soundtrackId
    def __str__(self):
        if not self.soundtrackId or len(self.soundtrackId) > 80:
            s = "The soundtrack does not exist."
        else:
            s = "The soundtrack \"{ost}\" does not exist.".format(ost=self.soundtrackId)
        return s


class Soundtrack(object):
    """A KHInsider soundtrack. Initialize with a soundtrack ID.
    
    Properties:
    * id:     The soundtrack's unique ID, used at the end of its URL.
    * url:    The full URL of the soundtrack.
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
        contentSoup = soup.find(id='EchoTopic')
        if contentSoup.find('p').string == "No such album":
            # The EchoTopic and p exist even if the soundtrack doesn't, so no
            # need for error handling here.
            raise NonexistentSoundtrackError(self.id)
        return contentSoup

    @lazyProperty
    def availableFormats(self):
        table = self._contentSoup.find('table')
        header = table.find('tr')
        headings = [td.get_text(strip=True) for td in header(['th', 'td'])]
        formats = [s.lower() for s in headings if s not in {"Track", "Song Name", "Download", "Size"}]
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
        anchors = self._contentSoup('p')[1]('a')
        urls = [a['href'] for a in anchors]
        images = [File(urljoin(self.url, url)) for url in urls]
        return images

    def download(self, path='', makeDirs=True, formatOrder=None, verbose=False):
        """Download the soundtrack to the directory specified by `path`!
        
        Create any directories that are missing if `makeDirs` is set to True.

        Set `formatOrder` to a list of file extensions to specify the order
        in which to prefer file formats. If set to ['flac', 'mp3'], for
        example, FLAC files will be downloaded if available, and otherwise MP3.
        
        Print progress along the way if `verbose` is set to True.

        Return True if all files were downloaded successfully, False if not.
        """
        path = os.path.join(os.getcwd(), path)
        path = os.path.abspath(os.path.realpath(path))
        if formatOrder:
            formatOrder = [extension.lower() for extension in formatOrder]
            if not set(self.availableFormats) & set(formatOrder):
                if verbose:
                    print("The soundtrack \"{}\" does not seem to be available in {}.".format(
                          self.id,
                          "that format" if len(formatOrder) == 1 else "any of those formats"))
                return

        if verbose and not self._isLoaded('songs'):
            print("Getting song list...")
        files = []
        for song in self.songs:
            files.append(getAppropriateFile(song, formatOrder))
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
        return getSoup(self.url)

    @lazyProperty
    def name(self):
        return self._soup('p')[2]('b')[1].get_text()

    @lazyProperty
    def files(self):
        anchors = [p.find('a') for p in self._soup('p', string=re.compile(r'^\s*Click here to download'))]
        return [File(urljoin(self.url, a['href'])) for a in anchors]


class File(object):
    """A file belonging to a soundtrack on KHInsider.
    
    Properties:
    * url:      The full URL of the file.
    * filename: The file's... filename. You got it.
    """

    def __init__(self, url):
        self.url = url
        self.filename = unquote(url.rsplit('/', 1)[-1])

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
    return Soundtrack(soundtrackId).download(path, makeDirs, formatOrder, verbose)


def search(term):
    """Return a list of Soundtrack objects for the search term `term`."""
    soup = getSoup(urljoin(BASE_URL, 'search'), params={'search': term})
    anchors = soup('p')[1]('a')
    soundtrackIds = [a['href'].split('/')[-1] for a in anchors]

    return [Soundtrack(id) for id in soundtrackIds]

# --- And now for the execution. ---

if __name__ == '__main__':
    import argparse

    SCRIPT_NAME = os.path.split(sys.argv[0])[-1]

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

        outPath = arguments.outPath if arguments.outPath is not None else soundtrack

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
                searchResults = search(searchTerm)
                if searchResults:
                    print("Soundtracks found (to download, "
                          "run \"{} soundtrack-name\"):".format(SCRIPT_NAME))
                    for soundtrack in searchResults:
                        print(soundtrack.id)
                else:
                    print("No soundtracks found.")
            else:
                try:
                    success = download(soundtrack, outPath, formatOrder=formatOrder, verbose=True)
                    if not success:
                        print("\nNot all files could be downloaded.", file=sys.stderr)
                        return 1
                except NonexistentSoundtrackError:
                    searchResults = search(searchTerm)
                    print("\nThe soundtrack \"{}\" does not seem to exist.".format(soundtrack), file=sys.stderr)

                    if searchResults: # aww yeah we gon' do some searchin'
                        print(file=sys.stderr)
                        print("These exist, though:", file=sys.stderr)
                        for soundtrack in searchResults:
                            print(soundtrack.id, file=sys.stderr)
                    
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
            print("An unexpected error occurred! "
                  "If it isn't too much to ask, please report to "
                  "https://github.com/obskyr/khinsider/issues.",
                  file=sys.stderr)
            print("Attach the following error message:", file=sys.stderr)
            print(file=sys.stderr)
            raise
    
        return 0
    
    sys.exit(doIt())

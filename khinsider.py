#!/usr/bin/env python
# -*- coding: utf-8 -*-

# A script to download full soundtracks from KHInsider.

# __future__ import for forwards compatibility with Python 3
from __future__ import print_function
from __future__ import unicode_literals

# --- Install prerequisites---

# (This section in `if __name__ == '__main__':` is entirely unrelated to the
# rest of the module, and doesn't even run if the module isn't run by itself.)

if __name__ == '__main__':
    import imp # To check modules without importing them.

    requiredModules = [
        ['requests', 'requests'], # Some modules don't have the same pypi name as
        ['bs4', 'beautifulsoup4'] # import name. Therefore, two entries per module.
    ]

    def moduleExists(module):
        try:
            imp.find_module(module[0])
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
        pip.main(['install', '--quiet', package])
    def installModules(modules, verbose=True):
        for module in modules:
            if verbose:
                print("Installing {}...".format(module[1]))
            install(module[1])
    def installRequiredModules(needed=None, verbose=True):
        needed = neededInstalls() if needed is None else needed
        installModules(neededInstalls(), verbose)

    needed = neededInstalls()
    if needed: # Only import pip if modules are actually missing.
        try:
            import pip # To install modules if they're not there.
        except ImportError:
            print("You don't seem to have pip installed!")
            print("Get it from https://pip.readthedocs.org/en/latest/installing.html")

    installRequiredModules(needed)

# ------

import requests
from bs4 import BeautifulSoup

import sys
import os

import re # For the syntax error in the HTML.

def getSoup(*args, **kwargs):
    r = requests.get(*args, **kwargs)

    # --- Fix errors in khinsider's HTML
    removeRe = re.compile(r"^</td>\s*$", re.MULTILINE)
    # ---
    
    return BeautifulSoup(re.sub(removeRe, b'', r.content), 'html.parser')

class NonexistentSoundtrackError(Exception):
    def __init__(self, ostName=""):
        super(NonexistentSoundtrackError, self).__init__(ostName)
        self.ostName = ostName
    def __str__(self):
        if not self.ostName or len(self.ostName) > 80:
            s = "The soundtrack does not exist."
        else:
            s = "The soundtrack \"{ost}\" does not exist.".format(ost=self.ostName)
        return s

def getOstSoup(ostName):
    url = "http://downloads.khinsider.com/game-soundtracks/album/" + ostName
    soup = getSoup(url)
    if soup.find(id='EchoTopic').find('p').string == "No such album":
        # The EchoTopic and p exist even if the soundtrack doesn't, so no
        # need for error handling here.
        raise NonexistentSoundtrackError(ostName)
    return soup

def getSongPageUrlList(ostName):
    soup = getOstSoup(ostName)
    table = soup('table')[5] # This might change if the page layout ever changes.
    trs = table('tr')[1:] # The first tr is a header.
    anchors = [tr('td')[1].find('a') for tr in trs]
    urls = [a['href'] for a in anchors]
    return urls

def getSongList(ostName):
    """Get a list of songs from the OST with ID `ostName`."""
    # Each entry is in the format [name, url].
    songPageUrls = getSongPageUrlList(ostName)
    songList = [getSongInfo(url) for url in songPageUrls]
    return songList

def getSongInfo(songPageUrl):
    """Get the file name and URL of the song at `songPageUrl`. Return a list of [songName, songUrl]."""
    info = []
    soup = getSoup(songPageUrl)
    info.append(getSongName(soup))
    info.append(getSongUrl(soup))
    return info
def getSongName(songPage):
    name = songPage('p')[2]('b')[1].get_text()
    return name
def getSongUrl(songPage):
    url = songPage('p')[3].find('a')['href'] # Download link.
    return url

def download(ostName, path="", verbose=False):
    """Download an OST with the ID `ostName` to `path`."""
    if verbose:
        print("Getting song list...")
    songInfos = getSongList(ostName)
    for name, url in songInfos:
        downloadSong(url, path, name, verbose=verbose)
def downloadSong(songUrl, path, name="song", numTries=3, verbose=False):
    """Download a single song at `songUrl` to `path`."""
    if verbose:
        print("Downloading {}...".format(name).encode(
            sys.stdout.encoding, 'replace'))

    tries = 0
    while tries < numTries:
        try:
            if tries and verbose:
                print("Couldn't download {}. Trying again...".format(
                    name).encode(sys.stdout.encoding, 'replace'))
            song = requests.get(songUrl)
            break
        except requests.ConnectionError:
            tries += 1
    else:
        if verbose:
            print("Couldn't download {}. Skipping over.".format(
                name).encode(sys.stdout.encoding, 'replace'))
        return

    try:
        with open(os.path.join(path, name), 'wb') as outfile:
            outfile.write(song.content)
    except IOError:
        if verbose:
            print("Couldn't save {}. Check your permissions.".format(
                name).encode(sys.stdout.encoding, 'replace'))

def search(term):
    """Return a list of OST IDs for the search term `term`."""
    soup = getSoup("http://downloads.khinsider.com/search", params={'search': term})
    anchors = soup('p')[1]('a')
    ostNames = [a['href'].split('/')[-1] for a in anchors]

    return ostNames

# --- And now for the execution. ---

if __name__ == '__main__':
    def doIt(): # Only in a function to be able to stop after errors, really.
        try:
            ostName = sys.argv[1].decode(sys.getfilesystemencoding())
        except AttributeError: # Python 3's argv is in Unicode
            ostName = sys.argv[1]
        except IndexError:
            print("No soundtrack specified! As the first parameter, use the name the soundtrack uses in its URL.")
            print("If you want to, you can also specify an output directory as the second parameter.")
            print("You can also search for soundtracks by using your search term as parameter - as long as it's not an existing soundtrack.")
            return
        try:
            outPath = sys.argv[2]
        except IndexError:
            outPath = ostName

        madeDir = False
        if not os.path.isdir(outPath):
            os.mkdir(outPath)
            madeDir = True

        try:
            download(ostName, outPath, verbose=True)
        except NonexistentSoundtrackError:
            try:
                searchTerm = ' '.join([a.decode(sys.getfilesystemencoding())
                    for a in sys.argv[1:]
                ]).replace('-', ' ')
            except AttributeError: # Python 3, again
                searchTerm = ' '.join(sys.argv[1:]).replace('-', ' ')
            
            searchResults = search(searchTerm)
            # I don't know, maybe in some crazy circumstance the encoding for
            # arguments doesn't match stdout's encoding.
            print("\nThe soundtrack \"{}\" does not seem to exist.".format(
                ostName).encode(sys.stdout.encoding, 'replace'))

            if searchResults: # aww yeah we gon' do some searchin'
                print()
                print("These exist, though:")
                for name in searchResults:
                    print(name)

            if madeDir:
                os.rmdir(outPath)
            return
        except requests.ConnectionError:
            print("Could not connect to KHInsider.")
            print("Make sure you have a working internet connection.")

            if madeDir:
                os.rmdir(outPath)
            return

    doIt()

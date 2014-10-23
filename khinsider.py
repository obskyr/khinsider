#!/usr/bin/env python
# -*- coding: utf-8 -*-

# A script to download full soundtracks from khinsider.

# --- Install prerequisites---

# (This section in `if __name__ == '__main__':` is entirely unrelated to the
# rest of the module, and doesn't even run if the module isn't run by itself.)

if __name__ == '__main__':
    import imp # To check modules without importing them.

    requiredModules = [
        ['requests', 'requests'], # Some modules don't have the same pypi name as
        ['bs4', 'beautifulsoup4']  # import name. Therefore, two entries per module.
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
                print "Installing " + module[1] + "..."
            install(module[1])
    def installRequiredModules(needed=None, verbose=True):
        needed = neededInstalls() if needed is None else needed
        installModules(neededInstalls(), verbose)

    needed = neededInstalls()
    if needed: # Only import pip if modules are actually missing.
        try:
            import pip # To install modules if they're not there.
        except ImportError:
            print "You don't seem to have pip installed!"
            print "Get it from https://pip.readthedocs.org/en/latest/installing.html"

    installRequiredModules(needed)

# ------

import requests
from bs4 import BeautifulSoup

import sys
import os

import re # For the syntax error in the HTML.

def getSoup(url):
    r = requests.get(url)

    # --- Fix errors in khinsider's HTML
    removeRe = re.compile(r"^</td>\s*$", re.MULTILINE)
    # ---

    return BeautifulSoup(re.sub(removeRe, '', r.text))

def getOstSoup(ostName):
    url = "http://downloads.khinsider.com/game-soundtracks/album/" + ostName
    return getSoup(url)

def getSongPageUrlList(ostName):
    soup = getOstSoup(ostName)
    table = soup('table')[5] # This might change if the page layout ever changes.
    trs = table('tr')[1:] # The first tr is a header.
    anchors = [tr('td')[1].find('a') for tr in trs]
    urls = [a['href'] for a in anchors]
    return urls

def getSongList(ostName):
    # Each entry is in the format [name, url].
    songPageUrls = getSongPageUrlList(ostName)
    songList = [getSongInfo(url) for url in songPageUrls]
    return songList

def getSongInfo(songPageUrl):
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
    songInfos = getSongList(ostName)
    for name, url in songInfos:
        downloadSong(url, path, name, verbose)
def downloadSong(songUrl, path, name, verbose=False):
    if verbose:
        print "Downloading " + name + "..."

    try:
        song = requests.get(songUrl)
    except ConnectionError:
        if verbose:
            print "Couldn't download " + name + "."
    try:
        with open(os.path.join(path, name), 'wb') as outfile:
            outfile.write(song.content)
    except IOError:
        if verbose:
            print "Couldn't save " + name + ". Check your permissions."

def search(term):
    r = requests.get("http://downloads.khinsider.com/search", params={'search': term})
    soup = BeautifulSoup(r.text)
    anchors = soup('p')[1]('a')
    ostNames = [a['href'].split('/')[-1] for a in anchors]

    return ostNames

# --- And now for the execution. ---

if __name__ == '__main__':
    def doIt(): # Only in a function to be able to stop after errors, really.
        try:
            ostName = sys.argv[1]
        except IndexError:
            print "No soundtrack specified! As the first parameter, use the name the soundtrack uses in its URL."
            return

        if not os.path.isdir(ostName):
            os.mkdir(ostName)
            madeDir = True

        try:
            download(ostName, ostName, verbose=True)
        except IndexError:
            searchResults = search(ostName)
            print "The soundtrack \"" + ostName + "\" does not seem to exist."

            if searchResults: # aww yeah we gon' do some searchin'
                print
                print "These exist, though:"
                for name in searchResults:
                    print name

            if madeDir:
                os.rmdir(ostName)
            return

    doIt()

#!/usr/bin/env python

from distutils.core import setup

setup(name='khinsider',
      version='1.0',
      description='A script for khinsider mass downloads. Get video game soundtracks quickly and easily! Also a Python interface.',
      author='Samuel R. M.',
      author_email='powpowd@gmail.com',
      url='https://github.com/obskyr/khinsider/',
      py_modules=['khinsider'],
      scripts=['khinsider.py'],
      requires=['requests (>= 2.17.3)', 'beautifulsoup4 (>= 4.6.0)']
      )

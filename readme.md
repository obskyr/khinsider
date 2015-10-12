#khinsider.py
`khinsider.py` is a [Python](https://www.python.org/) interface for getting [khinsider](http://downloads.khinsider.com/) soundtracks. It makes khinsider mass downloads a breeze. It's easy to use - check it!

From command line:
```
khinsider.py jumping-flash
```

As an import:
```python
import khinsider
khinsider.download('jumping-flash')
# And bam, you've got the Jumping Flash soundtrack!
```

Carefully put together by [@obskyr](http://twitter.com/obskyr)!

## Usage
Just run `khinsider.py` from the command line with the sole parameter being the soundtrack you want to download. Easy!

If you want, you can also add another parameter as the output folder, but that's optional.

If you don't want to go to the actual site to look for soundtracks, you can also just type a search term as the first parameter(s), and provided it's not a valid soundtrack, `khinsider.py` will give you a list of soundtracks matching that term.

You're going to need [Python](https://www.python.org/downloads/) (2 or 3 - `khinsider.py` works with both), so install that (and [add it to your path](http://superuser.com/a/143121)) if you haven't already.

You will also need to have [pip](https://pip.readthedocs.org/en/latest/installing.html) installed (download `get-pip.py` and run it) if you don't already have [requests](https://pypi.python.org/pypi/requests) and [Beautiful Soup 4](https://pypi.python.org/pypi/beautifulsoup4). The first time `khinsider.py` runs, it will install these two for you.

Download for `khinsider.py` is on the right of this GitHub page - click "Download ZIP"!

## As a module
`khinsider.py` requires two non-standard modules: [requests](https://pypi.python.org/pypi/requests) and [beautifulsoup4](https://pypi.python.org/pypi/beautifulsoup4). Just run a `pip install` on them (with [pip](https://pip.readthedocs.org/en/latest/installing.html)), or just run `khinsider.py` on its own once and it'll install them for you.

Here are the functions you will be using:

###`khinsider.download(soundtrackName[, path="", verbose=False])`
Download the soundtrack `soundtrackName`. This should be the name the soundtrack uses at the end of its album URL.

If `path` is specified, the soundtrack files will be downloaded to that path. If `verbose` is `True`, it will print progress as it is downloading.

###`khinsider.search(term)`
Search khinsider for `term`. Return a list of soundtrack IDs matching the search term.

# Talk to me!
You can easily get to me by:

* [@obskyr](http://twitter.com/obskyr/) on Twitter!
* [E-mail](mailto:powpowd@gmail.com) me!

I'd love to hear it if you like `khinsider.py`! If there's a problem, or you'd like a new feature, submit an issue here on GitHub.

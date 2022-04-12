# khinsider.py

`khinsider.py` is a [Python](https://www.python.org/) interface and script for getting [khinsider](http://downloads.khinsider.com/) soundtracks. It makes khinsider mass downloads a breeze. It's easy to use - check it!

From the command line (i.e. regular usage):

```cmd
khinsider.py jumping-flash
```

As an import (for when you're programming):

```python
import khinsider
khinsider.download('jumping-flash')
# And bam, you've got the Jumping Flash soundtrack!
```

For anime music, [check out `thehylia.py`](https://github.com/obskyr/thehylia).

Carefully put together by [@obskyr](http://twitter.com/obskyr)!

### **[Download it here!](https://github.com/obskyr/khinsider/archive/master.zip)**

## Usage

Just run `khinsider.py` from the command line with the sole parameter being the soundtrack you want to download. You can either use the soundtrack's ID, or simply copy its entire URL. Easy!

If you want, you can also add another parameter as the output folder, but that's optional.

You can also download other file formats (if available), like FLAC or OGG, as following:

```cmd
khinsider.py --format flac mother-3
```

If you don't want to go to the actual site to look for soundtracks, you can also just type a search term as the first parameter(s), and provided it's not a valid soundtrack, `khinsider.py` will give you a list of soundtracks matching that term.

You're going to need [Python](https://www.python.org/downloads/) (if you don't know which version to get, choose the latest version of Python 3 - `khinsider.py` works with both 2 and 3), so install that (and [add it to your path](http://superuser.com/a/143121)) if you haven't already.

You will also need to have [pip](https://pip.readthedocs.org/en/latest/installing.html) installed (if you have Python 3, it is most likely already installed - otherwise, download `get-pip.py` and run it) if you don't already have [requests](https://pypi.python.org/pypi/requests) and [Beautiful Soup 4](https://pypi.python.org/pypi/beautifulsoup4). The first time `khinsider.py` runs, it will install these two for you.

For more detailed information, try running `khinsider.py --help`!

## As a module

`khinsider.py` requires two non-standard modules: [requests](https://pypi.python.org/pypi/requests) and [beautifulsoup4](https://pypi.python.org/pypi/beautifulsoup4). Just run a `pip install` on them (with [pip](https://pip.readthedocs.org/en/latest/installing.html)), or just run `khinsider.py` on its own once and it'll install them for you.

Here are the main functions you will be using:

### `khinsider.download(soundtrackName[, path="", makeDirs=True, formatOrder=None, verbose=False])`

Download the soundtrack `soundtrackName`. This should be the name the soundtrack uses at the end of its album URL.

If `path` is specified, the soundtrack files will be downloaded to the directory that path points to.

If `makeDirs` is `True`, the directory will be created if it doesn't exist.

You can specify `formatOrder` to download soundtracks in specific formats. `formatOrder=['flac', 'mp3']`, for example, will download FLACs if available, and MP3s if not.

If `verbose` is `True`, it will print progress as it is downloading.

### `khinsider.search(term)`

Search khinsider for `term`. Return a list of `Soundtrack`s matching the search term. You can then access `soundtrack.id` or `soundtrack.url`.

### More

There's a lot more detail to the API - more than would be sensible to write here. If you want to use `khinsider.py` as a module in a more advanced capacity, have a look at the `Soundtrack`, `Song`, and `File` objects in the source code! They're documented properly there for your reading pleasure.

# Talk to me!

You can easily get to me in these ways:

* [@obskyr](http://twitter.com/obskyr/) on Twitter!
* [E-mail](mailto:powpowd@gmail.com) me!

I'd love to hear it if you like `khinsider.py`! If there's a problem, or you'd like a new feature, submit an issue here on GitHub.

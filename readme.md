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

## Usage
Just run `khinsider.py` from the command line with the sole parameter being the soundtrack you want to download. Easy!

You're going to need [Python 2](https://www.python.org/downloads/), so install that (and [add it to your path](http://superuser.com/a/143121)) if you haven't already.

## As a module
Here are the functions you will be using:

###`khinsider.download(soundtrackName[, path="", verbose=False])`
Download the soundtrack `soundtrackName`. This should be the name the soundtrack uses at the end of its album URL.

If `path` is specified, the soundtrack files will be downloaded to that path. If `verbose` is `True`, it will print progress as it is downloading.

###`khinsider.search(term)`
Search khinsider for `term`. Return a list of soundtrack IDs matching the search term.

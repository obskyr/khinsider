@echo off
cls
ECHO.
echo Welcome to the KHInsider Downloader Helper! Made by CVFD so he can scrape audio (windows only)
echo You need Python 3 in your path for this to work. Original made by obskyr!
ECHO.
echo -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
ECHO.
echo What's the ID for your album? It comes at the end of the URL, so it would be "sonic-mania-plus-original-soundtrack" for
echo downloads.khinsider.com/game-soundtracks/album/sonic-mania-plus-original-soundtrack
ECHO.
set /P id="   "
cls
ECHO.
echo Welcome to the KHInsider Downloader Helper! Made by CVFD so he can scrape audio (windows only)
echo You need Python 3 in your path for this to work. Original made by obskyr!
ECHO.
echo -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
ECHO.
echo What format do you want to download in? E.G: MP3, FLAC (flac is usually available, otherwise use mp3)
ECHO.
set /P format="   "
cls
ECHO.
echo Welcome to the KHInsider Downloader Helper! Made by CVFD so he can scrape audio (windows only)
echo You need Python 3 in your path for this to work. Original made by obskyr!
ECHO.
echo -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
ECHO.
echo Thanks for the information! The script will now start proccessing your request, watch and enjoy!
ECHO.
ECHO.
python khinsider.py --format %format% %id%
cls
ECHO.
echo Welcome to the KHInsider Downloader Helper! Made by CVFD so he can scrape audio (windows only)
echo You need Python 3 in your path for this to work. Original made by obskyr!
ECHO.
echo -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
ECHO.
echo The program has finished, thanks for using it! It will exit in 5 seconds.
PING localhost -n 6 >NUL
exit
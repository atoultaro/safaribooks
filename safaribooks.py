#!/usr/bin/env python3
# coding: utf-8
import re
import os
import sys
import json
import shutil
import pathlib
import posixpath
import getpass
import time
import logging
import argparse
import requests
import traceback
from html import escape
from random import random
from lxml import html, etree
from multiprocessing import Process, Queue, Value
from urllib.parse import urljoin, urlparse, parse_qs, quote_plus, unquote


PATH = os.path.dirname(os.path.realpath(__file__))
COOKIES_FILE = os.path.join(PATH, "cookies.json")

ORLY_BASE_HOST = "oreilly.com"  # PLEASE INSERT URL HERE

SAFARI_BASE_HOST = "learning." + ORLY_BASE_HOST
API_ORIGIN_HOST = "api." + ORLY_BASE_HOST

ORLY_BASE_URL = "https://www." + ORLY_BASE_HOST
SAFARI_BASE_URL = "https://" + SAFARI_BASE_HOST
API_ORIGIN_URL = "https://" + API_ORIGIN_HOST
PROFILE_URL = SAFARI_BASE_URL + "/profile/"

# DEBUG
USE_PROXY = False
PROXIES = {"https": "https://127.0.0.1:8080"}


class Display:
    BASE_FORMAT = logging.Formatter(
        fmt="[%(asctime)s] %(message)s",
        datefmt="%d/%b/%Y %H:%M:%S"
    )

    SH_DEFAULT = "\033[0m" if "win" not in sys.platform else ""  # TODO: colors for Windows
    SH_YELLOW = "\033[33m" if "win" not in sys.platform else ""
    SH_BG_RED = "\033[41m" if "win" not in sys.platform else ""
    SH_BG_YELLOW = "\033[43m" if "win" not in sys.platform else ""

    def __init__(self, log_file):
        self.output_dir = ""
        self.output_dir_set = False
        self.log_file = os.path.join(PATH, log_file)

        self.logger = logging.getLogger("SafariBooks")
        self.logger.setLevel(logging.INFO)
        logs_handler = logging.FileHandler(filename=self.log_file)
        logs_handler.setFormatter(self.BASE_FORMAT)
        logs_handler.setLevel(logging.INFO)
        self.logger.addHandler(logs_handler)

        self.columns, _ = shutil.get_terminal_size()

        self.logger.info("** Welcome to SafariBooks! **")

        self.book_ad_info = False
        self.css_ad_info = Value("i", 0)
        self.images_ad_info = Value("i", 0)
        self.last_request = (None,)
        self.in_error = False

        self.state_status = Value("i", 0)
        sys.excepthook = self.unhandled_exception

    def set_output_dir(self, output_dir):
        self.info("Output directory:\n    %s" % output_dir)
        self.output_dir = output_dir
        self.output_dir_set = True

    def unregister(self):
        self.logger.handlers[0].close()
        sys.excepthook = sys.__excepthook__

    def log(self, message):
        try:
            self.logger.info(str(message, "utf-8", "replace"))

        except (UnicodeDecodeError, Exception):
            self.logger.info(message)

    def out(self, put):
        pattern = "\r{!s}\r{!s}\n"
        try:
            s = pattern.format(" " * self.columns, str(put, "utf-8", "replace"))

        except TypeError:
            s = pattern.format(" " * self.columns, put)

        sys.stdout.write(s)

    def info(self, message, state=False):
        self.log(message)
        output = (self.SH_YELLOW + "[*]" + self.SH_DEFAULT if not state else
                  self.SH_BG_YELLOW + "[-]" + self.SH_DEFAULT) + " %s" % message
        self.out(output)

    def error(self, error):
        if not self.in_error:
            self.in_error = True

        self.log(error)
        output = self.SH_BG_RED + "[#]" + self.SH_DEFAULT + " %s" % error
        self.out(output)

    def exit(self, error):
        self.error(str(error))

        if self.output_dir_set:
            output = (self.SH_YELLOW + "[+]" + self.SH_DEFAULT +
                      " Please delete the output directory '" + self.output_dir + "'"
                      " and restart the program.")
            self.out(output)

        output = self.SH_BG_RED + "[!]" + self.SH_DEFAULT + " Aborting..."
        self.out(output)

        self.save_last_request()
        sys.exit(1)

    def unhandled_exception(self, _, o, tb):
        self.log("".join(traceback.format_tb(tb)))
        self.exit("Unhandled Exception: %s (type: %s)" % (o, o.__class__.__name__))

    def save_last_request(self):
        if any(self.last_request):
            self.log("Last request done:\n\tURL: {0}\n\tDATA: {1}\n\tOTHERS: {2}\n\n\t{3}\n{4}\n\n{5}\n"
                     .format(*self.last_request))

    def intro(self):
        output = self.SH_YELLOW + (r"""
       ____     ___         _
      / __/__ _/ _/__ _____(_)
     _\ \/ _ `/ _/ _ `/ __/ /
    /___/\_,_/_/ \_,_/_/ /_/
      / _ )___  ___  / /__ ___
     / _  / _ \/ _ \/  '_/(_-<
    /____/\___/\___/_/\_\/___/
""" if random() > 0.5 else r"""
 ██████╗     ██████╗ ██╗  ██╗   ██╗██████╗
██╔═══██╗    ██╔══██╗██║  ╚██╗ ██╔╝╚════██╗
██║   ██║    ██████╔╝██║   ╚████╔╝   ▄███╔╝
██║   ██║    ██╔══██╗██║    ╚██╔╝    ▀▀══╝
╚██████╔╝    ██║  ██║███████╗██║     ██╗
 ╚═════╝     ╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝
""") + self.SH_DEFAULT
        output += "\n" + "~" * (self.columns // 2)

        self.out(output)

    def parse_description(self, desc):
        if not desc:
            return "n/d"

        try:
            return SafariBooks._html_from_source(desc).text_content()

        except (html.etree.ParseError, html.etree.ParserError, ValueError) as e:
            self.log("Error parsing the description: %s" % e)
            return "n/d"

    def book_info(self, info):
        description = self.parse_description(info.get("description", None)).replace("\n", " ")
        for t in [
            ("Title", info.get("title", "")), ("Authors", ", ".join(aut.get("name", "") for aut in info.get("authors", []))),
            ("Identifier", info.get("identifier", "")), ("ISBN", info.get("isbn", "")),
            ("Publishers", ", ".join(pub.get("name", "") for pub in info.get("publishers", []))),
            ("Rights", info.get("rights", "")),
            ("Description", description[:500] + "..." if len(description) >= 500 else description),
            ("Release Date", info.get("issued", "")),
            ("URL", info.get("web_url", ""))
        ]:
            self.info("{0}{1}{2}: {3}".format(self.SH_YELLOW, t[0], self.SH_DEFAULT, t[1]), True)

    def state(self, origin, done):
        progress = int(done * 100 / origin)
        bar = int(progress * (self.columns - 11) / 100)
        if self.state_status.value < progress:
            self.state_status.value = progress
            sys.stdout.write(
                "\r    " + self.SH_BG_YELLOW + "[" + ("#" * bar).ljust(self.columns - 11, "-") + "]" +
                self.SH_DEFAULT + ("%4s" % progress) + "%" + ("\n" if progress == 100 else "")
            )

    def done(self, epub_file):
        self.info("Done: %s\n\n" % epub_file +
                  "    If you like it, please * this project on GitHub to make it known:\n"
                  "        https://github.com/lorenzodifuccia/safaribooks\n"
                  "    e don't forget to renew your Safari Books Online subscription:\n"
                  "        " + SAFARI_BASE_URL + "\n\n" +
                  self.SH_BG_RED + "[!]" + self.SH_DEFAULT + " Bye!!")

    @staticmethod
    def api_error(response):
        message = "API: "
        if "detail" in response and "Not found" in response["detail"]:
            message += "book's not present in Safari Books Online.\n" \
                       "    The book identifier is the digits that you can find in the URL:\n" \
                       "    `" + SAFARI_BASE_URL + "/library/view/book-name/XXXXXXXXXXXXX/`"

        else:
            os.remove(COOKIES_FILE)
            message += "Out-of-Session%s.\n" % (" (%s)" % response["detail"]) if "detail" in response else "" + \
                       Display.SH_YELLOW + "[+]" + Display.SH_DEFAULT + \
                       " Use the `--cred` or `--login` options in order to perform the auth login to Safari."

        return message


class WinQueue(list):  # TODO: error while use `process` in Windows: can't pickle _thread.RLock objects
    def put(self, el):
        self.append(el)

    def qsize(self):
        return self.__len__()


class SafariBooks:
    LOGIN_URL = ORLY_BASE_URL + "/member/auth/login/"
    LOGIN_ENTRY_URL = SAFARI_BASE_URL + "/login/unified/?next=/home/"

    API_TEMPLATE = SAFARI_BASE_URL + "/api/v1/book/{0}/"
    SEARCH_V2_URLS = [
        SAFARI_BASE_URL + "/api/v2/search/",
        "https://falcon.platform.gcp.oreilly.com/api/v2/search/"
    ]
    EPUB_FILES_V2_TEMPLATE = SAFARI_BASE_URL + "/api/v2/epubs/{0}/files"

    BASE_01_HTML = "<!DOCTYPE html>\n" \
                   "<html lang=\"en\" xml:lang=\"en\" xmlns=\"http://www.w3.org/1999/xhtml\"" \
                   " xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"" \
                   " xsi:schemaLocation=\"http://www.w3.org/2002/06/xhtml2/" \
                   " http://www.w3.org/MarkUp/SCHEMA/xhtml2.xsd\"" \
                   " xmlns:epub=\"http://www.idpf.org/2007/ops\">\n" \
                   "<head>\n" \
                   "{0}\n" \
                   "<style type=\"text/css\">" \
                   "body{{margin:1em;background-color:transparent!important;}}" \
                   "#sbo-rt-content *{{text-indent:0pt!important;}}#sbo-rt-content .bq{{margin-right:1em!important;}}"

    KINDLE_HTML = "#sbo-rt-content *{{word-wrap:break-word!important;" \
                  "word-break:break-word!important;}}#sbo-rt-content table,#sbo-rt-content pre" \
                  "{{overflow-x:unset!important;overflow:unset!important;" \
                  "overflow-y:unset!important;white-space:pre-wrap!important;}}"

    BASE_02_HTML = "</style>" \
                   "</head>\n" \
                   "<body>{1}</body>\n</html>"

    CONTAINER_XML = "<?xml version=\"1.0\"?>" \
                    "<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">" \
                    "<rootfiles>" \
                    "<rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\" />" \
                    "</rootfiles>" \
                    "</container>"

    # Format: ID, Title, Authors, Description, Subjects, Publisher, Rights, Date, CoverId, MANIFEST, SPINE, CoverUrl
    CONTENT_OPF = "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" \
                  "<package xmlns=\"http://www.idpf.org/2007/opf\" unique-identifier=\"bookid\" version=\"2.0\" >\n" \
                  "<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\" " \
                  " xmlns:opf=\"http://www.idpf.org/2007/opf\">\n" \
                  "<dc:title>{1}</dc:title>\n" \
                  "{2}\n" \
                  "<dc:description>{3}</dc:description>\n" \
                  "{4}" \
                  "<dc:publisher>{5}</dc:publisher>\n" \
                  "<dc:rights>{6}</dc:rights>\n" \
                  "<dc:language>en-US</dc:language>\n" \
                  "<dc:date>{7}</dc:date>\n" \
                  "<dc:identifier id=\"bookid\">{0}</dc:identifier>\n" \
                  "<meta name=\"cover\" content=\"{8}\"/>\n" \
                  "</metadata>\n" \
                  "<manifest>\n" \
                  "<item id=\"ncx\" href=\"toc.ncx\" media-type=\"application/x-dtbncx+xml\" />\n" \
                  "{9}\n" \
                  "</manifest>\n" \
                  "<spine toc=\"ncx\">\n{10}</spine>\n" \
                  "<guide><reference href=\"{11}\" title=\"Cover\" type=\"cover\" /></guide>\n" \
                  "</package>"

    # Format: ID, Depth, Title, Author, NAVMAP
    TOC_NCX = "<?xml version=\"1.0\" encoding=\"utf-8\" standalone=\"no\" ?>\n" \
              "<!DOCTYPE ncx PUBLIC \"-//NISO//DTD ncx 2005-1//EN\"" \
              " \"http://www.daisy.org/z3986/2005/ncx-2005-1.dtd\">\n" \
              "<ncx xmlns=\"http://www.daisy.org/z3986/2005/ncx/\" version=\"2005-1\">\n" \
              "<head>\n" \
              "<meta content=\"ID:ISBN:{0}\" name=\"dtb:uid\"/>\n" \
              "<meta content=\"{1}\" name=\"dtb:depth\"/>\n" \
              "<meta content=\"0\" name=\"dtb:totalPageCount\"/>\n" \
              "<meta content=\"0\" name=\"dtb:maxPageNumber\"/>\n" \
              "</head>\n" \
              "<docTitle><text>{2}</text></docTitle>\n" \
              "<docAuthor><text>{3}</text></docAuthor>\n" \
              "<navMap>{4}</navMap>\n" \
              "</ncx>"

    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Referer": LOGIN_ENTRY_URL,
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/90.0.4430.212 Safari/537.36"
    }

    COOKIE_FLOAT_MAX_AGE_PATTERN = re.compile(r'(max-age=\d*\.\d*)', re.IGNORECASE)
    HTML_CHARSET_PATTERN = re.compile(
        br"<meta[^>]+charset=[\"']?\s*([A-Za-z0-9._:-]+)|<\?xml[^>]+encoding=[\"']([^\"']+)",
        re.IGNORECASE
    )
    MOJIBAKE_MARKERS = ("\u00e2", "\u00c3", "\u00c2", "\x80", "\x81", "\x82", "\x83",
                        "\x84", "\x85", "\x86", "\x87", "\x88", "\x89", "\x8a", "\x8b",
                        "\x8c", "\x8d", "\x8e", "\x8f", "\x90", "\x91", "\x92", "\x93",
                        "\x94", "\x95", "\x96", "\x97", "\x98", "\x99", "\x9a", "\x9b",
                        "\x9c", "\x9d", "\x9e", "\x9f")
    CHAPTER_FETCH_RETRIES = 6
    CHAPTER_FETCH_RETRY_BASE_DELAY = 1.2
    CHAPTER_FETCH_RETRY_MAX_DELAY = 12.0
    CHAPTER_FETCH_PACING_SECONDS = 0.08
    PROFILE_CHECK_RETRIES = 6
    PROFILE_CHECK_RETRY_BASE_DELAY = 1.5
    PROFILE_CHECK_RETRY_MAX_DELAY = 20.0

    def __init__(self, args):
        self.args = args
        self.display = Display("info_%s.log" % escape(args.bookid))
        self.display.intro()

        self.session = requests.Session()
        if USE_PROXY:  # DEBUG
            self.session.proxies = PROXIES
            self.session.verify = False

        self.session.headers.update(self.HEADERS)

        self.jwt = {}

        if not args.cred:
            if not os.path.isfile(COOKIES_FILE):
                self.display.exit("Login: unable to find `cookies.json` file.\n"
                                  "    Please use the `--cred` or `--login` options to perform the login.")

            self.session.cookies.update(json.load(open(COOKIES_FILE)))

        else:
            self.display.info("Logging into Safari Books Online...", state=True)
            self.do_login(*args.cred)
            if not args.no_cookies:
                json.dump(self.session.cookies.get_dict(), open(COOKIES_FILE, 'w'))

        self.check_login()

        self.book_id = args.bookid
        self.api_url = self.API_TEMPLATE.format(self.book_id)

        self.display.info("Retrieving book info...")
        self.book_info = self.get_book_info()
        self.display.book_info(self.book_info)

        self.display.info("Retrieving book chapters...")
        self.book_chapters = self.get_book_chapters()

        self.chapters_queue = self.book_chapters[:]

        if len(self.book_chapters) > sys.getrecursionlimit():
            sys.setrecursionlimit(len(self.book_chapters))

        self.book_title = self.book_info["title"]
        self.base_url = self.book_info["web_url"]

        self.clean_book_title = "".join(self.escape_dirname(self.book_title).split(",")[:2]) \
                                + " ({0})".format(self.book_id)

        books_dir = os.path.join(PATH, "Books")
        if not os.path.isdir(books_dir):
            os.mkdir(books_dir)

        self.BOOK_PATH = os.path.join(books_dir, self.clean_book_title)
        self.display.set_output_dir(self.BOOK_PATH)
        self.css_path = ""
        self.images_path = ""
        self.create_dirs()

        self.chapter_title = ""
        self.filename = ""
        self.chapter_stylesheets = []
        self.css = []
        self.images = []

        self.display.info("Downloading book contents... (%s chapters)" % len(self.book_chapters), state=True)
        self.BASE_HTML = self.BASE_01_HTML + (self.KINDLE_HTML if not args.kindle else "") + self.BASE_02_HTML

        self.cover = False
        self.get()
        if not self.cover:
            self.cover = self.get_default_cover() if self.book_info.get("cover") else False
            if self.cover:
                cover_html = self.parse_html(
                    html.fromstring("<div id=\"sbo-rt-content\"><img src=\"Images/{0}\"></div>".format(self.cover)), True
                )

                self.book_chapters = [{
                    "filename": "default_cover.xhtml",
                    "title": "Cover"
                }] + self.book_chapters

                self.filename = self.book_chapters[0]["filename"]
                self.save_page_html(cover_html)

        self.css_done_queue = Queue(0) if "win" not in sys.platform else WinQueue()
        self.display.info("Downloading book CSSs... (%s files)" % len(self.css), state=True)
        self.collect_css()
        self.images_done_queue = Queue(0) if "win" not in sys.platform else WinQueue()
        self.display.info("Downloading book images... (%s files)" % len(self.images), state=True)
        self.collect_images()

        self.display.info("Creating EPUB file...", state=True)
        self.create_epub()

        if not args.no_cookies:
            json.dump(self.session.cookies.get_dict(), open(COOKIES_FILE, "w"))

        self.display.done(os.path.join(self.BOOK_PATH, self.book_id + ".epub"))
        self.display.unregister()

        if not self.display.in_error and not args.log:
            os.remove(self.display.log_file)

    def handle_cookie_update(self, set_cookie_headers):
        for morsel in set_cookie_headers:
            # Handle Float 'max-age' Cookie
            if self.COOKIE_FLOAT_MAX_AGE_PATTERN.search(morsel):
                cookie_key, cookie_value = morsel.split(";")[0].split("=")
                self.session.cookies.set(cookie_key, cookie_value)

    @staticmethod
    def _html_declared_encoding(data):
        if not isinstance(data, bytes):
            return None

        match = SafariBooks.HTML_CHARSET_PATTERN.search(data[:4096])
        if not match:
            return None

        charset = match.group(1) or match.group(2)
        if not charset:
            return None

        return charset.decode("ascii", "ignore").strip()

    @staticmethod
    def _mojibake_score(text):
        if not isinstance(text, str):
            return 0

        return sum(text.count(marker) for marker in SafariBooks.MOJIBAKE_MARKERS)

    @staticmethod
    def _repair_mojibake(text):
        if not isinstance(text, str):
            return text

        best = text
        best_score = SafariBooks._mojibake_score(text)
        if best_score == 0:
            return text

        for _ in range(2):
            improved = False
            for encoding in ("windows-1252", "latin-1"):
                try:
                    candidate = best.encode(encoding).decode("utf-8")
                except UnicodeError:
                    continue

                candidate_score = SafariBooks._mojibake_score(candidate)
                if candidate_score < best_score:
                    best = candidate
                    best_score = candidate_score
                    improved = True

            if not improved or best_score == 0:
                break

        return best

    @staticmethod
    def _clean_html_text(text):
        text = SafariBooks._repair_mojibake(text)
        return re.sub(r"^\ufeff?\s*<\?xml[^>]*\?>\s*", "", text, count=1, flags=re.IGNORECASE)

    @staticmethod
    def _decode_html_bytes(data, *preferred_encodings):
        encodings = ["utf-8-sig", SafariBooks._html_declared_encoding(data)]
        encodings.extend(preferred_encodings)
        encodings.extend(["windows-1252", "latin-1"])

        seen = set()
        for encoding in encodings:
            if not encoding:
                continue

            encoding_key = encoding.lower()
            if encoding_key in {"iso-8859-1", "iso8859-1", "latin-1", "latin1"}:
                encoding = "windows-1252"
                encoding_key = encoding

            if encoding_key in seen:
                continue
            seen.add(encoding_key)

            try:
                return SafariBooks._clean_html_text(data.decode(encoding))
            except (LookupError, UnicodeDecodeError):
                continue

        return SafariBooks._clean_html_text(data.decode("utf-8", "replace"))

    @staticmethod
    def _html_from_source(source, **kwargs):
        if hasattr(source, "content"):
            source = SafariBooks._decode_html_bytes(
                source.content,
                getattr(source, "encoding", None),
                getattr(source, "apparent_encoding", None)
            )

        elif isinstance(source, bytes):
            source = SafariBooks._decode_html_bytes(source)

        elif isinstance(source, str):
            source = SafariBooks._clean_html_text(source)

        return html.fromstring(source, **kwargs)

    def requests_provider(self, url, is_post=False, data=None, perform_redirect=True, **kwargs):
        try:
            response = getattr(self.session, "post" if is_post else "get")(
                url,
                data=data,
                allow_redirects=False,
                **kwargs
            )

            self.handle_cookie_update(response.raw.headers.getlist("Set-Cookie"))

            self.display.last_request = (
                url, data, kwargs, response.status_code, "\n".join(
                    ["\t{}: {}".format(*h) for h in response.headers.items()]
                ), response.text
            )

        except (requests.ConnectionError, requests.ConnectTimeout, requests.RequestException) as request_exception:
            self.display.error(str(request_exception))
            return 0

        if response.is_redirect and perform_redirect:
            return self.requests_provider(response.next.url, is_post, None, perform_redirect)
            # TODO How about **kwargs?

        return response

    @staticmethod
    def parse_cred(cred):
        if ":" not in cred:
            return False

        sep = cred.index(":")
        new_cred = ["", ""]
        new_cred[0] = cred[:sep].strip("'").strip('"')
        if "@" not in new_cred[0]:
            return False

        new_cred[1] = cred[sep + 1:]
        return new_cred

    def do_login(self, email, password):
        response = self.requests_provider(self.LOGIN_ENTRY_URL)
        if response == 0:
            self.display.exit("Login: unable to reach Safari Books Online. Try again...")

        next_parameter = None
        try:
            next_parameter = parse_qs(urlparse(response.request.url).query)["next"][0]

        except (AttributeError, ValueError, IndexError):
            self.display.exit("Login: unable to complete login on Safari Books Online. Try again...")

        redirect_uri = API_ORIGIN_URL + quote_plus(next_parameter)

        response = self.requests_provider(
            self.LOGIN_URL,
            is_post=True,
            json={
                "email": email,
                "password": password,
                "redirect_uri": redirect_uri
            },
            perform_redirect=False
        )

        if response == 0:
            self.display.exit("Login: unable to perform auth to Safari Books Online.\n    Try again...")

        if response.status_code != 200:  # TODO To be reviewed
            try:
                error_page = self._html_from_source(response)
                errors_message = error_page.xpath("//ul[@class='errorlist']//li/text()")
                recaptcha = error_page.xpath("//div[@class='g-recaptcha']")
                messages = (["    `%s`" % error for error in errors_message
                             if "password" in error or "email" in error] if len(errors_message) else []) + \
                           (["    `ReCaptcha required (wait or do logout from the website).`"] if len(
                               recaptcha) else [])
                self.display.exit(
                    "Login: unable to perform auth login to Safari Books Online.\n" + self.display.SH_YELLOW +
                    "[*]" + self.display.SH_DEFAULT + " Details:\n" + "%s" % "\n".join(
                        messages if len(messages) else ["    Unexpected error!"])
                )
            except (html.etree.ParseError, html.etree.ParserError, ValueError) as parsing_error:
                self.display.error(parsing_error)
                self.display.exit(
                    "Login: your login went wrong and it encountered in an error"
                    " trying to parse the login details of Safari Books Online. Try again..."
                )

        self.jwt = response.json()  # TODO: save JWT Tokens and use the refresh_token to restore user session
        response = self.requests_provider(self.jwt["redirect_uri"])
        if response == 0:
            self.display.exit("Login: unable to reach Safari Books Online. Try again...")

    def check_login(self):
        retriable_statuses = {403, 408, 409, 425, 429, 500, 502, 503, 504}
        response = 0

        for attempt in range(1, self.PROFILE_CHECK_RETRIES + 1):
            response = self.requests_provider(PROFILE_URL, perform_redirect=False)

            if response != 0 and response.status_code == 200:
                break

            status_code = response.status_code if response != 0 else "n/a"
            if attempt < self.PROFILE_CHECK_RETRIES and (response == 0 or (response != 0 and response.status_code in retriable_statuses)):
                delay = min(
                    self.PROFILE_CHECK_RETRY_BASE_DELAY * (2 ** (attempt - 1)),
                    self.PROFILE_CHECK_RETRY_MAX_DELAY
                ) + (random() * 0.35)
                self.display.info(
                    "Authentication check got HTTP %s; retrying in %.1fs (%s/%s)." %
                    (status_code, delay, attempt, self.PROFILE_CHECK_RETRIES),
                    state=True
                )
                time.sleep(delay)
                continue

            if response == 0:
                self.display.exit("Login: unable to reach Safari Books Online. Try again...")

            self.display.exit("Authentication issue: unable to access profile page.")

        if "user_type\":\"Expired\"" in response.text:
            self.display.exit("Authentication issue: account subscription expired.")

        self.display.info("Successfully authenticated.", state=True)

    def parse_json_response(self, response, context):
        try:
            return response.json()

        except ValueError:
            content_type = response.headers.get("Content-Type", "n/a")
            self.display.log(
                "API JSON parse failed (%s): HTTP %s Content-Type `%s` URL `%s`" %
                (context, response.status_code, content_type, getattr(response.request, "url", "n/a"))
            )
            return None

    def _book_id_candidates(self):
        candidates = [self.book_id]
        candidates.extend(re.findall(r"\d{10,13}", self.book_id))
        return [c for c in dict.fromkeys(candidates) if c]

    @staticmethod
    def _name_payload(values):
        if not isinstance(values, list):
            return []

        result = []
        for value in values:
            if isinstance(value, str) and value:
                result.append({"name": value})

            elif isinstance(value, dict) and value.get("name"):
                result.append({"name": value["name"]})

        return result

    @staticmethod
    def _extract_payload_file_paths(payload):
        paths = []
        file_ext_pattern = re.compile(
            r"\.(x?html?|css|png|jpe?g|gif|svg|webp|bmp|ico)$",
            re.IGNORECASE
        )

        def collect(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str):
                        candidate = None
                        if "/files/" in value:
                            candidate = value.split("/files/", 1)[-1]
                        elif key.lower() in {"path", "filename", "name", "href", "url"} and file_ext_pattern.search(value):
                            candidate = value

                        if candidate:
                            candidate = candidate.split("?", 1)[0].split("#", 1)[0].lstrip("/")
                            if SafariBooks._is_valid_epub_path(candidate):
                                paths.append(candidate)

                    collect(value)

            elif isinstance(node, list):
                for value in node:
                    collect(value)

            elif isinstance(node, str):
                candidate = None
                if "/files/" in node:
                    candidate = node.split("/files/", 1)[-1]
                elif file_ext_pattern.search(node):
                    candidate = node

                if candidate:
                    candidate = candidate.split("?", 1)[0].split("#", 1)[0].lstrip("/")
                    if SafariBooks._is_valid_epub_path(candidate):
                        paths.append(candidate)

        collect(payload)
        return list(dict.fromkeys(paths))

    @staticmethod
    def _extract_payload_file_urls(payload):
        urls = []

        def collect(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str) and key.lower() == "url":
                        urls.append(value)

                    collect(value)

            elif isinstance(node, list):
                for value in node:
                    collect(value)

        collect(payload)
        return list(dict.fromkeys(urls))

    @staticmethod
    def _epub_path_from_reference(reference):
        if not isinstance(reference, str):
            return ""

        candidate = reference.split("?", 1)[0].split("#", 1)[0].strip()
        if "/files/" in candidate:
            candidate = candidate.split("/files/", 1)[-1]

        return candidate.lstrip("/")

    @staticmethod
    def _normalize_epub_content_path(path):
        if not isinstance(path, str):
            return ""

        normalized = path.strip().replace("\\", "/").lstrip("/")
        if normalized.lower().startswith("oebps/"):
            normalized = normalized.split("/", 1)[1]

        return normalized

    @staticmethod
    def _build_epub_asset_url(reference, path, asset_base_url):
        if not isinstance(reference, str):
            return asset_base_url.rstrip("/") + "/" + path.lstrip("/")

        candidate = reference.split("?", 1)[0].split("#", 1)[0].strip()
        if not candidate:
            return asset_base_url.rstrip("/") + "/" + path.lstrip("/")

        if candidate.startswith("//"):
            return "https:" + candidate

        if SafariBooks.url_is_absolute(candidate):
            return candidate

        if candidate.startswith("/"):
            return urljoin(SAFARI_BASE_URL, candidate)

        return asset_base_url.rstrip("/") + "/" + path.lstrip("/")

    @staticmethod
    def _is_valid_epub_path(path):
        if not isinstance(path, str):
            return False

        normalized = path.strip().lstrip("/")
        if not normalized or normalized in {".", ".."}:
            return False

        basename = normalized.rsplit("/", 1)[-1].strip()
        if not basename or basename in {".", ".."}:
            return False

        suffix = pathlib.Path(basename).suffix.lower()
        if suffix in {".htm", ".html", ".xhtm", ".xhtml", ".css", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".bmp", ".ico"}:
            # Reject placeholders like `.html` / `.css` that have no filename stem.
            if basename.startswith("."):
                return False
            if not pathlib.Path(basename).stem.strip(". "):
                return False

        return True

    @staticmethod
    def _is_navigation_document(path):
        if not isinstance(path, str):
            return False

        basename = pathlib.Path(path.split("?", 1)[0].split("#", 1)[0]).name.lower()
        return basename in {"nav.htm", "nav.html", "nav.xhtm", "nav.xhtml"}

    @staticmethod
    def _is_html_document(path):
        return isinstance(path, str) and path.lower().endswith((".htm", ".html", ".xhtm", ".xhtml"))

    @staticmethod
    def _is_source_toc_document(path):
        if not SafariBooks._is_html_document(path):
            return False

        basename = pathlib.Path(path.split("?", 1)[0].split("#", 1)[0]).name.lower()
        stem = pathlib.Path(basename).stem
        return stem in {"toc", "contents"} or stem.endswith(("_toc", "_contents"))

    @staticmethod
    def _epub_path_from_href(href, base_path=""):
        if not isinstance(href, str):
            return ""

        href = href.strip()
        if not href or href.startswith(("mailto:", "javascript:", "data:")):
            return ""

        parsed = urlparse(href)
        candidate = parsed.path if parsed.scheme or parsed.netloc else href.split("?", 1)[0].split("#", 1)[0]
        if "/files/" in candidate:
            candidate = candidate.split("/files/", 1)[-1]

        candidate = unquote(candidate).lstrip("/")
        if not candidate:
            return ""

        if not parsed.scheme and not parsed.netloc and not href.startswith("/") and base_path:
            base_dir = posixpath.dirname(base_path)
            candidate = posixpath.normpath(posixpath.join(base_dir, candidate))

        return SafariBooks._normalize_epub_content_path(candidate)

    @staticmethod
    def _node_text(node):
        if isinstance(node, str):
            return SafariBooks._normalize_text(node)

        return SafariBooks._normalize_text(" ".join(node.xpath(".//text()")))

    def _extract_source_toc_entries(self, root, toc_path):
        if root is None:
            return []

        content = root.xpath("//div[@id='sbo-rt-content']")
        search_root = content[0] if len(content) else root

        chapter_link_xpath = (
            ".//*[contains(concat(' ', normalize-space(@class), ' '), ' contentschaptertitle ') "
            "or contains(concat(' ', normalize-space(@class), ' '), ' tocchapter ') "
            "or contains(concat(' ', normalize-space(@class), ' '), ' chaptertitle ')]//a[@href]"
        )
        anchors = search_root.xpath(chapter_link_xpath)
        if not len(anchors):
            anchors = search_root.xpath(".//a[@href]")

        entries = []
        seen_paths = set()
        for anchor in anchors:
            path = self._epub_path_from_href(anchor.attrib.get("href", ""), toc_path)
            if not self._is_valid_epub_path(path):
                continue

            path_without_fragment = path.split("#", 1)[0]
            if not self._is_html_document(path_without_fragment):
                continue
            if self._is_navigation_document(path_without_fragment) or self._is_source_toc_document(path_without_fragment):
                continue
            if path_without_fragment in seen_paths:
                continue

            label = self._node_text(anchor)
            entries.append({
                "path": path_without_fragment,
                "label": label
            })
            seen_paths.add(path_without_fragment)

        return entries

    def _extract_source_toc_entries_from_ref(self, toc_ref):
        response = self.requests_provider(toc_ref["url"])
        if response == 0 or response.status_code != 200:
            return []

        try:
            root = self._html_from_source(response, base_url=toc_ref["url"])
        except (html.etree.ParseError, html.etree.ParserError, ValueError) as parsing_error:
            self.display.log("V2 TOC parse failed for `%s`: %s" % (toc_ref["path"], parsing_error))
            return []

        return self._extract_source_toc_entries(root, toc_ref["path"])

    def get_book_info_v2(self):
        candidates = self._book_id_candidates()
        search_params = {
            "limit": 25,
            "formats": ["book"],
            "include_facets": "false"
        }
        search_headers = {
            "Accept": "application/json",
            "orm-service": "safaribooks"
        }

        for search_url in self.SEARCH_V2_URLS:
            for query in candidates:
                response = self.requests_provider(
                    search_url,
                    params=dict(search_params, query=query),
                    headers=search_headers
                )
                if response == 0 or response.status_code != 200:
                    continue

                payload = self.parse_json_response(response, "search v2")
                if not isinstance(payload, dict) or "results" not in payload:
                    continue

                results = payload.get("results", [])
                if not isinstance(results, list) or not len(results):
                    continue

                selected = None
                for result in results:
                    if not isinstance(result, dict):
                        continue

                    archive_id = str(result.get("archive_id", ""))
                    isbn = str(result.get("isbn", ""))
                    ourn = str(result.get("ourn", ""))
                    web_url = str(result.get("web_url", ""))

                    if any(c in [archive_id, isbn] for c in candidates):
                        selected = result
                        break

                    if any(ourn.endswith(c) or ("/%s/" % c) in web_url for c in candidates):
                        selected = result
                        break

                if selected is None:
                    selected = results[0]

                authors = self._name_payload(selected.get("authors", []))
                publishers = self._name_payload(selected.get("publishers", []))
                topics_payload = selected.get("topics_payload", [])
                subjects = self._name_payload(topics_payload if isinstance(topics_payload, list) else [])

                web_url = selected.get("web_url", "")
                if isinstance(web_url, str) and web_url.startswith("/"):
                    web_url = urljoin(SAFARI_BASE_URL, web_url)

                cover = selected.get("cover_url", "")
                if isinstance(cover, str) and cover.startswith("/"):
                    cover = urljoin(SAFARI_BASE_URL, cover)

                archive_id = str(selected.get("archive_id", "") or self.book_id)
                ourn = str(selected.get("ourn", "") or "urn:orm:book:%s" % archive_id)

                return {
                    "title": selected.get("title", archive_id),
                    "authors": authors,
                    "identifier": archive_id,
                    "isbn": selected.get("isbn", archive_id),
                    "publishers": publishers,
                    "rights": selected.get("rights", ""),
                    "description": selected.get("description", ""),
                    "issued": selected.get("issued", ""),
                    "web_url": web_url,
                    "cover": cover,
                    "subjects": subjects,
                    "ourn": ourn
                }

        return None

    def get_book_info(self):
        response = self.requests_provider(self.api_url)
        if response != 0 and response.status_code == 200:
            payload = self.parse_json_response(response, "book info v1")
            if isinstance(payload, dict) and len(payload.keys()) != 1:
                if "last_chapter_read" in payload:
                    del payload["last_chapter_read"]

                for key, value in payload.items():
                    if value is None:
                        payload[key] = 'n/a'

                if "ourn" not in payload:
                    payload["ourn"] = "urn:orm:book:%s" % self.book_id
                return payload

        fallback = self.get_book_info_v2()
        if not fallback:
            self.display.exit(
                "API: unable to retrieve book info from legacy v1 endpoint "
                "or Search v2 fallback."
            )

        for key, value in fallback.items():
            if value is None:
                fallback[key] = 'n/a'

        return fallback

    def get_book_chapters_v2(self):
        ourn = self.book_info.get("ourn", "urn:orm:book:%s" % self.book_id)
        asset_base_url = self.EPUB_FILES_V2_TEMPLATE.format(ourn)

        refs = []
        payloads = []
        visited_urls = set()

        first_response = self.requests_provider(asset_base_url + "/")
        if first_response == 0 or first_response.status_code != 200:
            first_response = self.requests_provider(asset_base_url)
            if first_response == 0 or first_response.status_code != 200:
                return []

        first_payload = self.parse_json_response(first_response, "epub files v2")
        if first_payload is None:
            return []

        payloads.append(first_payload)
        visited_urls.add(getattr(first_response.request, "url", asset_base_url + "/"))

        next_url = first_payload.get("next") if isinstance(first_payload, dict) else None
        while next_url:
            absolute_next = next_url if self.url_is_absolute(next_url) else urljoin(SAFARI_BASE_URL, next_url)
            if absolute_next in visited_urls:
                break

            visited_urls.add(absolute_next)
            next_response = self.requests_provider(absolute_next)
            if next_response == 0 or next_response.status_code != 200:
                break

            next_payload = self.parse_json_response(next_response, "epub files v2 page")
            if next_payload is None:
                break

            payloads.append(next_payload)
            next_url = next_payload.get("next") if isinstance(next_payload, dict) else None

        urls = []
        for payload in payloads:
            urls.extend(self._extract_payload_file_urls(payload))

        for raw_url in urls:
            raw_path = self._epub_path_from_reference(raw_url)
            path = self._normalize_epub_content_path(raw_path)
            if not self._is_valid_epub_path(path):
                continue

            refs.append({
                "path": path,
                "url": self._build_epub_asset_url(raw_url, raw_path, asset_base_url)
            })

        # Fallback for payloads that do not expose a `url` field.
        if not refs:
            paths = []
            for payload in payloads:
                paths.extend(self._extract_payload_file_paths(payload))

            refs = [{
                "path": self._normalize_epub_content_path(p),
                "url": asset_base_url.rstrip("/") + "/" + p.lstrip("/")
            } for p in paths if self._is_valid_epub_path(self._normalize_epub_content_path(p))]

        # Deduplicate by logical file path.
        unique_refs = {}
        for ref in refs:
            if ref["path"] not in unique_refs:
                unique_refs[ref["path"]] = ref["url"]

        refs = [{"path": p, "url": u} for p, u in unique_refs.items()]
        refs = sorted(refs, key=lambda x: x["path"])

        self.display.log(
            "V2 files discovered for `%s`: %s entries (sample: %s)" %
            (   
                ourn,
                len(refs),
                ", ".join(
                    "%s -> %s" % (x["path"], x["url"])
                    for x in refs[:6]
                )
            )
        )
        html_refs = [x for x in refs if self._is_html_document(x["path"])]
        html_files = [
            x for x in html_refs
            if not self._is_navigation_document(x["path"])
            and not self._is_source_toc_document(x["path"])
        ]
        css_files = [x for x in refs if x["path"].lower().endswith(".css")]
        image_files = [
            x for x in refs if x["path"].lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".bmp"))
        ]

        if not html_files:
            return []

        refs_by_path = {x["path"]: x for x in refs}
        source_toc_ref = next(
            (x for x in html_refs if self._is_source_toc_document(x["path"])),
            None
        )
        source_toc_entries = self._extract_source_toc_entries_from_ref(source_toc_ref) if source_toc_ref else []

        chapters = []
        ordered_html_files = []
        used_html_paths = set()
        for toc_entry in source_toc_entries:
            chapter = refs_by_path.get(toc_entry["path"])
            if not chapter:
                continue

            ordered_html_files.append({
                "path": chapter["path"],
                "url": chapter["url"],
                "title": toc_entry["label"],
                "title_from_toc": True
            })
            used_html_paths.add(chapter["path"])

        ordered_html_files.extend(x for x in html_files if x["path"] not in used_html_paths)

        for i, chapter in enumerate(ordered_html_files):
            chapter_path = chapter["path"]
            chapter_title = chapter.get("title", "")
            if not chapter_title:
                chapter_title = pathlib.Path(chapter_path).stem.replace("-", " ").replace("_", " ").strip()
            if not chapter_title:
                chapter_title = "Chapter %s" % (i + 1)

            chapter_item = {
                "title": chapter_title,
                "filename": chapter_path,
                "content": chapter["url"],
                "asset_base_url": asset_base_url,
                "title_from_toc": chapter.get("title_from_toc", False)
            }

            if i == 0 and len(image_files):
                chapter_item["images"] = [img["url"] for img in image_files]

            if i == 0 and len(css_files):
                chapter_item["stylesheets"] = [{"url": css["url"]} for css in css_files]

            chapters.append(chapter_item)

        return chapters

    def get_book_chapters(self, page=1):
        response = self.requests_provider(urljoin(self.api_url, "chapter/?page=%s" % page))
        if response != 0 and response.status_code == 200:
            payload = self.parse_json_response(response, "book chapters v1")
            if isinstance(payload, dict) and len(payload.keys()) != 1 and payload.get("results"):
                if payload["count"] > sys.getrecursionlimit():
                    sys.setrecursionlimit(payload["count"])

                result = []
                result.extend([c for c in payload["results"] if "cover" in c["filename"] or "cover" in c["title"]])
                for c in result:
                    del payload["results"][payload["results"].index(c)]

                result += payload["results"]
                return result + (self.get_book_chapters(page + 1) if payload["next"] else [])

        fallback = self.get_book_chapters_v2()
        if len(fallback):
            return fallback

        self.display.exit("API: unable to retrieve book chapters from v1 or v2 endpoints.")

    def get_default_cover(self):
        response = self.requests_provider(self.book_info["cover"], stream=True)
        if response == 0:
            self.display.error("Error trying to retrieve the cover: %s" % self.book_info["cover"])
            return False

        file_ext = response.headers["Content-Type"].split("/")[-1]
        with open(os.path.join(self.images_path, "default_cover." + file_ext), 'wb') as i:
            for chunk in response.iter_content(1024):
                i.write(chunk)

        return "default_cover." + file_ext

    def get_html(self, url):
        retriable_statuses = {403, 408, 409, 425, 429, 500, 502, 503, 504}
        response = 0
        for attempt in range(1, self.CHAPTER_FETCH_RETRIES + 1):
            response = self.requests_provider(url)
            if response != 0 and response.status_code == 200:
                break

            status_code = response.status_code if response != 0 else "n/a"
            if response != 0 and response.status_code in retriable_statuses and attempt < self.CHAPTER_FETCH_RETRIES:
                delay = min(
                    self.CHAPTER_FETCH_RETRY_BASE_DELAY * (2 ** (attempt - 1)),
                    self.CHAPTER_FETCH_RETRY_MAX_DELAY
                ) + (random() * 0.35)
                self.display.info(
                    "Crawler: temporary HTTP %s while retrieving `%s`; retrying in %.1fs (%s/%s)." %
                    (status_code, self.filename, delay, attempt, self.CHAPTER_FETCH_RETRIES),
                    state=True
                )
                time.sleep(delay)
                continue

            self.display.exit(
                "Crawler: error trying to retrieve this page: %s (%s)\n    From: %s" %
                (self.filename, self.chapter_title, url)
            )

        root = None
        try:
            root = self._html_from_source(response, base_url=SAFARI_BASE_URL)

        except (html.etree.ParseError, html.etree.ParserError, ValueError) as parsing_error:
            self.display.error(parsing_error)
            self.display.exit(
                "Crawler: error trying to parse this page: %s (%s)\n    From: %s" %
                (self.filename, self.chapter_title, url)
            )

        return root

    @staticmethod
    def url_is_absolute(url):
        return bool(urlparse(url).netloc)

    @staticmethod
    def is_image_link(url: str):
        return pathlib.Path(url).suffix[1:].lower() in ["jpg", "jpeg", "png", "gif", "svg", "webp", "bmp", "ico", "avif"]

    @staticmethod
    def to_xhtml_path(path):
        if not isinstance(path, str) or not path:
            return path

        fragment_split = path.split("#", 1)
        query_split = fragment_split[0].split("?", 1)

        base = re.sub(r"\.(x?html?)$", ".xhtml", query_split[0], flags=re.IGNORECASE)

        if len(query_split) == 2:
            base += "?" + query_split[1]
        if len(fragment_split) == 2:
            base += "#" + fragment_split[1]

        return base

    def _relative_oebps_href(self, target_path):
        target = (target_path or "").lstrip("/")
        chapter_file = self.to_xhtml_path(getattr(self, "filename", "") or "")
        chapter_file = chapter_file.replace("\\", "/")
        chapter_dir = pathlib.PurePosixPath(chapter_file).parent
        depth = 0 if str(chapter_dir) == "." else len(chapter_dir.parts)
        return ("../" * depth) + target

    def link_replace(self, link):
        if link and not link.startswith("mailto"):
            if not self.url_is_absolute(link):
                if any(x in link for x in ["cover", "images", "graphics"]) or \
                        self.is_image_link(link):
                    image = link.split("/")[-1].split("?", 1)[0].split("#", 1)[0]
                    return self._relative_oebps_href("Images/" + image)

                return self.to_xhtml_path(link)

            else:
                link_path = urlparse(link).path
                if any(x in link_path.lower() for x in ["cover", "images", "graphics"]) or self.is_image_link(link_path):
                    image = link_path.split("/")[-1].split("?", 1)[0].split("#", 1)[0]
                    return self._relative_oebps_href("Images/" + image)

                if self.book_id in link:
                    return self.link_replace(link.split(self.book_id)[-1])

        return link

    @staticmethod
    def get_cover(html_root):
        lowercase_ns = etree.FunctionNamespace(None)
        lowercase_ns["lower-case"] = lambda _, n: n[0].lower() if n and len(n) else ""

        images = html_root.xpath("//img[contains(lower-case(@id), 'cover') or contains(lower-case(@class), 'cover') or"
                                 "contains(lower-case(@name), 'cover') or contains(lower-case(@src), 'cover') or"
                                 "contains(lower-case(@alt), 'cover')]")
        if len(images):
            return images[0]

        divs = html_root.xpath("//div[contains(lower-case(@id), 'cover') or contains(lower-case(@class), 'cover') or"
                               "contains(lower-case(@name), 'cover') or contains(lower-case(@src), 'cover')]//img")
        if len(divs):
            return divs[0]

        a = html_root.xpath("//a[contains(lower-case(@id), 'cover') or contains(lower-case(@class), 'cover') or"
                            "contains(lower-case(@name), 'cover') or contains(lower-case(@src), 'cover')]//img")
        if len(a):
            return a[0]

        return None

    @staticmethod
    def _normalize_text(text):
        if not isinstance(text, str):
            return ""

        return re.sub(r"\s+", " ", text).strip()

    def _extract_semantic_title(self, root, fallback=""):
        if root is None:
            return fallback

        content = root.xpath("//div[@id='sbo-rt-content']")
        if not len(content):
            title_tag = root.xpath("//title/text()")
            if len(title_tag):
                return self._normalize_text(title_tag[0]) or fallback
            return fallback

        content = content[0]

        def first_text(xpath_expr):
            nodes = content.xpath(xpath_expr)
            if not len(nodes):
                return ""

            node = nodes[0]
            if isinstance(node, str):
                return self._normalize_text(node)

            text_bits = node.xpath(".//text()")
            return self._normalize_text(" ".join(text_bits))

        def class_texts(class_names):
            if not isinstance(class_names, (list, tuple)):
                class_names = [class_names]

            values = []
            for class_name in class_names:
                xpath_token = ".//*[contains(concat(' ', normalize-space(@class), ' '), ' %s ')]" % class_name
                for node in content.xpath(xpath_token):
                    value = self._node_text(node)
                    if value and value not in values:
                        values.append(value)

                # Preserve support for class variants like "chaptitle1" without
                # letting short aliases like "ct" match arbitrary body classes.
                if len(class_name) >= 4:
                    xpath_contains = ".//*[contains(@class, '%s')]" % class_name
                    for node in content.xpath(xpath_contains):
                        value = self._node_text(node)
                        if value and value not in values:
                            values.append(value)

            return values

        def first_class_text(class_names):
            values = class_texts(class_names)
            return values[0] if len(values) else ""

        # Prefer explicit structural titles when present.
        part_num = first_class_text(["partnum", "part_num", "pt"])
        part_title = first_class_text(["parttitle", "part_title", "pst"])
        if part_num and part_title:
            return "%s - %s" % (part_num, part_title)
        if part_title:
            return part_title

        chap_num = first_class_text(["chapnum", "chapter-number", "chapternum", "ct"])
        chap_titles = class_texts(["chaptitle", "chapter-title", "chaptertitle", "cst"])
        chap_title = chap_titles[0] if len(chap_titles) else ""
        if not chap_num and len(chap_titles) > 1 and re.match(r"^(chapter|chap\.?)\s+\S+", chap_titles[0], re.IGNORECASE):
            return "%s - %s" % (chap_titles[0], chap_titles[1])
        if chap_num and chap_title:
            return "%s - %s" % (chap_num, chap_title)
        if chap_title:
            return chap_title

        # Fallback to first meaningful heading.
        for xp in [".//h1", ".//h2", ".//h3"]:
            heading = first_text(xp)
            if heading:
                return heading

        title_tag = root.xpath("//title/text()")
        if len(title_tag):
            return self._normalize_text(title_tag[0]) or fallback

        return fallback

    def _extract_title_from_existing_file(self, file_path, fallback=""):
        if not os.path.isfile(file_path):
            return fallback

        try:
            parsed = html.parse(file_path)
            root = parsed.getroot()
            return self._extract_semantic_title(root, fallback)
        except Exception:
            return fallback

    @staticmethod
    def _chapter_order_sort_key(chapter, index):
        title = SafariBooks._normalize_text(chapter.get("title", ""))
        title_lower = title.lower()
        filename = str(chapter.get("filename", "")).lower()

        if filename.startswith("default_cover") or title_lower == "cover" or "cover" in filename or "_cov" in filename:
            return (0, 0, (0,), index)

        front_matter_order = [
            ("series", 10),
            ("title page", 20),
            ("copyright", 30),
            ("dedication", 40),
            ("foreword", 50),
            ("preface", 60),
            ("acknowledgments", 70),
            ("acknowledgements", 70),
            ("introduction", 80),
        ]
        for label, order in front_matter_order:
            if title_lower == label:
                return (1, order, (0,), index)

        chapter_match = re.match(r"^chapter\s+(\d+)\b", title, re.IGNORECASE)
        if chapter_match:
            return (2, int(chapter_match.group(1)), (0,), index)

        section_match = re.match(r"^(\d+(?:\.\d+)*)\s*[\.)]\s+", title)
        if section_match:
            parts = [int(part) for part in section_match.group(1).split(".")]
            return (2, parts[0], tuple(parts[1:] or [0]), index)

        appendix_match = re.match(r"^appendix\s+([a-z0-9]+)\b", title, re.IGNORECASE)
        if appendix_match:
            appendix = appendix_match.group(1).lower()
            appendix_order = ord(appendix[0]) - ord("a") + 1 if appendix[:1].isalpha() else int(appendix)
            return (3, appendix_order, (0,), index)

        back_matter_order = [
            ("about the authors", 10),
            ("about the author", 10),
            ("bibliography", 20),
            ("references", 30),
            ("index", 90),
        ]
        for label, order in back_matter_order:
            if title_lower == label:
                return (4, order, (0,), index)

        return (5, 0, (0,), index)

    def _reorder_chapters_from_titles(self):
        if not self.book_chapters:
            return

        # When a source TOC exists, its order and labels should win.
        if any(chapter.get("title_from_toc") for chapter in self.book_chapters):
            return

        enumerated = list(enumerate(self.book_chapters))
        ordered = [
            chapter for index, chapter in sorted(
                enumerated,
                key=lambda item: self._chapter_order_sort_key(item[1], item[0])
            )
        ]

        if ordered != self.book_chapters:
            self.book_chapters = ordered
            self.display.log("Reordered chapter list from inferred titles before EPUB creation.")

    def parse_html(self, root, first_page=False):
        if random() > 0.8:
            if len(root.xpath("//div[@class='controls']/a/text()")):
                self.display.exit(self.display.api_error(" "))

        book_content = root.xpath("//div[@id='sbo-rt-content']")
        if not len(book_content):
            self.display.exit(
                "Parser: book content's corrupted or not present: %s (%s)" %
                (self.filename, self.chapter_title)
            )

        resource_base_url = getattr(self, "current_resource_url", self.base_url)

        page_css = ""
        if len(self.chapter_stylesheets):
            for chapter_css_url in self.chapter_stylesheets:
                if chapter_css_url not in self.css:
                    self.css.append(chapter_css_url)
                    self.display.log("Crawler: found a new CSS at %s" % chapter_css_url)

                page_css += "<link href=\"{0}\" " \
                            "rel=\"stylesheet\" type=\"text/css\" />\n".format(
                                self._relative_oebps_href("Styles/Style{0:0>2}.css".format(self.css.index(chapter_css_url)))
                            )

        stylesheet_links = root.xpath("//link[@rel='stylesheet']")
        if len(stylesheet_links):
            for s in stylesheet_links:
                css_url = urljoin("https:", s.attrib["href"]) if s.attrib["href"][:2] == "//" \
                    else urljoin(resource_base_url, s.attrib["href"])

                if css_url not in self.css:
                    self.css.append(css_url)
                    self.display.log("Crawler: found a new CSS at %s" % css_url)

                page_css += "<link href=\"{0}\" " \
                            "rel=\"stylesheet\" type=\"text/css\" />\n".format(
                                self._relative_oebps_href("Styles/Style{0:0>2}.css".format(self.css.index(css_url)))
                            )

        stylesheets = root.xpath("//style")
        if len(stylesheets):
            for css in stylesheets:
                if "data-template" in css.attrib and len(css.attrib["data-template"]):
                    css.text = css.attrib["data-template"]
                    del css.attrib["data-template"]

                try:
                    page_css += html.tostring(css, method="xml", encoding='unicode') + "\n"

                except (html.etree.ParseError, html.etree.ParserError) as parsing_error:
                    self.display.error(parsing_error)
                    self.display.exit(
                        "Parser: error trying to parse one CSS found in this page: %s (%s)" %
                        (self.filename, self.chapter_title)
                    )

        # TODO: add all not covered tag for `link_replace` function
        svg_image_tags = root.xpath("//image")
        if len(svg_image_tags):
            for img in svg_image_tags:
                image_attr_href = [x for x in img.attrib.keys() if "href" in x]
                if len(image_attr_href):
                    svg_url = img.attrib.get(image_attr_href[0])
                    svg_root = img.getparent().getparent()
                    new_img = svg_root.makeelement("img")
                    new_img.attrib.update({"src": svg_url})
                    svg_root.remove(img.getparent())
                    svg_root.append(new_img)

        book_content = book_content[0]

        def register_image_candidate(raw_url):
            if not isinstance(raw_url, str):
                return

            candidate = raw_url.strip()
            if not len(candidate) or candidate.startswith(("data:", "javascript:", "mailto:")):
                return

            if candidate.startswith("//"):
                candidate = "https:" + candidate
            else:
                candidate = urljoin(resource_base_url, candidate)

            if candidate not in self.images:
                self.images.append(candidate)
                self.display.log("Crawler: found a new image at %s" % candidate)

        for img in book_content.xpath(".//img[@src]"):
            register_image_candidate(img.attrib.get("src", ""))

        for srcset in book_content.xpath(".//source[@srcset]/@srcset"):
            for srcset_item in srcset.split(","):
                register_image_candidate(srcset_item.strip().split(" ", 1)[0])

        book_content.rewrite_links(self.link_replace)

        xhtml = None
        try:
            if first_page:
                is_cover = self.get_cover(book_content)
                if is_cover is not None:
                    page_css = "<style>" \
                               "body{display:table;position:absolute;margin:0!important;height:100%;width:100%;}" \
                               "#Cover{display:table-cell;vertical-align:middle;text-align:center;}" \
                               "img{height:90vh;margin-left:auto;margin-right:auto;}" \
                               "</style>"
                    cover_html = html.fromstring("<div id=\"Cover\"></div>")
                    cover_div = cover_html.xpath("//div")[0]
                    cover_img = cover_div.makeelement("img")
                    cover_img.attrib.update({"src": is_cover.attrib["src"]})
                    cover_div.append(cover_img)
                    book_content = cover_html

                    self.cover = is_cover.attrib["src"]

            xhtml = html.tostring(book_content, method="xml", encoding='unicode')

        except (html.etree.ParseError, html.etree.ParserError) as parsing_error:
            self.display.error(parsing_error)
            self.display.exit(
                "Parser: error trying to parse HTML of this page: %s (%s)" %
                (self.filename, self.chapter_title)
            )

        return page_css, xhtml

    @staticmethod
    def escape_dirname(dirname, clean_space=False):
        if ":" in dirname:
            if dirname.index(":") > 15:
                dirname = dirname.split(":")[0]

            elif "win" in sys.platform:
                dirname = dirname.replace(":", ",")

        for ch in ['~', '#', '%', '&', '*', '{', '}', '\\', '<', '>', '?', '/', '`', '\'', '"', '|', '+', ':']:
            if ch in dirname:
                dirname = dirname.replace(ch, "_")

        return dirname if not clean_space else dirname.replace(" ", "")

    def create_dirs(self):
        if os.path.isdir(self.BOOK_PATH):
            self.display.log("Book directory already exists: %s" % self.BOOK_PATH)

        else:
            os.makedirs(self.BOOK_PATH)

        oebps = os.path.join(self.BOOK_PATH, "OEBPS")
        if not os.path.isdir(oebps):
            self.display.book_ad_info = True
            os.makedirs(oebps)

        self.css_path = os.path.join(oebps, "Styles")
        if os.path.isdir(self.css_path):
            self.display.log("CSSs directory already exists: %s" % self.css_path)

        else:
            os.makedirs(self.css_path)
            self.display.css_ad_info.value = 1

        self.images_path = os.path.join(oebps, "Images")
        if os.path.isdir(self.images_path):
            self.display.log("Images directory already exists: %s" % self.images_path)

        else:
            os.makedirs(self.images_path)
            self.display.images_ad_info.value = 1

    def save_page_html(self, contents):
        self.filename = self.to_xhtml_path(self.filename)
        output_path = os.path.join(self.BOOK_PATH, "OEBPS", self.filename)
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.isdir(output_dir):
            os.makedirs(output_dir)

        open(output_path, "wb").write(
            self.BASE_HTML.format(contents[0], contents[1]).encode("utf-8", 'xmlcharrefreplace')
        )
        self.display.log("Created: %s" % self.filename)

    def get(self):
        len_books = len(self.book_chapters)

        for _ in range(len_books):
            if not len(self.chapters_queue):
                return

            first_page = len_books == len(self.chapters_queue)

            next_chapter = self.chapters_queue.pop(0)
            self.chapter_title = next_chapter["title"]
            self.filename = next_chapter["filename"]
            self.current_resource_url = next_chapter.get("content", self.base_url)

            asset_base_url = next_chapter['asset_base_url']
            api_v2_detected = False
            if 'v2' in next_chapter['content']:
                api_v2_detected = True

            if "images" in next_chapter and len(next_chapter["images"]):
                for img_url in next_chapter['images']:
                    if api_v2_detected:
                        if self.url_is_absolute(img_url):
                            if img_url not in self.images:
                                self.images.append(img_url)
                        elif img_url.startswith("/"):
                            resolved_img_url = urljoin(SAFARI_BASE_URL, img_url)
                            if resolved_img_url not in self.images:
                                self.images.append(resolved_img_url)
                        else:
                            resolved_img_url = asset_base_url.rstrip("/") + "/" + img_url.lstrip("/")
                            if resolved_img_url not in self.images:
                                self.images.append(resolved_img_url)
                    else:
                        resolved_img_url = urljoin(next_chapter['asset_base_url'], img_url)
                        if resolved_img_url not in self.images:
                            self.images.append(resolved_img_url)


            # Stylesheets
            self.chapter_stylesheets = []
            if "stylesheets" in next_chapter and len(next_chapter["stylesheets"]):
                self.chapter_stylesheets.extend(x["url"] for x in next_chapter["stylesheets"])

            if "site_styles" in next_chapter and len(next_chapter["site_styles"]):
                self.chapter_stylesheets.extend(next_chapter["site_styles"])

            output_file = os.path.join(self.BOOK_PATH, "OEBPS", self.to_xhtml_path(self.filename))
            if os.path.isfile(output_file):
                if not next_chapter.get("title_from_toc"):
                    inferred_title = self._extract_title_from_existing_file(output_file, next_chapter.get("title", ""))
                    if inferred_title and inferred_title != next_chapter.get("title", ""):
                        next_chapter["title"] = inferred_title
                        self.chapter_title = inferred_title

                if not self.display.book_ad_info and \
                        next_chapter not in self.book_chapters[:self.book_chapters.index(next_chapter)]:
                    self.display.info(
                        ("File `%s` already exists.\n"
                         "    If you want to download again all the book,\n"
                         "    please delete the output directory '" + self.BOOK_PATH + "' and restart the program.")
                         % self.to_xhtml_path(self.filename)
                    )
                    self.display.book_ad_info = 2

            else:
                root = self.get_html(next_chapter["content"])
                if not next_chapter.get("title_from_toc"):
                    inferred_title = self._extract_semantic_title(root, next_chapter.get("title", ""))
                    if inferred_title and inferred_title != next_chapter.get("title", ""):
                        next_chapter["title"] = inferred_title
                        self.chapter_title = inferred_title

                self.save_page_html(self.parse_html(root, first_page))

            if api_v2_detected and self.CHAPTER_FETCH_PACING_SECONDS > 0:
                time.sleep(self.CHAPTER_FETCH_PACING_SECONDS)

            self.display.state(len_books, len_books - len(self.chapters_queue))

        self._reorder_chapters_from_titles()

    def _thread_download_css(self, url):
        css_file = os.path.join(self.css_path, "Style{0:0>2}.css".format(self.css.index(url)))
        if os.path.isfile(css_file):
            if not self.display.css_ad_info.value and url not in self.css[:self.css.index(url)]:
                self.display.info(("File `%s` already exists.\n"
                                   "    If you want to download again all the CSSs,\n"
                                   "    please delete the output directory '" + self.BOOK_PATH + "'"
                                   " and restart the program.") %
                                  css_file)
                self.display.css_ad_info.value = 1

        else:
            response = self.requests_provider(url)
            if response == 0:
                self.display.error("Error trying to retrieve this CSS: %s\n    From: %s" % (css_file, url))

            with open(css_file, 'wb') as s:
                s.write(response.content)

        self.css_done_queue.put(1)
        self.display.state(len(self.css), self.css_done_queue.qsize())


    def _thread_download_images(self, url):
        image_name = url.split("/")[-1].split("?", 1)[0].split("#", 1)[0]
        if not len(image_name):
            self.display.error("Error trying to retrieve this image: invalid name\n    From: %s" % url)
            return

        image_path = os.path.join(self.images_path, image_name)
        if os.path.isfile(image_path):
            if not self.display.images_ad_info.value and url not in self.images[:self.images.index(url)]:
                self.display.info(("File `%s` already exists.\n"
                                   "    If you want to download again all the images,\n"
                                   "    please delete the output directory '" + self.BOOK_PATH + "'"
                                   " and restart the program.") %
                                  image_name)
                self.display.images_ad_info.value = 1

        else:
            response = self.requests_provider(urljoin(SAFARI_BASE_URL, url), stream=True)
            if response == 0:
                self.display.error("Error trying to retrieve this image: %s\n    From: %s" % (image_name, url))
                return

            with open(image_path, 'wb') as img:
                for chunk in response.iter_content(1024):
                    img.write(chunk)

        self.images_done_queue.put(1)
        self.display.state(len(self.images), self.images_done_queue.qsize())

    def _start_multiprocessing(self, operation, full_queue):
        if len(full_queue) > 5:
            for i in range(0, len(full_queue), 5):
                self._start_multiprocessing(operation, full_queue[i:i + 5])

        else:
            process_queue = [Process(target=operation, args=(arg,)) for arg in full_queue]
            for proc in process_queue:
                proc.start()

            for proc in process_queue:
                proc.join()

    def collect_css(self):
        self.display.state_status.value = -1

        # "self._start_multiprocessing" seems to cause problem. Switching to mono-thread download.
        for css_url in self.css:
            self._thread_download_css(css_url)

    def collect_images(self):
        if self.display.book_ad_info == 2:
            self.display.info("Some of the book contents were already downloaded.\n"
                              "    If you want to be sure that all the images will be downloaded,\n"
                              "    please delete the output directory '" + self.BOOK_PATH +
                              "' and restart the program.")

        self.display.state_status.value = -1

        # "self._start_multiprocessing" seems to cause problem. Switching to mono-thread download.
        for image_url in self.images:
            self._thread_download_images(image_url)

    def create_content_opf(self):
        self.css = next(os.walk(self.css_path))[2]
        self.images = next(os.walk(self.images_path))[2]

        manifest = []
        spine = []
        for c in self.book_chapters:
            c["filename"] = self.to_xhtml_path(c["filename"])
            item_id = escape(re.sub(r"[^A-Za-z0-9_-]", "_", "".join(c["filename"].split(".")[:-1])))
            manifest.append("<item id=\"{0}\" href=\"{1}\" media-type=\"application/xhtml+xml\" />".format(
                item_id, c["filename"]
            ))
            spine.append("<itemref idref=\"{0}\"/>".format(item_id))

        image_id_map = {}
        used_image_ids = set()
        for i in set(self.images):
            dot_split = i.rsplit(".", 1)
            image_stem = dot_split[0]
            extension = dot_split[1] if len(dot_split) == 2 else ""

            head = "img_" + re.sub(r"[^A-Za-z0-9_-]", "_", image_stem)
            base_head = head
            suffix = 2
            while head in used_image_ids:
                head = "%s_%s" % (base_head, suffix)
                suffix += 1

            used_image_ids.add(head)
            image_id_map[i] = head

            manifest.append("<item id=\"{0}\" href=\"Images/{1}\" media-type=\"image/{2}\" />".format(
                head, i, "jpeg" if "jp" in extension else extension
            ))

        cover_filename = os.path.basename(str(self.cover or "")).split("?", 1)[0].split("#", 1)[0]
        cover_id = image_id_map.get(cover_filename, "")
        if not cover_id and len(image_id_map):
            cover_id = next(iter(image_id_map.values()))

        for i in range(len(self.css)):
            manifest.append("<item id=\"style_{0:0>2}\" href=\"Styles/Style{0:0>2}.css\" "
                            "media-type=\"text/css\" />".format(i))

        authors = "\n".join("<dc:creator opf:file-as=\"{0}\" opf:role=\"aut\">{0}</dc:creator>".format(
            escape(aut.get("name", "n/d"))
        ) for aut in self.book_info.get("authors", []))

        subjects = "\n".join("<dc:subject>{0}</dc:subject>".format(escape(sub.get("name", "n/d")))
                             for sub in self.book_info.get("subjects", []))

        return self.CONTENT_OPF.format(
            (self.book_info.get("isbn",  self.book_id)),
            escape(self.book_title),
            authors,
            escape(self.book_info.get("description", "")),
            subjects,
            ", ".join(escape(pub.get("name", "")) for pub in self.book_info.get("publishers", [])),
            escape(self.book_info.get("rights", "")),
            self.book_info.get("issued", ""),
            cover_id,
            "\n".join(manifest),
            "\n".join(spine),
            self.to_xhtml_path(self.book_chapters[0]["filename"])
        )

    @staticmethod
    def parse_toc(l, c=0, mx=0):
        r = ""
        for cc in l:
            c += 1
            if int(cc["depth"]) > mx:
                mx = int(cc["depth"])

            r += "<navPoint id=\"{0}\" playOrder=\"{1}\">" \
                 "<navLabel><text>{2}</text></navLabel>" \
                 "<content src=\"{3}\"/>".format(
                    cc["fragment"] if len(cc["fragment"]) else cc["id"], c,
                    escape(cc["label"]),
                    SafariBooks.to_xhtml_path(cc["href"].split("/files/", 1)[-1])
                    if cc["href"].startswith("http") else SafariBooks.to_xhtml_path(cc["href"])
                 )

            if cc["children"]:
                sr, c, mx = SafariBooks.parse_toc(cc["children"], c, mx)
                r += sr

            r += "</navPoint>\n"

        return r, c, mx

    def create_toc(self):
        response = self.requests_provider(urljoin(self.api_url, "toc/"))
        payload = None
        if response != 0 and response.status_code == 200:
            payload = self.parse_json_response(response, "toc v1")

        if isinstance(payload, list):
            navmap, _, max_depth = self.parse_toc(payload)
        else:
            self.display.info("Legacy TOC endpoint unavailable; generating TOC from downloaded chapter list.")
            synthetic_toc = [{
                "id": "chap_%s" % i,
                "fragment": "",
                "label": c.get("title", "Chapter %s" % i),
                "href": c.get("filename", ""),
                "depth": 1,
                "children": []
            } for i, c in enumerate(self.book_chapters, start=1)]
            navmap, _, max_depth = self.parse_toc(synthetic_toc)

        return self.TOC_NCX.format(
            (self.book_info["isbn"] if self.book_info["isbn"] else self.book_id),
            max_depth,
            self.book_title,
            ", ".join(aut.get("name", "") for aut in self.book_info.get("authors", [])),
            navmap
        )

    def create_epub(self):
        open(os.path.join(self.BOOK_PATH, "mimetype"), "w").write("application/epub+zip")
        meta_info = os.path.join(self.BOOK_PATH, "META-INF")
        if os.path.isdir(meta_info):
            self.display.log("META-INF directory already exists: %s" % meta_info)

        else:
            os.makedirs(meta_info)

        open(os.path.join(meta_info, "container.xml"), "wb").write(
            self.CONTAINER_XML.encode("utf-8", "xmlcharrefreplace")
        )
        open(os.path.join(self.BOOK_PATH, "OEBPS", "content.opf"), "wb").write(
            self.create_content_opf().encode("utf-8", "xmlcharrefreplace")
        )
        open(os.path.join(self.BOOK_PATH, "OEBPS", "toc.ncx"), "wb").write(
            self.create_toc().encode("utf-8", "xmlcharrefreplace")
        )

        zip_file = os.path.join(PATH, "Books", self.book_id)
        if os.path.isfile(zip_file + ".zip"):
            os.remove(zip_file + ".zip")

        shutil.make_archive(zip_file, 'zip', self.BOOK_PATH)
        os.rename(zip_file + ".zip", os.path.join(self.BOOK_PATH, self.book_id) + ".epub")


# MAIN
if __name__ == "__main__":
    arguments = argparse.ArgumentParser(prog="safaribooks.py",
                                        description="Download and generate an EPUB of your favorite books"
                                                    " from Safari Books Online.",
                                        add_help=False,
                                        allow_abbrev=False)

    login_arg_group = arguments.add_mutually_exclusive_group()
    login_arg_group.add_argument(
        "--cred", metavar="<EMAIL:PASS>", default=False,
        help="Credentials used to perform the auth login on Safari Books Online."
             " Es. ` --cred \"account_mail@mail.com:password01\" `."
    )
    login_arg_group.add_argument(
        "--login", action='store_true',
        help="Prompt for credentials used to perform the auth login on Safari Books Online."
    )

    arguments.add_argument(
        "--no-cookies", dest="no_cookies", action='store_true',
        help="Prevent your session data to be saved into `cookies.json` file."
    )
    arguments.add_argument(
        "--kindle", dest="kindle", action='store_true',
        help="Add some CSS rules that block overflow on `table` and `pre` elements."
             " Use this option if you're going to export the EPUB to E-Readers like Amazon Kindle."
    )
    arguments.add_argument(
        "--preserve-log", dest="log", action='store_true', help="Leave the `info_XXXXXXXXXXXXX.log`"
                                                                " file even if there isn't any error."
    )
    arguments.add_argument("--help", action="help", default=argparse.SUPPRESS, help='Show this help message.')
    arguments.add_argument(
        "bookid", metavar='<BOOK ID>',
        help="Book digits ID that you want to download. You can find it in the URL (X-es):"
             " `" + SAFARI_BASE_URL + "/library/view/book-name/XXXXXXXXXXXXX/`"
    )

    args_parsed = arguments.parse_args()
    if args_parsed.cred or args_parsed.login:
        print("WARNING: Due to recent changes on ORLY website, \n" \
                "the `--cred` and `--login` options are temporarily disabled.\n"
                "    Please use the `cookies.json` file to authenticate your account.\n"
                "    See: https://github.com/lorenzodifuccia/safaribooks/issues/358")
        arguments.exit()
        
        # user_email = ""
        # pre_cred = ""

        # if args_parsed.cred:
        #     pre_cred = args_parsed.cred

        # else:
        #     user_email = input("Email: ")
        #     passwd = getpass.getpass("Password: ")
        #     pre_cred = user_email + ":" + passwd

        # parsed_cred = SafariBooks.parse_cred(pre_cred)

        # if not parsed_cred:
        #     arguments.error("invalid credential: %s" % (
        #         args_parsed.cred if args_parsed.cred else (user_email + ":*******")
        #     ))

        # args_parsed.cred = parsed_cred

    else:
        if args_parsed.no_cookies:
            arguments.error("invalid option: `--no-cookies` is valid only if you use the `--cred` option")

    SafariBooks(args_parsed)
    # Hint: do you want to download more then one book once, initialized more than one instance of `SafariBooks`...
    sys.exit(0)

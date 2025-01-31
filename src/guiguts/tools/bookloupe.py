"""Bookloupe check functionality"""

# Based on http://www.juiblex.co.uk/pgdp/bookloupe which
# was based on https://sourceforge.net/projects/gutcheck

from typing import Optional

import logging
import regex as re
import roman  # type: ignore[import-untyped]

from guiguts.checkers import CheckerDialog, CheckerViewOptionsDialog, CheckerFilterText
from guiguts.maintext import maintext
from guiguts.misc_tools import tool_save
from guiguts.utilities import IndexRange, DiacriticRemover
from guiguts.widgets import ToolTip

logger = logging.getLogger(__package__)

_the_bookloupe_checker = None  # pylint: disable=invalid-name

# fmt: off
# Period after these is abbreviation, not end of sentence
_abbreviations = [
    "cent", "cents", "viz", "vol", "vols", "vid", "ed", "al", "etc",
    "op", "cit", "deg", "min", "chap", "oz", "mme", "mlle", "mssrs",
]
# Comma, colon, semicolon can't follow these
_nocomma = [
    "the", "it's", "their", "an", "mrs", "a", "our", "that's", "its", "whose",
    "every", "i'll", "your", "my", "mr", "mrs", "mss", "mssrs", "ft", "pm",
    "st", "dr", "rd", "pp", "cf", "jr", "sr", "vs", "lb", "lbs", "ltd", "i'm",
    "during", "let", "toward", "among",
]
# Period can't follow these
_noperiod = [
    "every", "i'm", "during", "that's", "their", "your", "our", "my", "or",
    "and", "but", "as", "if", "the", "its", "it's", "until", "than", "whether",
    "i'll", "whose", "who", "because", "when", "let", "till", "very", "an",
    "among", "those", "into", "whom", "having", "thence",
]
# Common typos
_typos = [
    "teh", "th", "og", "fi", "ro", "adn", "yuo", "ot", "fo", "thet", "ane",
    "nad", "te", "ig", "acn", "ahve", "alot", "anbd", "andt", "awya", "aywa",
    "bakc", "om", "btu", "byt", "cna", "cxan", "coudl", "dont", "didnt",
    "couldnt", "wouldnt", "doesnt", "shouldnt", "doign", "ehr", "hmi", "hse",
    "esle", "eyt", "fitrs", "firts", "foudn", "frmo", "fromt", "fwe", "gaurd",
    "gerat", "goign", "gruop", "haev", "hda", "hearign", "seeign", "sayign",
    "herat", "hge", "hsa", "hsi", "hte", "htere", "htese", "htey", "htis",
    "hvae", "hwich", "idae", "ihs", "iits", "int", "iwll", "iwth", "jsut",
    "loev", "sefl", "myu", "nkow", "nver", "nwe", "nwo", "ocur", "ohter",
    "omre", "onyl", "otehr", "otu", "owrk", "owuld", "peice", "peices",
    "peolpe", "peopel", "perhasp", "perhpas", "pleasent", "poeple", "porblem",
    "porblems", "rwite", "saidt", "saidh", "saids", "seh", "smae", "smoe",
    "sohw", "stnad", "stopry", "stoyr", "stpo", "tahn", "taht", "tath",
    "tehy", "tghe", "tghis", "theri", "theyll", "thgat", "thge", "thier",
    "thna", "thne", "thnig", "thnigs", "thsi", "thsoe", "thta", "timne",
    "tirne", "tkae", "tthe", "tyhat", "tyhe", "veyr", "vou", "vour", "vrey",
    "waht", "wasnt", "awtn", "watn", "wehn", "whic", "whcih", "whihc", "whta",
    "wihch", "wief", "wiht", "witha", "wiull", "wnat", "wnated", "wnats",
    "woh", "wohle", "wokr", "woudl", "wriet", "wrod", "wroet", "wroking",
    "wtih", "wuould", "wya", "yera", "yeras", "yersa", "yoiu", "youve",
    "ytou", "yuor", "abead", "ahle", "ahout", "ahove", "altbough", "balf",
    "bardly", "bas", "bave", "baving", "bebind", "beld", "belp", "belped",
    "ber", "bere", "bim", "bis", "bome", "bouse", "bowever", "buge",
    "dehates", "deht", "han", "hecause", "hecome", "heen", "hefore", "hegan",
    "hegin", "heing", "helieve", "henefit", "hetter", "hetween", "heyond",
    "hig", "higber", "huild", "huy", "hy", "jobn", "joh", "meanwbile",
    "memher", "memhers", "numher", "numhers", "perbaps", "prohlem", "puhlic",
    "witbout", "arn", "hin", "hirn", "wrok", "wroked", "amd", "aud",
    "prornise", "prornised", "modem", "bo", "heside", "chapteb", "chaptee", "se",
]
# Allowed to follow a number e.g. "19th"
_alnum_suffixes = [
    "st", "rd", "nd", "th", "sts", "rds", "nds", "ths",
    "stly", "rdly", "ndly", "thly",
    "^{st}", "^{rd}", "^{nd}", "^{th}", "^{sts}", "^{rds}", "^{nds}", "^{ths}",
    "^{stly}", "^{rdly}", "^{ndly}", "^{thly}",
    "l", "d", "s",
]
# Common scannos or letter pairs that rarely start words
_nostart = [
    "hr", "hl", "cb", "sb", "tb", "wb", "tl", "tn", "rn", "lt", "tj",
]
# Common scannos or letter pairs that rarely end words
_noend = [
    "cb", "gb", "pb", "sb", "tb", "wh", "fr", "br", "qu", "tw", "gl", "fl",
    "sw", "gr", "sl", "cl", "iy",
]
# Common scannos or letter groups that are rarely in words
_noanywhere = [
    "cb", "gbt", "pbt", "tbs", "mrn", "ahle", "ihle", "tbi", "tbe", "ii",
]
# Words that other tests will flag as typos
_okwords = [
    "mr", "mrs", "mss", "mssrs", "ft", "pm", "st", "dr", "hmm", "h'm", "hmmm",
    "rd", "sh", "br", "pp", "hm", "cf", "jr", "sr", "vs", "lb", "lbs", "ltd",
    "pompeii", "hawaii", "hawaiian", "hotbed", "heartbeat", "heartbeats",
    "outbid", "outbids", "frostbite", "frostbitten", "s^t",
]
# fmt: on
checker_filters = [
    CheckerFilterText("Asterisk", "Asterisk.*"),
    CheckerFilterText("Begins with punctuation", "Begins with punctuation.*"),
    CheckerFilterText("Broken em-dash", "Broken em-dash.*"),
    CheckerFilterText('Capital "S"', 'Capital "S".*'),
    CheckerFilterText("Caret character", "Caret character.*"),
    CheckerFilterText("Double punctuation", "Double punctuation.*"),
    CheckerFilterText("Endquote missing punctuation", "Endquote missing punctuation.*"),
    CheckerFilterText("Extra period", "Extra period.*"),
    CheckerFilterText("Forward slash", "Forward slash.*"),
    CheckerFilterText("HTML Tag", "HTML Tag.*"),
    CheckerFilterText("HTML symbol", "HTML symbol.*"),
    CheckerFilterText("Hyphen at end of line", "Hyphen at end of line.*"),
    CheckerFilterText("Long line", "Long line .*"),
    CheckerFilterText("Mismatched curly brackets", "Mismatched curly brackets.*"),
    CheckerFilterText("Mismatched double quotes", "Mismatched double quotes.*"),
    CheckerFilterText("Mismatched round brackets", "Mismatched round brackets.*"),
    CheckerFilterText("Mismatched single quotes", "Mismatched single quotes.*"),
    CheckerFilterText("Mismatched square brackets", "Mismatched square brackets.*"),
    CheckerFilterText("Mismatched underscores", "Mismatched underscores.*"),
    CheckerFilterText("Missing space", "Missing space.*"),
    CheckerFilterText("No punctuation at para end", "No punctuation at para end.*"),
    CheckerFilterText(
        "Paragraph starts with lower-case", "Paragraph starts with lower-case.*"
    ),
    CheckerFilterText("Query digit in", "Query digit in.*"),
    CheckerFilterText("Query had/bad error", "Query had/bad error.*"),
    CheckerFilterText("Query he/be error", "Query he/be error.*"),
    CheckerFilterText("Query hut/but error", "Query hut/but error.*"),
    CheckerFilterText("Query I=exclamation mark", "Query I=exclamation mark.*"),
    CheckerFilterText(
        "Query missing paragraph break", "Query missing paragraph break.*"
    ),
    CheckerFilterText("Query punctuation after", "Query punctuation after .*"),
    CheckerFilterText("Query single character line", "Query single character line.*"),
    CheckerFilterText("Query standalone 0", "Query standalone 0.*"),
    CheckerFilterText("Query standalone 1", "Query standalone 1.*"),
    CheckerFilterText("Query word", "Query word .*"),
    CheckerFilterText("Short line", "Short line .*"),
    CheckerFilterText("Spaced dash", "Spaced dash.*"),
    CheckerFilterText("Spaced em-dash", "Spaced em-dash.*"),
    CheckerFilterText("Spaced punctuation", "Spaced punctuation.*"),
    CheckerFilterText("Tab character", "Tab character.*"),
    CheckerFilterText("Tilde character", "Tilde character.*"),
    CheckerFilterText("Unspaced bracket", "Unspaced bracket.*"),
    CheckerFilterText("Unspaced quotes", "Unspaced quotes.*"),
    CheckerFilterText("Wrongspaced quotes", "Wrongspaced quotes.*"),
    CheckerFilterText("Wrongspaced singlequotes", "Wrongspaced singlequotes.*"),
]


class BookloupeCheckerViewOptionsDialog(CheckerViewOptionsDialog):
    """Minimal class to identify dialog type."""

    manual_page = "Tools_Menu#Bookloupe_View_Options"


class BookloupeCheckerDialog(CheckerDialog):
    """Minimal class to identify dialog type."""

    manual_page = "Tools_Menu#Bookloupe"


class BookloupeChecker:
    """Provides bookloupe check functionality."""

    def __init__(self) -> None:
        """Initialize BookloupeChecker class."""
        self.dictionary: dict[str, int] = {}
        self.dialog: Optional[BookloupeCheckerDialog] = None
        self.hebe_regex = re.compile(
            r'(?i)(\b(be could|be would|be is|was be|is be|to he)|",? be)\b'
        )
        self.hadbad_regex = re.compile(
            r"(?i)\b(the had|a had|they bad|she bad|he bad|you bad|i bad)\b"
        )
        self.hutbut_regex = re.compile(r"(?i)[;,] hut\b")

    def check_file(self) -> None:
        """Check for bookloupe errors in the currently loaded file."""

        # Create the checker dialog to show results
        self.dialog = BookloupeCheckerDialog.show_dialog(
            "Bookloupe Results",
            rerun_command=bookloupe_check,
            view_options_dialog_class=BookloupeCheckerViewOptionsDialog,
            view_options_filters=checker_filters,
        )
        ToolTip(
            self.dialog.text,
            "\n".join(
                [
                    "Left click: Select & find issue",
                    "Right click: Remove message from list",
                    "Shift-Right click: Remove all matching messages",
                ]
            ),
            use_pointer_pos=True,
        )
        self.dialog.reset()
        self.run_bookloupe()
        self.dialog.display_entries()

    def run_bookloupe(self) -> None:
        """Run the bookloupe checks and display the results in the dialog.

        Args:
            checkerdialog: Dialog to contain results.
        """
        next_step = 1
        para_first_step = 1
        paragraph = ""  # Store up paragraph for those checks that need whole para
        step_end = maintext().end().row
        while next_step <= step_end:
            step = next_step
            next_step += 1
            line = maintext().get(f"{step}.0", f"{step}.end")
            # If line is block markup or all asterisks/hyphens, pretend it's empty
            if self.is_skippable_line(line):
                line = ""
            # If it's a page separator, skip the line
            if self.is_page_separator(line):
                continue
            # Are we starting a new paragraph?
            if line and not paragraph:
                para_first_step = step
                paragraph = ""
            # Deal with blank line
            if not line:
                # If paragraph has just ended, check quotes, etc. & ending punctuation
                if paragraph:
                    self.check_para(para_first_step, step - 1, paragraph)
                    paragraph = ""
                continue
            # Normal line
            self.check_odd_characters(step, line)
            self.check_hyphens(step, line)
            self.check_line_length(step, line)
            self.check_starting_punctuation(step, line)
            self.check_missing_para_break(step, line)
            self.check_jeebies(step, line)
            self.check_orphan_character(step, line)
            self.check_pling_scanno(step, line)
            self.check_extra_period(step, line)
            self.check_following_punctuation(step, line)
            self.check_typos(step, line)
            self.check_misspaced_punctuation(step, line)
            self.check_double_punctuation(step, line)
            self.check_miscased_genitive(step, line)
            self.check_unspaced_bracket(step, line)
            self.check_unpunctuated_endquote(step, line)
            self.check_html_tag(step, line)
            self.check_html_entity(step, line)
            # Add line to paragraph
            if paragraph:
                paragraph += "\n" + line
            else:
                paragraph = line
        # End of file - check the final para
        if paragraph:
            self.check_para(para_first_step, step, paragraph)

    def check_para(self, para_start: int, para_end: int, para_text: str) -> None:
        """Check quotes & brackets are paired within given paragraph.
        Also that paragaph ends with suitable punctuation.

        For now, to be compatible with historic bookloupe, only checks
        straight quotes, and just does a simple count of open/close brackets.

        Args:
            para_start: First line number of paragraph.
            para_end: Last line number of paragraph.
            para_text: Text of paragraph.
        """
        assert self.dialog is not None
        start_index = f"{para_start}.0"
        end_index = maintext().index(f"{para_end}.end")
        para_range = IndexRange(start_index, end_index)
        # Straight double quotes - an odd number means a potential error unless
        # the next paragraph starts with a double quote
        if para_text.count('"') % 2 and maintext().get(f"{para_end}.0+2l") != '"':
            self.dialog.add_entry(
                "Mismatched double quotes",
                para_range,
            )
        # Check double quotes are correctly spaced
        quotes_open = False
        quote_index = f"{start_index}-1c"
        while True:
            quote_index = maintext().search('"', f"{quote_index}+1c", end_index)
            if not quote_index:
                break
            quote_index_p1 = maintext().index(f"{quote_index}+1c")
            # Attempt to ignore ditto marks (double space or line break both sides)
            # Get two characters each side of quotes, i.e. 'XX"XX'
            test_text = maintext().get(f"{quote_index}-2c", f"{quote_index}+3c")
            if len(test_text) != 5:  # Never happens in real life
                continue
            if (
                test_text[1:5] == '\n"  '
                or test_text[0:4] == '  "\n'
                or test_text == '  "  '
            ):
                continue
            # Always an error to not have whitespace or punctuation on one side or the other
            space_punc_regex = r"[\s\p{Punctuation}]"
            if not re.match(space_punc_regex, test_text[1]) and not re.match(
                space_punc_regex, test_text[3]
            ):
                self.dialog.add_entry(
                    "Unspaced quotes?",
                    IndexRange(quote_index, quote_index_p1),
                )
                continue
            # Check for space after quotes when quotes are already open or at start of line
            # or for space before quotes when quotes are not already open or at end of line
            should_be_close = quotes_open or test_text[3] == "\n"
            should_be_open = not quotes_open or test_text[1] == "\n"
            if (should_be_close and test_text[1] == " ") or (
                should_be_open and test_text[3] == " "
            ):
                self.dialog.add_entry(
                    "Wrongspaced quotes?",
                    IndexRange(quote_index, quote_index_p1),
                )
                continue
            # Only toggle flag if no error, otherwise get lots of reports from one early error
            quotes_open = not quotes_open

        # Check single quotes are correctly spaced
        # Apostrophes mess things up, so just check start/end of line
        quote_index = f"{start_index}-1c"
        while True:
            quote_index = maintext().search(
                "(^' | '$)", f"{quote_index}+1c", end_index, regexp=True
            )
            if not quote_index:
                break
            self.dialog.add_entry(
                "Wrongspaced singlequotes?",
                IndexRange(quote_index, maintext().index(f"{quote_index}+2c")),
            )

        # Straight single quotes - add the open quotes, subtract the close quotes,
        # try to allow for apostrophes, so should get zero. Allow +1 if the next
        # paragraph starts with a single quote
        open_quote_count = len(re.findall(r"(?<!\p{Letter})'(?=\p{Letter})", para_text))
        open_quote_count -= len(re.findall(r"'[Tt]is\b", para_text))  # Common exception
        close_quote_count = len(
            re.findall(r"(?<=[\p{Letter}\p{Punctuation}])'(?!\p{Letter})", para_text)
        )
        if open_quote_count != close_quote_count and (
            open_quote_count != close_quote_count + 1
            or maintext().get(f"{para_end}.0+2l") != "'"
        ):
            self.dialog.add_entry(
                "Mismatched single quotes?",
                para_range,
            )
        # Underscores - should be an even number
        if para_text.count("_") % 2:
            self.dialog.add_entry(
                "Mismatched underscores?",
                para_range,
            )
        # Brackets - should be equal number of open & close
        if para_text.count("(") != para_text.count(")"):
            self.dialog.add_entry(
                "Mismatched round brackets?",
                para_range,
            )
        if para_text.count("[") != para_text.count("]"):
            self.dialog.add_entry(
                "Mismatched square brackets?",
                para_range,
            )
        if para_text.count("{") != para_text.count("}"):
            self.dialog.add_entry(
                "Mismatched curly brackets?",
                para_range,
            )
        # Does paragraph begin with a lowercase letter?
        # Skip markup, spaces or non-alphanumerics before first letter
        skip_para = re.sub(r"^(<.+?>|[ \P{IsAlnum}])+", "", para_text)
        if re.match(r"\p{Lowercase_Letter}", skip_para):
            skip_len = len(para_text) - len(skip_para)
            self.dialog.add_entry(
                "Paragraph starts with lower-case",
                IndexRange(
                    maintext().rowcol(f"{start_index}+{skip_len}c"),
                    maintext().rowcol(f"{start_index}+{skip_len + 1}c"),
                ),
            )
        # Does paragraph end with suitable punctuation
        # Ignore single line paragraphs & those without any lowercase letters,
        # in order to avoid false positives from chapter headings, etc.
        if para_start == para_end or not re.search(r"\p{Lowercase_Letter}", para_text):
            return
        # Ignoring any character that is not alphanumeric or sentence-ending punctuation,
        # last character (ignoring inline markup) must be sentence-ending punctuation.
        para_punc = re.escape("])}-—.:!?")
        last_line = para_text.splitlines()[-1]
        last_line = self.remove_inline_markup(last_line)
        last_line = re.sub(rf"[^{para_punc}\p{{Letter}}\p{{Number}}]", "", last_line)
        if last_line and last_line[-1] not in para_punc:
            self.dialog.add_entry(
                "No punctuation at para end?",
                IndexRange(maintext().rowcol(f"{end_index}-1c"), end_index),
            )

    def check_odd_characters(self, step: int, line: str) -> None:
        """Check for tabs, tildes, etc.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        assert self.dialog is not None
        # Regexes & names of odd characters
        odd_char_names = {
            r"\t+": "Tab character?",
            r"~+": "Tilde character?",
            r"\^+": "Caret character?",
            r"(?<!\d)/+|/+(?!\d)": "Forward slash?",  # But not if surrounded by numbers (e.g 3/4)
            r"\*+": "Asterisk",
        }
        # Hide slash in "</x>" - keep line length the same
        prep_line = re.sub("(?<=<)/(?=.+?>)", " ", line)
        # Find occurrences of one or more consecutive odd chars
        for regex, msg in odd_char_names.items():
            for match in re.finditer(regex, prep_line):
                self.add_match_entry(step, match, msg)

    def check_hyphens(self, step: int, line: str) -> None:
        """Check for leading/trailing hyphens, etc.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        assert self.dialog is not None
        # Single (not double) hyphen at end of line
        if len(line) > 1 and line[-1] == "-" and line[-2] != "-":
            # If next line starts with hyphen, broken emdash?
            if maintext().get(f"{step + 1}.0") == "-":
                self.dialog.add_entry(
                    "Broken em-dash?",
                    IndexRange(maintext().rowcol(f"{step}.end-1c"), f"{step + 1}.1"),
                )
            # Otherwise query end of line hyphen
            else:
                self.dialog.add_entry(
                    "Hyphen at end of line?",
                    IndexRange(
                        maintext().rowcol(f"{step}.end-1c"),
                        maintext().rowcol(f"{step}.end"),
                    ),
                )
        # Spaced emdash (4 hyphens represents a word, so is allowed to be spaced)
        for match in re.finditer(" -- |(?<!--)-- | --(?!--)", line):
            self.add_match_entry(step, match, "Spaced em-dash?")
        # Spaced single hyphen/dash (don't report emdashes again)
        for match in re.finditer(" - |(?<!-)- | -(?!-)", line):
            self.add_match_entry(step, match, "Spaced dash?")

    def check_line_length(self, step: int, line: str) -> None:
        """Check for long or short lines.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
            para_text: Text of paragraph up to this point,
        """
        assert self.dialog is not None
        longest_pg_line = 75
        shortest_pg_line = 55
        line_len = len(line)
        if line_len > longest_pg_line:
            self.dialog.add_entry(
                f"Long line {line_len}",
                IndexRange(f"{step}.{longest_pg_line}", f"{step}.{line_len + 1}"),
            )
            return
        # Short lines are not reported if they are not short!
        if line_len >= shortest_pg_line:
            return
        # Nor if they are indented (e.g. poetry)
        if line_len > 0 and line[0] == " ":
            return
        # Nor if they are the last line of a paragraph (allowed to be short)
        # Look backwards to find first non-skippable line & check if it's blank
        end_step = maintext().end().row
        for check_step in range(step + 1, end_step + 1):
            check_line = maintext().get(f"{check_step}.0", f"{check_step}.end")
            if not (
                self.is_skippable_line(check_line) or self.is_page_separator(check_line)
            ):
                if len(check_line) == 0:
                    return
                break
        # Nor if the previous line was a short line (may be short-lined para, such as letter header)
        # Look backwards to find first non-skippable line & check its length
        for check_step in range(step - 1, 0, -1):
            check_line = maintext().get(f"{check_step}.0", f"{check_step}.end")
            if not (
                self.is_skippable_line(check_line) or self.is_page_separator(check_line)
            ):
                if (
                    len(check_line) <= shortest_pg_line
                ):  # <= rather than < for backward compatibility
                    return
                break
        # None of the situations above happened, so it's a suspect short line
        self.dialog.add_entry(
            f"Short line {line_len}?",
            IndexRange(f"{step}.0", f"{step}.{line_len + 1}"),
        )

    def check_starting_punctuation(self, step: int, line: str) -> None:
        """Check for bad punctuation at start of line

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        assert self.dialog is not None
        if re.match(r"[?!,;:]|\.(?!( \. \.|\.\.))", line):
            self.dialog.add_entry(
                "Begins with punctuation?",
                IndexRange(f"{step}.0", f"{step}.1"),
            )

    def check_missing_para_break(self, step: int, line: str) -> None:
        """Check for missing paragraph break between quotes - straight doubles only.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        for match in re.finditer(r'(?<!(^ ?|  ))"  ?"(?!( ?$|  ))', line):
            self.add_match_entry(
                step,
                match,
                "Query missing paragraph break?",
            )

    def check_jeebies(self, step: int, line: str) -> None:
        """Check for common he/be and other h/b errors.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        assert self.dialog is not None
        for match in re.finditer(self.hebe_regex, line):
            self.add_match_entry(step, match, "Query he/be error?")
        for match in re.finditer(self.hadbad_regex, line):
            self.add_match_entry(step, match, "Query had/bad error?")
        for match in re.finditer(self.hutbut_regex, line):
            self.add_match_entry(step, match, "Query hut/but error?")

    def check_orphan_character(self, step: int, line: str) -> None:
        """Check for single character line, except (chapter/section/Roman?) numbers

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        assert self.dialog is not None
        if len(line) == 1 and line[0] not in "IVXL0123456789":
            self.dialog.add_entry(
                "Query single character line",
                IndexRange(f"{step}.0", f"{step}.1"),
            )

    def check_pling_scanno(self, step: int, line: str) -> None:
        """Check for ` I"`- often should be ` !`

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        for match in re.finditer(' I"', line):
            self.add_match_entry(step, match, "Query I=exclamation mark?")

    def check_extra_period(self, step: int, line: str) -> None:
        """Check for period not followed by capital letter.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        # Allow inline markup before start of next sentence as well as other non-word characters,
        # such as quotes or brackets. use `[^\w<]` rather than `\W`to stop it matching inline markup at that point
        for match in re.finditer(
            r"(\p{Letter}+)[\"”'’\)]*(\. (</?[a-z]+>)*[^\w<]*\p{Lowercase_Letter})",
            line,
        ):
            # Get the word before the period
            test_word = match[1]
            # Ignore single letter words or common abbreviations
            if len(test_word) == 1 or test_word.lower() in _abbreviations:
                continue
            # Ignore valid Roman numerals
            try:
                roman.fromRoman(test_word.upper())
                continue
            except roman.InvalidRomanNumeralError:
                pass
            # Only report if previous word contains vowels
            # (backward compatibility, except for addition of "y" as a vowel, for words like "try")
            if re.search("[aeiouy]", DiacriticRemover.remove_diacritics(test_word)):
                self.add_match_entry(step, match, "Extra period?", group=2)

    def check_following_punctuation(self, step: int, line: str) -> None:
        """Check for surprising punctuation following certain words.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        # Loop for no-comma words and no-period words
        for sep_regex, word_list in ((r"[,;:]", _nocomma), (r"\.", _noperiod)):
            for word in word_list:
                for match in re.finditer(
                    rf"\b({word}){sep_regex}", line, flags=re.IGNORECASE
                ):
                    # Use correctly-cased match in message, not lower-case word
                    self.add_match_entry(
                        step, match, f"Query punctuation after {match[1]}?"
                    )

    def check_typos(self, step: int, line: str) -> None:
        """Check for common typos.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        # Consider hyphenated words (but not numbers, e.g. 1-3/4) as two separate words
        line = re.sub(r"(?<!\d)-(?!\d)", " ", line)
        # Split at spaces, ignoring leading/trailing non-word characters on words
        for match in re.finditer(
            r"(?<![^ ])[^\p{Letter}\p{Number}'’{}]*(.+?)[^\p{Letter}\p{Number}'’{}]*(?![^ ])",
            line,
        ):
            # Trim any markup or footnote remnants left at start/end of word
            word = re.sub(r"^.+>|<.+$|\[.+$", "", match[1])
            word_lower = word.lower()
            # Query standalone 0 or 1 except in `^[Footnote 1:`
            if word in ("0", "1") and (
                match.start() != 10
                or maintext().get(f"{step}.0", f"{step}.12") != "[Footnote 1:"
            ):
                self.add_match_entry(step, match, f"Query standalone {word}")
                continue
            # Check for mixed alpha & numeric (with some exceptions)
            if re.search(r"\p{Letter}", word_lower) and re.search(
                r"\p{Number}", word_lower
            ):
                # If number followed by acceptable suffix, it's OK (e.g. 1st)
                suffix = re.sub(r"^[\p{Number},]+", "", word_lower)
                # If "L/l" followed by number, it's OK (English pounds)
                prefix = re.sub(r"[\p{Number},]+$", "", word_lower)
                if suffix not in _alnum_suffixes and prefix != "l":
                    self.add_match_entry(step, match, f"Query digit in {word}?")
                    continue
            # if not Latin script, then checks below are pointless
            if not re.search(r"[\p{Latin}\p{Common}]", word_lower):
                continue
            # Set typo flag false at start, then re-set it under various circumstances
            # Since few words are typos, avoid code complexity of repeated "if not typo:"
            # and just do all the tests anyway - very little time wasted.
            typo = False
            # Check for mixed case (uppercase after lower case) (with some exceptions)
            # Allow MacDonald and l'Abbe
            if (
                re.search(r"\p{Lowercase_Letter}.*\p{Uppercase_Letter}", word)
                and not re.fullmatch(
                    r"Ma?c\p{Uppercase_Letter}\p{Lowercase_Letter}+", word
                )
                and not re.fullmatch(
                    r"\p{Letter}*{Lowercase_Letter}+['’]\p{Uppercase_Letter}{Lowercase_Letter}+",
                    word,
                )
            ):
                typo = True
            for combo in _nostart:
                if word_lower.startswith(combo):
                    typo = True
            for combo in _noend:
                if word_lower.endswith(combo):
                    typo = True
            for combo in _noanywhere:
                if combo in word_lower:
                    typo = True
            # Words should have at least 1 vowel and 1 consonant ("y" and digits count as both!)
            word_no_accent = DiacriticRemover.remove_diacritics(word_lower)
            if len(word_no_accent) > 1 and not (
                re.search("[0-9aeiouy]", word_no_accent)
                and re.search("[0-9b-df-hj-np-tv-z]", word_no_accent)
            ):
                typo = True
            # Ignore valid Roman numerals
            if typo:
                try:
                    roman.fromRoman(word.upper())
                    typo = False
                except roman.InvalidRomanNumeralError:
                    pass
                # Certain words are always good
                for ok_word in _okwords:
                    if word_lower == ok_word:
                        typo = False
            # But certain words are always typos
            for typo_word in _typos:
                if word_lower == typo_word:
                    typo = True
            # Also certain single lowercase letters: "s", "l", "i", "m" and
            # "j" = ";"; "d" in "he d" (missing apostrophe); "n" for "in"
            if len(word) == 1 and word in "slimjdn":
                typo = True
            if typo:
                self.add_match_entry(step, match, f"Query word {word}")

    def check_misspaced_punctuation(self, step: int, line: str) -> None:
        """Check for mis-spaced punctuation.

        Examples are `!` with spaces both sides, or no space either side.
        Also badly spaced quotes.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        # Find all punctuation
        for match in re.finditer(r"\.?!,;:_", line):
            punctuation = match[0]
            position = match.start()
            # Not checking punctuation at start of line here
            if position == 0:
                continue
            # Check for letters both sides, or letter directly after "?!,;:"
            if (position < len(line) - 1 and line[position + 1].isalpha()) and (
                line[position - 1].isalpha() or punctuation in "?!,;:"
            ):
                # But it's OK if it's a period and there's another 2 steps before/after, e.g. "M.D."
                if not (
                    punctuation == "."
                    and (position >= 2 and line[position - 2] == ".")
                    or (position < len(line) - 2 and line[position + 2] == ".")
                ):
                    self.add_match_entry(step, match, "Missing space?")
                    continue
            # Check for space before punctuation
            if line[position - 1] == " ":
                # For period, it's OK unless end of line or next character is a letter
                # For all other punctuation, it's badly spaced
                if (
                    punctuation != "."
                    or position >= len(line) - 1
                    or line[position + 1].isalpha()
                ):
                    self.add_match_entry(step, match, "Spaced punctuation?")
                    continue

    def check_double_punctuation(self, step: int, line: str) -> None:
        """Check for double punctuation except "..", "!!" or "??".

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        for match in re.finditer(r"[.?!,;:]{2,}", line):
            if re.fullmatch(r"([.?!])\1+", match[0]):
                continue
            self.add_match_entry(
                step,
                match,
                "Double punctuation?",
            )

    def check_miscased_genitive(self, step: int, line: str) -> None:
        """Check for "lowercase'S".

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        for match in re.finditer(r"(?<=\p{Lowercase_Letter}['’])S", line):
            self.add_match_entry(
                step,
                match,
                'Capital "S"?',
            )

    def check_unspaced_bracket(self, step: int, line: str) -> None:
        """Check for "a]most", i.e. bracket surrounded by letters.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        for match in re.finditer(r"(?<=\p{Letter})[][}{)(](?=\p{Letter})", line):
            self.add_match_entry(
                step,
                match,
                "Unspaced bracket?",
            )

    def check_unpunctuated_endquote(self, step: int, line: str) -> None:
        """Check for letter followed by double quote.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        for match in re.finditer(r'(?<=\p{Letter})["”]', line):
            self.add_match_entry(
                step,
                match,
                "Endquote missing punctuation?",
            )

    def check_html_tag(self, step: int, line: str) -> None:
        """Check for "<" and ">" with something between.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        for match in re.finditer(r"</?[a-z].*?>", line):
            if re.fullmatch(r"</?(i|b|f|g|sc)>", match[0]):
                continue
            self.add_match_entry(
                step,
                match,
                f"HTML Tag? {match[0]}",
            )

    def check_html_entity(self, step: int, line: str) -> None:
        """Check for "&" and ";" with something reasonable between.

        Args:
            step: Line number being checked.
            line: Text of line being checked.
        """
        for match in re.finditer(r"&#?\p{IsAlnum}+;", line):
            self.add_match_entry(
                step,
                match,
                f"HTML symbol? {match[0]}",
            )

    def add_match_entry(
        self, step: int, match: re.Match, message: str, group: int = 0
    ) -> None:
        """Add message about given match to dialog.

        Args:
            step: Line number being checked.
            match: Match for error on line.
            message: Text for error message.
            group: Optional captured group number
        """
        assert self.dialog is not None
        self.dialog.add_entry(
            message,
            IndexRange(f"{step}.{match.start(group)}", f"{step}.{match.end(group)}"),
        )

    def is_skippable_line(self, line: str) -> bool:
        """Return whether line should be skipped.

        Lines that contain DP block markup or thought breaks
        (`<tb>` or only asterisks/hyphens and spaces).

        Args:
            line: Text of line being checked.

        Returns:
            True if line should be skipped.
        """
        return bool(
            re.fullmatch(
                r"(/[\$#xf\*plrci](\[\d+)?(\.\d+)?(,\d+)?]?|[\$#xf\*plrci]/|<tb>|[* ]+|[- ]+) *",
                line,
                flags=re.IGNORECASE,
            )
        )

    def is_page_separator(self, line: str) -> bool:
        """Return True if a page separator line"""
        return line.startswith("-----File:")

    def remove_inline_markup(self, string: str) -> str:
        """Remove all types of DP inline markup from given string.

        Args:
            line: Text to be checked.

        Returns:
            String with DP inline markup  removed.
        """
        return re.sub(r"</?([ibfg]|sc)>", "", string)


def bookloupe_check() -> None:
    """Check for jeebies in the currently loaded file."""
    global _the_bookloupe_checker

    if not tool_save():
        return

    if _the_bookloupe_checker is None:
        _the_bookloupe_checker = BookloupeChecker()

    _the_bookloupe_checker.check_file()

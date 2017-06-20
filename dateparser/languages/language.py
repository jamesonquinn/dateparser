# -*- coding: utf-8 -*-
import regex as re
from itertools import chain

from dateutil import parser

from dateparser.timezone_parser import pop_tz_offset_from_string
from dateparser.utils import wrap_replacement_for_regex, normalize_unicode

from .dictionary import Dictionary, NormalizedDictionary, ALWAYS_KEEP_TOKENS
from .validation import LanguageValidator


class Language(object):
    _dictionary = None
    _normalized_dictionary = None
    _simplifications = None
    _simplification_patterns = None
    _normalized_simplifications = None
    _splitters = None
    _wordchars = None

    def __init__(self, shortname, language_info):
        self.shortname = shortname
        self.info = language_info

    def validate_info(self, validator=None):
        if validator is None:
            validator = LanguageValidator

        return validator.validate_info(language_id=self.shortname, info=self.info)

    def is_applicable(self, date_string, strip_timezone=False, settings=None):
        if strip_timezone:
            date_string, _ = pop_tz_offset_from_string(date_string, as_offset=False)

        date_string = self._simplify(date_string, settings=settings)
        tokens = self._split(date_string, keep_formatting=False, settings=settings)
        if self._is_date_consists_of_digits_only(tokens):
            return True
        else:
            return self._are_all_words_in_the_dictionary(tokens, settings)

    def translate(self, date_string, keep_formatting=False, settings=None):
        date_string = self._simplify(date_string, settings=settings)
        words = self._split(date_string, keep_formatting, settings=settings)

        dictionary = self._get_dictionary(settings)
        for i, word in enumerate(words):
            word = word.lower()
            if word in dictionary:
                words[i] = dictionary[word] or ''
        if "in" in words:
            words = self._clear_future_words(words)

        return self._join(
            list(filter(bool, words)), separator="" if keep_formatting else " ", settings=settings)

    def translate_search(self, search_string, settings=None):
        dashes = ['-', '——', '—', '～']
        sentences = self._sentence_split(search_string)
        dictionary = self._get_dictionary(settings)
        translated = []
        original = []
        for sentence in sentences:
            words = self._word_split(sentence, settings=settings)
            translated_chunk = []
            original_chunk = []
            for i, word in enumerate(words):
                word = self._simplify(word.lower(), settings=settings)
                if word.strip('()\"{}[],.') in dictionary and word not in dashes:
                    translated_chunk.append(dictionary[word.strip('()\"{}[],.')])
                    original_chunk.append(words[i])
                elif self._token_with_digits_is_ok(word):
                    translated_chunk.append(word)
                    original_chunk.append(words[i])
                else:
                    if translated_chunk:
                        translated.append(translated_chunk)
                        translated_chunk = []
                        original.append(original_chunk)
                        original_chunk = []
            if translated_chunk:
                translated.append(translated_chunk)
                original.append(original_chunk)
        for i in range(len(translated)):
            if "in" in translated[i]:
                translated[i] = self._clear_future_words(translated[i])
            translated[i] = self._join_chunk(list(filter(bool, translated[i])), settings=settings)
            original[i] = self._join_chunk(list(filter(bool, original[i])), settings=settings)
        return translated, original

    def _sentence_split(self, string):
        splitters_dict = {1: '[\.!?;…\r\n]+(?:\s|$)*',  # most European, Tagalog, Hebrew, Georgian,
                                                        # Indonesian, Vietnamese
                          2: '(?:[¡¿]+|[\.!?;…\r\n]+(?:\s|$))*',  # Spanish
                          3: '[|!?;\r\n]+(?:\s|$)*',  # Hindi and Bangla
                          4: '[。…‥\.!?？！;\r\n]+(?:\s|$)*',  # Japanese and Chinese
                          5: '[\r\n]+',  # Thai
                          6: '[\r\n؟!\.…]+(?:\s|$)*'}  # Arabic and Farsi
        if 'sentence_splitter_group' not in self.info:
            sentences = re.split(splitters_dict[1], string)
        else:
            sentences = re.split(splitters_dict[self.info['sentence_splitter_group']], string)
        for i in sentences:
            if not i:
                sentences.remove(i)
        return sentences

    def _word_split(self, string, settings):
        if 'no_word_spacing' in self.info:
            return self._split(string, keep_formatting=True, settings=settings)
        else:
            return string.split()

    def _join_chunk(self, chunk, settings):
        if 'no_word_spacing' in self.info:
            return self._join(chunk, separator="", settings=settings)
        else:
            return " ".join(chunk)

    def _token_with_digits_is_ok(self, token):
        if 'no_word_spacing' in self.info:
            if re.search('[\d\.:\-/]+', token) is not None:
                return True
            else:
                return False

        else:
            if re.search('\d+', token) is not None:
                return True
            else:
                return False

    def _simplify(self, date_string, settings=None):
        date_string = date_string.lower()
        for simplification in self._get_simplifications(settings=settings):
            pattern, replacement = self._get_simplification_substitution(simplification)
            date_string = pattern.sub(replacement, date_string).lower()
        return date_string

    def _get_simplification_substitution(self, simplification):
        pattern, replacement = list(simplification.items())[0]
        if not self.info.get('no_word_spacing', False):
            replacement = wrap_replacement_for_regex(replacement, pattern)
            pattern = r'(\A|\d|_|\W)%s(\d|_|\W|\Z)' % pattern

        if self._simplification_patterns is None:
            self._simplification_patterns = {}

        if pattern not in self._simplification_patterns:
            self._simplification_patterns[pattern] = re.compile(pattern, flags=re.IGNORECASE | re.UNICODE)
        pattern = self._simplification_patterns[pattern]
        return pattern, replacement

    def _clear_future_words(self, words):
        freshness_words = set(['day', 'week', 'month', 'year', 'hour', 'minute', 'second'])
        if set(words).isdisjoint(freshness_words):
            words.remove("in")
        return words

    def _is_date_consists_of_digits_only(self, tokens):
        for token in tokens:
            if not token.isdigit():
                return False
        else:
            return True

    def _are_all_words_in_the_dictionary(self, words, settings=None):
        dictionary = self._get_dictionary(settings=settings)
        for word in words:
            word = word.lower()
            if (word.isdigit() or word in dictionary):
                continue
            else:
                return False
        else:
            return True

    def _split(self, date_string, keep_formatting, settings=None):
        tokens = [date_string]
        tokens = list(self._split_tokens_with_regex(tokens, r"(\d+)"))
        tokens = list(
            self._split_tokens_by_known_words(tokens, keep_formatting, settings=settings))
        return tokens

    def _split_tokens_with_regex(self, tokens, regex):
        tokens = tokens[:]
        for i, token in enumerate(tokens):
            tokens[i] = re.split(regex, token)
        return filter(bool, chain(*tokens))

    def _split_tokens_by_known_words(self, tokens, keep_formatting, settings=None):
        dictionary = self._get_dictionary(settings)
        for i, token in enumerate(tokens):
            tokens[i] = dictionary.split(token, keep_formatting)
        return list(chain(*tokens))

    def _join(self, tokens, separator=" ", settings=None):
        if not tokens:
            return ""

        capturing_splitters = self._get_splitters(settings)['capturing']
        joined = tokens[0]
        for i in range(1, len(tokens)):
            left, right = tokens[i - 1], tokens[i]
            if left not in capturing_splitters and right not in capturing_splitters:
                joined += separator
            joined += right

        return joined

    def _get_dictionary(self, settings=None):
        if not settings.NORMALIZE:
            if self._dictionary is None:
                self._generate_dictionary()
            self._dictionary._settings = settings
            return self._dictionary
        else:
            if self._normalized_dictionary is None:
                self._generate_normalized_dictionary()
            self._normalized_dictionary._settings = settings
            return self._normalized_dictionary

    def _get_wordchars(self, settings=None):
        if self._wordchars is None:
            self._set_wordchars(settings)
        return self._wordchars

    def _get_splitters(self, settings=None):
        if self._splitters is None:
            self._set_splitters(settings)
        return self._splitters

    def _set_splitters(self, settings=None):
        splitters = {
            'wordchars': set(),  # The ones that split string only if they are not surrounded by letters from both sides
            'capturing': set(),  # The ones that are not filtered out from tokens after split
        }
        splitters['capturing'] |= set(ALWAYS_KEEP_TOKENS)

        wordchars = self._get_wordchars(settings)
        skip = set(self.info.get('skip', [])) | splitters['capturing']
        for token in skip:
            if not re.match(r'^\W+$', token, re.UNICODE):
                continue
            if token in wordchars:
                splitters['wordchars'].add(token)

        self._splitters = splitters

    def _set_wordchars(self, settings=None):
        wordchars = set()
        for word in self._get_dictionary(settings):
            if re.match(r'^[\W\d_]+$', word, re.UNICODE):
                continue
            for char in word:
                wordchars.add(char.lower())

        self._wordchars = wordchars - {" "} | {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}

    def _generate_dictionary(self, settings=None):
        self._dictionary = Dictionary(self.info, settings=settings)

    def _generate_normalized_dictionary(self, settings=None):
        self._normalized_dictionary = NormalizedDictionary(self.info, settings=settings)

    def _get_simplifications(self, settings=None):
        if not settings.NORMALIZE:
            if self._simplifications is None:
                self._simplifications = self._generate_simplifications(
                    normalize=False)
            return self._simplifications
        else:
            if self._normalized_simplifications is None:
                self._normalized_simplifications = self._generate_simplifications(
                    normalize=True)
            return self._normalized_simplifications

    def _generate_simplifications(self, normalize=False):
        simplifications = []
        for simplification in self.info.get('simplifications', []):
            c_simplification = {}
            key, value = list(simplification.items())[0]
            if normalize:
                key = normalize_unicode(key)

            if isinstance(value, int):
                c_simplification[key] = str(value)
            else:
                c_simplification[key] = normalize_unicode(value) if normalize else value

            simplifications.append(c_simplification)
        return simplifications

    def to_parserinfo(self, base_cls=parser.parserinfo):
        attributes = {
            'JUMP': self.info.get('skip', []),
            'PERTAIN': self.info.get('pertain', []),
            'WEEKDAYS': [self.info['monday'],
                         self.info['tuesday'],
                         self.info['wednesday'],
                         self.info['thursday'],
                         self.info['friday'],
                         self.info['saturday'],
                         self.info['sunday']],
            'MONTHS': [self.info['january'],
                       self.info['february'],
                       self.info['march'],
                       self.info['april'],
                       self.info['may'],
                       self.info['june'],
                       self.info['july'],
                       self.info['august'],
                       self.info['september'],
                       self.info['october'],
                       self.info['november'],
                       self.info['december']],
            'HMS': [self.info['hour'],
                    self.info['minute'],
                    self.info['second']],
        }
        name = '{language}ParserInfo'.format(language=self.info['name'])
        return type(name, bases=[base_cls], dict=attributes)

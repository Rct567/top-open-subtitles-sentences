# Build the top-open-subtitles-sentences repository

from concurrent.futures import ProcessPoolExecutor
from functools import partial
import os
import shutil
import sys
import time
import zipfile
import gzip
import re
import itertools
from collections import Counter
import pandas as pd
import requests


###############################################################################
# Settings

# languages (see valid_langcodes)
run_langcodes = ["af", "ar", "bg", "bn", "br", "bs", "ca", "cs", "da", "de",
             "el", "en", "eo", "es", "et", "eu", "fa", "fi", "fr", "gl",
             "he", "hi", "hr", "hu", "hy", "id", "is", "it", "ja", "ka",
             "kk", "ko", "lt", "lv", "mk", "ml", "ms", "nl", "no", "pl",
             "pt", "pt_br", "ro", "ru", "si", "sk", "sl", "sq", "sr",
             "sv", "ta", "te", "th", "tl", "tr", "uk", "ur", "vi",
             "ze_en", "ze_zh", "zh_cn", "zh_tw"]

# type of corpus data to use as source
source_data_type = "raw"   # "raw", "text", "tokenized"

# parts to run
get_source_data = True
redownload_source_data = False
get_parsed_text = True
get_sentences = True
get_words = True
get_words_using_tokenized = False
get_summary_table = False
delete_tmpfile = True
delete_source_data = True
always_keep_raw_data = True

# parsing settings (only in effect when source_data_type = "raw")
year_min = 0   # lowest: 0
year_max = 2018   # largest: 2018

# performance
download_chunk_size = 1000000
min_count = 5
lines_per_chunk = 10000000
n_process = 6

# finetuning
original_language_only = False
one_subtitle_per_movie = False
use_regex_tokenizer = False
regex_tokenizer_pattern = r"\w+|[^\w\s]+"
linestrip_pattern = " /-–\n\t\""
lowcase_cutoff = 0.08 # set to 0.5 to get words faster
md_summary_table = True

# output settings
n_top_sentences = 10000
n_top_words = 30000


###############################################################################
# Info

# Valid langcodes:
# See the variable 'valid_langcodes' below. 'languages' contains a key.
# Note that 'ze' signifies files containing dual Chinese and English subtitles.

# Storage requirements:
# With 'delete_source_data', source data of type "text" and "tokenized" is
# deleted after extracting top sentences/words. These source files are smaller
# than those for 'source_data_type' = "raw". If additionally
# 'always_keep_raw_data' is 'False', "raw" data is also deleted.
# Size of the extracted 'raw' corpus data:
# "all 62 languages": 427.6GB, "en": 54.2GB, "pt_br": 32.0GB, "pl": 29.5GB,
# "es": 27.1GB, "ro": 24.4GB, "tr": 21.8Gb

# Memory requirements:
# With raw data, langcode = "en", year_min = 0, and year_max = 2018, the corpus
# is parsed into a file of 13GB. This file is then loaded 'lines_per_chunk'
# lines at a time into a Counter (dict subclass) object which at its peak takes
# 26GB of memory. By setting 'min_count', entries with count less than that can
# be omitted to save memory, but this only happens after the whole tempfile has
# been loaded (otherwise the final counts would not be correct).

# Download time:
# Variable 'download_chunk_size' influences the download time. The default
# works well with a bandwidth of 50MB/s, bringing the download speed close to
# that. The zipped raw corpus for a large language like "en" is around 13GB
# and hence takes around 4 minutes to download at that rate.

# Runtime:
# Runtime excluding data download (on M1 MBP) with the default settings:
# "all 62 languages": 18h, "pl": 1h11min, "ar": 29min, "fr": 30min.
# Runtime is substantially faster without 'get_words', or when using another
# datatype than 'raw' via 'source_data_type' or 'get_words_using_tokenized'.
# The drawback is that this allows no control over the years and subtitle
# files to include, or the tokenization. With 'use_regex_tokenizer' a faster
# bare-bones tokenizer is always used instead of spaCy (normally it is only
# used as fallback for 'langs_not_in_spacy').


###############################################################################
# Constants etc.

valid_langcodes = ["af", "ar", "bg", "bn", "br", "bs", "ca", "cs", "da", "de",
                   "el", "en", "eo", "es", "et", "eu", "fa", "fi", "fr", "gl",
                   "he", "hi", "hr", "hu", "hy", "id", "is", "it", "ja", "ka",
                   "kk", "ko", "lt", "lv", "mk", "ml", "ms", "nl", "no", "pl",
                   "pt", "pt_br", "ro", "ru", "si", "sk", "sl", "sq", "sr",
                   "sv", "ta", "te", "th", "tl", "tr", "uk", "ur", "vi",
                   "ze_en", "ze_zh", "zh_cn", "zh_tw"]

languages = {"af": "Afrikaans", "ar": "Arabic", "bg": "Bulgarian", "bn":
             "Bengali", "br": "Breton", "bs": "Bosnian", "ca": "Catalan",
             "cs": "Czech", "da": "Danish", "de": "German", "el": "Greek",
             "en": "English", "eo": "Esperanto", "es": "Spanish", "et":
             "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish",
             "fr": "French", "gl": "Galician", "he": "Hebrew", "hi": "Hindi",
             "hr": "Croatian", "hu": "Hungarian", "hy": "Armenian", "id":
             "Indonesian", "is": "Icelandic", "it": "Italian", "ja":
             "Japanese", "ka": "Georgian", "kk": "Kazakh", "ko": "Korean",
             "lt": "Lithuanian", "lv": "Latvian", "mk": "Macedonian", "ml":
             "Malayalam", "ms": "Malay", "nl": "Dutch", "no": "Norwegian",
             "pl": "Polish", "pt": "Portuguese", "pt_br": "Portuguese, Brazil",
             "ro": "Romanian", "ru": "Russian", "si": "Sinhala", "sk":
             "Slovak", "sl": "Slovenian", "sq": "Albanian", "sr": "Serbian",
             "sv": "Swedish", "ta": "Tamil", "te": "Telugu", "th": "Thai",
             "tl": "Tagalog", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu",
             "vi": "Vietnamese", "ze_en": "English, ze", "ze_zh":
             "Chinese, ze", "zh_cn": "Chinese", "zh_tw": "Chinese, Taiwan"}

langs_not_in_spacy = ['br', 'bs', 'eo', 'gl', 'ka', 'kk', 'ms', 'no', 'ko']
#TODO 'ko' should work but dependency not installing and config buggy

non_latin_langs = ['ar', 'bg', 'bn', 'el', 'fa', 'he', 'hi', 'hy', 'ja', 'ka',
                   'kk', 'ko', 'mk', 'ml', 'ru', 'si', 'ta', 'te', 'th', 'uk',
                   'ur', 'ze_zh', 'zh_cn', 'zh_tw']

def source_zipfile(langcode, source_data_type):
    url_base = "https://object.pouta.csc.fi/OPUS-OpenSubtitles/"
    if source_data_type == "raw":
        return (url_base + f"v2018/raw/{langcode}.zip")
    if source_data_type == "text":
        return (url_base + f"v2018/mono/{langcode}.txt.gz")
    if source_data_type == "tokenized":
        return (url_base + f"v2018/mono/{langcode}.tok.gz")
    else:
        raise Exception(f"Error: {source_data_type} not a valid " +
                        "source_data_type.")

basedatadir = "src/data"

def rawdatadir(langcode):
    return f"{basedatadir}/{langcode}/raw"

def parsedfile(langcode, source_data_type):
    if source_data_type == "raw":
        return f"bld/tmp/{langcode}_raw.txt"
    if source_data_type == "text":
        return f"src/data/{langcode}/{langcode}_text.txt"
    if source_data_type == "tokenized":
        return f"src/data/{langcode}/{langcode}_tokenized.txt"

def tmpfile(langcode):
    return f"bld/tmp/{langcode}_raw.txt"

def sentence_outfile(langcode):
    return f"bld/top_sentences/{langcode}_top_sentences.csv"

def word_outfile(langcode):
    return f"bld/top_words/{langcode}_top_words.csv"

def extra_sentences_to_exclude():
    return (pd.read_csv(f"src/extra_settings/extra_sentences_to_exclude.csv")
            .to_dict('list'))

total_counts_sentences_file = "bld/total_counts_sentences.csv"
total_counts_words_file = "bld/total_counts_words.csv"


###############################################################################
# Functions

def download_data_and_extract(basedatadir, langcode, source_data_type):
    print("Downloading data:")
    if not os.path.exists(basedatadir):
        os.makedirs(basedatadir)
    f = download_data_file(source_zipfile(langcode, source_data_type),
                           basedatadir, langcode)
    extension = os.path.splitext(f)[1]
    if source_data_type in ["text", "tokenized"]:
        with gzip.open(f, 'rb') as f_in:
            with open(parsedfile(langcode, source_data_type), 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
    else:
        with zipfile.ZipFile(f, 'r') as zip_ref:
            #zip_ref.extractall(os.path.join(basedatadir, f"{langcode}"))
            zip_ref.extractall(os.path.join(basedatadir, f"{langcode}/raw"))
    os.remove(f)


def download_data_file(url, basedatadir, langcode):
    extension = os.path.splitext(url)[1]
    local_filename = os.path.join(basedatadir, f"{langcode}{extension}")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        total_length = int(r.headers.get('content-length'))
        print(f"   downloading {total_length/1e6:.1f} MB...")
        print_period = max(round(total_length/download_chunk_size/10), 1)        
        download_length = 0
        i = 1
        with open(local_filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=download_chunk_size):
                download_length += len(chunk)
                f.write(chunk)
                if i % print_period == 0:
                    print(f"   {download_length/total_length*100:.0f}% done")
                i += 1
    return local_filename


def parse_rawdatadir_to_tmpfile(langcode, rawdatadir, tmpfile,
                                year_min, year_max):  
    start = time.perf_counter()
    print("Parsing data:")
    if os.path.exists(tmpfile):
        os.remove(tmpfile)
    if not os.path.exists("bld/tmp"):
        os.makedirs("bld/tmp")
    global n_subfiles, n_original_info, n_matching_original
    n_subfiles = 0
    n_original_info = 0
    n_matching_original = 0
    yeardatadir = os.path.join(rawdatadir, f"OpenSubtitles/raw/{langcode}")
    fout = open(tmpfile, 'a', encoding='utf-8')
    
    with ProcessPoolExecutor(max_workers=n_process) as executor:
        for ydir in os.listdir(yeardatadir):
            if int(ydir) < year_min or int(ydir) > year_max:
                continue

            print(f"   {ydir}")
            xml_files = []
            for mdir in os.listdir(os.path.join(yeardatadir, ydir)):
                mdirfull = os.path.join(yeardatadir, ydir, mdir)
                if not os.path.isdir(mdirfull):
                    continue
                if one_subtitle_per_movie:
                    # sort to make deterministic and take last
                    fname = sorted([f for f in os.listdir(mdirfull)
                                    if not f.startswith('.')])[-1]
                    fpathfull = os.path.join(yeardatadir, ydir,
                                                mdir, fname)
                    n_subfiles += 1
                    xml_files.append(fpathfull)
                else:
                    for fname in os.listdir(mdirfull):
                        if fname.startswith('.'):
                            continue
                        fpathfull = os.path.join(yeardatadir, ydir,
                                                    mdir, fname)
                        n_subfiles += 1
                        if original_language_only:
                            if check_if_original(fpathfull,
                                                    langcode):
                                n_matching_original += 1
                                xml_files.append(fpathfull)
                        else:
                            xml_files.append(fpathfull)
                    
            y_text = list(executor.map(text_from_xmlfile, xml_files))                    
            fout.write("".join(y_text))

    fout.close()
    parse_time = time.perf_counter() - start
    print(f"   {n_subfiles} files parsed in {parse_time:.1f} seconds")
    if original_language_only:
        print(f"   {n_original_info/n_subfiles:.0%} with original info")
        print(f"   {n_matching_original/n_subfiles:.0%} "
              + "match original language")


def check_if_original(infile, langcode):
    global n_original_info
    fin = open(infile, 'r', encoding='utf-8')
    intext = fin.read()
    fin.close()
    m = re.search("<original>(.*?)</original>", intext)
    if m:
        n_original_info += 1
        #print(f"     {m.group(1)}")
        if languages[langcode].split(",")[0] in m.group(1):
            return True
        else:
            return False
    else:
        return False


def text_from_xmlfile(infile):
    text_lines = []
    with open(infile, 'r', encoding='utf-8') as fin:
        for line in fin:
            if line.startswith(('<', ' ')):
                continue
            stripped_line = line.strip(linestrip_pattern)
            if stripped_line:
                text_lines.append(stripped_line + "\n")
    return ''.join(text_lines)


def batched(iterable, batch_size: int):
    it = iter(iterable)
    while True:
        batch = tuple(itertools.islice(it, batch_size))
        if not batch:
            return
        yield batch


def chunked_reader(file_path, lines_per_chunk):
    with open(file_path, "r", encoding='utf-8') as f:
        for chunk in batched(f, lines_per_chunk):
            yield chunk
     
            
def check_line_count(file_path):
    with open(file_path, 'br') as f:
        nlines = sum(1 for i in f)
    if nlines == 0:
        print(f"   No lines to process.")
    else:
        print(f"   processing {nlines} lines...")
    return nlines


def parsedfile_to_top_sentences(parsedfile, outfile,
                                langcode, source_data_type):
    print("Getting top sentences:")
    start = time.perf_counter()
    # Chunking is faster once the tmpfile is too large to fit in RAM
    # and only slightly slower when it fits in RAM.
    # The below section takes around 5min with 'es' and all years.
    if not os.path.exists("bld/top_sentences"):
        os.makedirs("bld/top_sentences")
    nlines = check_line_count(parsedfile)
    if nlines == 0:
        return

    d = Counter()
    chunks_done = 0
    for lines in chunked_reader(parsedfile, lines_per_chunk):
        if source_data_type != "raw":
            lines = [l.strip(linestrip_pattern) for l in lines]
        d.update(lines)
        chunks_done += 1
        print(f"   {chunks_done * min(nlines, lines_per_chunk)} " +
                "lines done")
            
    count_time = time.perf_counter() - start
    print(f"   Done counting sentences in {count_time:.1f} seconds")

    # remove empty entries
    d.pop(None, None)
    d.pop("", None)
    
    # remove punctuation and numbers
    # remove 'sentences' starting with parenthesis
    # remove 'sentences' ending with colon
    # remove entries with latin characters
    punctuation_and_numbers_regex = r"^[ _\W0-9]+$"
    parenthesis_start_regex = r"^[(\[{]"
    colon_end_regex = r":$"
    pattern = "|".join([punctuation_and_numbers_regex,
                      parenthesis_start_regex, colon_end_regex])    
    if langcode in non_latin_langs:
        latin_regex = "[a-zA-Zà-üÀ-Ü]"
        pattern = "|".join([pattern, latin_regex])
    d = Counter({k:v for (k, v) in d.items()
                 if (not re.search(pattern, k))})

    # save total counts
    if os.path.exists(total_counts_sentences_file):
        total_counts = (pd.read_csv(total_counts_sentences_file)
                        .to_dict('records'))[0]
    else:
        total_counts = dict()
    total_counts[langcode] = sum(d.values())
    (pd.DataFrame(total_counts, index=[0])
     .to_csv(total_counts_sentences_file, index=False))
    
    # remove less common items to save memory
    if not (min_count == None or min_count == 0):
        d = Counter({key:value for key, value in d.items()
                      if value >= min_count})

    # "/n" is still at the end of every sentence
    if source_data_type == "raw":
        d = Counter({k.strip(): v for (k, v) in d.items()})
    # this takes the same time as instead running below:
    # d['sentence'] = d['sentence'].str.strip()

    # TODO try doing everything in a dictionary instead of using pandas
    d = pd.DataFrame(d.most_common(), columns=['sentence', 'count'])
    d['count'] = pd.to_numeric(d['count'],
                               downcast="unsigned") # saves ~50% memory
    d = d.astype({"sentence":"string[pyarrow]"}) # saves ~66% memory

    d = collapse_if_only_ending_differently(d, "sentence", "count")

    try:
        d = d[~d['sentence'].isin(extra_sentences_to_exclude()[langcode])]
    except KeyError as e:
        print("   no extra sentences to exclude")
    
    (d
     .head(n_top_sentences)
     .to_csv(outfile, index=False))

    
def collapse_if_only_ending_differently(df, sentence, count):
    return (df
            .sort_values(by=[count], ascending=False)
            .assign(Sm1=df[sentence].str.strip(" .?!¿¡"))
            .groupby('Sm1', as_index=False)
            .agg({sentence:'first', count:'sum'})
            .drop(columns=['Sm1'])
            .sort_values(by=[count], ascending=False)
            .reset_index(drop=True))


def parsedfile_to_top_words(parsedfile, outfile, langcode, source_data_type):
    print("Getting top words:")
    start = time.perf_counter()
    if not os.path.exists("bld/top_words"):
        os.makedirs("bld/top_words")       
    nlines = check_line_count(parsedfile)
    if nlines == 0:
        return
    d = Counter()
    chunks_done = 0
    with ProcessPoolExecutor(max_workers=n_process) as executor:
        for lines in chunked_reader(parsedfile, lines_per_chunk):
            tokenized_lines = tokenize_lines_mp(lines, langcode, executor)
            d.update(tokenized_lines)
            chunks_done += 1
            print(f"   {chunks_done * min(nlines, lines_per_chunk)} "
                    + "lines done")
            # 6 min per 10,000,000 lines ("nl" has 107,000,000 lines) 
            
    count_time = time.perf_counter() - start
    print(f"   Done tokenizing lines and counting words in {count_time:.1f} seconds")         
    
    # remove empty entries
    d.pop("", None)
    d.pop(None, None)
    
    # remove punctuation and numbers
    # remove entries with latin characters
    punctuation_and_numbers_regex = r"^[ _\W0-9]+$"
    pattern = punctuation_and_numbers_regex
    if langcode in non_latin_langs:
        latin_regex = "[a-zA-Zà-üÀ-Ü]"
        pattern = "|".join([pattern, latin_regex])
    d = Counter({k:v for (k, v) in d.items() if not
                 re.search(pattern, k)})

    # save total counts
    if os.path.exists(total_counts_words_file):
        total_counts = (pd.read_csv(total_counts_words_file)
                        .to_dict('records'))[0]
    else:
        total_counts = dict()
    total_counts[langcode] = sum(d.values())
    (pd.DataFrame(total_counts, index=[0])
     .to_csv(total_counts_words_file, index=False))
    
    # remove less common items to save memory
    if not (min_count == None or min_count == 0):
        d = Counter({key:value for key, value in d.items()
                      if value >= min_count})

    d = pd.DataFrame(d.most_common(), columns=['word', 'count'])
    d['count'] = pd.to_numeric(d['count'],
                               downcast="unsigned") # saves ~50% memory
    d = d.astype({"word":"string[pyarrow]"}) # saves ~66% memory

    d = collapse_case(d, "word", "count", "wordlow", lowcase_cutoff)
    
    #TODO add more cleaning steps from google-books-ngram-frequency repo
    
    (d
     .head(n_top_words)
     .to_csv(outfile, index=False))


current_spacy = None
current_spacy_langcode = None

def get_spacy_pipeline(langcode):
    
    global current_spacy, current_spacy_langcode
    
    lang_code_normalized = normalized_langcode(langcode)
    
    if current_spacy and current_spacy_langcode == lang_code_normalized:
        return current_spacy
    
    import spacy
    
    model_mapping = {
        #"zh": "zh_core_web_sm",
    }
    model_name = model_mapping.get(langcode)
    if model_name:
        return spacy.load(model_name)
    
    if langcode in ['zh_cn', 'zh_tw', 'ze_zh']:
        import jieba
        jieba.setLogLevel(20)
        cfg = {"segmenter": "jieba"}
        nlp = spacy.blank(lang_code_normalized).from_config(
            {"nlp": {"tokenizer": cfg}}
        )
    elif lang_code_normalized == 'ja':
        
        # Japanese with MeCab tokenizer
        nlp = spacy.blank(lang_code_normalized)
        
        # Define custom MeCab tokenizer
        import MeCab
        
        class MecabTokenizer:
            def __init__(self, nlp):
                self.mecab = MeCab.Tagger()
                self.vocab = nlp.vocab

            def __call__(self, text):
                tokens = []
                node = self.mecab.parseToNode(text)
                while node:
                    if node.surface.strip():
                        tokens.append(node.surface)
                    node = node.next
                return spacy.tokens.Doc(self.vocab, words=tokens)
        
        # Assign custom tokenizer
        nlp.tokenizer = MecabTokenizer(nlp)
    else:
        nlp = spacy.blank(lang_code_normalized)
        
    current_spacy = nlp
    current_spacy_langcode = lang_code_normalized
        
    return nlp


def join_to_min_length(strings, n: int):
    current_words: list[str] = []
    current_length = 0

    for word in strings:
        if not current_words:
            current_words.append(word)
            current_length = len(word)
        else:
            new_length = current_length + 1 + len(word)
            if new_length >= n:
                yield ' '.join(current_words + [word])
                current_words = []
                current_length = 0
            else:
                current_words.append(word)
                current_length = new_length

    if current_words:
        yield ' '.join(current_words)

def create_word_token(text: str, lang_id) -> str:

    assert "\t" not in text and "\n" not in text and "\r" not in text

    token = text.strip(".,'’\"' \t\n\r!@#$%^&*()_-=+{}:\"<>?/;")
    
    if token == "":
        return token

    if str(lang_id) in {"en", "fr", "it", "de", "es", "el"}:
        token = token.replace("’", "'")
        token = token.replace("`", "'")

    if str(lang_id) in {'en', 'nl', 'af'}:
        if len(token) > 2 and token[-1] == "s" and token[-2] == "'":
            token = token[:-2]

    return token
  
def regex_tokenizer(text: str, lang_id: str) -> list[str]:
    text = str(text+" ").replace(". ", " ")
    # Arabic diacritical marks: u0610-\u061A\u064B-\u065F
    non_word_chars = r"[^\w\-\_\'\’\.\u0610-\u061A\u064B-\u065F]{1,}"
    tokens = [create_word_token(w, lang_id) for w in re.split(non_word_chars, text)]
    return [token for token in tokens if token]        

def tokenize_lines(lines: list, langcode: str, get_spacy_pipeline) -> list[list[str]]:
    
    using_regex_tokenizer = use_regex_tokenizer
    
    if normalized_langcode(langcode) in {'zh', 'ja', 'th', 'lo', 'km'}:
        using_regex_tokenizer = False
    
    if source_data_type == "text":
        lines = (l.strip(linestrip_pattern) for l in lines)
    if source_data_type == "tokenized":
        # no tokenizer needed
        dt = [l.strip(linestrip_pattern).split(" ") for l in lines]
    elif (using_regex_tokenizer
          or normalized_langcode(langcode) in langs_not_in_spacy):
        # use regex tokenizer
        dt = [regex_tokenizer(l, langcode) for l in lines]  
    else:
        # use spacy tokenizer
        nlp = get_spacy_pipeline(langcode)
        big_lines = join_to_min_length(lines, 5_000)
        dt = [
            [w.text.strip("-") for w in doc if not w.is_punct]
            for doc in nlp.pipe(big_lines, n_process=1)
        ]
        
    return dt

def tokenize_lines_mp(lines, langcode, executor: ProcessPoolExecutor):

    lines_batched = batched(lines, int(lines_per_chunk/(n_process*25)))
    tokenize_func = partial(tokenize_lines, langcode=langcode, get_spacy_pipeline=get_spacy_pipeline)
    dt = itertools.chain.from_iterable(executor.map(tokenize_func, lines_batched))  
    return itertools.chain.from_iterable(dt)


def normalized_langcode(langcode):
    if langcode == "ze_en" or langcode == "ze_zh":
        return langcode.split("_")[1]
    else:
        return langcode.split("_")[0]


def collapse_case(df, word, count, wordlow, cutoff=0.5):
    if cutoff == 0.5:
        return (df
                .sort_values(by=[count], ascending=False)
                .assign(wordlow=df[word].str.lower())
                .groupby(wordlow, as_index=False)
                .agg({word:'first', count:'sum'})
                .drop(columns=[wordlow])
                .sort_values(by=[count], ascending=False)
                .reset_index(drop=True))
    else:
        return (df
                .assign(wordlow=df["word"].str.lower())
                .groupby(wordlow, as_index=False)
                .apply(wordcase_by_cutoff, word, count, wordlow, cutoff)
                .drop(columns=[wordlow])
                .sort_values(by=[count], ascending=False)
                .reset_index(drop=True))


def wordcase_by_cutoff(df, word, count, wordlow, cutoff):
    """Return series of word case and count based on a cutoff value.
    If it exists, the lowercase version of 'word' is returned as long
    as its share of all words in 'df' is larger than 'cutoff'.
    Else the most common version is returned.
    """
    group_count = sum(df[count])
    share = df[count]/group_count
    is_low = df[word] == df[wordlow]
    if (is_low & (share > cutoff)).any():
        return pd.Series([df.loc[(is_low & (share > cutoff)).idxmax(), wordlow],
                          group_count], index=[word, count])
    else:
        return pd.Series([df.loc[share.idxmax(), word],
                          group_count], index=[word, count])


def run_one_langcode(langcode, source_data_type):
    t0 = time.time()
    check_cwd()
    print("\nLanguage:", langcode)
    if get_source_data:
        if ((source_data_type == "raw" and
            (not os.path.exists(rawdatadir(langcode))
             or redownload_source_data)) or
            (source_data_type != "raw" and
             (not os.path.exists(parsedfile(langcode, source_data_type))
              or redownload_source_data))):
            download_data_and_extract(basedatadir, langcode, source_data_type)
        if (get_words_using_tokenized and
            source_data_type != "tokenized" and
            (not os.path.exists(parsedfile(langcode, "tokenized"))
             or redownload_source_data)):
            download_data_and_extract(basedatadir, langcode, "tokenized")
    if get_parsed_text and source_data_type == "raw":
        parse_rawdatadir_to_tmpfile(langcode, rawdatadir(langcode),
                                    tmpfile(langcode),
                                    year_min, year_max)
    if get_sentences:

        parsedfile_to_top_sentences(parsedfile(langcode, source_data_type),
                                    sentence_outfile(langcode),
                                    langcode, source_data_type)
    if get_words:
        if not get_words_using_tokenized:
            parsedfile_to_top_words(parsedfile(langcode, source_data_type),
                                    word_outfile(langcode),
                                    langcode,
                                    source_data_type)
        else:
            parsedfile_to_top_words(parsedfile(langcode, "tokenized"),
                                    word_outfile(langcode),
                                    langcode,
                                    "tokenized")
    if delete_tmpfile:
        if os.path.exists(tmpfile(langcode)):
            os.remove(tmpfile(langcode))
            if not os.listdir("bld/tmp"):
                os.rmdir(f"bld/tmp")
    if delete_source_data:
        if source_data_type == "raw" and not always_keep_raw_data:
            if os.path.exists(rawdatadir(langcode)):
                shutil.rmtree(rawdatadir(langcode))
        if source_data_type == "text" or source_data_type == "tokenized":
            if os.path.exists(parsedfile(langcode, source_data_type)):
                os.remove(parsedfile(langcode, source_data_type))
        if get_words_using_tokenized:
            if os.path.exists(parsedfile(langcode, "tokenized")):
                os.remove(parsedfile(langcode, "tokenized"))
        if os.path.exists(f"basedatadir/{langcode}"):
            if not os.listdir(f"basedatadir/{langcode}"):
                os.rmdir(f"basedatadir/{langcode}")
    t1 = time.time()
    print(f"Total time (s): {t1-t0:.1f}\n")


def check_cwd():
    if not os.path.isfile('src/top_open_subtitles_sentences.py'):
        print("Warning: 'src/top_open_subtitles_sentences.py' not found "
              + "where expected. Trying to switch to parent directory.")
        os.chdir("..")
        if not os.path.isfile('src/top_open_subtitles_sentences.py'):
            raise Exception("Error: Working directory is not the repository "
                            + "base directory. "
                            + "'src/top_open_subtitles_sentences.py' "
                            + "not found.")


def check_langcodes(langcodes):
    for langcode in langcodes:
        if langcode not in valid_langcodes:
            raise Exception(f"Error: Not a valid langcode: {langcode}")


def summary_table(langcodes):
    sc = pd.read_csv(total_counts_sentences_file)
    wc = pd.read_csv(total_counts_words_file)
    if md_summary_table:
        (pd.DataFrame({"code": langcodes})
         .assign(language=[languages[l] for l in st['code']])
         .assign(sentences=[f"[{sc[l][0]:,}]({sentence_outfile(l)})"
                            for l in st['code']])
         .assign(words=[f"[{wc[l][0]:,}]({word_outfile(l)})"
                        for l in st['code']])
         .to_markdown("bld/summary_table.md", index=False,
                      colalign=["left", "left", "right", "right"]))
    else:
        (pd.DataFrame({"code": langcodes})
         .assign(language=[languages[l] for l in st['code']])
         .assign(sentences=[sc[l][0] for l in st['code']])
         .assign(words=[wc[l][0] for l in st['code']])
         .to_csv("bld/summary_table.csv", index=False))


def main():
    check_langcodes(run_langcodes)
    check_cwd()
    if os.path.exists(total_counts_sentences_file):
            os.remove(total_counts_sentences_file)
    if os.path.exists(total_counts_words_file):
            os.remove(total_counts_words_file)
    for langcode in run_langcodes:
        run_one_langcode(langcode, source_data_type)
    if get_summary_table:
        summary_table(run_langcodes)


###############################################################################
# Run

if __name__ == "__main__":
    main()

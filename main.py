import feedparser
import configparser
import os
import httpx
from openai import OpenAI
from jinja2 import Template
from bs4 import BeautifulSoup
import re
import logging
import datetime
import requests
from fake_useragent import UserAgent
#from dateutil.parser import parse

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def fetch_feed(url, log_file):
    feed = None
    response = None
    headers = {}
    try:
        ua = UserAgent()
        headers['User-Agent'] = ua.random.strip()
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            feed = feedparser.parse(response.text)
            return {'feed': feed, 'status': 'success'}
        else:
            with open(log_file, 'a') as f:
                f.write(f"Fetch error: {response.status_code}\n")
            return {'feed': None, 'status': response.status_code}
    except requests.RequestException as e:
        with open(log_file, 'a') as f:
            f.write(f"Fetch error: {e}\n")
        return {'feed': None, 'status': 'failed'}

def generate_untitled(entry):
    try: return entry.title
    except: 
        try: return entry.article[:50]
        except: return entry.link

def get_cfg(sec, name, default=None):
    value=config.get(sec, name, fallback=default)
    if value:
        return value.strip('"')

def clean_html(html_content):
    """
    This function is used to clean the HTML content.
    It will remove all the <script>, <style>, <img>, <a>, <video>, <audio>, <iframe>, <input> tags.
    Returns:
        Cleaned text for summarization
    """
    soup = BeautifulSoup(html_content, "html.parser")

    for script in soup.find_all("script"):
        script.decompose()

    for style in soup.find_all("style"):
        style.decompose()

    for img in soup.find_all("img"):
        img.decompose()

    for a in soup.find_all("a"):
        a.decompose()

    for video in soup.find_all("video"):
        video.decompose()

    for audio in soup.find_all("audio"):
        audio.decompose()
    
    for iframe in soup.find_all("iframe"):
        iframe.decompose()
    
    for input in soup.find_all("input"):
        input.decompose()

    return soup.get_text()

def filter_entry(entry, filter_apply, filter_type, filter_rule):
    """
    This function is used to filter the RSS feed.

    Args:
        entry: RSS feed entry
        filter_apply: title, article or link
        filter_type: include or exclude or regex match or regex not match
        filter_rule: regex rule or keyword rule, depends on the filter_type

    Raises:
        Exception: filter_apply not supported
        Exception: filter_type not supported
    """
    if filter_apply == 'title':
        text = entry.title
    elif filter_apply == 'article':
        text = entry.article
    elif filter_apply == 'link':
        text = entry.link
    elif not filter_apply:
        return True
    else:
        raise Exception('filter_apply not supported')

    if filter_type == 'include':
        return re.search(filter_rule, text)
    elif filter_type == 'exclude':
        return not re.search(filter_rule, text)
    elif filter_type == 'regex match':
        return re.search(filter_rule, text)
    elif filter_type == 'regex not match':
        return not re.search(filter_rule, text)
    elif not filter_type:
        return True
    else:
        raise Exception('filter_type not supported')

def read_entry_from_file(sec):
    """
    This function is used to read the RSS feed entries from the feed.xml file.

    Args:
        sec: section name in config.ini
    """
    out_dir = os.path.join(BASE, get_cfg(sec, 'name'))
    try:
        with open(out_dir + '.xml', 'r') as f:
            rss = f.read()
        feed = feedparser.parse(rss)
        return feed.entries
    except:
        return []

def truncate_entries(entries, max_entries):
    if len(entries) > max_entries:
        entries = entries[:max_entries]
    return entries

def gpt_summary(query,model,language):
    if language == "zh":
        messages = [
            {"role": "user", "content": query},
            {"role": "assistant", "content": f"请用中文总结这篇文章，先提取出{keyword_length}个关键词，在同一行内输出，然后换行，用中文在{summary_length}字内写一个包含所有要点的总结，按顺序分要点输出，并按照以下格式输出'<br><br>总结:'，<br>是HTML的换行符，输出时必须保留2个，并且必须在'总结:'二字之前"}
        ]
    else:
        messages = [
            {"role": "user", "content": query},
            {"role": "assistant", "content": f"Please summarize this article in {language} language, first extract {keyword_length} keywords, output in the same line, then line break, write a summary containing all the points in {summary_length} words in {language}, output in order by points, and output in the following format '<br><br>Summary:' , <br> is the line break of HTML, 2 must be retained when output, and must be before the word 'Summary:'"}
        ]
    if not OPENAI_PROXY:
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
        )
    else:
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            # Or use the `OPENAI_BASE_URL` env var
            base_url=OPENAI_BASE_URL,
            # example: "http://my.test.server.example.com:8083",
            http_client=httpx.Client(proxy=OPENAI_PROXY),
            # example:"http://my.test.proxy.example.com",
        )
    completion = client.chat.completions.create(
        model=model,
        messages=messages,
    )
    return completion.choices[0].message.content


def output(sec, language):
    log_file = os.path.join(BASE, get_cfg(sec, 'name') + '.log')
    out_dir = os.path.join(BASE, get_cfg(sec, 'name'))
    rss_urls = get_cfg(sec, 'url').split(',')

    filter_apply = get_cfg(sec, 'filter_apply')
    filter_type = get_cfg(sec, 'filter_type')
    filter_rule = get_cfg(sec, 'filter_rule')

    if (filter_apply and filter_type and filter_rule) or (not filter_apply and not filter_type and not filter_rule):
        pass
    else:
        raise Exception('filter_apply, type, rule must be set together')

    max_items = int(get_cfg(sec, 'max_items') or 0)
    cnt = 0
    existing_entries = read_entry_from_file(sec)
    logging.info(f'Started processing section: {sec}')
    logging.info(f'Existing entries: {len(existing_entries)}')

    existing_entries = truncate_entries(existing_entries, max_entries=max_entries)
    append_entries = []

    for rss_url in rss_urls:
        logging.info(f"Fetching from {rss_url}")
        feed = fetch_feed(rss_url, log_file)['feed']
        if not feed:
            logging.error(f"Fetch failed from {rss_url}")
            continue

        for entry in feed.entries:
            if cnt > max_entries:
                logging.info(f"Skipping: [{entry.title}]({entry.link}) - Max entries reached")
                break

            if entry.link.find('#replay') and entry.link.find('v2ex'):
                entry.link = entry.link.split('#')[0]

            if entry.link in [x.link for x in existing_entries + append_entries]:
                continue

            entry.title = generate_untitled(entry)

            try:
                entry.article = entry.content[0].value
            except AttributeError:
                try:
                    entry.article = entry.description
                except AttributeError:
                    entry.article = entry.title

            cleaned_article = clean_html(entry.article)

            if not filter_entry(entry, filter_apply, filter_type, filter_rule):
                logging.info(f"Filtered: [{entry.title}]({entry.link})")
                continue

            cnt += 1
            if cnt > max_items:
                entry.summary = None
            elif OPENAI_API_KEY:
                token_length = len(cleaned_article)
                entry.summary = None
                for attempt in range(3):  # 尝试3次
                    try:
                        if token_length > 4000:  # 如果内容过长，截断它
                            cleaned_article = cleaned_article[:4000]
                        entry.summary = gpt_summary(cleaned_article, model="gpt-4o-mini", language=language)
                        logging.info(f"Summarized using gpt-4o-mini (attempt {attempt + 1})")
                        break
                    except Exception as e:
                        logging.warning(f"gpt-4o-mini failed (attempt {attempt + 1}): {str(e)}")
                        try:
                            entry.summary = gpt_summary(cleaned_article, model="gpt-4-turbo-preview", language=language)
                            logging.info(f"Summarized using GPT-4-turbo-preview (attempt {attempt + 1})")
                            break
                        except Exception as e:
                            logging.error(f"Summarization failed (attempt {attempt + 1}): {str(e)}")
                            if attempt == 2:  # 如果是最后一次尝试
                                entry.summary = cleaned_article[:200] + "..."  # 使用文章开头作为摘要
                                logging.info("Using article beginning as summary")

            append_entries.append(entry)
            logging.info(f"Appended: [{entry.title}]({entry.link})")

    logging.info(f'Appended entries: {len(append_entries)}')

    template = Template(open('template.xml').read())

    try:
        rss = template.render(feed=feed, append_entries=append_entries, existing_entries=existing_entries)
        with open(out_dir + '.xml', 'w') as f:
            f.write(rss)
        logging.info(f'Finished processing section: {sec}')
    except Exception as e:
        logging.error(f"Error when rendering xml for {out_dir}: {str(e)}")

config = configparser.ConfigParser()
config.read('config.ini')
secs = config.sections()
# Maxnumber of entries to in a feed.xml file
max_entries = 1000

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
U_NAME = os.environ.get('U_NAME')
OPENAI_PROXY = os.environ.get('OPENAI_PROXY')
OPENAI_BASE_URL = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')
deployment_url = f'https://{U_NAME}.github.io/RSS-GPT/'
BASE =get_cfg('cfg', 'BASE')
keyword_length = int(get_cfg('cfg', 'keyword_length'))
summary_length = int(get_cfg('cfg', 'summary_length'))
language = get_cfg('cfg', 'language')

try:
    os.mkdir(BASE)
except:
    pass

feeds = []
links = []

for x in secs[1:]:
    output(x, language=language)
    feed = {"url": get_cfg(x, 'url').replace(',','<br>'), "name": get_cfg(x, 'name')}
    feeds.append(feed)  # for rendering index.html
    links.append("- "+ get_cfg(x, 'url').replace(',',', ') + " -> " + deployment_url + feed['name'] + ".xml\n")

def append_readme(readme, links):
    with open(readme, 'r') as f:
        readme_lines = f.readlines()
    while readme_lines[-1].startswith('- ') or readme_lines[-1] == '\n':
        readme_lines = readme_lines[:-1]  # remove 1 line from the end for each feed
    readme_lines.append('\n')
    readme_lines.extend(links)
    with open(readme, 'w') as f:
        f.writelines(readme_lines)

append_readme("README.md", links)
append_readme("README-zh.md", links)

# Rendering index.html used in my GitHub page, delete this if you don't need it.
# Modify template.html to change the style
with open(os.path.join(BASE, 'index.html'), 'w') as f:
    template = Template(open('template.html').read())
    html = template.render(update_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), feeds=feeds)
    f.write(html)

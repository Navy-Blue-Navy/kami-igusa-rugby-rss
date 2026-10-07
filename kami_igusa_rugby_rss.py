import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin, urlparse, urlunparse
import hashlib
import re
import time


BASE_URL = "http://www.kami-igusa.com"
OUTPUT = Path(__file__).parent / "kami_igusa_rugby.xml"

CATEGORY = "早稲田ラグビー"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
}

session = requests.Session()
session.headers.update(HEADERS)


# --------------------------------------------------
# URLから :80 を取り除く
# --------------------------------------------------

def clean_url(url):
    parsed = urlparse(url)

    netloc = parsed.netloc.replace(":80", "")

    return urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


# --------------------------------------------------
# 既存RSSを読み込む
# --------------------------------------------------

old_items = {}

if OUTPUT.exists():
    try:
        old_tree = ET.parse(OUTPUT)

        for item in old_tree.getroot().findall("./channel/item"):
            guid = item.findtext("guid", "")

            if guid:
                old_items[guid] = {
                    "title": item.findtext("title", ""),
                    "link": item.findtext("link", ""),
                    "description": item.findtext("description", ""),
                    "pubDate": item.findtext("pubDate", ""),
                    "guid": guid,
                }

    except Exception:
        old_items = {}


# --------------------------------------------------
# 「早稲田ラグビー」カテゴリーの記事一覧を取得
# --------------------------------------------------

article_links = []
seen_urls = set()

total_count = None
page = 1


while True:
    page_url = f"{BASE_URL}/article/browse/{page}"

    response = session.post(
        page_url,
        data={
            "category": CATEGORY
        },
        timeout=30
    )

    response.raise_for_status()

    # サイトの実データはUTF-8
    # apparent_encodingは古い記事等で誤判定するため使わない
    response.encoding = "utf-8"

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )


    # --------------------------------------------------
    # 総件数を取得
    # --------------------------------------------------

    if total_count is None:
        page_text = soup.get_text(
            " ",
            strip=True
        )

        count_match = re.search(
            r"(\d+)\s*件",
            page_text
        )

        if count_match:
            total_count = int(
                count_match.group(1)
            )

            print(
                "早稲田ラグビー総件数:",
                total_count,
                "件"
            )


    # --------------------------------------------------
    # このページの記事リンクを取得
    # --------------------------------------------------

    page_articles = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")

        if not re.search(
            r"/article/read/\d+",
            href
        ):
            continue

        title = a.get_text(
            " ",
            strip=True
        )

        title = re.sub(
            r"\s+",
            " ",
            title
        ).strip()

        if not title:
            continue

        article_url = urljoin(
            response.url,
            href
        )

        article_url = clean_url(
            article_url
        )

        if article_url in seen_urls:
            continue

        seen_urls.add(article_url)

        # 一覧タイトル先頭の #821 などを削除
        clean_title = re.sub(
            r"^#\d+\s*",
            "",
            title
        ).strip()

        article = {
            "title": clean_title,
            "link": article_url,
        }

        page_articles.append(article)
        article_links.append(article)


    print(
        f"ページ {page}: "
        f"{len(page_articles)}件 "
        f"（累計 {len(article_links)}件）"
    )


    # --------------------------------------------------
    # 終了判定
    # --------------------------------------------------

    if (
        total_count is not None
        and len(article_links) >= total_count
    ):
        break

    if len(page_articles) == 0:
        break

    page += 1

    # 無限ループ防止
    if page > 100:
        break

    time.sleep(0.3)


# --------------------------------------------------
# 各記事から公開日・本文を取得
# --------------------------------------------------

current_items = []

meta_pattern = re.compile(
    r"#(\d+)\s*/\s*"
    r"公開日:\s*"
    r"(\d{4})/(\d{2})/(\d{2})\s*/\s*"
    r"カテゴリー:\s*(.+)"
)


for number, article in enumerate(
    article_links,
    start=1
):
    article_url = article["link"]

    try:
        response = session.get(
            article_url,
            timeout=30
        )

        response.raise_for_status()

        # 重要：
        # apparent_encodingを使わずUTF-8固定
        response.encoding = "utf-8"

    except Exception as e:
        print(
            "取得失敗:",
            article_url,
            e
        )
        continue


    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    article_container = soup.find(
        "div",
        class_="article-read"
    )

    if article_container is None:
        print(
            f"[{number}/{len(article_links)}] "
            f"解析失敗: article-readなし "
            f"{article_url}"
        )
        continue


    # --------------------------------------------------
    # 公開日・カテゴリー
    # --------------------------------------------------

    meta = article_container.find(
        "p",
        class_="text-left"
    )

    if meta is None:
        print(
            f"[{number}/{len(article_links)}] "
            f"解析失敗: 公開日情報なし "
            f"{article_url}"
        )
        continue

    meta_text = meta.get_text(
        " ",
        strip=True
    )

    meta_text = re.sub(
        r"\s+",
        " ",
        meta_text
    ).strip()

    match = meta_pattern.search(
        meta_text
    )

    if not match:
        print(
            f"[{number}/{len(article_links)}] "
            f"解析失敗: メタ情報形式不一致 "
            f"{article_url}"
        )
        print(
            "  META:",
            meta_text
        )
        continue


    article_number = match.group(1)

    year = int(match.group(2))
    month = int(match.group(3))
    day = int(match.group(4))

    category = match.group(5).strip()


    # --------------------------------------------------
    # 念のためカテゴリーを再確認
    # --------------------------------------------------

    if category != CATEGORY:
        print(
            f"[{number}/{len(article_links)}] "
            f"カテゴリー不一致: "
            f"{category} "
            f"{article_url}"
        )
        continue


    # --------------------------------------------------
    # タイトル
    # --------------------------------------------------

    title = article["title"]

    h3 = soup.find("h3")

    if h3:
        full_title = h3.get_text(
            " ",
            strip=True
        )

        full_title = re.sub(
            r"\s+",
            " ",
            full_title
        ).strip()

        if full_title:
            title = full_title


    # h3が取れなかった場合はtitleタグも利用
    if not title and soup.title:
        title = soup.title.get_text(
            " ",
            strip=True
        )

        title = re.sub(
            r"\s*\|\s*上井草商店街へようこそ！\s*$",
            "",
            title
        )

        title = re.sub(
            r"\s+",
            " ",
            title
        ).strip()


    if not title:
        title = f"記事 #{article_number}"


    # --------------------------------------------------
    # 本文
    # --------------------------------------------------

    body = article_container.find(
        "div",
        class_="article-body"
    )

    description = ""

    if body:
        description = body.get_text(
            " ",
            strip=True
        )

        description = re.sub(
            r"\s+",
            " ",
            description
        ).strip()


    if not description:
        description = (
            "上井草商店街 "
            "早稲田ラグビー"
        )


    # --------------------------------------------------
    # RSS用データ
    # --------------------------------------------------

    pub_date = datetime(
        year,
        month,
        day,
        12,
        0,
        0,
        tzinfo=JST
    )

    guid = hashlib.sha256(
        article_url.encode("utf-8")
    ).hexdigest()


    current_items.append(
        {
            "title": title,
            "link": article_url,
            "description": description,
            "pubDate": format_datetime(
                pub_date
            ),
            "guid": guid,
        }
    )


    print(
        f"[{number}/{len(article_links)}] "
        f"#{article_number} "
        f"{title}"
    )

    time.sleep(0.3)


# --------------------------------------------------
# 既存RSSの記事も残す
# --------------------------------------------------

all_items = []
seen_guids = set()


for item in current_items:
    if item["guid"] not in seen_guids:
        all_items.append(item)
        seen_guids.add(item["guid"])


for guid, item in old_items.items():
    if guid not in seen_guids:
        all_items.append(item)
        seen_guids.add(guid)


# --------------------------------------------------
# 公開日の新しい順に並べる
# --------------------------------------------------

def get_date(item):
    try:
        return parsedate_to_datetime(
            item["pubDate"]
        )

    except Exception:
        return datetime.min.replace(
            tzinfo=timezone.utc
        )


all_items.sort(
    key=get_date,
    reverse=True
)

# 十分余裕を持って300件保存
all_items = all_items[:300]


# --------------------------------------------------
# RSS 2.0作成
# --------------------------------------------------

rss = ET.Element(
    "rss",
    version="2.0"
)

channel = ET.SubElement(
    rss,
    "channel"
)

ET.SubElement(
    channel,
    "title"
).text = "上井草商店街 早稲田ラグビー"

ET.SubElement(
    channel,
    "link"
).text = (
    f"{BASE_URL}/article/browse/1"
)

ET.SubElement(
    channel,
    "description"
).text = (
    "上井草商店街公式サイトの"
    "「早稲田ラグビー」カテゴリー新着情報"
)

ET.SubElement(
    channel,
    "language"
).text = "ja"


# --------------------------------------------------
# RSS記事を書き込む
# --------------------------------------------------

for item in all_items:
    element = ET.SubElement(
        channel,
        "item"
    )

    ET.SubElement(
        element,
        "title"
    ).text = item["title"]

    ET.SubElement(
        element,
        "link"
    ).text = item["link"]

    ET.SubElement(
        element,
        "description"
    ).text = item["description"]

    ET.SubElement(
        element,
        "pubDate"
    ).text = item["pubDate"]

    guid_element = ET.SubElement(
        element,
        "guid"
    )

    guid_element.set(
        "isPermaLink",
        "false"
    )

    guid_element.text = item["guid"]


# --------------------------------------------------
# XML保存
# --------------------------------------------------

tree = ET.ElementTree(rss)

ET.indent(
    tree,
    space="  "
)

tree.write(
    OUTPUT,
    encoding="utf-8",
    xml_declaration=True
)


# --------------------------------------------------
# 結果表示
# --------------------------------------------------

print()
print("RSS作成成功")

print(
    "サイト上の総件数:",
    total_count,
    "件"
)

print(
    "今回取得:",
    len(current_items),
    "件"
)

print(
    "RSS保存件数:",
    len(all_items),
    "件"
)

print(
    "保存先:",
    OUTPUT
)

print()
print("最新15件:")

for item in all_items[:15]:
    print(
        item["pubDate"],
        item["title"],
        "->",
        item["link"]
    )
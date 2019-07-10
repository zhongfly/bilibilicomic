# encoding:UTF-8
# python3.6

import requests

url = "https://manga.bilibili.com/twirp/comic.v1.Comic/Search"
headers = {
    'Content-Type': "application/x-www-form-urlencoded",
    'user-agent': "Mozilla/5.0 BiliComic/2.0.3",
    'Host': "manga.bilibili.com",
    }
while True:
    keyword=input('您想要搜索的漫画关键词：')
    payload = f"key_word={keyword}&page_size=10&page_num=1"
    r = requests.post(url, data=payload.encode('utf-8'), headers=headers)
    data=r.json()['data']['list']
    for item in data:
        comicId=item['id']
        title=item['org_title']
        print(f"comicId={comicId},{title}")
    print("\n")
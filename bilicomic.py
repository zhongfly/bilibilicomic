# encoding:UTF-8
# python3.6

import tempfile
import io
import json
import os
import zipfile
import requests
import threading
import queue
import time
import toml

conf='config.toml'
workDir = os.getcwd()

# access_key = "59f81208b0b95a55c2f99e4e7eddc461"
# appkey = "cc8617fd6961e070"
# comicId = 26399
# beginId = 0
# endId = 99999999999

with open(conf, encoding="utf-8") as f:
    dict_conf = toml.load(f)
access_key = dict_conf['user']['access_key']
appkey = dict_conf['user']['appkey']
comicId = dict_conf['comic']['comicId']
beginId = int(dict_conf['comic']['beginId'])
endId = int(dict_conf['comic']['endId'])


headers = {
    'Content-Type': "application/x-www-form-urlencoded; charset=UTF-8",
    'user-agent': "Mozilla/5.0 BiliComic/2.0.3",
    'Host': "manga.bilibili.com",
}
getHeaders = {
    'User-Agent': 'okhttp/3.10.0',
    'Host': 'i0.hdslb.com'
}


def makeDir(dirPath):
    if os.path.isdir(dirPath) == False:
        os.makedirs(dirPath)
    else:
        pass


def getComicDetail(comicId):
    url = "https://manga.bilibili.com/twirp/comic.v2.Comic/ComicDetail"
    data = {
      'access_key': access_key,
      'appkey': appkey,
      'comic_id': comicId,
      'device': 'android',
    }
    r = requests.post(url, data=payload, headers=headers)
    if r.status_code == requests.codes.ok:
        try:
            data = r.json()
            return data['data']
        except Exception as e:
            print(e)
    else:
        print(f"getComicDetail fail,id={comicId},{r.status_code}")
    return 0


def getEpList(ep_list, filter=True, beginId=0, endId=9999999):
    EpList = []
    for ep in ep_list:
        n = int(ep['id'])
        if n <= beginId or n > endId:
            continue
        epDict = {"episodeId": ep['id'], "name": ep['short_title']}
        if filter:
            if ep["is_locked"] == False:
                EpList.append(epDict)
            else:
                pass
        else:
            EpList.append(epDict)
    return EpList


def getEpIndex(comicId, episodeId):
    def generateHashKey(comicId, episodeId):
        n = [None for i in range(8)]
        e = int(comicId)
        t = int(episodeId)
        n[0] = t
        n[1] = t >> 8
        n[2] = t >> 16
        n[3] = t >> 24
        n[4] = e
        n[5] = e >> 8
        n[6] = e >> 16
        n[7] = e >> 24
        for idx in range(8):
            n[idx] = n[idx] % 256
        return n

    def unhashContent(hashKey, indexData):
        for idx in range(len(indexData)):
            indexData[idx] ^= hashKey[idx % 8]
        return bytes(indexData)

    url = "https://manga.bilibili.com/twirp/comic.v1.Comic/Index"
    payload = f"access_key={access_key}&appkey={appkey}&device=android&ep_id={episodeId}&mobi_app=android_comic&platform=android"
    r = requests.post(url, headers=headers, data=payload)
    data = "https://i0.hdslb.com"+r.json()["data"].replace(r"\u003d", r"=")

    r = requests.get(data, headers=getHeaders)
    indexData = r.content
    hashKey = generateHashKey(comicId, episodeId)
    indexData = list(indexData)[9:]
    indexData = unhashContent(hashKey=hashKey, indexData=indexData)

    file = io.BytesIO(indexData)
    tmp_dir = tempfile.TemporaryDirectory()
    obj = zipfile.ZipFile(file)
    obj.extractall(tmp_dir.name)
    json_file = os.path.join(tmp_dir.name, "index.dat")

    return json.load(open(json_file))


def getImageToken(imageUrls):
    url = "https://manga.bilibili.com/twirp/comic.v1.Comic/ImageToken"
    data = {
        'access_key': access_key,
        'appkey': appkey,
        # 必须使用json.dumps否则会400，可能是requests对数组处理有问题
        'urls': json.dumps(imageUrls),
    }
    r = requests.post(url, data=data, headers=headers)
    token = r.json()["data"]
    return token


def download(url, token, imgPath):
    url = url+f"?token={token}"
    r = requests.get(url, headers=getHeaders, stream=True)
    if r.status_code == 200:
        with open(imgPath, 'wb') as f:
            for chunk in r:
                f.write(chunk)


def DownloadThread(q):
    while True:
        try:
            # 不阻塞的读取队列数据
            task = q.get_nowait()
            url = task["url"]
            token = task["token"]
            imgPath = task["imgPath"]
            download(url, token, imgPath)
            time.sleep(1)
        except queue.Empty as e:
            break
        except Exception as e:
            print(e)
            break
        q.task_done()


def main():
    detail = getComicDetail(comicId)
    comicName = detail["title"]
    comicDir = os.path.join(workDir, comicName)
    makeDir(comicDir)
    print(f"已获取漫画《{comicName}》详情，并建立文件夹")

    ep_list = detail["ep_list"]
    if detail["discount_type"] == 2:
        EpList = getEpList(ep_list, filter=False, beginId=beginId, endId=endId)
    else:
        EpList = getEpList(ep_list, filter=True, beginId=beginId, endId=endId)
    print("已获取章节列表")

    for ep in EpList:
        episodeId = ep["episodeId"]
        epDir = os.path.join(comicDir, f"{ep['name']} #{episodeId}#")
        makeDir(epDir)

        indexData = getEpIndex(comicId, episodeId)
        imageUrls = ["https://i0.hdslb.com{}".format(url)
                     for url in indexData["pics"]]
        data = getImageToken(imageUrls)
        print(f"已获取章节{ep['name']}的图片链接，章节id：{episodeId}")

        n = 1
        q = queue.Queue()
        for task in data:
            imgPath = os.path.join(epDir, f"{n}.jpg".zfill(6))
            n = n+1
            task["imgPath"] = imgPath
            q.put(task)
        threads = []
        for i in range(20):
            # 第一个参数是线程函数变量，第二个参数args是一个数组变量参数，
            # 如果只传递一个值，就只需要q, 如果需要传递多个参数，那么还可以继续传递下去其他的参数，
            # 其中的逗号不能少，少了就不是数组了，就会出错。
            thread = threading.Thread(target=DownloadThread, args=(q,))
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        print(f"已下载章节{ep['name']}，章节id：{episodeId}")

    print(f"漫画《{comicName}》下载完毕！\n"+"#"*10)
    input('按任意键退出')


if __name__ == '__main__':
    main()

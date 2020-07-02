# encoding:UTF-8
# python3.6

import zipfile
import json
import toml
import os
import time
import requests
from func_timeout import func_set_timeout, FunctionTimedOut
from tenacity import *
import threading
import queue
import qrcode
import hashlib
from urllib.parse import urlencode
from io import BytesIO

download_timeout = 60
max_threads = 10
epName_rule = "[@ord] @short_title @title"
epName_filter = True


class Bili:
    pc_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.97 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'accept-encoding': 'gzip, deflate',
    }
    app_headers = {
        'User-Agent': "Mozilla/5.0 BiliDroid/5.58.0 (bbcallen@gmail.com)",
        'Accept-encoding': "gzip",
        'Buvid': "XZ11bfe2654a9a42d885520a680b3574582eb3",
        'Display-ID': "146771405-1521008435",
        'Device-Guid': "2d0bbec5-df49-43c5-8a27-ceba3f74ffd7",
        'Device-Id': "469a6aaf431b46f8b58a1d4a91d0d95b202004211125026456adffe85ddcb44818",
        'Accept-Language': "zh-CN",
        'Accept': "text/html,application/xhtml+xml,*/*;q=0.8",
        'Connection': "keep-alive",
    }
    app_params = {
        'access_key': '',
        'appkey': '1d8b6e7d45233436',
    }
    appkey = '1d8b6e7d45233436'
    app_secret = '560c52ccd288fed045859ed18bffd973'
    access_key = ''
    cookies = {}
    login_platform = set()

    def __init__(self, s, dict_user=None):
        # s requests.session()
        # dict_user dict 从配置文件中读取的用户登录信息
        self.s = s
        if 'access_key' in dict_user:
            self.access_key = dict_user['access_key']
            self.app_params['access_key'] = self.access_key
        if 'cookies' in dict_user:
            cookiesStr = dict_user['cookies']
            if cookiesStr != "":
                cookies = {}
                for line in cookiesStr.split(';'):
                    key, value = line.strip().split('=', 1)
                    cookies[key] = value
                self.cookies = cookies

    def _session(self, method, url, platform='pc', level=1, **kwargs):
        if platform == 'app':
            if 'params' in kwargs:
                kwargs['params'].update(self.app_params)
            else:
                kwargs['params'] = self.app_params
            kwargs['params']['ts'] = str(int(time.time()))
            kwargs['params']['sign'] = self.calc_sign(kwargs['params'])
        if not 'headers' in kwargs:
            kwargs['headers'] = Bili.pc_headers if platform == 'pc' else Bili.app_headers
        r = self.s.request(method, url, **kwargs)
        return r.json()['data'] if level == 2 else r.json() if level == 1 else r

    def calc_sign(self, params: dict):
        params_list = list(params.items())
        params_list.sort()
        params_str = urlencode(params_list)
        sign_hash = hashlib.md5()
        sign_hash.update(f"{params_str}{Bili.app_secret}".encode('utf-8'))
        return sign_hash.hexdigest()

    def isLogin(self, platform='pc'):
        if platform == 'pc':
            if self.cookies:
                r = self._session(
                    'get', 'http://api.bilibili.com/nav', cookies=self.cookies)
                if r['code'] == 0:
                    self.s.cookies = requests.utils.cookiejar_from_dict(
                        self.cookies, cookiejar=None, overwrite=True)
            else:
                r = self._session(
                    'get', 'https://api.bilibili.com/x/web-interface/nav/stat')
            status = True if r['code'] == 0 else False
            if status:
                self.login_platform.add('pc')
            else:
                self.login_platform.discard('pc')
        else:
            url = "https://passport.bilibili.com/api/v2/oauth2/info"
            r = self._session('get', url, platform='app')
            status = True if r['code'] == 0 else False
            if status:
                self.login_platform.add('app')
            else:
                self.login_platform.discard('app')
        return status

    def key2cookie(self):
        params = {
            'gourl': 'https://account.bilibili.com/account/home',
        }
        r = self._session(
            'get', 'https://passport.bilibili.com/api/login/sso', level=0, params=params)
        return requests.utils.dict_from_cookiejar(self.s.cookies)

    def renewToken(self):
        r = self._session(
            'get', 'https://account.bilibili.com/api/login/renewToken')
        if r['code'] == 0:
            str_time = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(r['expires']))
            print(f"access_key的有效期已延长至{str_time}")
            return True
        else:
            print(f"access_key的有效期延长失败,{r['message']}")
            return False

    def get_qrcode(self):
        r = self._session(
            'get', 'https://passport.bilibili.com/qrcode/getLoginUrl', level=2)
        code_url = r['url']
        img = qrcode.make(code_url)
        self.oauthKey = r['oauthKey']
        return img

    def get_qrcodeInfo(self):
        r = self._session('post', 'https://passport.bilibili.com/qrcode/getLoginInfo',
                          data={'oauthKey': self.oauthKey})
        while not r['status']:
            time.sleep(2)
            r = self._session(
                'post', 'https://passport.bilibili.com/qrcode/getLoginInfo', data={'oauthKey': self.oauthKey})
            if r['data'] == -2:
                print('二维码已过期')
                break
            elif r['data'] == -1:
                print('oauthKey错误')
                break
        return r['status']

    def login_qrcode(self, path=None):
        # path QR码图片的存储位置
        if path == None:
            path = os.getcwd()
        qr = self.get_qrcode()
        qr.save(os.path.join(path, 'QR.jpg'))
        print("请打开图片QR.jpg，用app扫码")
        info = self.get_qrcodeInfo()
        if info:
            self.login_platform.add('pc')
            print("扫码登录成功")
            return True
        else:
            print("扫码登录失败")
            return False


class DownloadThread(threading.Thread):
    def __init__(self, queue, overwrite=True):
        threading.Thread.__init__(self)
        self.queue = queue
        self.overwrite = overwrite

    def run(self):
        while True:
            if self.queue.empty():
                break
            url, path = self.queue.get_nowait()
            try:
                if not self.overwrite and os.path.exists(path):
                    print(f"图片{os.path.basename(path)}已存在，跳过")
                else:
                    self.download(url, path)
            except Exception as e:
                print(f"{url} download fail:{e}")
            self.queue.task_done()
            time.sleep(1)

    @func_set_timeout(download_timeout)
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
    def download(self, url, path):
        r = requests.get(url, stream=True)
        r.raise_for_status()
        f = open(path, 'wb')
        for chunk in r.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
        f.close()
        r.close()


class BiliManga:
    pc_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.97 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'accept-encoding': 'gzip, deflate',
    }
    app_headers = {
        'Content-Type': "application/x-www-form-urlencoded; charset=UTF-8",
        'user-agent': "Mozilla/5.0 BiliComic/3.0.0",
        'Host': "manga.bilibili.com",
        'accept-encoding': 'gzip',
    }
    okhttp_headers = {
        'User-Agent': 'okhttp/3.10.0',
        'Host': 'manga.hdslb.com'
    }
    app_params = {
        'device': 'android',
        'mobi_app': 'android_comic',
        'platform': 'android',
        'version': '3.0.0',
        'buuild': '30000001',
        'is_teenager': '0',
        'appkey': 'cc8617fd6961e070',
    }
    pc_params = {
        'device': 'pc',
        'platform': 'web',
    }
    URL_DETAIL = "https://manga.bilibili.com/twirp/comic.v2.Comic/ComicDetail"
    URL_IMAGE_INDEX = "https://manga.bilibili.com/twirp/comic.v1.Comic/GetImageIndex"
    URL_IMAGE_TOKEN = "https://manga.bilibili.com/twirp/comic.v1.Comic/ImageToken"

    def __init__(self, s, comicId, platform='pc', access_key=None):
        self.s = s
        self.comicId = int(comicId)
        self.platform = platform
        if access_key != None:
            self.app_params['access_key'] = access_key

    def _session(self, method, url, level=2, **kwargs):
        if not 'headers' in kwargs:
            kwargs['headers'] = self.pc_headers if self.platform == 'pc' else self.app_headers
        if self.platform == 'app':
            if 'data' in kwargs:
                kwargs['data'].update(self.app_params)
        elif self.platform == 'pc':
            if 'params' not in kwargs:
                kwargs['params'] = self.pc_params
        r = self.s.request(method, url, **kwargs)
        return r.json()['data'] if level == 2 else r.json() if level == 1 else r.content

    def getComicDetail(self, comicId=None):
        if comicId == None:
            comicId = self.comicId
        try:
            detail = self._session('post', self.URL_DETAIL, data={
                                   'comic_id': comicId})
            self.detail = detail
            return detail
        except Exception as e:
            print(f"getComicDetail fail,id={comicId},{e}")
            raise e

    def printList(self, path, ep_list=None, filter=True):
        if ep_list == None:
            ep_list = self.detail['ep_list']
        file = os.path.join(path, "漫画详情.txt")
        text = ""
        for ep in ep_list:
            if filter:
                if ep["is_locked"] and not ep["is_in_free"]:
                    continue
            text = text+f"ord:{ep['ord']:<3} 章节id：{ep['id']},章节名：{ep['short_title']} {ep['title']}\n"
        with open(file, "w+", encoding="utf-8") as f:
            f.write(text)

    def getImages(self, ep_id):
        ep_id = int(ep_id)
        c = self.comicId
        data = self._session('post', self.URL_IMAGE_INDEX,
                             data={'ep_id': ep_id})
        url = data['host'] + data['path'].replace(r"\u003d", "=")
        data = bytearray(self._session('get', url, level=0,
                                       headers=self.okhttp_headers)[9:])
        key = [ep_id & 0xff, ep_id >> 8 & 0xff, ep_id >> 16 & 0xff, ep_id >> 24 & 0xff,
               c & 0xff, c >> 8 & 0xff, c >> 16 & 0xff, c >> 24 & 0xff]
        for i in range(len(data)):
            data[i] ^= key[i % 8]
        file = BytesIO(data)
        zf = zipfile.ZipFile(file)
        data = json.loads(zf.read('index.dat'))
        zf.close()
        file.close()
        return data['pics']

    def getImageToken(self, imageUrls):
        data = self._session('post', self.URL_IMAGE_TOKEN, data={
                             'urls': json.dumps(imageUrls)})
        pic_list = []
        for i in data:
            pic_list.append(f"{i['url']}?token={i['token']}")
        return pic_list

    def downloadEp(self, ep_data, path, overwrite=True):
        epName = custom_name(ep_data, epName_filter, epName_rule)
        epDir = os.path.join(path, epName)
        ep_id = ep_data['id']
        pic_list = [
            "https://manga.hdslb.com{}".format(url) for url in self.getImages(ep_id)]
        filetype = pic_list[0].split(".")[-1]
        imageUrls = self.getImageToken(pic_list)
        q = queue.Queue()
        for n, url in enumerate(imageUrls, 1):
            imgPath = os.path.join(epDir, f"{n}.{filetype}")
            q.put((url, imgPath))
        num = min(len(imageUrls), max_threads)
        for i in range(num):
            t = DownloadThread(q, overwrite)
            t.setDaemon(True)
            t.start()
        q.join()

    def parser_ep_str(self, ep_str):
        chapter_number_list = []
        last = self.detail['ep_list'][0]['ord']
        first = self.detail['ep_list'][-1]['ord']
        offset = 1-first  # 有时ord可能并非从1开始
        if ep_str == 'all':
            for number in range(1, last + 1):
                chapter_number_list.append(number)
        else:
            # try:
            #     chapter_number = max(1,int(ep_str))
            # except ValueError:
            #     pass

            appeared = set()
            for block in ep_str.split(','):
                if '-' in block:
                    start, end = block.split('-', 1)
                    start = max(first, int(start))
                    end = max(start, int(end)) if int(end) <= last else last
                    for number in range(start, end + 1):
                        if number not in appeared:
                            appeared.add(number)
                            chapter_number_list.append(number)
                else:
                    number = int(block)
                    if number not in appeared:
                        appeared.add(number)
                        chapter_number_list.append(number)
        chapter_list = []
        for n in chapter_number_list:
            ep = self.detail['ep_list'][-n-offset]
            if ep["is_locked"] and not ep["is_in_free"]:
                continue
            chapter_list.append(ep)
        return chapter_list


def makeDir(dirPath):
    if os.path.isdir(dirPath) == False:
        os.makedirs(dirPath)
        return True
    else:
        return False


def safe_filename(filename, replace=' '):
    """文件名过滤非法字符串
    """
    filename = filename.rstrip('\t')
    ILLEGAL_STR = r'\/:*?"<>|'
    replace_illegal_str = str.maketrans(
        ILLEGAL_STR, replace * len(ILLEGAL_STR))
    new_filename = filename.translate(replace_illegal_str).strip()
    if new_filename:
        return new_filename
    raise Exception('文件名不合法. new_filename={}'.format(new_filename))


def custom_name(ep_data, filter=True, name=epName_rule):
    trans_dict = {
        "@ord": str(ep_data["ord"]),
        "@id": str(ep_data["id"]),
        "@short_title": ep_data["short_title"],
        "@title": ep_data["title"],
        "@pub_time": ep_data["pub_time"],
    }
    if filter:
        appeared = set()
        for k, v in trans_dict.items():
            if v in appeared:
                trans_dict[k] = ""
            else:
                appeared.add(v)
    for k, v in trans_dict.items():
        name = name.replace(k, v)
    return safe_filename(name)


def load_config(conf='config.toml'):
    with open(conf, encoding="utf-8") as f:
        dict_conf = toml.load(f)
    is_ok = True
    if "user" not in dict_conf:
        is_ok = False
    elif 'access_key' not in dict_conf['user']:
        is_ok = False
    elif 'cookies' not in dict_conf['user']:
        is_ok = False

    if 'comic' not in dict_conf:
        is_ok = False
    elif 'comicId' not in dict_conf['comic']:
        is_ok = False
    elif 'ep_str' not in dict_conf['comic']:
        is_ok = False
    if not is_ok:
        print("配置文件缺少内容")
        exit()
    if 'setting' in dict_conf:
        global max_threads, epName_rule, epName_filter
        setting = dict_conf['setting']
        max_threads = setting['max_threads']
        epName_rule = setting['epName_rule']
        epName_filter = True if setting['epName_filter'] == "True" else False
    return dict_conf


def cookies2conf(cookies: dict, conf='config.toml'):
    cookiesStr = ""
    for k, v in cookies.items():
        cookiesStr = cookiesStr+f"{k}={v};"
    with open(conf, 'r', encoding="utf-8") as f:
        dict_conf = toml.load(f)
    dict_conf['user']['cookies'] = cookiesStr
    with open(conf, 'w', encoding="utf-8") as f:
        toml.dump(dict_conf, f)


def main():
    workDir = os.getcwd()
    global config
    config = os.path.join(workDir, 'config.toml')
    if os.path.exists(config):
        dict_conf = load_config(config)
        dict_user = dict_conf['user']
        dict_comic = dict_conf['comic']
    else:
        print("未找到配置文件")
        exit()

    if dict_comic['comicId'] == "":
        comicId = int(input("输入mc号（纯数字）："))
    else:
        comicId = int(dict_comic['comicId'])

    s = requests.session()
    bili = Bili(s, dict_user)

    if dict_user['access_key'] != "" and bili.isLogin('app'):
        print("成功使用app端登录")
        bili.renewToken()
        manga = BiliManga(s, comicId, 'app', dict_user['access_key'])
    elif dict_user['cookies'] != "" and bili.isLogin('pc'):
        print("成功使用pc端登录")
        manga = BiliManga(s, comicId)
    else:
        if not bili.login_qrcode(workDir):
            ok = True if input("目前未登录，输入任意内容时继续下载，按回车退出:") else False
            if not ok:
                exit()
        else:
            cookies = requests.utils.dict_from_cookiejar(s.cookies)
            cookies2conf(cookies, config)
        manga = BiliManga(s, comicId)

    manga.getComicDetail()
    comicName = safe_filename(manga.detail['title'])
    mangaDir = os.path.join(workDir, comicName)
    makeDir(mangaDir)
    manga.printList(mangaDir)
    print(f"已获取漫画《{comicName}》详情，并建立文件夹。")

    if dict_comic['ep_str'] != "":
        ep_str = dict_comic['ep_str']
    else:
        print("#"*10+"\n如何输入下载范围：\n输入1-4表示下载ord（序号）1至4的章节\n输入3,5表示下载ord（序号）3、5的章节\n同理，可混合输入1-5,9,55-60")
        print(f"漫画章节详情见“{comicName}/漫画详情.txt”文件（只列出了目前可下载的章节）")
        print("ps：请使用英文输入法，按回车键结束输入\n"+"#"*10)
        ep_str = input("请输入下载范围：")
    download_list = manga.parser_ep_str(ep_str)
    print("已获取章节列表")

    for ep in download_list:
        manga.downloadEp(ep, mangaDir)
        print(f"已下载章节{epName}，章节id：{ep['id']},ord:{ep['ord']}")

    print(f"漫画《{comicName}》下载完毕！\n"+"#"*10)
    input('按任意键退出')


if __name__ == '__main__':
    main()

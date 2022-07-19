# encoding:UTF-8
# python3.6

import hashlib
import json
import os
import queue
import threading
import time
import zipfile
from io import BytesIO
from urllib.parse import urlencode, urlparse, parse_qs

import qrcode
import requests
import toml
from func_timeout import FunctionTimedOut, func_set_timeout
from tenacity import *

download_timeout = 60
max_threads = 10
epName_rule = "[@ord] @short_title @title"
epName_filter = False
bonusName_rule = "[@id] @title @detail"
bonusName_filter = False


def find_index(list, key):
    try:
        index = list.index(key)
    except ValueError:
        index = None
    return index


class Bili:
    pc_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.97 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "accept-encoding": "gzip, deflate",
    }
    app_headers = {
        "User-Agent": "Mozilla/5.0 BiliDroid/5.58.0 (bbcallen@gmail.com)",
        # "Accept-encoding": "gzip",
        # "Buvid": "XZ11bfe2654a9a42d885520a680b3574582eb3",
        # "Display-ID": "146771405-1521008435",
        # "Device-Guid": "2d0bbec5-df49-43c5-8a27-ceba3f74ffd7",
        # "Device-Id": "469a6aaf431b46f8b58a1d4a91d0d95b202004211125026456adffe85ddcb44818",
        # "Accept-Language": "zh-CN",
        # "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        # "Connection": "keep-alive",
    }
    app_params = {
        "appkey": "4409e2ce8ffd12b8",
    }
    app_secret = "59b43e04ad6965f34319062b478f83dd"
    URL_TEST_PC_LOGIN = "https://api.bilibili.com/nav"
    URL_TEST_APP_LOGIN = "https://app.bilibili.com/x/v2/account/myinfo"
    URL_RENEW_KEY = "https://account.bilibili.com/api/login/renewToken"
    URL_KEY_TO_COOKIE = "https://passport.bilibili.com/api/login/sso"
    URL_COOKIE_TO_KEY = "https://passport.bilibili.com/login/app/third?appkey=27eb53fc9058f8c3&api=http://link.acg.tv/forum.php&sign=67ec798004373253d60114caaad89a8c"

    cookies = {}
    login_platform = set()

    def __init__(self, s, dict_user=None):
        # s requests.session()
        # dict_user dict 从配置文件中读取的用户登录信息
        self.s = s
        if "access_key" in dict_user:
            # api接口要求access_key在params中排第一
            if dict_user["access_key"] != "":
                params = {"access_key": dict_user["access_key"]}
                params.update(self.app_params)
                self.app_params = params.copy()
        if "cookies" in dict_user:
            cookiesStr = dict_user["cookies"]
            if cookiesStr != "":
                cookies = {}
                for line in cookiesStr.split(";"):
                    if line == "":
                        break
                    key, value = line.strip().split("=", 1)
                    cookies[key] = value
                self.cookies = cookies

    def _session(self, method, url, platform="pc", level=1, **kwargs):
        if platform == "app":
            # api接口要求在params中access_key排第一,sign排最末。
            # py3.6及之后dict中item顺序为插入顺序
            if "params" in kwargs:
                params = self.app_params.copy()
                params.update(kwargs["params"])
                kwargs["params"] = params
            else:
                kwargs["params"] = self.app_params
            kwargs["params"]["ts"] = str(int(time.time()))
            kwargs["params"]["sign"] = self.calc_sign(kwargs["params"])
        if not "headers" in kwargs:
            kwargs["headers"] = (
                Bili.pc_headers if platform == "pc" else Bili.app_headers
            )
        r = self.s.request(method, url, **kwargs)
        return r.json()["data"] if level == 2 else r.json() if level == 1 else r

    def calc_sign(self, params: dict):
        params_list = sorted(params.items())
        params_str = urlencode(params_list)
        sign_hash = hashlib.md5()
        sign_hash.update(f"{params_str}{Bili.app_secret}".encode())
        return sign_hash.hexdigest()

    def isLogin(self, platform="pc"):
        if platform == "pc":
            if self.cookies:
                r = self._session("get", self.URL_TEST_PC_LOGIN, cookies=self.cookies)
                if r["code"] == 0:
                    self.s.cookies = requests.utils.cookiejar_from_dict(
                        self.cookies, cookiejar=None, overwrite=True
                    )
            else:
                r = self._session("get", self.URL_TEST_PC_LOGIN)
            status = True if r["code"] == 0 else False
            if status:
                self.login_platform.add("pc")
            else:
                self.login_platform.discard("pc")
        else:
            r = self._session("get", self.URL_TEST_APP_LOGIN, platform="app")
            status = True if r["code"] == 0 else False
            if status:
                self.login_platform.add("app")
            else:
                self.login_platform.discard("app")
        return status

    def key2cookie(self):
        params = {
            "gourl": "https://account.bilibili.com/account/home",
        }
        r = self._session("get", self.URL_KEY_TO_COOKIE, level=0, params=params)
        return requests.utils.dict_from_cookiejar(self.s.cookies)

    def cookie2key(self):
        r = self._session("get", self.URL_COOKIE_TO_KEY, platform="pc")
        if r["status"]:
            confirm_uri = r["data"]["confirm_uri"]
            r = self._session(
                "get", confirm_uri, platform="pc", level=0, allow_redirects=False
            )
            redirect_url = r.headers.get("Location")
            access_key = parse_qs(urlparse(redirect_url).query)["access_key"][0]
            return access_key
        else:
            raise Exception(f"由cookies获取access_key失败：{r}")

    def renewToken(self):
        r = self._session("get", self.URL_RENEW_KEY)
        if r["code"] == 0:
            str_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["expires"]))
            print(f"access_key的有效期已延长至{str_time}")
            return True
        else:
            print(f"access_key的有效期延长失败,{r['message']}")
            return False

    def login_qrcode(self, path=None):
        # path QR码图片的存储位置
        def get_qrcode():
            r = self._session("get", "https://passport.bilibili.com/qrcode/getLoginUrl")
            if r["status"]:
                code_url = r["data"]["url"]
                img = qrcode.make(code_url)
                self.oauthKey = r["data"]["oauthKey"]
                return img
            else:
                raise Exception(f"请求登录二维码失败：{r}")

        def get_qrcodeInfo():
            while True:
                r = self._session(
                    "post",
                    "https://passport.bilibili.com/qrcode/getLoginInfo",
                    data={"oauthKey": self.oauthKey},
                )
                # print(r)
                if r["status"]:
                    break
                elif r["data"] == -2:
                    raise Exception("二维码已过期")
                elif r["data"] == -1:
                    raise Exception("oauthKey错误")
                time.sleep(2)
            return r["status"]

        if path is None:
            path = os.getcwd()
        qr = get_qrcode()
        qr.save(os.path.join(path, "QR.jpg"))
        print("请打开图片QR.jpg，用app扫码")
        info = get_qrcodeInfo()
        if info:
            self.login_platform.add("pc")
            print("扫码登录成功")
            return True
        else:
            print("扫码登录失败")
            return False

    def login_qrcode_tv(self, path=None):
        if path is None:
            path = os.getcwd()
        r = self._session(
            "post",
            "http://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code",
            platform="app",
            params={"local_id": "0"},
        )
        if r["code"] == 0:
            code_url = r["data"]["url"]
            img = qrcode.make(code_url)
            self.auth_code = r["data"]["auth_code"]
            img.save(os.path.join(path, "QR.jpg"))
            print("请打开图片QR.jpg，用app扫码")
        elif r["code"] == -3:
            raise Exception("API校验密匙错误")
        elif r["code"] == -400:
            raise Exception("请求错误")

        input("app扫码确认完毕后，按任意键继续……")
        while True:
            r = self._session(
                "post",
                "http://passport.bilibili.com/x/passport-tv-login/qrcode/poll",
                platform="app",
                data={"auth_code": self.auth_code, "local_id": "0"},
            )
            # print(r)
            if r["code"] == 0:
                break
            elif r["code"] == 86038:
                raise Exception("二维码已过期")
            elif r["code"] == -3:
                raise Exception("API校验密匙错误")
            elif r["code"] == -400:
                raise Exception("请求错误")
            time.sleep(2)
        info = r["data"]
        params = {"access_key": info["access_token"]}
        self.app_params.pop("access_key", None)
        params.update(self.app_params)
        self.app_params = params.copy()
        self.login_platform.add("app")
        print("扫码（tv）登录成功")
        return True


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
        f = open(path, "wb")
        for chunk in r.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
        f.close()
        r.close()


class BiliManga:
    comicId = None
    platform = "pc"
    pc_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.97 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "accept-encoding": "gzip, deflate",
    }
    app_headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "user-agent": "Mozilla/5.0 BiliComic/3.0.0",
        "Host": "manga.bilibili.com",
        "accept-encoding": "gzip",
    }
    okhttp_headers = {"User-Agent": "okhttp/3.10.0", "Host": "manga.hdslb.com"}
    app_params = {
        "access_key": "",
        "device": "android",
        "mobi_app": "android_comic",
        "platform": "android",
        "version": "3.0.0",
        "buuild": "30000001",
        "is_teenager": "0",
        "appkey": "cc8617fd6961e070",
    }
    pc_params = {
        "device": "pc",
        "platform": "web",
    }
    URL_DETAIL = "https://manga.bilibili.com/twirp/comic.v2.Comic/ComicDetail"
    URL_IMAGE_INDEX = "https://manga.bilibili.com/twirp/comic.v1.Comic/GetImageIndex"
    URL_IMAGE_TOKEN = "https://manga.bilibili.com/twirp/comic.v1.Comic/ImageToken"
    URL_BONUS = "https://manga.bilibili.com/twirp/comic.v1.Comic/GetComicAlbumPlus"

    def __init__(self, s, comicId, platform="pc", access_key=None):
        self.s = s
        self.comicId = int(comicId)
        self.platform = platform
        if access_key is not None:
            self.app_params["access_key"] = access_key

    def _session(self, method, url, level=2, **kwargs):
        if not "headers" in kwargs:
            kwargs["headers"] = (
                self.pc_headers if self.platform == "pc" else self.app_headers
            )
        if self.platform == "app":
            if "data" in kwargs:
                kwargs["data"].update(self.app_params)
        elif self.platform == "pc":
            if "params" not in kwargs:
                kwargs["params"] = self.pc_params
        r = self.s.request(method, url, **kwargs)
        return r.json()["data"] if level == 2 else r.json() if level == 1 else r.content

    def getComicDetail(self, comicId=None):
        if comicId is None:
            comicId = self.comicId
        try:
            detail = self._session("post", self.URL_DETAIL, data={"comic_id": comicId})
            epData = {}
            for ep in detail["ep_list"]:
                epData[str(ep["ord"])] = ep
            detail["epData"] = epData
            self.detail = detail
            return detail
        except Exception as e:
            print(f"getComicDetail fail,id={comicId},{e}")
            raise e

    def getBonusData(self, comicId=None):
        if comicId is None:
            comicId = self.comicId
        try:
            detail = self._session("post", self.URL_BONUS, data={"comic_id": comicId})
            epData = {}
            for ep in detail["list"]:
                bonus_item = ep["item"]
                bonus_item["is_locked"] = ep["isLock"]
                epData[str(bonus_item["id"])] = bonus_item
            self.BonusData = epData
            return epData
        except Exception as e:
            print(f"getBonus fail,id={comicId},{e}")
            raise e

    def printList(self, path, ep_list=None, filter=True, isBonus=False):
        if isBonus:
            if ep_list is None:
                ep_list = list(self.BonusData.values())
            filename = "漫画详情（特典）.txt"
        else:
            if ep_list is None:
                ep_list = self.detail["ep_list"]
            filename = "漫画详情.txt"
        file = os.path.join(path, filename)
        text = ""
        for ep in ep_list:
            if filter:
                if (
                    not isBonus
                    and ep.get("is_locked", False)
                    and not ep.get("is_in_free", True)
                ):
                    continue
                elif isBonus and ep.get("is_locked", False):
                    continue
            if not isBonus:
                text = (
                    text
                    + f"ord:{ep['ord']:<6} 章节id：{ep['id']},章节名：{ep['short_title']} {ep['title']}\n"
                )
            else:
                text = text + f"id：{ep['id']:<6} 特典标题：{ep['title']} 详情：{ep['detail']}\n"
        if text == "":
            text = "不存在可以下载的章节"
        with open(file, "w+", encoding="utf-8") as f:
            f.write(text)

    def getindex(self, content, ep_id, comicId=None):
        content = content[9:]
        if comicId is None:
            comicId = self.comicId
        key = [
            ep_id & 0xFF,
            ep_id >> 8 & 0xFF,
            ep_id >> 16 & 0xFF,
            ep_id >> 24 & 0xFF,
            comicId & 0xFF,
            comicId >> 8 & 0xFF,
            comicId >> 16 & 0xFF,
            comicId >> 24 & 0xFF,
        ]
        for i in range(len(content)):
            content[i] ^= key[i % 8]
        file = BytesIO(content)
        zf = zipfile.ZipFile(file)
        data = json.loads(zf.read("index.dat"))
        zf.close()
        file.close()
        return data

    def getImages(self, ep_id):
        ep_id = int(ep_id)
        c = self.comicId
        data = self._session("post", self.URL_IMAGE_INDEX, data={"ep_id": ep_id})
        pics = ["{}".format(image["path"]) for image in data["images"]]
        # url = data['host'] + data['path'].replace(r"\u003d", "=")
        # content = bytearray(self._session('get', url, level=0,
        #                                headers=self.okhttp_headers))
        # data = self.getindex(content, ep_id)
        # return data["pics"]
        return pics

    def getImageToken(self, imageUrls):
        data = self._session(
            "post", self.URL_IMAGE_TOKEN, data={"urls": json.dumps(imageUrls)}
        )
        pic_list = []
        for i in data:
            pic_list.append(f"{i['url']}?token={i['token']}")
        return pic_list

    def downloadEp(self, ep_data, path, overwrite=True, isBonus=False):
        if isBonus:
            if ep_data.get("is_locked", False):
                return
            epName = self.custom_name(ep_data, bonusName_filter, bonusName_rule)
            epDir = os.path.join(path, epName)
            os.makedirs(epDir, exist_ok=True)
            imageUrls = ep_data["pic"]
            filetype = imageUrls[0].split(".")[-1].split("?")[0]
        else:
            if ep_data.get("is_locked", False) and not ep_data.get("is_in_free", True):
                return
            epName = self.custom_name(ep_data, epName_filter, epName_rule)
            ep_id = ep_data["id"]
            pic_list = [
                "https://manga.hdslb.com{}".format(url) for url in self.getImages(ep_id)
            ]
            filetype = pic_list[0].split(".")[-1]
            imageUrls = self.getImageToken(pic_list)

        epDir = os.path.join(path, epName)
        os.makedirs(epDir, exist_ok=True)
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

    def parser_ep_str(self, ep_str, isBonus=False):
        if isBonus:
            epData = self.BonusData
            sortKey = "id"
        else:
            epData = self.detail["epData"]
            sortKey = "ord"
        chapter_list = []
        if ep_str.lower() == "all":
            appeared = set(epData.keys())
        else:
            keys = list(epData.keys())
            keys.sort(key=lambda x: float(x))
            appeared = set()
            for block in ep_str.split(","):
                if "-" in block:
                    start, end = block.split("-", 1)
                    start = start if float(start) > float(keys[0]) else keys[0]
                    end = end if float(end) < float(keys[-1]) else keys[-1]
                    ep_range = lambda elem: float(elem) <= float(end) and float(
                        elem
                    ) >= float(start)
                    for key in filter(ep_range, keys):
                        if key not in appeared:
                            appeared.add(key)
                else:
                    key = block
                    if key not in appeared and epData.get(key):
                        appeared.add(key)

        for key in appeared:
            ep = epData[key]
            if (
                not isBonus
                and ep.get("is_locked", False)
                and not ep.get("is_in_free", True)
            ):
                continue
            elif isBonus and ep.get("is_locked", False):
                continue
            chapter_list.append(epData[key])
        chapter_list.sort(key=lambda x: float(x[sortKey]))
        return chapter_list

    def custom_name(self, ep_data, filter=False, name=epName_rule):
        trans_dict = {
            "@ord": str(ep_data.get("ord", "")),
            "@id": str(ep_data.get("id", "")),
            "@short_title": ep_data.get("short_title", ""),
            "@title": ep_data.get("title", ""),
            "@detail": ep_data.get("detail", ""),
        }
        # 重复的变量会被忽略，避免名称中重复出现几个词
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


def safe_filename(filename, replace=" "):
    """文件名过滤非法字符串"""
    filename = filename.rstrip("\t")
    ILLEGAL_STR = r'\/:*?"<>|'
    replace_illegal_str = str.maketrans(ILLEGAL_STR, replace * len(ILLEGAL_STR))
    new_filename = filename.translate(replace_illegal_str).strip()
    if new_filename:
        return new_filename
    raise Exception("文件名不合法. new_filename={}".format(new_filename))


def load_config(conf="config.toml"):
    with open(conf, encoding="utf-8") as f:
        dict_conf = toml.load(f)
    is_ok = True
    if "user" not in dict_conf:
        is_ok = False
    elif "access_key" not in dict_conf["user"]:
        is_ok = False
    elif "cookies" not in dict_conf["user"]:
        is_ok = False

    if "comic" not in dict_conf:
        is_ok = False
    elif "comicId" not in dict_conf["comic"]:
        is_ok = False
    elif "ep_str" not in dict_conf["comic"]:
        is_ok = False
    if not is_ok:
        print("配置文件缺少内容")
        exit()
    if "setting" in dict_conf:
        global max_threads, epName_rule, epName_filter, bonusName_rule, bonusName_filter
        setting = dict_conf["setting"]
        max_threads = setting.get("max_threads", max_threads)
        epName_rule = setting.get("epName_rule", epName_rule)
        epName_filter = (
            True if setting.get("epName_filter", epName_filter) == "True" else False
        )
        bonusName_rule = setting.get("bonusName_rule", bonusName_rule)
        bonusName_filter = (
            True
            if setting.get("bonusName_filter", bonusName_filter) == "True"
            else False
        )
    return dict_conf


def cookies2conf(cookies: dict, conf="config.toml"):
    cookiesStr = ""
    for k, v in cookies.items():
        cookiesStr = cookiesStr + f"{k}={v};"
    with open(conf, "r", encoding="utf-8") as f:
        dict_conf = toml.load(f)
    dict_conf["user"]["cookies"] = cookiesStr
    with open(conf, "w", encoding="utf-8") as f:
        toml.dump(dict_conf, f)


def ak2conf(access_key: str, conf="config.toml"):
    with open(conf, "r", encoding="utf-8") as f:
        dict_conf = toml.load(f)
    dict_conf["user"]["access_key"] = access_key
    with open(conf, "w", encoding="utf-8") as f:
        toml.dump(dict_conf, f)


def main():
    workDir = os.getcwd()
    global config
    config = os.path.join(workDir, "config.toml")
    if os.path.exists(config):
        dict_conf = load_config(config)
        dict_user = dict_conf["user"]
        dict_comic = dict_conf["comic"]
    else:
        print("未找到配置文件")
        exit()

    if dict_comic["comicId"] == "":
        comicId = int(input("输入mc号（纯数字）："))
    else:
        comicId = int(dict_comic["comicId"])

    s = requests.session()
    bili = Bili(s, dict_user)

    if dict_user["access_key"] != "" and bili.isLogin("app"):
        print("成功使用app端登录")
        manga = BiliManga(s, comicId, "app", dict_user["access_key"])
    elif dict_user["cookies"] != "" and bili.isLogin("pc"):
        print("成功使用pc端登录")
        # manga = BiliManga(s, comicId)
        access_key = bili.cookie2key()
        dict_user["access_key"] = access_key
        ak2conf(access_key, config)
        manga = BiliManga(s, comicId, platform="app", access_key=access_key)

    else:
        choise = input("目前未登录，输入0继续下载，输入1进行扫码登录（网页），输入2进行扫码登录（app）:")

        if choise == "1":
            if bili.login_qrcode(workDir):
                cookies = requests.utils.dict_from_cookiejar(s.cookies)
                cookies2conf(cookies, config)
                # manga = BiliManga(s, comicId)
                access_key = bili.cookie2key()
                dict_user["access_key"] = access_key
                ak2conf(access_key, config)
                manga = BiliManga(s, comicId, platform="app", access_key=access_key)
            else:
                choise = "0" if input("扫码登录（网页）失败，按回车退出，按其他键以未登录身份下载:") else "-1"
        elif choise == "2":
            if bili.login_qrcode_tv(workDir):
                access_key = bili.app_params["access_key"]
                ak2conf(access_key, config)
                manga = BiliManga(s, comicId, platform="app", access_key=access_key)
            else:
                choise = "0" if input("扫码登录（app）失败，按回车退出，按其他键以未登录身份下载:") else "-1"
        elif choise == "0":
            manga = BiliManga(s, comicId)
        else:
            exit()

    manga.getComicDetail()
    comicName = safe_filename(manga.detail["title"])
    mangaDir = os.path.join(workDir, comicName)
    os.makedirs(mangaDir, exist_ok=True)
    print(f"已获取漫画《{comicName}》详情，并建立文件夹。")
    while True:
        if manga.platform == "app" and manga.detail.get("album_count", 0) > 0:
            choice = input("下载漫画章节输入y，下载特典输入n：（y/n）")
            download_mode = "normal" if choice.lower() == "y" else "bonus"
        else:
            download_mode = "normal"

        if download_mode == "normal":
            manga.printList(mangaDir)
            if dict_comic["ep_str"] != "":
                ep_str = dict_comic["ep_str"]
            else:
                print(
                    "#" * 10
                    + "\n如何输入下载范围：\n输入1-4表示下载ord（序号）1至4的章节\n输入3,5表示下载ord（序号）3、5的章节\n同理，可混合输入1-5,9,55-60"
                    + "\n输入“all”可以下载所有章节"
                )
                print(f"漫画章节详情见“{comicName}/漫画详情.txt”文件（只列出了目前可下载的章节）")
                print("ps：请使用英文输入法，按回车键结束输入\n" + "#" * 10)
                ep_str = input("请输入下载范围：")
            download_list = manga.parser_ep_str(ep_str)
            print("已获取章节列表")

            for ep in download_list:
                manga.downloadEp(ep, mangaDir)
                print(f"已下载章节“{ep['title']}”，章节id：{ep['id']},ord:{ep['ord']}")
        elif download_mode == "bonus":
            manga.getBonusData()
            manga.printList(mangaDir, isBonus=True)
            print(
                "#" * 10
                + "\n如何输入下载范围：\n输入1-4表示下载id（序号）1至4的章节\n输入3,5表示下载id（序号）3、5的章节\n同理，可混合输入1-5,9,55-60"
            )
            print(f"漫画特典详情见“{comicName}/漫画详情(特典).txt”文件（只列出了目前可下载的特典）")
            print("ps：请使用英文输入法，按回车键结束输入\n" + "#" * 10)
            ep_str = input("请输入下载范围：")
            download_list = manga.parser_ep_str(ep_str, isBonus=True)
            print("已获取章节列表")

            for ep in download_list:
                manga.downloadEp(ep, mangaDir, isBonus=True)
                print(f"已下载特典“{ep['title']}{ep['detail']}”，章节id：{ep['id']}")

        print(f"漫画《{comicName}》的下载任务已完成！\n" + "#" * 10)
        if input("按任意键继续，输入y退出").lower() == "y":
            break


if __name__ == "__main__":
    main()

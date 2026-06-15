# ====================================================================
# FASIH-SM Scraper & Manager
# Version: 1.0.0 (Based on v10)
# Description: Automasi Scraping & Manajemen Penugasan FASIH-SM
# ====================================================================
import subprocess
import sys
import os
import time
import platform
import json
import pickle
import base64
import hashlib
import threading
import urllib.parse
from datetime import datetime
from getpass import getpass
import tkinter as tk
from tkinter import filedialog
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# Package pip
REQUIRED_PACKAGES = [
    ("requests",    "requests"),
    ("pandas",      "pandas"),
    ("openpyxl",    "openpyxl"),
    ("tqdm",        "tqdm"),
    ("selenium",    "selenium"),
    ("urllib3",     "urllib3"),
    ("undetected-chromedriver", "undetected_chromedriver"),
    ("webdriver-manager",        "webdriver_manager"),
]

def _auto_install_packages():
    missing = []
    for pip_name, import_name in REQUIRED_PACKAGES:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print(f"📦 Package belum terinstall: {', '.join(missing)}")
        print(f"⏳ Menginstall otomatis...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet"] + missing, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            print(f"✅ Berhasil menginstall: {', '.join(missing)}")
        except:
            print(f"❌ Gagal install. Jalankan manual: pip install {' '.join(missing)}")
            sys.exit(1)

_auto_install_packages()

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests.cookies import RequestsCookieJar
from tqdm import tqdm
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, StaleElementReferenceException

try:
    import undetected_chromedriver as uc
except ImportError:
    uc = None

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None

# ====================================================================
# KONFIGURASI
# ====================================================================
MAX_WORKERS_WILAYAH = 15
MAX_WORKERS_DETAIL = 20
REQUEST_TIMEOUT = 30
MAX_RETRIES = 5
MANUAL_MAX_RETRIES = 5
BASE_URL = "https://fasih-sm.bps.go.id"
BASE_OUTPUT_DIR = None

# ====================================================================
# UTILITY
# ====================================================================
def clear_screen():
    os.system('cls' if platform.system() == 'Windows' else 'clear')

def pilih_folder_simpan(judul) -> str:
    global BASE_OUTPUT_DIR
    if BASE_OUTPUT_DIR and os.path.exists(BASE_OUTPUT_DIR): return BASE_OUTPUT_DIR
    root = tk.Tk(); root.withdraw()
    folder = filedialog.askdirectory(title=judul)
    BASE_OUTPUT_DIR = folder if folder else os.getcwd()
    return BASE_OUTPUT_DIR

def create_resilient_session(cookies=None, headers=None) -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(total=MAX_RETRIES, backoff_factor=1.0, backoff_max=60, 
                            status_forcelist=[500, 502, 503, 504, 429], allowed_methods=["GET", "POST"], raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=100, pool_maxsize=100)
    session.mount("https://", adapter); session.mount("http://", adapter)
    if cookies: session.cookies = cookies
    if headers: session.headers.update(headers)
    return session

# ====================================================================
# SESSION & LOGIN
# ====================================================================
_OBF_KEY = b'fasih-sm-scraper-v8-key'
def _obf(p):
    if not p: return ''
    x = bytes(b ^ _OBF_KEY[i % len(_OBF_KEY)] for i, b in enumerate(p.encode()))
    return base64.b64encode(x).decode()

def _deobf(e):
    if not e: return ''
    try:
        x = base64.b64decode(e.encode())
        return bytes(b ^ _OBF_KEY[i % len(_OBF_KEY)] for i, b in enumerate(x)).decode()
    except: return e

def simpan_session(user, head, cook, sess, pwd, ls=None, ss=None):
    s_dir = os.path.join(pilih_folder_simpan("Pilih Folder Session"), "sessions")
    os.makedirs(s_dir, exist_ok=True)
    with open(os.path.join(s_dir, f"{user}_session.pkl"), 'wb') as f:
        pickle.dump({
            'user': user, 'pwd': _obf(pwd), 'head': head, 'cook': cook,
            'ls': ls, 'ss': ss, 'time': time.time()
        }, f)

def muat_session(user):
    s_dir = os.path.join(pilih_folder_simpan("Pilih Folder Session"), "sessions")
    f_path = os.path.join(s_dir, f"{user}_session.pkl")
    if os.path.exists(f_path):
        try:
            with open(f_path, 'rb') as f:
                d = pickle.load(f)
                if all(k in d for k in ['head', 'cook', 'pwd']):
                    # Jika session > 1 jam (3600 detik), anggap expired
                    if time.time() - d.get('time', 0) > 3600:
                        print("⏳ Session sudah lebih dari 1 jam. Perlu login ulang.")
                        # print(f"Password= {_deobf(d['pwd'])}")
                        # print(f"Password= {d['pwd']}")
                        return None, None, None, _deobf(d['pwd']), None, None
                    return d['head'], d['cook'], create_resilient_session(d['cook'], d['head']), _deobf(d['pwd']), d.get('ls'), d.get('ss')
        except:
            pass
    return None, None, None, None, None, None

def setup_driver():
    opts = uc.ChromeOptions() if uc else Options()
    opts.add_argument("--incognito")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-features=CalculateNativeWinOcclusion")
    
    # Deteksi versi Chrome untuk UC
    chrome_version = None
    if uc:
        try:
            import subprocess
            if platform.system() == 'Windows':
                cmd = 'reg query "HKEY_CURRENT_USER\\Software\\Google\\Chrome\\BLBeacon" /v version'
                output = subprocess.check_output(cmd, shell=True).decode()
                chrome_version = int(output.strip().split()[-1].split('.')[0])
            print(f"🔍 Terdeteksi Chrome Versi: {chrome_version}")
        except: pass

    if uc:
        try:
            print("🌐 Mencoba membuka browser (Undetected Mode)...")
            driver = uc.Chrome(options=opts, headless=False, use_subprocess=True, version_main=chrome_version)
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"})
            return driver
        except Exception as e:
            print(f"⚠️ Undetected-Chromedriver gagal: {e}")
            print("🔄 Mencoba metode cadangan (Selenium Standar + Manager)...")

    try:
        if ChromeDriverManager:
            print("🛠️ Mengunduh driver yang sesuai...")
            service = Service(ChromeDriverManager().install())
        else:
            service = Service()
        
        driver = webdriver.Chrome(service=service, options=opts)
        return driver
    except Exception as e:
        clear_screen()
        print("\n" + "="*50)
        print("❌ GAGAL MEMBUKA BROWSER ❌")
        print("="*50)
        print(f"Detail Error: {e}")
        print("-" * 50)
        print("SARAN SOLUSI:")
        print("1. Update Google Chrome Anda (Settings > About Chrome).")
        print("2. Jika error 'version mismatch', hapus folder %LOCALAPPDATA%\\undetected_chromedriver.")
        print("3. Pastikan tidak ada Chrome yang sedang terbuka secara abnormal.")
        print("="*50)
        input("\nTekan ENTER untuk keluar...")
        sys.exit(1)

def ambil_cookies_dan_buat_session(driver, pwd):
    selenium_cookies = driver.get_cookies()
    # Ambil LocalStorage & SessionStorage
    try:
        ls = driver.execute_script("return window.localStorage;")
        ss = driver.execute_script("return window.sessionStorage;")
    except:
        ls, ss = {}, {}

    jar = RequestsCookieJar()
    xsrf = ""
    for c in selenium_cookies:
        jar.set(c['name'], c['value'], domain=c.get('domain'), path=c.get('path', '/'))
        if c['name'] == 'XSRF-TOKEN': xsrf = urllib.parse.unquote(c['value'])
    head = {
        'X-Requested-With': 'XMLHttpRequest', 'X-XSRF-TOKEN': xsrf, 'Referer': BASE_URL + '/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Content-Type': 'application/json', 'Accept': 'application/json, text/plain, */*', 'Connection': 'keep-alive'
    }
    return head, jar, create_resilient_session(jar, head), pwd, ls, ss

def suntik_cookies_ke_driver(driver, jar, ls=None, ss=None):
    print("💉 Menyuntikkan session ke browser...")
    try:
        # 1. Buka domain target
        driver.get(BASE_URL + "/favicon.ico") 
        time.sleep(3)
        
        # 2. Injeksi Cookies
        current_domain = urllib.parse.urlparse(driver.current_url).netloc
        success_count = 0
        for cookie in jar:
            try:
                c = {'name': cookie.name, 'value': cookie.value, 'path': cookie.path}
                if cookie.domain:
                    domain = cookie.domain
                    if domain.startswith('.'):
                        if domain[1:] in current_domain or domain == current_domain:
                            c['domain'] = domain
                    else:
                        c['domain'] = domain
                driver.add_cookie(c)
                success_count += 1
            except: pass
        
        # 3. Injeksi Web Storage
        if ls:
            for k, v in ls.items():
                driver.execute_script(f"window.localStorage.setItem(arguments[0], arguments[1]);", k, v)
        if ss:
            for k, v in ss.items():
                driver.execute_script(f"window.sessionStorage.setItem(arguments[0], arguments[1]);", k, v)
        
        # 4. Refresh & Verifikasi
        driver.get(BASE_URL + "/")
        print("⏳ Menunggu verifikasi session...")
        
        # Tunggu sampai URL bukan lagi oauth_login atau kembali ke home
        start_wait = time.time()
        is_ok = False
        while time.time() - start_wait < 15: # Timeout 15 detik
            curr = driver.current_url
            if "oauth_login" not in curr and BASE_URL in curr:
                # Coba cek apakah ada indikator login (misal hilangnya tombol login)
                try:
                    driver.find_element(By.XPATH, '//*[@id="login-in"]/a[2]')
                except:
                    is_ok = True; break
            time.sleep(1)
            
        if is_ok:
            print(f"✅ Berhasil menyuntikkan session.")
            return True
        else:
            print("⚠️ Injeksi gagal atau dialihkan ke halaman login.")
            return False
            
    except Exception as e:
        print(f"❌ Gagal total menyuntikkan session: {e}")
        return False

def main_login(driver, user, pwd=None):
    if not pwd: pwd = input("Masukkan password SSO: ")
    
    # 1. Bersihkan sesi lama
    try:
        driver.get(BASE_URL + "/")
        time.sleep(2); driver.delete_all_cookies()
        driver.execute_script("window.localStorage.clear(); window.sessionStorage.clear(); if(window.indexedDB){indexedDB.databases().then(dbs=>dbs.forEach(db=>indexedDB.deleteDatabase(db.name)))}")
        driver.refresh()
    except: pass

    # 2. Buka FASIH dan klik tombol Login
    print("🌐 Membuka halaman FASIH dan menekan tombol login...")
    try:
        wait = WebDriverWait(driver, 15)
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="login-in"]/a[2]')))
        driver.execute_script("arguments[0].click();", btn)
        # Tunggu sampai browser dialihkan ke halaman SSO BPS
        WebDriverWait(driver, 10).until(lambda d: "sso.bps.go.id" in d.current_url)
    except:
        # Fallback jika gagal klik tombol
        driver.get(f"{BASE_URL}/oauth2/authorization/ics")
    
    try:
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.NAME, "username"))).send_keys(user)
        driver.find_element(By.NAME, "password").send_keys(pwd)
        driver.find_element(By.ID, "kc-login").click()
    except: pass

    try:
        otp_field = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.ID, "otp")))
        otp = input("Masukkan OTP SSO Anda: ").strip()
        otp_field.send_keys(otp)
        driver.find_element(By.ID, "kc-login").click()
    except: pass

    # Tunggu sampai login berhasil dan browser otomatis kembali ke FASIH (meninggalkan sso.bps.go.id)
    print("⏳ Menunggu Anda menyelesaikan login (Buka browser jika ada kendala/captcha)...")
    try:
        WebDriverWait(driver, 60).until(lambda d: "sso.bps.go.id" not in d.current_url)
    except:
        pass # Biarkan lanjut saja untuk mencoba memuat dashboard
        
    print("🔐 Otorisasi berhasil, memuat halaman FASIH...")
    time.sleep(3)
    
    # Masuk ke dashboard
    driver.get(f"{BASE_URL}/survey-collection/survey")
    WebDriverWait(driver, 15).until(lambda d: "survey-collection" in d.current_url)
    
    print("✅ Berhasil masuk ke dashboard FASIH!")
    return ambil_cookies_dan_buat_session(driver, pwd)

# ====================================================================
# CORE LOGIC (FETCH & PROCESS)
# ====================================================================
def get_survey_period(sid, sess, head):
    d = sess.get(f"{BASE_URL}/survey/api/v1/surveys/{sid}", headers=head).json()['data']['surveyPeriods']
    for i, p in enumerate(d): print(f"{i}. {p['name']}")
    sel = d[int(input("Pilih index period: "))]
    return sel['id'], sel['name']

def getRoles(pid, head, sess):
    try: return sess.get(f"{BASE_URL}/survey/api/v1/users/myinfo?surveyPeriodId={pid}", headers=head).json()['data']['surveyRole']['description']
    except: return "Admin"

def _get_lvl(lvl, pid, gid, head, sess):
    try: return sess.get(f"{BASE_URL}/region/api/v1/region/level{lvl}?groupId={gid}&level{lvl-1}Id={pid}", headers=head).json().get('data', [])
    except: return []

def ambil_semua_sls_parallel(kid, lvls, gid, head, jar, r1, r2):
    hierarki = " => ".join([name.get('name', '') for name in lvls])
    print(f"\n🚀 [Parallel] Mengambil data wilayah hierarki ({hierarki}) untuk {r2['name']}...")

    if not isinstance(lvls, list) or len(lvls) < 3:
        if len(lvls) == 2: return pd.DataFrame([r2])
        if len(lvls) == 1: return pd.DataFrame([r1])
        return pd.DataFrame()
        
    r1_id = r1['id']
    r2_id = r2['id']

    kecs = _get_lvl(3, kid, gid, head, create_resilient_session(jar))
    if not kecs:
        print("❌ Tidak ada kecamatan ditemukan.")
        return pd.DataFrame()

    all_desa = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
        fs = {ex.submit(_get_lvl, 4, k['id'], gid, head, create_resilient_session(jar)): k for k in kecs}
        for f in tqdm(as_completed(fs), total=len(fs), desc="📂 Mengambil Desa per Kec", unit="kec"):
            for d in f.result(): 
                d['pkid'] = fs[f]['id']; d['pkn'] = fs[f]['name']; all_desa.append(d)

    if len(lvls) == 3: # Hanya sampai Kecamatan
        return pd.DataFrame([{
            'region1Id': r1_id, 'region2Id': r2_id, 'region3Id': k['id'],
            f'{lvls[2]["name"]}': k['name'], 'smallcode': k['fullCode']
        } for k in kecs])

    all_sls = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
        fs = {ex.submit(_get_lvl, 5, d['id'], gid, head, create_resilient_session(jar)): d for d in all_desa}
        for f in tqdm(as_completed(fs), total=len(fs), desc="🏠 Mengambil SLS per Desa", unit="desa"):
            for s in f.result(): 
                s.update({'pdid': fs[f]['id'], 'pdn': fs[f]['name'], 'pkid': fs[f]['pkid'], 'pkn': fs[f]['pkn']})
                all_sls.append(s)

    if len(lvls) == 4: # Hanya sampai Desa
        return pd.DataFrame([{
            'region1Id': r1_id, 'region2Id': r2_id, 'region3Id': d['pkid'],
            'region4Id': d['id'], f'{lvls[2]["name"]}': d['pkn'],
            f'{lvls[3]["name"]}': d['name'], 'smallcode': d['fullCode']
        } for d in all_desa])

    if len(lvls) >= 6: # Sub-SLS (Level 6)
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
            fs = {ex.submit(_get_lvl, 6, s['id'], gid, head, create_resilient_session(jar)): s for s in all_sls}
            for f in tqdm(as_completed(fs), total=len(fs), desc="📍 Mengambil Sub-SLS", unit="sls"):
                for b in f.result(): 
                    results.append({
                        'region1Id': r1_id, 'region2Id': r2_id, 'region3Id': fs[f]['pkid'],
                        'region4Id': fs[f]['pdid'], 'region5Id': fs[f]['id'], 'region6Id': b['id'],
                        f'{lvls[2]["name"]}': fs[f]['pkn'],
                        f'{lvls[3]["name"]}': fs[f]['pdn'],
                        f'{lvls[4]["name"]}': fs[f]['name'],
                        f'{lvls[5]["name"]}': b['name'],
                        'smallcode': b['fullCode']
                    })
        return pd.DataFrame(results)

    # Fallback to SLS (Level 5)
    return pd.DataFrame([{
        'region1Id': r1_id, 'region2Id': r2_id, 'region3Id': s['pkid'],
        'region4Id': s['pdid'], 'region5Id': s['id'], 'region6Id': None,
        f'{lvls[2]["name"]}': s['pkn'], f'{lvls[3]["name"]}': s['pdn'],
        f'{lvls[4]["name"]}': s['name'], 'smallcode': s['fullCode']
    } for s in all_sls])

def flatten_val(val):
    if isinstance(val, list):
        return ', '.join([str(v.get('label', v)) if isinstance(v, dict) else str(v) for v in val])
    if isinstance(val, dict):
        return str(val.get('label', val.get('value', str(val))))
    return str(val) if val is not None else ""

def safe_extract_data(items):
    result = {}
    if not items: return result
    for item in items:
        key = item.get('dataKey')
        if key: result[key] = flatten_val(item.get('answer'))
    return result

def fetch_detail_task(sess, head, d, tid, pid):
    aid = d.get('id') or d.get('assignmentId')
    url = f"{BASE_URL}/assignment-general/api/assignment/get-by-id-with-data-for-scm?id={aid}"
    try:
        res = sess.get(url, headers=head, timeout=REQUEST_TIMEOUT).json().get('data', {})
        if not res: return None
        
        # Metadata dasar
        meta = {
            'assignment_id': aid,
            'current_user': res.get('current_user_username', ''),
            'status': res.get('assignment_status_alias', ''),
            'identity': res.get('code_identity', ''),
            'data1': res.get('data1', ''),
            'data2': res.get('data2', ''),
            'data3': res.get('data3', ''),
            'data4': res.get('data4', ''),
            'data5': res.get('data5', ''),
            'data6': res.get('data6', ''),
            'data7': res.get('data7', ''),
            'data8': res.get('data8', ''),
            'data9': res.get('data9', ''),
            'data10': res.get('data10', ''),
        }
        
        # Predefined Data
        pre_raw = json.loads(res.get('pre_defined_data', '{"predata":[]}'))['predata']
        pref = safe_extract_data(pre_raw)
        pref['assignment_id'] = aid
        
        # Answers
        ans_raw = json.loads(res.get('data', '{"answers":[]}'))['answers']
        ans = safe_extract_data(ans_raw)
        ans['assignment_id'] = aid
        
        return {'meta': meta, 'pref': pref, 'ans': ans}
    except Exception as e:
        return None

def fetch_assignments_dynamic(sess, head, pid, gid, filt, current_level=2, max_level=6, role="Admin", id_survey=None, user_ids=None, status_filter=None):
    url = f"{BASE_URL}/analytic/api/v2/assignment/datatable-all-user-survey-periode"
    extra_param = {**filt, "surveyPeriodId": pid, "currentUserId": None}
    if status_filter:
        extra_param["assignmentStatusAlias"] = status_filter
    payload = {"draw": 1, "start": 0, "length": 1, "assignmentExtraParam": extra_param}
    try:
        r = sess.post(url, headers=head, json=payload).json()
        hit = r.get('totalHit', 0)
        
        if current_level >= max_level or ("admin" in role.lower() and hit <= 1000):
            if hit == 0: return []
            
            # Strategi By User untuk hit > 1000 (batas return API)
            if current_level >= max_level and hit > 1000 and user_ids:
                all_collected = []
                collected_ids = set()
                
                def _fetch_user_task(uid):
                    p = payload.copy()
                    p['assignmentExtraParam'] = p['assignmentExtraParam'].copy()
                    p['assignmentExtraParam']['currentUserId'] = uid
                    p['start'] = 0
                    p['length'] = 1000
                    user_results = []
                    while True:
                        try:
                            resp = sess.post(url, headers=head, json=p, timeout=20).json()
                            data = resp.get('searchData', [])
                            user_results.extend(data)
                            if len(data) == 1000:
                                p['start'] += 1000
                            else:
                                break
                        except: break
                    return user_results
                
                with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
                    fs = {ex.submit(_fetch_user_task, uid): uid for uid in user_ids}
                    for f in as_completed(fs):
                        for item in f.result():
                            if item['id'] not in collected_ids:
                                all_collected.append(item)
                                collected_ids.add(item['id'])
                return all_collected

            # Jika tidak lebih dari 1000 atau user_ids kosong, tarik langsung
            res = []
            for s in range(0, hit, 1000):
                payload.update({"start": s, "length": 1000})
                res.extend(sess.post(url, headers=head, json=payload).json().get('searchData', []))
            return res
            
        sub = _get_lvl(current_level + 1, filt.get(f'region{current_level}Id'), gid, head, sess)
        all_r = []
        for c in sub:
            nf = filt.copy(); nf[f'region{current_level+1}Id'] = c['id']; nf['smallcode'] = c.get('fullCode')
            if current_level <= 3: print(f"   📂 Mencari di: {c['name']}...")
            all_r.extend(fetch_assignments_dynamic(sess, head, pid, gid, nf, current_level + 1, max_level, role, id_survey, user_ids, status_filter))
        return all_r
    except Exception as e:
        print(f"⚠️ Error fetch assignments: {e}")
        return []

def approve_condition(r, s, e):
    r_lower, s_lower = str(r).lower(), str(s).lower()
    
    admin_kab_allowed = ['approved by pengawas', 'approved by pml', 'edited by admin kabupaten']
    if globals().get('ADMIN_BYPASS_PML', False):
        admin_kab_allowed.extend(['submitted by pencacah', 'submitted by ppl'])
        
    return (
        (r_lower == 'pengawas' and s_lower == 'submitted by pencacah') or
        (r_lower == 'pml' and s_lower == 'submitted by ppl') or
        (r_lower == 'admin kabupaten' and s_lower in admin_kab_allowed) or
        (r_lower == 'admin provinsi' and s_lower == 'completed by admin kabupaten')
    )

def revoke_condition(r, s, e):
    return str(r).lower() == 'pengawas' and str(s).lower() == 'completed by pengawas' and str(e.get('status_keberadaan', '')).lower() == '3. tidak ditemukan'

def reject_condition(r, s, e):
    return str(r).lower() == 'pengawas' and str(s).lower() == 'submitted by pencacah' and str(e.get('status_keberadaan', '')).lower() == '3. tidak ditemukan'

def process_assignments_generic(sid, tid, pid, kn, sn, alist, head, jar, sess, drv, type, cond):
    role = getRoles(pid, head, sess)
    log = []
    failed = []
    
    # Sanitasi nama agar aman untuk path
    safe_sn = "".join([c for c in sn if c.isalnum() or c in (' ', '_', '-')]).strip()
    chk_dir = os.path.join(os.getcwd(), "output_scraper", "checkpoints", f"process_{type}_{safe_sn}_{pid}")
    os.makedirs(chk_dir, exist_ok=True)
    
    # Cari checkpoint yang sudah ada
    existing_files = [f for f in os.listdir(chk_dir) if f.endswith('.json')]
    processed_ids = set()
    
    if existing_files:
        pilihan_chk = input(f"\n🔄 Terdeteksi {len(existing_files)} data sudah diproses di checkpoint. Lanjutkan? (Y/N, default Y): ").strip().upper()
        if pilihan_chk != 'N':
            print("⏳ Memuat progress sebelumnya dari checkpoint...")
            for f_name in existing_files:
                try:
                    with open(os.path.join(chk_dir, f_name), 'r') as f_in:
                        item = json.load(f_in)
                        log.append(item)
                        processed_ids.add(f_name.replace('.json', ''))
                except: pass
        else:
            # Hapus file checkpoint lama
            for f in existing_files:
                try: os.remove(os.path.join(chk_dir, f))
                except: pass
                
    to_process = [d for d in alist if (d.get('id') or d.get('assignmentId')) not in processed_ids]
    
    print(f"🚀 Memproses {len(alist)} data {type} ({len(log)} sudah diproses, {len(to_process)} sisa)..."); start = time.time()
    
    for d in tqdm(to_process):
        aid = d.get('id') or d.get('assignmentId'); st = d.get('assignmentStatusAlias', 'N/A')
        
        # Ambil detail untuk cek status_keberadaan (seperti v8/v9)
        extra = {}
        try:
            d_url = f"{BASE_URL}/assignment-general/api/assignment/get-by-id-with-data-for-scm?id={aid}"
            res_d = sess.get(d_url, headers=head, timeout=10).json().get('data', {})
            extra['status_keberadaan'] = res_d.get('data6') # data6 biasanya status keberadaan
            if st == 'N/A' or not st:
                st = res_d.get('assignment_status_alias', 'N/A')
        except: pass

        if not cond(role, st, extra):
            log_item = {'id': aid, 'status': st, 'ok': False, 'msg': 'Skip: Kriteria status'}
            log.append(log_item)
            try:
                with open(os.path.join(chk_dir, f"{aid}.json"), 'w') as f_out:
                    json.dump(log_item, f_out)
            except: pass
            continue
            
        try:
            drv.get(f"{BASE_URL}/survey-collection/survey-review/{aid}/{tid}/{pid}/a/1")
            wait = WebDriverWait(drv, 30)
            btn_id = f"button{type.capitalize()}"
            confirm_xpath = '//*[@id="fasih"]/div/div/div[6]/button[1]'
            
            # Tunggu tombol utama muncul
            btn = wait.until(EC.element_to_be_clickable((By.ID, btn_id)))
            
            # Retry klik sampai dialog konfirmasi muncul
            clicked = False
            for _ in range(5):
                try:
                    # Coba klik Selenium biasa, jika gagal/terhalang, pakai JS
                    try: btn.click()
                    except: drv.execute_script("arguments[0].click();", btn)
                    
                    # Tunggu dialog konfirmasi muncul (maksimal 3 detik)
                    WebDriverWait(drv, 3).until(EC.presence_of_element_located((By.XPATH, confirm_xpath)))
                    clicked = True
                    break
                except:
                    time.sleep(1) # Tunggu sebentar sebelum klik ulang
            
            if not clicked:
                raise Exception("Gagal memunculkan konfirmasi setelah beberapa kali klik")
                
            # Klik tombol konfirmasi (Yes / OK)
            confirm_btn = wait.until(EC.element_to_be_clickable((By.XPATH, confirm_xpath)))
            try: confirm_btn.click()
            except: drv.execute_script("arguments[0].click();", confirm_btn)
            
            # Double check kalau dialog masih ada
            try: 
                time.sleep(0.5)
                WebDriverWait(drv, 2).until(EC.element_to_be_clickable((By.XPATH, confirm_xpath))).click()
            except: pass
            
            log_item = {'id': aid, 'status': st, 'ok': True, 'msg': 'Success'}
            log.append(log_item)
            try:
                with open(os.path.join(chk_dir, f"{aid}.json"), 'w') as f_out:
                    json.dump(log_item, f_out)
            except: pass
        except Exception as e:
            log_item = {'id': aid, 'status': st, 'ok': False, 'msg': str(e)}
            log.append(log_item)
            try:
                with open(os.path.join(chk_dir, f"{aid}.json"), 'w') as f_out:
                    json.dump(log_item, f_out)
            except: pass
            failed.append(d)
    
    pd.DataFrame(log).to_excel(os.path.join(pilih_folder_simpan("Log"), f"Log_{type}_{timestamp()}.xlsx"), index=False)
    
    # Hapus file checkpoint jika semua data berhasil diproses (tidak ada yang gagal)
    if not failed:
        for f in os.listdir(chk_dir):
            try: os.remove(os.path.join(chk_dir, f))
            except: pass
        try: os.rmdir(chk_dir)
        except: pass
        
    print(f"⏱️ Selesai dalam {int(time.time()-start)}s"); return failed

def timestamp(): return datetime.now().strftime("%Y%m%d_%H%M%S")

def main1(user, pwd, head, jar, sess, drv):
    global MAX_WORKERS_WILAYAH
    clear_screen()
    
    print("\n⚠️ Silakan periksa terlebih dahulu status login pada browser (driver).")
    print("   Pastikan Anda sudah berada di Dashboard FASIH sebelum melanjutkan.")
    
    auto_tried = False
    while True:
        # Cek apakah session saat ini valid
        test_ok = False
        if sess:
            try:
                # Gunakan POST sesuai permintaan user
                r = sess.post(f"{BASE_URL}/survey/api/v1/surveys/datatable?surveyType=Pencacahan", json={"pageNumber":0,"pageSize":100,"sortBy":"CREATED_AT","sortDirection":"DESC"}, timeout=10)
                if r.status_code == 200 and 'data' in r.json(): test_ok = True
            except: pass

        if test_ok:
            print("✅ Session API saat ini masih valid.")
            break

        current_url = ""
        try: current_url = drv.current_url
        except: pass

        if "survey-collection/survey" in current_url and not auto_tried:
            print("\n🔄 Browser terdeteksi sudah di Dashboard. Mensinkronkan otomatis...")
            auto_tried = True
        else:
            print("\n🔄 Session belum terdeteksi atau tidak valid.")
            input("👉 Tekan ENTER jika Anda sudah berhasil login dan melihat Dashboard FASIH di browser...")
            print("🔄 Membaca ulang session dari browser...")
            
        try:
            # Paksa pindah ke domain utama jika belum
            if "fasih-sm.bps.go.id" not in drv.current_url:
                drv.get(BASE_URL + "/")
                time.sleep(3) # Tunggu agar JS sempat memuat token
                
            new_head, new_jar, new_sess, _, new_ls, new_ss = ambil_cookies_dan_buat_session(drv, pwd)
            
            # Verifikasi apakah session dari browser ini benar-benar bisa menembus backend
            try:
                test_r = new_sess.post(f"{BASE_URL}/survey/api/v1/surveys/datatable?surveyType=Pencacahan", json={"pageNumber":0,"pageSize":100,"sortBy":"CREATED_AT","sortDirection":"DESC"}, timeout=10)
                if test_r.status_code != 200:
                    raise Exception(f"HTTP Status {test_r.status_code} (Harusnya 200 OK)")
                if 'data' not in test_r.json():
                    raise Exception("Response JSON tidak memuat 'data'.")
            except Exception as e_api:
                raise Exception(f"Session belum menembus API. Detail: {e_api}")
                
            simpan_session(user, new_head, new_jar, new_sess, pwd, new_ls, new_ss)
            head, jar, sess = new_head, new_jar, new_sess
            print("✅ Session berhasil diperbarui dari browser.")
            break
        except Exception as e:
            print(f"❌ Error: {e}")
            print("   Silakan periksa kembali browser Anda.")

    t = input(f"\nThread (default {MAX_WORKERS_WILAYAH}): ").strip()
    if t: MAX_WORKERS_WILAYAH = int(t)
    
    print("\n=== Pilih Tipe Survey ===")
    print("1. Pencacahan")
    print("2. Pelatihan")
    print("3. Uji Coba")
    st_choice = input("Pilihan (1-3, default 1): ").strip()
    stype = "Pencacahan"
    if st_choice == "2": stype = "Pelatihan"
    elif st_choice == "3": stype = "Ujicoba"

    surveys = sess.post(f"{BASE_URL}/survey/api/v1/surveys/datatable?surveyType={stype}", json={"pageNumber":0,"pageSize":50,"sortBy":"CREATED_AT","sortDirection":"DESC"}).json()['data']['content']
    for i, s in enumerate(surveys): print(f"{i+1}. {s['name']}")
    sel_s = surveys[int(input("Pilih survei: "))-1]
    sid, sn = sel_s['id'], sel_s['name']
    
    meta = sess.get(f"{BASE_URL}/survey/api/v1/surveys/{sid}", headers=head).json()['data']
    gid, tid = meta['regionGroupId'], meta['surveyTemplates'][-1]['templateId']
    lvls = sess.get(f"{BASE_URL}/region/api/v1/region-metadata?id={gid}", headers=head).json()['data']['level']
    
    provs = sess.get(f"{BASE_URL}/region/api/v1/region/level1?groupId={gid}", headers=head).json()['data']
    for i, p in enumerate(provs): print(f"{i+1}. {p['name']}")
    sel_p = provs[int(input("Pilih prov: "))-1]
    
    kabs = sess.get(f"{BASE_URL}/region/api/v1/region/level2?groupId={gid}&level1FullCode={sel_p['fullCode']}", headers=head).json()['data']
    for i, k in enumerate(kabs): print(f"{i+1}. {k['name']}")
    sel_k = kabs[int(input("Pilih kab: "))-1]
    kn = sel_k['name']

    pid, pn = get_survey_period(sid, sess, head)
    
    # Setup folder data_wilayah
    w_dir = os.path.join(os.getcwd(), "data_wilayah")
    os.makedirs(w_dir, exist_ok=True)
    
    # Sanitasi nama file
    safe_sn = "".join([c for c in sn if c.isalnum() or c in (' ', '_', '-')]).strip()
    safe_pn = "".join([c for c in pn if c.isalnum() or c in (' ', '_', '-')]).strip()
    safe_kn = "".join([c for c in kn if c.isalnum() or c in (' ', '_', '-')]).strip()
    
    import glob
    pattern = os.path.join(w_dir, f"daftarwilayah_{safe_kn}_{safe_sn}_{safe_pn}.xlsx")
    files = glob.glob(pattern)
    
    if files:
        print(f"📦 Menggunakan cache wilayah: {os.path.basename(files[0])}")
        df_w = pd.read_excel(files[0])
    else:
        df_w = ambil_semua_sls_parallel(sel_k['id'], lvls, gid, head, jar, sel_p, sel_k)
        if not df_w.empty:
            df_w.to_excel(pattern, index=False)
            print(f"💾 Daftar wilayah disimpan ke: {os.path.relpath(pattern)}")

    clear_screen()
    print(f"✅ {len(df_w)} unit wilayah teridentifikasi.")
    
    # --- FITUR FILTER WILAYAH (Logika v9) ---
    pilih_filter = input("\nApakah Anda ingin memfilter wilayah tertentu? (Y/N): ").strip().upper()
    df_f = df_w.copy()
    if pilih_filter == 'Y':
        cols = [c for c in df_w.columns if not c.endswith('Id') and c != 'smallcode']
        for i, row in df_w.iterrows():
            nama_wilayah = " - ".join([str(row[c]) for c in cols if pd.notna(row[c])]) # Handle NaN
            print(f"[{i}] {row['smallcode']} | {nama_wilayah}")
        
        input_pilih = input("\nMasukkan index (contoh: '1,3,5' atau '0-10' atau 'all'): ").strip().lower()
        if input_pilih != 'all' and input_pilih != '':
            try:
                indices = []
                for part in input_pilih.split(','):
                    if '-' in part:
                        start, end = map(int, part.split('-'))
                        indices.extend(range(start, end + 1))
                    else: indices.append(int(part))
                indices = [idx for idx in indices if 0 <= idx < len(df_w)]
                if indices: df_f = df_w.iloc[indices].reset_index(drop=True)
            except: print("⚠️ Input tidak valid, menggunakan semua wilayah.")

    while True:
        clear_screen()
        print(f"📊 Survei: {sn}\n📍 Wilayah: {kn} ({len(df_f)} unit)\n👤 Role: {getRoles(pid, head, sess)}")
        print("\n=== Menu ===\n1. Scrape\n2. Approve\n3. Revoke\n4. Reject\n5. History Email Broadcast\n6. Ganti Survey\n7. Simpan Prelist (Cepat)")
        aksi = input("Pilihan: ").strip()
        if aksi == "6": break
        if aksi not in ["1", "2", "3", "4", "5", "7"]: continue
        
        # Filter status assignment untuk Scrape
        status_filter = None
        status_label = ""
        if aksi == "1":
            print("\n=== Filter Status Assignment ===")
            print("0. SEMUA")
            print("1. OPEN")
            print("2. DRAFT")
            print("3. SUBMITTED BY Pencacah")
            print("4. SUBMITTED RESPONDEN")
            status_choice = input("Pilihan (0-4, default 0): ").strip()
            status_map = {
                "1": "Open",
                "2": "Draft",
                "3": "Submitted by Pencacah",
                "4": "Submitted Responden",
            }
            if status_choice in status_map:
                status_filter = status_map[status_choice]
                status_label = f"_{status_filter.replace(' ', '_')}"
                print(f"   ✅ Filter status: {status_filter}")
        
        pilihan_mode = None
        excel_assignment_ids = []
        if aksi == "5":
            print("\n=== Mode History Email Broadcast ===")
            print("1. Berdasarkan Wilayah Terpilih (Satu/beberapa request per wilayah, sangat cepat)")
            print("2. Berdasarkan ID Tugas Terpilih (Parallel query per assignment dari hasil scan wilayah)")
            print("3. Berdasarkan ID Tugas dari File Excel (Parallel query per assignment dari file Excel)")
            pilihan_mode = input("Pilih mode (1-3, default 1): ").strip()
            if pilihan_mode not in ["1", "2", "3"]:
                pilihan_mode = "1"
            
            if pilihan_mode == "3":
                print("Pilih file Excel yang berisi kolom assignment_id (atau memuat kata 'id')...")
                root = tk.Tk(); root.withdraw()
                filter_file = filedialog.askopenfilename(title="Pilih File Excel Assignment ID", filetypes=[("Excel files", "*.xlsx *.xls")])
                if filter_file:
                    try:
                        df_filter = pd.read_excel(filter_file)
                        col_id = next((c for c in df_filter.columns if 'id' in str(c).lower() and 'assignment' in str(c).lower()), None)
                        if not col_id:
                            col_id = next((c for c in df_filter.columns if 'id' in str(c).lower()), df_filter.columns[0])
                        
                        excel_assignment_ids = list(df_filter[col_id].dropna().astype(str).unique())
                        print(f"✅ Berhasil memuat {len(excel_assignment_ids)} ID Tugas dari Excel.")
                    except Exception as e:
                        print(f"❌ Gagal membaca file Excel: {e}")
                        pilihan_mode = None
                else:
                    print("⚠️ Tidak ada file dipilih.")
                    pilihan_mode = None
        
        need_assignments = (aksi in ["1", "2", "3", "4", "7"]) or (aksi == "5" and pilihan_mode == "2")
        
        unique = []
        if need_assignments:
            print("\n=== Pilih Metode Pengambilan Assignment ID ===")
            print("1. Datatable/Analytic (Standar - lambat/drill-down)")
            print("2. Smallest Code API (Cepat)")
            opt_method = input("Pilihan (1-2, default 2): ").strip()
            if opt_method not in ["1", "2"]:
                opt_method = "2"
            
            seen = set()
            if opt_method == "2":
                print(f"⏳ Mengambil data via Smallest Code API...")
                smallcodes = list(df_f['smallcode'].dropna().astype(str).unique())
                
                def _fetch_smallest_code(sc):
                    url = f"{BASE_URL}/assignment-general/api/assignments/get-principal-values-by-smallest-code/{pid}/{sc}"
                    try:
                        r = sess.get(url, headers=head, timeout=REQUEST_TIMEOUT)
                        if r.status_code == 200:
                            res_json = r.json()
                            if res_json.get('success'):
                                return res_json.get('data', [])
                    except Exception:
                        pass
                    return []

                with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
                    fs = {ex.submit(_fetch_smallest_code, sc): sc for sc in smallcodes}
                    for f in tqdm(as_completed(fs), total=len(fs), desc="📥 Fetching Smallest Code"):
                        for item in f.result():
                            aid = item.get('assignmentId')
                            if aid and aid not in seen:
                                x = {
                                    'id': aid,
                                    'assignmentId': aid,
                                    'assignmentStatusAlias': 'N/A',
                                    **item
                                }
                                unique.append(x)
                                seen.add(aid)
            else:
                rid = sess.get(f"{BASE_URL}/survey/api/v1/survey-roles?surveyId={sid}").json()['data'][-1]['id']
                uids = [u['userId'] for u in sess.get(f"{BASE_URL}/survey/api/v1/survey-period-role-users/region?surveyPeriodId={pid}&surveyRoleId={rid}&regionCode={sel_k['fullCode']}").json()['data']] + [None]
                
                # Sinkronisasi browser
                print(f"🌐 Sinkronisasi browser...")
                # drv.get(f"https://fasih-sm.bps.go.id/survey-collection/collect/{sid}")
                
                # Pengambilan data (Drill-down vs Per Wilayah)
                print(f"🔍 Mencari data penugasan...")
                
                if len(df_f) == len(df_w): # Ambil semua (Drill-down)
                    ids = fetch_assignments_dynamic(sess, head, pid, gid, {"region1Id": sel_p['id'], "region2Id": sel_k['id']}, max_level=6, role=getRoles(pid, head, sess), id_survey=sid, user_ids=uids, status_filter=status_filter)
                    for x in ids:
                        if x['id'] not in seen and x['assignmentStatusAlias'] != 'Open': unique.append(x); seen.add(x['id'])
                else: # Per Wilayah yang difilter
                    max_lvl = len(lvls)
                    avail = [int(c.replace('region','').replace('Id','')) for c in df_f.columns if c.startswith('region') and c.endswith('Id') and not df_f[c].isnull().all()]
                    curr_lvl = max(avail) if avail else max_lvl
                    
                    def _fetch_row(row):
                        f = {f"region{i}Id": row.get(f'region{i}Id') for i in range(1, 11)}
                        f['smallcode'] = row.get('smallcode')
                        return fetch_assignments_dynamic(sess, head, pid, gid, f, current_level=curr_lvl, max_level=max_lvl, role=getRoles(pid, head, sess), id_survey=sid, user_ids=uids, status_filter=status_filter)

                    with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
                        fs = {ex.submit(_fetch_row, row): i for i, row in df_f.iterrows()}
                        for f in tqdm(as_completed(fs), total=len(fs), desc="📥 Fetching Data Wilayah"):
                            for x in f.result():
                                if x['id'] not in seen and x['assignmentStatusAlias'] != 'Open': unique.append(x); seen.add(x['id'])
            
            # SIMPAN LANGSUNG SEBAGAI PRELIST
            if unique:
                out_dir = os.path.join(os.getcwd(), "output_scraper")
                os.makedirs(out_dir, exist_ok=True)
                prelist_file = os.path.join(out_dir, f"Prelist_{safe_sn}{status_label}_{timestamp()}.xlsx")
                print(f"\n💾 Menyimpan prelist otomatis ({len(unique)} baris) ke: {os.path.relpath(prelist_file)} ...")
                df_settings = pd.DataFrame([
                    {"Setting": "id_survey", "Value": sid},
                    {"Setting": "survey_name", "Value": sn},
                    {"Setting": "surveyPeriodId", "Value": pid},
                    {"Setting": "survey_period_name", "Value": pn},
                    {"Setting": "kabupaten_name", "Value": kn},
                    {"Setting": "role", "Value": getRoles(pid, head, sess)},
                    {"Setting": "scraped_at", "Value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                ])
                try:
                    with pd.ExcelWriter(prelist_file) as writer:
                        pd.DataFrame(unique).to_excel(writer, sheet_name='Prelist', index=False)
                        df_settings.to_excel(writer, sheet_name='Settings', index=False)
                    print("✅ Prelist otomatis berhasil disimpan.")
                except Exception as e_save:
                    print(f"⚠️ Gagal menyimpan prelist otomatis: {e_save}")
        
        if aksi == "1":
            out_dir = os.path.join(os.getcwd(), "output_scraper")
            os.makedirs(out_dir, exist_ok=True)
            out_file = os.path.join(out_dir, f"Scrape_{safe_sn}{status_label}_{timestamp()}.xlsx")
            
            chk_dir = os.path.join(os.getcwd(), "output_scraper", "checkpoints", f"scrape_{safe_sn}_{pid}")
            os.makedirs(chk_dir, exist_ok=True)
            
            # Cari checkpoint yang sudah ada
            existing_files = [f for f in os.listdir(chk_dir) if f.endswith('.json')]
            use_checkpoint = False
            if existing_files:
                pilihan_chk = input(f"\n🔄 Terdeteksi {len(existing_files)} data di checkpoint. Lanjutkan? (Y/N, default Y): ").strip().upper()
                if pilihan_chk != 'N':
                    use_checkpoint = True
                else:
                    # Hapus file checkpoint lama
                    for f in existing_files:
                        try: os.remove(os.path.join(chk_dir, f))
                        except: pass
            
            all_meta, all_pref, all_ans = [], [], []
            scraped_ids = set()
            
            if use_checkpoint:
                print("⏳ Memuat data dari checkpoint...")
                for f_name in tqdm(existing_files, desc="📂 Loading Checkpoints"):
                    try:
                        with open(os.path.join(chk_dir, f_name), 'r') as f_in:
                            res = json.load(f_in)
                            if res:
                                all_meta.append(res['meta'])
                                all_pref.append(res['pref'])
                                all_ans.append(res['ans'])
                                scraped_ids.add(f_name.replace('.json', ''))
                    except: pass
            
            # Saring unique agar hanya memproses yang belum di-scrape
            to_scrape = [d for d in unique if str(d.get('id') or d.get('assignmentId')) not in scraped_ids]
            
            print(f"✅ {len(unique)} data ditemukan ({len(all_meta)} sudah ada di checkpoint, {len(to_scrape)} akan di-scrape). Memulai download detail...")
            
            def fetch_and_save_checkpoint(sess, head, d, tid, pid, chk_dir):
                res = fetch_detail_task(sess, head, d, tid, pid)
                if res:
                    aid = d.get('id') or d.get('assignmentId')
                    try:
                        with open(os.path.join(chk_dir, f"{aid}.json"), 'w') as f_out:
                            json.dump(res, f_out)
                    except: pass
                return res

            if to_scrape:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS_DETAIL) as ex:
                    fs = {ex.submit(fetch_and_save_checkpoint, sess, head, d, tid, pid, chk_dir): d for d in to_scrape}
                    for f in tqdm(as_completed(fs), total=len(fs), desc="📥 Scraping Detail", unit="data"):
                        res = f.result()
                        if res:
                            all_meta.append(res['meta'])
                            all_pref.append(res['pref'])
                            all_ans.append(res['ans'])
            
            # Hapus checkpoint jika berhasil mengunduh semua data
            if len(all_meta) >= len(unique) and len(unique) > 0:
                for f in os.listdir(chk_dir):
                    try: os.remove(os.path.join(chk_dir, f))
                    except: pass
                try: os.rmdir(chk_dir)
                except: pass
            
            print(f"💾 Menyusun file Excel...")
            total_data = len(all_meta)
            chunk_size = 50000
            ts = timestamp()
            
            if total_data == 0:
                print("⚠️ Tidak ada data untuk disimpan.")
            else:
                df_settings = pd.DataFrame([
                    {"Setting": "id_survey", "Value": sid},
                    {"Setting": "survey_name", "Value": sn},
                    {"Setting": "surveyPeriodId", "Value": pid},
                    {"Setting": "survey_period_name", "Value": pn},
                    {"Setting": "kabupaten_name", "Value": kn},
                    {"Setting": "role", "Value": getRoles(pid, head, sess)},
                    {"Setting": "scraped_at", "Value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                ])
                for i in range(0, total_data, chunk_size):
                    part_num = (i // chunk_size) + 1
                    part_suffix = f"_Part{part_num}" if total_data > chunk_size else ""
                    chunk_file = os.path.join(out_dir, f"Scrape_{safe_sn}{status_label}_{ts}{part_suffix}.xlsx")
                    
                    with pd.ExcelWriter(chunk_file) as writer:
                        pd.DataFrame(all_meta[i:i+chunk_size]).to_excel(writer, sheet_name='Daftar_Tugas', index=False)
                        if all_pref: pd.DataFrame(all_pref[i:i+chunk_size]).to_excel(writer, sheet_name='Pre-defined', index=False)
                        if all_ans: pd.DataFrame(all_ans[i:i+chunk_size]).to_excel(writer, sheet_name='Answers', index=False)
                        df_settings.to_excel(writer, sheet_name='Settings', index=False)
                    
                    if part_suffix:
                        print(f"✅ File {part_suffix.replace('_', '')} disimpan: {os.path.relpath(chunk_file)}")
                    else:
                        print(f"✅ Selesai! Data disimpan di: {os.path.relpath(chunk_file)}")
        elif aksi == "5":
            out_dir = os.path.join(os.getcwd(), "output_scraper")
            os.makedirs(out_dir, exist_ok=True)
            
            all_emails = []
            req_headers = head.copy()
            
            if pilihan_mode == "1":
                # Cari kolom wilayah yang tidak null
                region_cols = sorted([c for c in df_f.columns if c.startswith('region') and c.endswith('Id')], key=lambda x: int(x.replace('region','').replace('Id','')))
                df_regions = df_f[region_cols].drop_duplicates().dropna(how='all')
                
                print(f"⏳ Mengambil history email broadcast untuk {len(df_regions)} kelompok wilayah...")
                
                def fetch_emails_for_region_params(r_row):
                    param = {f"region{i}Id": "" for i in range(1, 11)}
                    for col in region_cols:
                        val = r_row[col]
                        if pd.notna(val):
                            param[col] = str(val)
                    param["surveyPeriodId"] = pid
                    param["assignmentId"] = ""
                    
                    url = f"{BASE_URL}/email/api/v1/email-schedule/datatable"
                    payload = {
                        "draw": 1,
                        "columns": [
                            {"data": "email", "name": "", "searchable": True, "orderable": True, "search": {"value": "", "regex": False}}
                        ],
                        "order": [{"column": 0, "dir": "asc"}],
                        "start": 0,
                        "length": 1000,
                        "search": {"value": "", "regex": False},
                        "emailScheduleParam": param
                    }
                    
                    emails = []
                    start_idx = 0
                    while True:
                        payload["start"] = start_idx
                        try:
                            r = sess.post(url, headers=req_headers, json=payload, timeout=30)
                            if r.status_code == 200:
                                res_json = r.json()
                                data = res_json.get('data', [])
                                if not data:
                                    break
                                emails.extend(data)
                                if len(data) < payload["length"]:
                                    break
                                start_idx += len(data)
                            else:
                                break
                        except Exception:
                            break
                    return emails

                with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
                    fs = {ex.submit(fetch_emails_for_region_params, row): i for i, row in df_regions.iterrows()}
                    for f in tqdm(as_completed(fs), total=len(fs), desc="📥 Fetching Email Broadcast", unit="wilayah"):
                        all_emails.extend(f.result())
            
            else:
                target_ids = []
                if pilihan_mode == "2":
                    target_ids = [d.get('id') or d.get('assignmentId') for d in unique if d.get('id') or d.get('assignmentId')]
                elif pilihan_mode == "3":
                    target_ids = excel_assignment_ids
                
                if not target_ids:
                    print("⚠️ Tidak ada ID Tugas yang akan diproses.")
                else:
                    print(f"⏳ Mengambil history email broadcast untuk {len(target_ids)} tugas secara paralel...")
                    
                    def fetch_emails_for_assignment_parallel(aid):
                        url = f"{BASE_URL}/email/api/v1/email-schedule/datatable"
                        payload = {
                            "draw": 1,
                            "columns": [
                                {"data": "email", "name": "", "searchable": True, "orderable": True, "search": {"value": "", "regex": False}}
                            ],
                            "order": [{"column": 0, "dir": "asc"}],
                            "start": 0,
                            "length": 100,
                            "search": {"value": "", "regex": False},
                            "emailScheduleParam": {
                                "region1Id": "",
                                "region2Id": "",
                                "region3Id": "",
                                "region4Id": "",
                                "region5Id": "",
                                "region6Id": "",
                                "region7Id": "",
                                "region8Id": "",
                                "region9Id": "",
                                "region10Id": "",
                                "surveyPeriodId": pid,
                                "assignmentId": aid
                            }
                        }
                        try:
                            r = sess.post(url, headers=req_headers, json=payload, timeout=20)
                            if r.status_code == 200:
                                return r.json().get('data', [])
                        except Exception:
                            pass
                        return []

                    with ThreadPoolExecutor(max_workers=MAX_WORKERS_DETAIL) as ex:
                        fs = {ex.submit(fetch_emails_for_assignment_parallel, aid): aid for aid in target_ids}
                        for f in tqdm(as_completed(fs), total=len(fs), desc="📥 Fetching Detail Email", unit="data"):
                            all_emails.extend(f.result())

            if not all_emails:
                print("⚠️ Tidak ada data email broadcast ditemukan.")
            else:
                seen_ids = set()
                dedup_emails = []
                for email_item in all_emails:
                    if not isinstance(email_item, dict):
                        continue
                    item_id = email_item.get('id') or email_item.get('emailScheduleId')
                    if item_id:
                        if item_id not in seen_ids:
                            dedup_emails.append(email_item)
                            seen_ids.add(item_id)
                    else:
                        dedup_emails.append(email_item)
                
                df_email = pd.DataFrame(dedup_emails)
                out_file = os.path.join(out_dir, f"Email_Broadcast_History_{safe_sn}_{timestamp()}.xlsx")
                df_email.to_excel(out_file, index=False)
                print(f"✅ Selesai! Data ({len(dedup_emails)} baris) disimpan di: {os.path.relpath(out_file)}")
        elif aksi == "7":
            # Prelist sudah otomatis disimpan setelah didapatkan di atas
            pass
        else:
            tanya_filter = input("\n📝 Apakah Anda ingin memfilter eksekusi menggunakan file Excel? (Y/N, default N): ").strip().upper()
            if tanya_filter == 'Y':
                print("Pilih file Excel yang berisi kolom assignment_id (atau memuat kata 'id')...")
                root = tk.Tk(); root.withdraw()
                filter_file = filedialog.askopenfilename(title="Pilih File Filter", filetypes=[("Excel files", "*.xlsx *.xls")])
                if filter_file:
                    try:
                        df_filter = pd.read_excel(filter_file)
                        # Cari kolom yang mirip dengan assignment_id
                        col_id = next((c for c in df_filter.columns if 'id' in str(c).lower() and 'assignment' in str(c).lower()), None)
                        if not col_id:
                            col_id = next((c for c in df_filter.columns if 'id' in str(c).lower()), df_filter.columns[0])
                            
                        filter_ids = set(df_filter[col_id].dropna().astype(str))
                        sebelum = len(unique)
                        unique = [u for u in unique if str(u.get('id') or u.get('assignmentId')) in filter_ids]
                        print(f"✅ Berhasil memfilter! Dari {sebelum} data menjadi {len(unique)} data yang akan dieksekusi.")
                    except Exception as e:
                        print(f"⚠️ Gagal membaca file filter: {e}. Menggunakan semua data.")
                else:
                    print("⚠️ Tidak ada file dipilih. Menggunakan semua data.")
                    
            if aksi == "2":
                tanya_bypass = input("\n⚠️ Aktifkan kondisi khusus Admin Kabupaten (Bypass PML)? (Y/N, default N): ").strip().upper()
                global ADMIN_BYPASS_PML
                ADMIN_BYPASS_PML = True if tanya_bypass == 'Y' else False
                if ADMIN_BYPASS_PML: print("   ✅ Mode Bypass PML AKTIF. Akan meng-approve 'Submitted by Pencacah/PPL'.\n")
            
            m = {"2": ("approve", approve_condition), "3": ("revoke", revoke_condition), "4": ("reject", reject_condition)}
            type, cond = m[aksi]
            failed = process_assignments_generic(sid, tid, pid, kn, sn, unique, head, jar, sess, drv, type, cond)
            while failed:
                if input(f"⚠️ {len(failed)} gagal. Ulang? (y/n): ").lower() == 'y':
                    failed = process_assignments_generic(sid, tid, pid, kn, sn, failed, head, jar, sess, drv, type, cond)
                else: break
        input("\n✅ Selesai. ENTER...")

if __name__ == "__main__":
    drv = setup_driver(); clear_screen()
    user = input("Username SSO: ")
    h, c, s, p, ls, ss = muat_session(user)
    
    success = False
    if s:
        success = suntik_cookies_ke_driver(drv, c, ls, ss)
    
    if not success:
        print("🔄 Melakukan login ulang untuk mendapatkan session baru...")
        # print(f"Password= {p}")
        h, c, s, p, ls, ss = main_login(drv, user, pwd=p)
        simpan_session(user, h, c, s, p, ls, ss)

    while True:
        try:
            main1(user, p, h, c, s, drv)
            if input("Keluar script? (y/n): ").lower() == 'y': break
        except Exception as e: print(f"❌ Error: {e}"); time.sleep(5)
    print("👋 Closing...")
    try: drv.quit()
    except: pass

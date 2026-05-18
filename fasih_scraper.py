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
                        return None, None, None, None, None, None
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
    
    # Bersihkan sesi lama
    try:
        driver.get(BASE_URL + "/")
        time.sleep(2); driver.delete_all_cookies()
        driver.execute_script("window.localStorage.clear(); window.sessionStorage.clear(); if(window.indexedDB){indexedDB.databases().then(dbs=>dbs.forEach(db=>indexedDB.deleteDatabase(db.name)))}")
    except: pass

    # Arahkan langsung ke SSO BPS
    driver.get("https://sso.bps.go.id")
    
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

    # Tunggu sampai SSO berhasil dan bukan lagi di halaman sso
    print("⏳ Menunggu Anda menyelesaikan login...")
    WebDriverWait(driver, 60).until(lambda d: "sso.bps.go.id" not in d.current_url)

    # Autorisasi FASIH
    print("🔐 Mengotorisasi FASIH...")
    driver.get(f"{BASE_URL}/oauth2/authorization/ics")
    time.sleep(5)
    
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
    print(f"🚀 Memulai pengambilan data wilayah untuk {r2['name']}...")
    kecs = _get_lvl(3, kid, gid, head, create_resilient_session(jar))
    
    all_desa = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
        fs = {ex.submit(_get_lvl, 4, k['id'], gid, head, create_resilient_session(jar)): k for k in kecs}
        for f in tqdm(as_completed(fs), total=len(fs), desc="📂 Mengambil Desa per Kec", unit="kec"):
            for d in f.result(): 
                d['pkid'] = fs[f]['id']; d['pkn'] = fs[f]['name']; all_desa.append(d)
                
    all_sls = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
        fs = {ex.submit(_get_lvl, 5, d['id'], gid, head, create_resilient_session(jar)): d for d in all_desa}
        for f in tqdm(as_completed(fs), total=len(fs), desc="🏠 Mengambil SLS per Desa", unit="desa"):
            for s in f.result(): 
                s.update({'pdid': fs[f]['id'], 'pdn': fs[f]['name'], 'pkid': fs[f]['pkid'], 'pkn': fs[f]['pkn']}); all_sls.append(s)
                
    if len(lvls) >= 6:
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
            fs = {ex.submit(_get_lvl, 6, s['id'], gid, head, create_resilient_session(jar)): s for s in all_sls}
            for f in tqdm(as_completed(fs), total=len(fs), desc="📍 Mengambil Sub-SLS", unit="sls"):
                for b in f.result(): 
                    results.append({'region1Id': r1['id'], 'region2Id': r2['id'], 'region3Id': fs[f]['pkid'], 'region4Id': fs[f]['pdid'], 'region5Id': fs[f]['id'], 'region6Id': b['id'], 'Kec': fs[f]['pkn'], 'Desa': fs[f]['pdn'], 'SLS': fs[f]['name'], 'SubSLS': b['name'], 'smallcode': b['fullCode']})
        return pd.DataFrame(results)
        
    return pd.DataFrame([{'region1Id': r1['id'], 'region2Id': r2['id'], 'region3Id': s['pkid'], 'region4Id': s['pdid'], 'region5Id': s['id'], 'Kec': s['pkn'], 'Desa': s['pdn'], 'SLS': s['name'], 'smallcode': s['fullCode']} for s in all_sls])

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
            'identity': res.get('code_identity', '')
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

def fetch_assignments_dynamic(sess, head, pid, gid, filt, current_level=2, max_level=6, role="Admin", id_survey=None, user_ids=None):
    url = f"{BASE_URL}/analytic/api/v2/assignment/datatable-all-user-survey-periode"
    payload = {"draw": 1, "start": 0, "length": 1, "assignmentExtraParam": {**filt, "surveyPeriodId": pid, "currentUserId": None}}
    try:
        r = sess.post(url, headers=head, json=payload).json()
        hit = r.get('totalHit', 0)
        if current_level >= max_level or ("admin" in role.lower() and hit <= 1000):
            res = []
            for s in range(0, hit, 1000):
                payload.update({"start": s, "length": 1000})
                res.extend(sess.post(url, headers=head, json=payload).json().get('searchData', []))
            return res
        sub = _get_lvl(current_level + 1, filt.get(f'region{current_level}Id'), gid, head, sess)
        all_r = []
        for c in sub:
            nf = filt.copy(); nf[f'region{current_level+1}Id'] = c['id']; nf['smallcode'] = c.get('fullCode')
            # Tambahkan print sederhana agar user tahu proses masih jalan
            if current_level <= 3: print(f"   📂 Mencari di: {c['name']}...")
            all_r.extend(fetch_assignments_dynamic(sess, head, pid, gid, nf, current_level + 1, max_level, role, id_survey, user_ids))
        return all_r
    except: return []

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
    print(f"🚀 Memproses {len(alist)} data {type}..."); start = time.time()
    failed = []
    for d in tqdm(alist):
        aid = d.get('id') or d.get('assignmentId'); st = d.get('assignmentStatusAlias', 'N/A')
        
        # Ambil detail untuk cek status_keberadaan (seperti v8/v9)
        extra = {}
        try:
            d_url = f"{BASE_URL}/assignment-general/api/assignment/get-by-id-with-data-for-scm?id={aid}"
            res_d = sess.get(d_url, headers=head, timeout=10).json().get('data', {})
            extra['status_keberadaan'] = res_d.get('data6') # data6 biasanya status keberadaan
        except: pass

        if not cond(role, st, extra):
            log.append({'id': aid, 'status': st, 'ok': False, 'msg': 'Skip: Kriteria status'})
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
            
            log.append({'id': aid, 'status': st, 'ok': True, 'msg': 'Success'})
        except Exception as e:
            log.append({'id': aid, 'status': st, 'ok': False, 'msg': str(e)}); failed.append(d)
    
    pd.DataFrame(log).to_excel(os.path.join(pilih_folder_simpan("Log"), f"Log_{type}_{timestamp()}.xlsx"), index=False)
    print(f"⏱️ Selesai dalam {int(time.time()-start)}s"); return failed

def timestamp(): return datetime.now().strftime("%Y%m%d_%H%M%S")

def main1(user, pwd, head, jar, sess, drv):
    global MAX_WORKERS_WILAYAH
    clear_screen()
    
    print("\n⚠️ Silakan periksa terlebih dahulu status login pada browser (driver).")
    print("   Jika belum berhasil login, silakan login manual terlebih dahulu sebelum lanjut.")
    t = input(f"Thread (default {MAX_WORKERS_WILAYAH}): ").strip()
    if t: MAX_WORKERS_WILAYAH = int(t)
    
    # Lakukan simpan session dari browser (mengambil cookies terbaru jika user baru login manual)
    try:
        new_head, new_jar, new_sess, _, new_ls, new_ss = ambil_cookies_dan_buat_session(drv, pwd)
        simpan_session(user, new_head, new_jar, new_sess, pwd, new_ls, new_ss)
        head, jar, sess = new_head, new_jar, new_sess
        print("✅ Session berhasil diperbarui dan disimpan.")
    except Exception as e:
        print(f"⚠️ Gagal menyimpan session baru: {e}")
    
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
            nama_wilayah = " - ".join([str(row[c]) for c in cols if pd.notna(row[row[c] == row[c]])]) # Handle NaN
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
        print("\n=== Menu ===\n1. Scrape\n2. Approve\n3. Revoke\n4. Reject\n5. Ganti Survey")
        aksi = input("Pilihan: ").strip()
        if aksi == "5": break
        if aksi not in "1234": continue
        
        rid = sess.get(f"{BASE_URL}/survey/api/v1/survey-roles?surveyId={sid}").json()['data'][-1]['id']
        uids = [u['userId'] for u in sess.get(f"{BASE_URL}/survey/api/v1/survey-period-role-users/region?surveyPeriodId={pid}&surveyRoleId={rid}&regionCode={sel_k['fullCode']}").json()['data']] + [None]
        
        # Sinkronisasi browser
        print(f"🌐 Sinkronisasi browser...")
        # drv.get(f"https://fasih-sm.bps.go.id/survey-collection/collect/{sid}")
        
        # Pengambilan data (Drill-down vs Per Wilayah)
        print(f"🔍 Mencari data penugasan...")
        unique = []
        seen = set()
        
        if len(df_f) == len(df_w): # Ambil semua (Drill-down)
            ids = fetch_assignments_dynamic(sess, head, pid, gid, {"region1Id": sel_p['id'], "region2Id": sel_k['id']}, max_level=6, role=getRoles(pid, head, sess), id_survey=sid, user_ids=uids)
            for x in ids:
                if x['id'] not in seen and x['assignmentStatusAlias'] != 'Open': unique.append(x); seen.add(x['id'])
        else: # Per Wilayah yang difilter
            max_lvl = len(lvls)
            def _fetch_row(row):
                f = {f"region{i}Id": row.get(f'region{i}Id') for i in range(1, 11)}
                f['smallcode'] = row.get('smallcode')
                return fetch_assignments_dynamic(sess, head, pid, gid, f, current_level=max_lvl-1, max_level=max_lvl, role=getRoles(pid, head, sess), id_survey=sid, user_ids=uids)

            with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as ex:
                fs = {ex.submit(_fetch_row, row): i for i, row in df_f.iterrows()}
                for f in tqdm(as_completed(fs), total=len(fs), desc="📥 Fetching Data Wilayah"):
                    for x in f.result():
                        if x['id'] not in seen and x['assignmentStatusAlias'] != 'Open': unique.append(x); seen.add(x['id'])
        
        if aksi == "1":
            out_dir = os.path.join(os.getcwd(), "output_scraper")
            os.makedirs(out_dir, exist_ok=True)
            out_file = os.path.join(out_dir, f"Scrape_{safe_sn}_{timestamp()}.xlsx")
            
            print(f"✅ {len(unique)} data ditemukan. Memulai download detail...")
            all_meta, all_pref, all_ans = [], [], []
            
            with ThreadPoolExecutor(max_workers=MAX_WORKERS_DETAIL) as ex:
                fs = {ex.submit(fetch_detail_task, sess, head, d, tid, pid): d for d in unique}
                for f in tqdm(as_completed(fs), total=len(fs), desc="📥 Scraping Detail", unit="data"):
                    res = f.result()
                    if res:
                        all_meta.append(res['meta'])
                        all_pref.append(res['pref'])
                        all_ans.append(res['ans'])
            
            print(f"💾 Menyusun file Excel...")
            with pd.ExcelWriter(out_file) as writer:
                pd.DataFrame(all_meta).to_excel(writer, sheet_name='Daftar_Tugas', index=False)
                if all_pref: pd.DataFrame(all_pref).to_excel(writer, sheet_name='Pre-defined', index=False)
                if all_ans: pd.DataFrame(all_ans).to_excel(writer, sheet_name='Answers', index=False)
            
            print(f"✅ Selesai! Data disimpan di: {os.path.relpath(out_file)}")
        else:
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
        h, c, s, p, ls, ss = main_login(drv, user)
        simpan_session(user, h, c, s, p, ls, ss)

    while True:
        try:
            main1(user, p, h, c, s, drv)
            if input("Keluar script? (y/n): ").lower() == 'y': break
        except Exception as e: print(f"❌ Error: {e}"); time.sleep(5)
    print("👋 Closing...")
    try: drv.quit()
    except: pass

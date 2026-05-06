# ====================================================================
# AUTO-INSTALL PACKAGE YANG BELUM TERINSTALL
# ====================================================================
import subprocess
import sys

# Daftar package pip yang dibutuhkan beserta nama import-nya
# Format: (nama_pip_install, nama_import_check)
REQUIRED_PACKAGES = [
    ("requests",    "requests"),
    ("pandas",      "pandas"),
    ("openpyxl",    "openpyxl"),      # engine untuk pandas to_excel
    ("tqdm",        "tqdm"),
    ("selenium",    "selenium"),
    ("urllib3",     "urllib3"),
]

def _auto_install_packages():
    """Cek dan install otomatis package yang belum terinstall."""
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
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            print(f"✅ Berhasil menginstall: {', '.join(missing)}")
        except subprocess.CalledProcessError as e:
            print(f"❌ Gagal menginstall beberapa package. Coba manual: pip install {' '.join(missing)}")
            sys.exit(1)

_auto_install_packages()

# ====================================================================
# IMPORTS
# ====================================================================
import time
import urllib.parse
from datetime import datetime
import os
from getpass import getpass
import tkinter as tk
from tkinter import filedialog
from typing import Dict, List, Optional, Tuple
from tqdm import tqdm
import pandas as pd
import requests
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
import platform
from http.cookiejar import Cookie, CookieJar
from requests.cookies import RequestsCookieJar
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, StaleElementReferenceException
import pickle
import base64
import hashlib
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import threading


# ====================================================================
# KONFIGURASI
# ====================================================================
MAX_WORKERS_WILAYAH = 15     # Jumlah thread untuk fetch wilayah
MAX_WORKERS_DETAIL = 20      # Jumlah thread untuk fetch detail
REQUEST_TIMEOUT = 30         # Timeout per request (detik)
MAX_RETRIES = 5              # Jumlah retry untuk request yang gagal
MANUAL_MAX_RETRIES = 5       # Retry manual untuk ConnectionResetError
POOL_CONNECTIONS = 100       # Jumlah koneksi pool
POOL_MAXSIZE = 100           # Ukuran maksimum pool
DETAIL_REQUEST_DELAY = 0.05  # Delay antar request detail (detik)
CHECKPOINT_SAVE_INTERVAL = 5 # Simpan checkpoint setiap N wilayah selesai


# ====================================================================
# SESSION MANAGEMENT
# ====================================================================

def create_resilient_session(cookies=None, headers=None) -> requests.Session:
    """Membuat session dengan retry logic dan connection pooling yang optimal."""
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=1.0,             # Backoff lebih agresif: 1s, 2s, 4s, 8s, 16s
        backoff_max=60,                 # Maksimum backoff 60 detik
        status_forcelist=[500, 502, 503, 504, 429],  # Tambah 429 (Too Many Requests)
        allowed_methods=["GET", "POST"],
        raise_on_status=False,          # Jangan langsung raise, biar bisa di-handle manual
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=POOL_CONNECTIONS,
        pool_maxsize=POOL_MAXSIZE,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    if cookies:
        session.cookies = cookies
    if headers:
        session.headers.update(headers)

    return session


# --- Password Obfuscation (XOR + Base64) ---
# Bukan enkripsi kuat, tapi cukup agar password tidak plain text di file pickle.
_OBFUSCATION_KEY = b'fasih-sm-scraper-v7-key-2026'

def _obfuscate_password(password: str) -> str:
    """Encode password agar tidak tersimpan plain text."""
    if not password:
        return ''
    key = _OBFUSCATION_KEY
    pwd_bytes = password.encode('utf-8')
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(pwd_bytes))
    return base64.b64encode(xored).decode('ascii')

def _deobfuscate_password(encoded: str) -> str:
    """Decode password yang sudah di-obfuscate."""
    if not encoded:
        return ''
    try:
        key = _OBFUSCATION_KEY
        xored = base64.b64decode(encoded.encode('ascii'))
        pwd_bytes = bytes(b ^ key[i % len(key)] for i, b in enumerate(xored))
        return pwd_bytes.decode('utf-8')
    except Exception:
        # Fallback: mungkin ini password lama yang belum di-obfuscate
        return encoded


def simpan_session(username, headers, cookies, session, password=None):
    session_path = pilih_folder_simpan("Pilih Folder untuk Menyimpan Session Login")

    if os.path.basename(os.path.normpath(session_path)).lower() == "sessions":
        sessions_dir = session_path
    else:
        sessions_dir = os.path.join(session_path, "sessions")
        os.makedirs(sessions_dir, exist_ok=True)

    # Obfuscate password sebelum simpan
    encoded_password = _obfuscate_password(password) if password else None

    filepath = os.path.join(sessions_dir, f"{username}_session.pkl")
    with open(filepath, 'wb') as f:
        pickle.dump({
            'username': username,
            'password': encoded_password,
            'password_encoded': True,
            'headers': headers,
            'cookies': cookies
            # session object removed due to unpickleable state
        }, f)


def muat_session(username):
    session_path = pilih_folder_simpan("Pilih Folder untuk Mengambil Session Login")

    if os.path.basename(os.path.normpath(session_path)).lower() == "sessions":
        sessions_dir = session_path
    else:
        sessions_dir = os.path.join(session_path, "sessions")

    filepath = os.path.join(sessions_dir, f"{username}_session.pkl")
    if os.path.exists(filepath):
        with open(filepath, 'rb') as f:
            data = pickle.load(f)
            raw_password = data.get('password', None)
            # Decrypt jika password sudah di-encode
            if data.get('password_encoded') and raw_password:
                password = _deobfuscate_password(raw_password)
            else:
                password = raw_password
            
            # Reconstruct session if not in file
            sess_obj = data.get('session')
            if sess_obj is None and data.get('cookies'):
                sess_obj = create_resilient_session(data.get('cookies'), data.get('headers'))
                
            return data.get('headers'), data.get('cookies'), sess_obj, password
    return None, None, None, None


def is_session_valid(session):
    try:
        resp = session.get("https://fasih-sm.bps.go.id/survey/api/v1/surveys",
                           allow_redirects=False, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


# ====================================================================
# SELENIUM / BROWSER
# ====================================================================

def setup_driver() -> webdriver.Chrome:
    service = Service()
    chrome_options = Options()
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument("--disable-logging")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    # Paksa timeout internal Selenium ke 10 jam (mengatasi ReadTimeoutError 120s)
    try:
        driver.command_executor._client_config.timeout = 36000
    except Exception:
        try:
            from selenium.webdriver.remote.remote_connection import RemoteConnection
            RemoteConnection.set_timeout(36000)
        except Exception:
            pass
            
    # Set page load timeout ke 10 jam juga
    try:
        driver.set_page_load_timeout(36000)
    except Exception:
        pass
        
    return driver


def setup_driver_with_cookies(cookies, url='https://fasih-sm.bps.go.id') -> webdriver.Chrome:
    driver = setup_driver()
    driver.get(url)

    for name, value in cookies.items():
        try:
            driver.add_cookie({'name': name, 'value': value, 'domain': '.bps.go.id'})
        except Exception as e:
            print(f"Gagal menambahkan cookie {name}: {e}")

    driver.refresh()
    return driver


def login_sso(driver: webdriver.Chrome, username: str, password: str) -> None:
    driver.get("https://sso.bps.go.id")
    driver.find_element(By.NAME, "username").send_keys(username)
    driver.find_element(By.NAME, "password").send_keys(password)
    driver.find_element(By.XPATH, '//*[@id="kc-login"]').click()
    time.sleep(1)
    try:
        otp_element = driver.find_element(By.XPATH, '//*[@id="otp"]')
        otp = input("Masukkan OTP yang Anda terima: ")
        otp_element.send_keys(otp)
        driver.find_element(By.XPATH, '//*[@id="kc-login"]').click()
        print("Login dengan OTP berhasil")
    except Exception:
        print("Login tanpa OTP berhasil")
    time.sleep(2)


def get_authenticated_cookies(driver: webdriver.Chrome) -> RequestsCookieJar:
    selenium_cookies = driver.get_cookies()
    jar = RequestsCookieJar()
    for cookie in selenium_cookies:
        jar.set(
            name=cookie['name'],
            value=cookie['value'],
            domain=cookie.get('domain'),
            path=cookie.get('path', '/'),
            secure=cookie.get('secure', False)
        )
    return jar


def apply_cookies_to_driver(driver, cookies, domain):
    driver.get(f"https://{domain}")
    time.sleep(2)
    for name, value in cookies.items():
        try:
            driver.add_cookie({
                'name': name, 'value': value,
                'domain': domain, 'path': '/',
            })
        except Exception as e:
            print(f"⚠️ Gagal menambahkan cookie {name}: {e}")
    print("✅ Cookies berhasil disuntikkan ke:", domain)
    try:
        driver.refresh() # Refresh agar cookies aktif
    except Exception:
        pass
    clear_screen()


# ====================================================================
# UTILITY
# ====================================================================

def clear_screen():
    os.system('cls' if platform.system() == 'Windows' else 'clear')


def pilih_file(filetypes=None) -> str:
    if filetypes is None:
        filetypes = [("Excel files", "*.xlsx *.xls"), ("All files", "*.*")]
    clear_screen()
    print("=== Pilih file untuk diproses ===")
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(title="Pilih file", filetypes=filetypes)
    if file_path:
        print(f"File terpilih: {file_path}")
        time.sleep(1)
        return file_path
    else:
        print("Tidak memilih file, membatalkan operasi.")
        time.sleep(1)
        return ""


def pilih_folder_simpan(judul) -> str:
    clear_screen()
    print(f"=== {judul} ===")
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title=f"{judul}")
    if folder:
        print(f"Folder terpilih: {folder}")
        time.sleep(1)
        return folder
    else:
        print("Tidak memilih folder, menggunakan direktori saat ini.")
        time.sleep(1)
        return os.getcwd()


# ====================================================================
# DATA EXTRACTION HELPERS
# ====================================================================

def extract_answers(answers: list) -> dict:
    """Ekstrak jawaban dari list answer menjadi flat dict."""
    result = {}
    for item in answers:
        key = item.get("dataKey")
        ans = item.get("answer")

        if isinstance(ans, list):
            if all(isinstance(i, dict) and 'value' in i and 'label' in i for i in ans):
                gabungan = [f"{i['value']}. {i['label']}" for i in ans]
                result[key] = ", ".join(gabungan)
            else:
                result[key] = ", ".join(str(i) for i in ans)
        elif isinstance(ans, dict):
            value = ans.get('value', '')
            label = ans.get('label', '')
            result[key] = f"{value}. {label}"
        else:
            result[key] = str(ans)
    return result


def parse_assignment_status(data_json: dict) -> list:
    """Parse status assignment dari response history API."""
    hasil = []
    data_list = data_json.get("data", [])

    if not data_list:
        hasil.append({
            "No": 0,
            "assignment_id": None,
            "date": None,
            "status_assignment": "Open",
            "current_user_username": ""
        })
    else:
        for i, item in enumerate(data_list, start=1):
            hasil.append({
                "No": i,
                "assignment_id": item.get("assignment_id"),
                "date": item.get("date_created"),
                "status_assignment": item.get("status_alias"),
                "current_user_username": item.get("current_user_username", "")
            })

    return hasil


def get_last_history(assignment_id: str, session: requests.Session, headers: dict) -> Tuple[str, str]:
    """Ambil status terakhir dan current user dari history assignment."""
    history_url = f'https://fasih-sm.bps.go.id/assignment-general/api/assignment-history/get-by-assignment-id?assignmentId={assignment_id}'
    resp_history = session.get(history_url, headers=headers, timeout=REQUEST_TIMEOUT)
    history = parse_assignment_status(resp_history.json())
    try:
        status_assignment = history[-1]['status_assignment']
        current_user_username = history[-1]['current_user_username']
    except (KeyError, IndexError):
        status_assignment = history[0].get('status_assignment', 'Open')
        current_user_username = history[0].get('current_user_username', '')

    return status_assignment, current_user_username


def get_status_keberadaan(api_response: dict) -> Optional[str]:
    """Mengambil status keberadaan (data6) dari response API assignment."""
    try:
        return api_response['data']['data6']
    except (TypeError, KeyError):
        return None


def getRoles(surveyPeriodeId, headers, cookies, session):
    try:
        url = f'https://fasih-sm.bps.go.id/survey/api/v1/users/myinfo?surveyPeriodId={surveyPeriodeId}'
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        return resp.json().get('data', {}).get('surveyRole', {}).get('description', 'Admin')
    except Exception:
        return "Admin"


def get_survey_period(id_survey, session, headers):
    """Ambil dan pilih survey period, return (surveyPeriodsId, surveyPeriodsName)."""
    url = f'https://fasih-sm.bps.go.id/survey/api/v1/surveys/{id_survey}'
    resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    survey_periods = resp.json()['data']['surveyPeriods']
    clear_screen()
    print("📅 Daftar Survey Periods:")
    for i, period in enumerate(survey_periods):
        print(f"{i}. ID: {period['id']}, Periode: {period['name']}, "
              f"Start: {period['startDate']}, End: {period['endDate']}")

    selected_index = int(input("Pilih index survey period: "))
    selected_period = survey_periods[selected_index]
    print(f"\n✅ Anda memilih: {selected_period['name']} (ID: {selected_period['id']})")
    return selected_period['id'], selected_period['name']


# ====================================================================
# WILAYAH (REGION)
# ====================================================================

def _get_level_data(level_num, parent_id, group_id, headers, cookies):
    """Helper untuk mengambil data wilayah di level tertentu."""
    url = f"https://fasih-sm.bps.go.id/region/api/v1/region/level{level_num}?groupId={group_id}&level{level_num-1}Id={parent_id}"
    try:
        resp = requests.get(url, headers=headers, cookies=cookies, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get('data', [])
    except Exception as e:
        print(f"   ⚠️ Gagal ambil Level {level_num} untuk ID {parent_id}: {e}")
        return []


def ambil_semua_sls_parallel(kabupaten_id, level_region, region_group_id,
                            headers, cookies, region_level1, region_level2):
    """
    Mengambil semua wilayah (Kec -> Desa -> SLS -> SubSLS) secara PARALLEL
    menggunakan ThreadPoolExecutor untuk kecepatan maksimal.
    """
    hierarki = " => ".join([name.get('name') for name in level_region])
    print(f"\n🚀 [Parallel] Mengambil data wilayah hierarki ({hierarki})...")
    
    if not isinstance(level_region, list) or len(level_region) < 3:
        # Penanganan jika level hanya sampai Prov atau Kab
        if len(level_region) == 2: return pd.DataFrame([region_level2])
        if len(level_region) == 1: return pd.DataFrame([region_level1])
        return pd.DataFrame()

    results = []
    region1_id = region_level1.get('id')
    region2_id = region_level2.get('id')
    
    # 1. Ambil Kecamatan (Level 3) - Masih linear karena cuma satu Kabupaten
    daftar_kecamatan = _get_level_data(3, kabupaten_id, region_group_id, headers, cookies)
    if not daftar_kecamatan:
        print("❌ Tidak ada kecamatan ditemukan.")
        return pd.DataFrame()

    print(f"📍 Ditemukan {len(daftar_kecamatan)} kecamatan. Menarik data desa secara paralel...")

    # 2. Ambil Desa (Level 4) secara Paralel
    all_desa = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as executor:
        future_to_kec = {executor.submit(_get_level_data, 4, kec['id'], region_group_id, headers, cookies): kec for kec in daftar_kecamatan}
        for future in future_to_kec:
            kec = future_to_kec[future]
            desa_list = future.result()
            for d in desa_list:
                d['parent_kec_id'] = kec['id']
                d['parent_kec_name'] = kec['name']
                all_desa.append(d)

    if len(level_region) == 3: # Hanya sampai Kecamatan
        return pd.DataFrame([{
            'region1Id': region1_id, 'region2Id': region2_id, 'region3Id': k['id'],
            f'{level_region[2]["name"]}': k['name'], 'smallcode': k['fullCode']
        } for k in daftar_kecamatan])

    # 3. Ambil SLS (Level 5) secara Paralel
    print(f"🏘️ Ditemukan {len(all_desa)} desa. Menarik data SLS secara paralel...")
    all_sls = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as executor:
        future_to_desa = {executor.submit(_get_level_data, 5, d['id'], region_group_id, headers, cookies): d for d in all_desa}
        for future in future_to_desa:
            desa = future_to_desa[future]
            sls_list = future.result()
            for s in sls_list:
                s['parent_desa_id'] = desa['id']
                s['parent_desa_name'] = desa['name']
                s['parent_kec_id'] = desa['parent_kec_id']
                s['parent_kec_name'] = desa['parent_kec_name']
                all_sls.append(s)

    if len(level_region) == 4: # Sampai Desa
        return pd.DataFrame([{
            'region1Id': region1_id, 'region2Id': region2_id, 'region3Id': d['parent_kec_id'],
            'region4Id': d['id'], f'{level_region[2]["name"]}': d['parent_kec_name'],
            f'{level_region[3]["name"]}': d['name'], 'smallcode': d['fullCode']
        } for d in all_desa])

    # 4. Ambil SubSLS (Level 6) secara Paralel jika diperlukan
    if len(level_region) >= 6:
        print(f"🧾 Ditemukan {len(all_sls)} SLS. Menarik data SubSLS secara paralel...")
        all_subsls = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as executor:
            future_to_sls = {executor.submit(_get_level_data, 6, s['id'], region_group_id, headers, cookies): s for s in all_sls}
            for future in future_to_sls:
                sls = future_to_sls[future]
                sub_list = future.result()
                for sub in sub_list:
                    all_subsls.append({
                        'region1Id': region1_id, 'region2Id': region2_id,
                        'region3Id': sls['parent_kec_id'], 'region4Id': sls['parent_desa_id'],
                        'region5Id': sls['id'], 'region6Id': sub['id'],
                        f'{level_region[2]["name"]}': sls['parent_kec_name'],
                        f'{level_region[3]["name"]}': sls['parent_desa_name'],
                        f'{level_region[4]["name"]}': sls['name'],
                        f'{level_region[5]["name"]}': sub['name'],
                        'smallcode': sub['fullCode']
                    })
        return pd.DataFrame(all_subsls)

    # Fallback to SLS (Level 5)
    return pd.DataFrame([{
        'region1Id': region1_id, 'region2Id': region2_id,
        'region3Id': s['parent_kec_id'], 'region4Id': s['parent_desa_id'],
        'region5Id': s['id'], 'region6Id': None,
        f'{level_region[2]["name"]}': s['parent_kec_name'],
        f'{level_region[3]["name"]}': s['parent_desa_name'],
        f'{level_region[4]["name"]}': s['name'],
        'smallcode': s['fullCode']
    } for s in all_sls])


def get_survey_role_id(id_survey, session, headers):
    """Ambil surveyRoleId (role terakhir dari daftar)."""
    try:
        url = f'https://fasih-sm.bps.go.id/survey/api/v1/survey-roles?surveyId={id_survey}'
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT).json()
        roles = resp.get('data', [])
        if roles:
            return roles[-1].get('id')
    except Exception as e:
        print(f"⚠️ Gagal ambil surveyRoleId: {e}")
    return None


def get_region_users(surveyPeriodId, surveyRoleId, regionCode, session, headers):
    """Ambil daftar user untuk suatu wilayah."""
    try:
        url = f'https://fasih-sm.bps.go.id/survey/api/v1/survey-period-role-users/region?surveyPeriodId={surveyPeriodId}&surveyRoleId={surveyRoleId}&regionCode={regionCode}'
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT).json()
        return resp.get('data', [])
    except Exception as e:
        print(f"⚠️ Gagal ambil daftar user wilayah: {e}")
    return []


def get_prelist_column_mapping(templateID, session, headers):
    """Ambil pemetaan kolom (data1 -> Nama Kolom) dari API template."""
    try:
        url = f"https://fasih-sm.bps.go.id/assignment-sync/api/mobile/template/custom-data/{templateID}"
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT).json()
        
        # Cek jika resp bukan dict (misal string error dari server)
        if not isinstance(resp, dict):
            print(f"⚠️ Response mapping bukan dictionary: {resp}")
            return {}

        # Mapping format: { 'data1': 'NAMA_KOLOM', ... }
        mapping = {}
        data_list = resp.get('data', [])
        if isinstance(data_list, list):
            for item in data_list:
                if isinstance(item, dict):
                    mapping[item.get('dataKey')] = item.get('columnName')
        return mapping
    except Exception as e:
        print(f"⚠️ Gagal ambil mapping kolom prelist: {e}")
    return {}


def fetch_assignments_dynamic(session, headers, surveyPeriodsId, group_id, region_filters, current_level=2, max_level=6, role="Admin", id_survey=None, user_ids=None):
    """
    Strategi Drill-Down Dinamis: Menarik data di level setinggi mungkin.
    Untuk non-admin, DIPAKSA drill-down hingga level maksimal agar data muncul.
    """
    url = "https://fasih-sm.bps.go.id/analytic/api/v2/assignment/datatable-all-user-survey-periode"
    is_admin = "admin" in role.lower()
    
    # 1. Cek totalHit untuk filter saat ini
    payload = {
        "draw": 1,
        "start": 0,
        "length": 1, # Cek total saja
        "assignmentExtraParam": {
            **region_filters,
            "surveyPeriodId": surveyPeriodsId,
            "currentUserId": None
        }
    }
    
    try:
        resp = session.post(url, headers=headers, json=payload).json()
        total_hit = resp.get('totalHit', 0)
        
        # 2. BASE CASE: 
        # a) Sudah di level paling bawah (max_level)
        # b) Atau kita adalah Admin dan data <= 1000 (bisa ditarik sekaligus)
        if current_level >= max_level or (is_admin and total_hit <= 1000):
            if total_hit == 0: return []
            
            # Jika di level terakhir dan data > 1000, coba akali dengan currentUserId
            if current_level >= max_level and total_hit > 1000:
                print(f"   🚀 Level {current_level} memiliki {total_hit} data (>1000). Menggunakan strategi currentUserId...")
                
                # Gunakan user_ids yang sudah di-fetch sebelumnya jika ada
                all_user_ids_to_use = user_ids
                if all_user_ids_to_use is None and id_survey:
                    surveyRoleId = get_survey_role_id(id_survey, session, headers)
                    region_code = region_filters.get('smallcode') or region_filters.get(f'region{current_level}FullCode')
                    if surveyRoleId and region_code:
                        users = get_region_users(surveyPeriodsId, surveyRoleId, region_code, session, headers)
                        all_user_ids_to_use = [u.get('userId') for u in users]
                
                if all_user_ids_to_use:
                    collected_ids = set()
                    all_collected = []
                    
                    def _fetch_user_task(uid):
                        p = json.loads(json.dumps(payload)) # Deep copy payload
                        p['assignmentExtraParam']['currentUserId'] = uid # Kembali ke dalam assignmentExtraParam
                        p['start'] = 0
                        p['length'] = 1000
                        p['draw'] += 1
                        
                        user_results = []
                        while True:
                            try:
                                r = session.post(url, headers=headers, json=p, timeout=REQUEST_TIMEOUT).json()
                                search_data = r.get('searchData', [])
                                user_results.extend(search_data)
                                if len(search_data) == 1000:
                                    p['start'] += 1000
                                    p['draw'] += 1
                                else:
                                    break
                            except Exception as e:
                                print(f"⚠️ Gagal fetch user {uid}: {e}")
                                break
                        return user_results

                    with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as user_executor:
                        user_futures = {user_executor.submit(_fetch_user_task, uid): uid for uid in all_user_ids_to_use}
                        for u_future in tqdm(as_completed(user_futures), total=len(user_futures), 
                                            desc="📥 Fetching by User", leave=False):
                            search_data = u_future.result()
                            for item in search_data:
                                if item['id'] not in collected_ids:
                                    all_collected.append(item)
                                    collected_ids.add(item['id'])
                    
                    return all_collected

            # Tarik semua data (max 1000 sesuai limit server)
            all_collected = []
            for start_idx in range(0, total_hit, 1000):
                payload['start'] = start_idx
                payload['length'] = 1000
                payload['draw'] += 1
                r = session.post(url, headers=headers, json=payload).json()
                all_collected.extend(r.get('searchData', []))
                
                # Paging limit check (hanya warning jika server memang membatasi)
                if start_idx == 0 and len(all_collected) == 1000 and total_hit > 1000:
                    print(f"   ⚠️ Info: Level {current_level} memiliki {total_hit} data. Mencoba menarik seluruhnya...")
            
            return all_collected
            
        # 3. RECURSIVE CASE: 
        # a) Admin dengan data > 1000
        # b) Atau Non-Admin (SELALU drill-down demi validitas payload)
        if is_admin:
            print(f"   🔍 Level {current_level} memiliki {total_hit} data (>1000). Bor ke Level {current_level+1}...")
        
        parent_id = region_filters.get(f'region{current_level}Id')
        children = _get_level_data(current_level + 1, parent_id, group_id, headers, session.cookies)
        
        if not children:
            if is_admin:
                print(f"   ⚠️ Tidak ada sub-wilayah ditemukan. Menarik 1000 data teratas.")
                payload['length'] = 1000
                r = session.post(url, headers=headers, json=payload).json()
                return r.get('searchData', [])
            return []

        all_results = []
        for child in children:
            child_filters = region_filters.copy()
            child_filters[f'region{current_level+1}Id'] = child['id']
            # Teruskan smallcode/fullcode untuk penanganan user-id di level terakhir
            child_filters['smallcode'] = child.get('fullCode')
            res = fetch_assignments_dynamic(session, headers, surveyPeriodsId, group_id, child_filters, current_level + 1, max_level, role, id_survey, user_ids)
            all_results.extend(res)
            
        return all_results

    except Exception as e:
        print(f"   ⚠️ Error fetch dynamic (Level {current_level}): {e}")
        return []






# ====================================================================
# CHECKPOINT SYSTEM — Resume scraping dari titik terakhir
# ====================================================================

def _get_checkpoint_path(save_dir: str, survey_id: str, period_id: str) -> str:
    """Path file checkpoint berdasarkan survey dan period."""
    safe_name = f"checkpoint_{survey_id}_{period_id}.json"
    return os.path.join(save_dir, safe_name)


def _save_checkpoint(checkpoint_path: str, completed_smallcodes: list,
                     answer_rows: list, assignment_data: list):
    """Simpan progres ke file checkpoint."""
    try:
        data = {
            'completed_smallcodes': completed_smallcodes,
            'answer_rows': answer_rows,
            'assignment_data': assignment_data,
            'timestamp': datetime.now().isoformat(),
        }
        # Tulis ke file temp dulu, lalu rename (atomic write)
        tmp_path = checkpoint_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        # Replace file atomically
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
        os.rename(tmp_path, checkpoint_path)
    except Exception as e:
        print(f"⚠️ Gagal simpan checkpoint: {e}")


def _load_checkpoint(checkpoint_path: str) -> dict:
    """Load checkpoint jika ada. Return dict atau None."""
    if not os.path.exists(checkpoint_path):
        return None
    try:
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"\n📂 Ditemukan checkpoint dari: {data.get('timestamp', 'unknown')}")
        print(f"   - Wilayah selesai: {len(data.get('completed_smallcodes', []))}")
        print(f"   - Data terkumpul: {len(data.get('answer_rows', []))} baris")
        return data
    except Exception as e:
        print(f"⚠️ Gagal membaca checkpoint: {e}")
        return None


def _delete_checkpoint(checkpoint_path: str):
    """Hapus file checkpoint setelah selesai."""
    try:
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
            print("🗑️ Checkpoint berhasil dihapus.")
    except Exception as e:
        print(f"⚠️ Gagal hapus checkpoint: {e}")


# ====================================================================
# APPROVE / REVOKE / REJECT — REFACTORED (SATU FUNGSI GENERIK)
# ====================================================================

def _process_single_assignment(driver, assignment_id, template_id, surveyPeriodsId,
                                smallCode, roles, session, headers, action_type,
                                condition_checker):
    """
    Proses satu assignment (approve/revoke/reject).
    
    Args:
        action_type: 'approve' | 'revoke' | 'reject'
        condition_checker: fungsi(roles, status_assignment, extra_data) -> bool
    
    Returns:
        dict log entry
    """
    review_assignment_url = (f'https://fasih-sm.bps.go.id/survey-collection/survey-review/'
                             f'{assignment_id}/{template_id}/{surveyPeriodsId}/a/1')

    button_id_map = {
        'approve': 'buttonApprove',
        'revoke': 'buttonRevoke',
        'reject': 'buttonReject'
    }
    button_id = button_id_map[action_type]

    # Ambil history
    history_url = (f'https://fasih-sm.bps.go.id/assignment-general/api/'
                   f'assignment-history/get-by-assignment-id?assignmentId={assignment_id}')
    resp_history = session.get(history_url, headers=headers, timeout=REQUEST_TIMEOUT)

    # Ambil data assignment (untuk cek keberadaan dll)
    data_url = (f'https://fasih-sm.bps.go.id/assignment-general/api/assignment/'
                f'get-by-id-with-data-for-scm?id={assignment_id}')
    resp_data = session.get(data_url, headers=headers, timeout=REQUEST_TIMEOUT)

    history = parse_assignment_status(resp_history.json())
    status_assignment = history[-1]['status_assignment']

    # Extra data untuk condition checker
    extra_data = {
        'status_keberadaan': get_status_keberadaan(resp_data.json()),
        'resp_data': resp_data.json()
    }

    approved = False
    keterangan = ""

    if condition_checker(roles, status_assignment, extra_data):
        try:
            driver.get(review_assignment_url)
            wait = WebDriverWait(driver, 45)

            wait.until(EC.presence_of_element_located((By.ID, button_id)))
            action_button = wait.until(EC.element_to_be_clickable((By.ID, button_id)))

            clicked = False
            attempt = 0
            max_attempts = 10

            while not clicked and attempt < max_attempts:
                try:
                    print(f"🔁 Mencoba klik tombol {action_type}... percobaan ke-{attempt+1}")
                    time.sleep(0.4)
                    action_button.click()
                    clicked = True
                    print(f"✅ Klik tombol {action_type} berhasil.")
                except (ElementClickInterceptedException, StaleElementReferenceException) as e:
                    attempt += 1
                    print(f"⚠️ Klik gagal: {e}. Mengulang...")
                    action_button = wait.until(EC.element_to_be_clickable((By.ID, button_id)))
                except Exception as e:
                    print(f"❌ Error lain saat klik {action_type}: {e}")
                    break

            # Konfirmasi 1
            confirm_xpath = '//*[@id="fasih"]/div/div/div[6]/button[1]'
            wait.until(EC.presence_of_element_located((By.XPATH, confirm_xpath)))
            wait.until(EC.element_to_be_clickable((By.XPATH, confirm_xpath)))
            driver.find_element(By.XPATH, confirm_xpath).click()

            # Konfirmasi 2 (jika ada)
            try:
                wait.until(EC.element_to_be_clickable((By.XPATH, confirm_xpath)))
                driver.find_element(By.XPATH, confirm_xpath).click()
            except TimeoutException:
                pass

            approved = True
            keterangan = f"✅ {action_type.capitalize()}d"
            print(f"✅ {action_type.capitalize()}d assignment {assignment_id}")

        except TimeoutException:
            keterangan = "❌ Timeout: Elemen tidak muncul"
            print(f"❌ Timeout untuk assignment {assignment_id}")
        except ElementClickInterceptedException as e:
            keterangan = f"❌ Klik gagal karena ditutup elemen lain: {e}"
            print(f"❌ Klik gagal untuk assignment {assignment_id}: {e}")
        except Exception as e:
            keterangan = f"❌ Error saat klik {action_type}: {e}"
            print(f"❌ Error klik {action_type} untuk assignment {assignment_id}: {e}")
    else:
        keterangan = f"❌ Belum memenuhi syarat {action_type} (status: {status_assignment})"
        print(f"ℹ️ Assignment {assignment_id} belum bisa di-{action_type} (status: {status_assignment})")

    return {
        'assignment_id': assignment_id,
        'link_assignment': review_assignment_url,
        'smallCode': smallCode,
        'status_assignment': status_assignment,
        'approved': approved,
        'keterangan': keterangan
    }


def process_assignments_generic(id_survey, template_id, nama_kab, nama_survey,
                                 assignment_list, headers, cookies, session,
                                 driver, action_type, condition_checker):
    """
    Fungsi generik untuk approve/revoke/reject assignment.
    Sekarang menerima assignment_list langsung (hasil fetch_assignments_dynamic).
    """
    try:
        surveyPeriodsId, surveyPeriodsName = get_survey_period(id_survey, session, headers)
    except Exception as e:
        print(f"❌ Gagal mengambil surveyPeriodsId: {e}")
        return

    save_dir = pilih_folder_simpan("Pilih lokasi penyimpanan file Excel")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename_log = f"Log_{action_type.capitalize()}_{nama_kab}_{nama_survey}_{timestamp}.xlsx"
    filepath_log = os.path.join(save_dir, filename_log)

    roles = getRoles(surveyPeriodsId, headers, cookies, session)
    print(f"Roles sebagai: {roles}")

    log_entries = []
    status_assignment_filter = ''
    start_time = time.time()

    print(f"\n🚀 Memproses {len(assignment_list)} data {action_type}...")

    for d in tqdm(assignment_list, desc=f"Memproses {action_type} data SLS", unit="Data"):
        # assignment_list bisa berupa list of dict (dari API) atau DataFrame record
        if isinstance(d, dict):
            assignment_id = d.get('id') or d.get('assignmentId')
            smallCode = d.get('regionFullCode') or d.get('smallcode') or "N/A"
        else:
            # Jika d adalah Series/Row dari DataFrame
            assignment_id = d['id']
            smallCode = d['smallcode'] if 'smallcode' in d else "N/A"

        try:
            log_entry = _process_single_assignment(
                driver=driver,
                assignment_id=assignment_id,
                template_id=template_id,
                surveyPeriodsId=surveyPeriodsId,
                smallCode=smallCode,
                roles=roles,
                session=session,
                headers=headers,
                action_type=action_type,
                condition_checker=condition_checker
            )
            if log_entry.get('approved'):
                status_assignment_filter = log_entry['status_assignment']
            log_entries.append(log_entry)
        except Exception as e:
            print(f"❌ Gagal memproses assignment {assignment_id}: {e}")
            review_url = (f'https://fasih-sm.bps.go.id/survey-collection/survey-review/'
                          f'{assignment_id}/{template_id}/{surveyPeriodsId}/a/1')
            log_entries.append({
                'assignment_id': assignment_id,
                'link_assignment': review_url,
                'smallCode': smallCode,
                'status_assignment': 'ERROR',
                'approved': False,
                'keterangan': f"❌ Exception: {e}"
            })

    # Simpan log
    df_log = pd.DataFrame(log_entries)
    if not df_log.empty:
        df_log.to_excel(filepath_log, index=False)

        if status_assignment_filter:
            status_filter = df_log['status_assignment'].isin([status_assignment_filter])
            jumlah_seharusnya = df_log[status_filter]
            jumlah_approve = jumlah_seharusnya['approved'].sum()
            jumlah_gagal = len(jumlah_seharusnya) - jumlah_approve
        else:
            jumlah_approve = df_log['approved'].sum()
            jumlah_gagal = len(df_log) - jumlah_approve
    else:
        jumlah_approve = 0
        jumlah_gagal = 0

    elapsed = time.time() - start_time
    jam, sisa = divmod(elapsed, 3600)
    menit, detik = divmod(sisa, 60)

    clear_screen()
    print(f"\n📄 Log hasil {action_type} disimpan di: {filepath_log}")
    print(f"✅ Proses {action_type} selesai untuk wilayah {nama_kab}")
    print(f"   - Jumlah berhasil {action_type}: {jumlah_approve}")
    print(f"   - Jumlah gagal {action_type}   : {jumlah_gagal}")
    print(f"⏱️ Proses selesai dalam {int(jam)} jam {int(menit)} menit {int(detik)} detik.")


# ====================================================================
# CONDITION CHECKERS — Syarat approve/revoke/reject
# ====================================================================

def approve_condition(roles, status_assignment, extra_data):
    """Syarat untuk approve assignment."""
    return (
        (roles == 'Pengawas' and status_assignment == 'SUBMITTED BY Pencacah') or
        (roles == 'PML' and status_assignment == 'SUBMITTED BY PPL') or
        (roles == 'Admin Kabupaten' and status_assignment == 'APPROVED BY Pengawas') or
        (roles == 'Admin Kabupaten' and status_assignment == 'APPROVED BY PML') or
        (roles == 'Admin Kabupaten' and status_assignment == 'EDITED BY Admin Kabupaten') or
        (roles == 'Admin' and status_assignment == 'APPROVED BY Pengawas') or
        (roles == 'Admin' and status_assignment == 'APPROVED BY PML') or
        (roles == 'Admin' and status_assignment == 'EDITED BY Admin Kabupaten') or
        (roles == 'Admin Provinsi' and status_assignment == 'COMPLETED BY Admin Kabupaten')
    )


def revoke_condition(roles, status_assignment, extra_data):
    """Syarat untuk revoke assignment."""
    status_keberadaan = extra_data.get('status_keberadaan')
    return (
        roles == 'Pengawas' and
        status_assignment == 'COMPLETED BY Pengawas' and
        status_keberadaan == '3. Tidak Ditemukan'
    )


def reject_condition(roles, status_assignment, extra_data):
    """Syarat untuk reject assignment."""
    status_keberadaan = extra_data.get('status_keberadaan')
    return (
        roles == 'Pengawas' and
        status_assignment == 'SUBMITTED BY Pencacah' and
        status_keberadaan == '3. Tidak Ditemukan'
    )


# Wrapper functions untuk backward compatibility
def approveByPML(id_survey, template_id, nama_kab, nama_survey,
                 assignment_list, headers, cookies, session, driver=None):
    process_assignments_generic(
        id_survey, template_id, nama_kab, nama_survey,
        assignment_list, headers, cookies, session, driver,
        action_type='approve', condition_checker=approve_condition
    )


def revokeByPML(id_survey, template_id, nama_kab, nama_survey,
                assignment_list, headers, cookies, session, driver=None):
    process_assignments_generic(
        id_survey, template_id, nama_kab, nama_survey,
        assignment_list, headers, cookies, session, driver,
        action_type='revoke', condition_checker=revoke_condition
    )


def rejectByPML(id_survey, template_id, nama_kab, nama_survey,
                assignment_list, headers, cookies, session, driver=None):
    process_assignments_generic(
        id_survey, template_id, nama_kab, nama_survey,
        assignment_list, headers, cookies, session, driver,
        action_type='reject', condition_checker=reject_condition
    )

# ====================================================================
# MAIN LOGIN FLOW
# ====================================================================

BASE_URL = "https://fasih-sm.bps.go.id"

def ambil_cookies_dan_buat_session(driver, password):
    """Ambil cookies dari driver dan buat session."""
    cookies = get_authenticated_cookies(driver)
    xsrf_token = urllib.parse.unquote(cookies.get('XSRF-TOKEN', ''))
    headers = {
        'X-Requested-With': 'XMLHttpRequest',
        'X-XSRF-TOKEN': xsrf_token,
        'Referer': 'https://fasih-sm.bps.go.id/',
        'User-Agent': 'Mozilla/5.0',
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://fasih-sm.bps.go.id'
    }
    session = create_resilient_session(cookies, headers)
    session.cookies.update(cookies)
    
    print("✅ Berhasil Login!")
    return headers, cookies, session, password


def main(driver, username, password=None):
    """Login ke FASIH-SM via SSO. Return (headers, cookies, session, password)."""
    clear_screen()
    if not password:
        password = input("Masukkan password SSO: ")
    
    # 1. Akses FASIH langsung
    while True:
        try:
            driver.get("https://fasih-sm.bps.go.id/")
            break
        except Exception as e:
            print(f"⏳ Menunggu koneksi ke FASIH (Timeout/Error): {e}")
            time.sleep(2)
    
    time.sleep(3)
    
    # 2. Klik tombol Login (OAuth redirect ke SSO)
    try:
        # Cek apakah sudah dalam posisi login
        if "fasih-sm.bps.go.id" in driver.current_url and "sso.bps.go.id" not in driver.current_url:
            # Jika tombol login tidak ada, berarti sudah masuk
            login_btns = driver.find_elements(By.XPATH, '//*[@id="login-in"]/a[2]')
            if not login_btns:
                print("✅ Terdeteksi sudah login, melewati tombol login.")
                return ambil_cookies_dan_buat_session(driver, password)
        
        wait = WebDriverWait(driver, 15)
        login_btn = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="login-in"]/a[2]')))
        login_btn.click()
        print("🔗 Redirect ke SSO BPS...")
        time.sleep(3)
    except Exception as e:
        # Jika gagal klik tapi URL sudah benar, mungkin sudah login
        if "fasih-sm.bps.go.id" in driver.current_url and "sso.bps.go.id" not in driver.current_url:
             print("✅ Mengasumsikan sudah login (tombol tidak ditemukan tapi URL benar).")
             return ambil_cookies_dan_buat_session(driver, password)
        else:
             print(f"⚠️ Tombol login tidak ditemukan: {e}")
    
    # 3. Input username & password di halaman SSO
    try:
        wait = WebDriverWait(driver, 15)
        username_field = wait.until(EC.presence_of_element_located((By.NAME, "username")))
        username_field.clear()
        username_field.send_keys(username)
        password_field = driver.find_element(By.NAME, "password")
        password_field.clear()
        password_field.send_keys(password)
        driver.find_element(By.XPATH, '//*[@id="kc-login"]').click()
        print("📤 Mengirim kredensial...")
        time.sleep(2)
    except Exception as e:
        print(f"⚠️ Halaman login SSO tidak muncul: {e}")
    
    # 4. Penanganan OTP (jika diperlukan)
    try:
        wait = WebDriverWait(driver, 5)
        otp_field = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="otp"]')))
        otp_value = input("Masukkan OTP yang Anda terima: ").strip()
        
        for attempt in range(5):
            try:
                alerts = driver.find_elements(By.CSS_SELECTOR, ".modal, .popup, .overlay, [role='dialog']")
                for a in alerts:
                    try:
                        a.find_element(By.CSS_SELECTOR, "button.close, .btn-close, [aria-label='Close']").click()
                        time.sleep(0.5)
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                driver.switch_to.alert.dismiss()
                time.sleep(0.5)
            except Exception:
                pass
            
            otp_field = driver.find_element(By.XPATH, '//*[@id="otp"]')
            otp_field.clear()
            otp_field.send_keys(otp_value)
            time.sleep(0.3)
            
            if otp_field.get_attribute("value") == otp_value:
                print(f"   ✅ OTP berhasil terinput")
                break
            print(f"   ⚠️ OTP retry {attempt+1}/5...")
            time.sleep(1)
        
        driver.find_element(By.XPATH, '//*[@id="kc-login"]').click()
        print("🔐 OTP dikirim...")
    except TimeoutException:
        print("ℹ️ OTP tidak diperlukan.")
    except Exception as e:
        print(f"⚠️ Error OTP: {e}")
    
    # 5. Tunggu redirect kembali ke FASIH
    print("⏳ Menunggu redirect ke FASIH...")
    try:
        WebDriverWait(driver, 30).until(
            lambda d: "fasih-sm.bps.go.id" in d.current_url and "sso.bps.go.id" not in d.current_url)
        print(f"✅ Redirect berhasil: {driver.current_url}")
    except TimeoutException:
        print(f"⚠️ Timeout redirect. URL: {driver.current_url}")
    
    time.sleep(3)
    
    # 6. Ambil cookies dan buat session
    return ambil_cookies_dan_buat_session(driver, password)



def main1(headers, cookies, session, driver):
    """Pilih survey, wilayah, dan jalankan scraping/approve/revoke/reject."""
    global MAX_WORKERS_WILAYAH, MAX_WORKERS_DETAIL
    
    # Input jumlah thread
    try:
        clear_screen()
        print(f"⚙️ Konfigurasi Performa:")
        t_input = input(f"Masukkan jumlah thread (default {MAX_WORKERS_WILAYAH}): ").strip()
        if t_input:
            MAX_WORKERS_WILAYAH = int(t_input)
            MAX_WORKERS_DETAIL = max(1, MAX_WORKERS_WILAYAH // 2) # Detail biasanya lebih berat, set setengahnya
            print(f"✅ Thread diatur ke: {MAX_WORKERS_WILAYAH} (Wilayah) / {MAX_WORKERS_DETAIL} (Detail)")
            time.sleep(1)
    except ValueError:
        print(f"⚠️ Input tidak valid, menggunakan default: {MAX_WORKERS_WILAYAH}")
        time.sleep(1)

    # Ambil daftar survei
    url = 'https://fasih-sm.bps.go.id/survey/api/v1/surveys/datatable?surveyType=Pencacahan'
    payload = {
        "pageNumber": 0,
        "pageSize": 100,
        "sortBy": "CREATED_AT",
        "sortDirection": "DESC",
        "keywordSearch": ""
    }
    response = session.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()

    clear_screen()
    print("\n=== Daftar Survei ===")
    for i, item in enumerate(data['data']['content']):
        print(f"{i+1}. {item['name']} (id: {item['id']})")

    pilihan = int(input("\nPilih nomor survei: "))
    id_survey = data['data']['content'][pilihan - 1]['id']
    nama_survey = data['data']['content'][pilihan - 1]['name']

    # Metadata survei
    url_group = f"https://fasih-sm.bps.go.id/survey/api/v1/surveys/{id_survey}"
    resp_group = session.get(url_group, headers=headers, timeout=REQUEST_TIMEOUT)
    group_id = resp_group.json()['data']['regionGroupId']
    template_id = resp_group.json()['data']['surveyTemplates'][-1]['templateId']

    url_level_region = f'https://fasih-sm.bps.go.id/region/api/v1/region-metadata?id={group_id}'
    level_region = session.get(url_level_region, headers=headers, timeout=REQUEST_TIMEOUT).json()['data']['level']

    # Provinsi
    url_prov = f"https://fasih-sm.bps.go.id/region/api/v1/region/level1?groupId={group_id}"
    data_prov = session.get(url_prov, headers=headers, timeout=REQUEST_TIMEOUT).json()

    clear_screen()
    print("\n=== Daftar Provinsi ===")
    for i, p in enumerate(data_prov['data']):
        print(f"{i+1}. {p['name']} (fullcode: {p['fullCode']})")

    pilihan_prov = int(input("Pilih nomor provinsi: "))
    fullcode_prov = data_prov['data'][pilihan_prov - 1]['fullCode']
    id_prov = data_prov['data'][pilihan_prov - 1]['id']
    code_prov = data_prov['data'][pilihan_prov - 1]['code']
    name_prov = data_prov['data'][pilihan_prov - 1]['name']

    # Kabupaten
    url_kab = f"https://fasih-sm.bps.go.id/region/api/v1/region/level2?groupId={group_id}&level1FullCode={fullcode_prov}"
    data_kab = session.get(url_kab, headers=headers, timeout=REQUEST_TIMEOUT).json()
    print("\n=== Daftar Kabupaten ===")
    for i, k in enumerate(data_kab['data']):
        print(f"{i+1}. {k['name']} (id: {k['id']})")

    pilihan_kab = int(input("Pilih nomor kabupaten: "))
    id_kab = data_kab['data'][pilihan_kab - 1]['id']
    nama_kab = data_kab['data'][pilihan_kab - 1]['name']
    fullcode_kab = data_kab['data'][pilihan_kab - 1]['fullCode']
    code_kab = data_kab['data'][pilihan_kab - 1]['code']

    driver.get(f"https://fasih-sm.bps.go.id/survey-collection/collect/{id_survey}")
    surveyPeriodsId, surveyPeriodsName = get_survey_period(id_survey, session, headers)

    region_level1 = {'id': id_prov, 'fullCode': fullcode_prov, 'code': code_prov,
                     'name': name_prov, 'smallcode': fullcode_prov}
    region_level2 = {'id': id_kab, 'fullCode': fullcode_kab, 'code': code_kab,
                     'name': nama_kab, 'smallcode': fullcode_kab}

    # TAHAP 1: Wilayah secara Parallel
    import glob
    wilayah_pattern = f"*daftarwilayah - {nama_survey} - {surveyPeriodsName}*.xlsx"
    existing_wilayah = glob.glob(wilayah_pattern)
    
    if existing_wilayah:
        # Gunakan file terbaru jika ada beberapa (berdasarkan urutan nama/tanggal)
        wilayah_filename = sorted(existing_wilayah)[-1]
        print(f"📦 Menggunakan data wilayah dari cache: {wilayah_filename}")
        daftarwilayah = pd.read_excel(wilayah_filename)
    else:
        timestamp_day = datetime.now().strftime("%Y%m%d")
        wilayah_filename = f"{timestamp_day}_daftarwilayah - {nama_survey} - {surveyPeriodsName}.xlsx"
        
        daftarwilayah = ambil_semua_sls_parallel(
            id_kab, level_region, group_id, headers, cookies,
            region_level1=region_level1, region_level2=region_level2
        )
        if not daftarwilayah.empty:
            daftarwilayah.to_excel(wilayah_filename, index=False)
            print(f"💾 Data wilayah disimpan ke: {wilayah_filename}")

    clear_screen()
    print(f"✅ {len(daftarwilayah)} unit wilayah teridentifikasi.")
    
    # --- FITUR FILTER WILAYAH ---
    pilih_filter = input("\nApakah Anda ingin memfilter wilayah tertentu? (Y/N): ").strip().upper()
    if pilih_filter == 'Y':
        print("\n=== Daftar Wilayah Terdeteksi ===")
        # Tampilkan kolom nama yang tersedia (tergantung level survey)
        cols = [c for c in daftarwilayah.columns if not c.endswith('Id') and c != 'smallcode']
        
        # Pagination sederhana untuk list wilayah agar tidak memenuhi terminal
        for i, row in daftarwilayah.iterrows():
            nama_wilayah = " - ".join([str(row[c]) for c in cols if pd.notna(row[c])])
            print(f"[{i}] {row['smallcode']} | {nama_wilayah}")
        
        input_pilih = input("\nMasukkan index wilayah yang ingin diambil\n"
                            "(Contoh: '1,3,5' atau '0-10' atau 'all'): ").strip().lower()
        
        if input_pilih != 'all' and input_pilih != '':
            try:
                indices = []
                for part in input_pilih.split(','):
                    if '-' in part:
                        start, end = map(int, part.split('-'))
                        indices.extend(range(start, end + 1))
                    else:
                        indices.append(int(part))
                
                # Validasi index
                indices = [idx for idx in indices if 0 <= idx < len(daftarwilayah)]
                if indices:
                    daftarwilayah = daftarwilayah.iloc[indices].reset_index(drop=True)
                    print(f"✅ Berhasil memfilter! Sekarang memproses {len(daftarwilayah)} wilayah.")
                else:
                    print("⚠️ Index tidak valid, menggunakan semua wilayah.")
            except Exception as e:
                print(f"⚠️ Gagal memproses input filter ({e}), menggunakan semua wilayah.")
        time.sleep(1)

    # Menu
    print("\n=== Pilih tindakan ===")
    print("1. Ambil Raw Data (Scrape)")
    print("2. Approve Assignment")
    print("3. Revoke Assignment")
    print("4. Reject Assignment")
    aksi = input("Pilihan (1/2/3/4): ").strip()

    if aksi in ["1", "2", "3", "4"]:
        AssignmentIds = []
        
        # Ambil surveyRoleId dan user_ids sekali saja untuk seluruh proses
        surveyRoleId = get_survey_role_id(id_survey, session, headers)
        user_ids = None
        if surveyRoleId:
            print("👥 Mengambil daftar seluruh user untuk strategi pagination...")
            # Gunakan fullcode_kab sebagai representasi wilayah luas
            users = get_region_users(surveyPeriodsId, surveyRoleId, fullcode_kab, session, headers)
            user_ids = [u.get('userId') for u in users] + [None]
            print(f"✅ Ditemukan {len(user_ids)} user ID.")

        # Pilih metode pengambilan
        print("\nMetode Pengambilan Assignment:")
        print("1. Drill-Down Dinamis (Cepat untuk cakupan luas/seluruh Kab)")
        print("2. Berdasarkan Daftar Wilayah (Hanya wilayah terpilih di atas)")
        pilih_metode = input("Pilih metode (1/2): ").strip()

        if pilih_metode == "1":
            max_lvl = len(level_region) if (level_region and isinstance(level_region, list)) else 6
            print(f"📡 Kedalaman wilayah: Level {max_lvl}")
            initial_filters = {f"region{i}Id": None for i in range(1, 11)}
            initial_filters["region1Id"] = id_prov
            initial_filters["region2Id"] = id_kab
            role = getRoles(surveyPeriodsId, headers, cookies, session)
            print(f"👤 Role Terdeteksi: {role}")
            print("🚀 Drill-Down Dinamis...")
            AssignmentIds = fetch_assignments_dynamic(
                session, headers, surveyPeriodsId, group_id, initial_filters,
                current_level=2, max_level=max_lvl, role=role, id_survey=id_survey, user_ids=user_ids
            )
        else:
            print(f"🚀 Mengambil daftar assignment dari {len(daftarwilayah)} wilayah...")
            max_lvl = len(level_region) if (level_region and isinstance(level_region, list)) else 6
            role = getRoles(surveyPeriodsId, headers, cookies, session)
            
            # Cari level tertinggi yang ada di daftarwilayah
            available_levels = [int(col.replace('region', '').replace('Id', '')) 
                               for col in daftarwilayah.columns if col.startswith('region') and col.endswith('Id')]
            current_row_level = max(available_levels) if available_levels else 2

            def _fetch_row_task(row):
                row_filters = {f"region{i}Id": row.get(f'region{i}Id') for i in range(1, 11)}
                row_filters['smallcode'] = row.get('smallcode')
                
                return fetch_assignments_dynamic(
                    session, headers, surveyPeriodsId, group_id, row_filters,
                    current_level=current_row_level, max_level=max_lvl, role=role, id_survey=id_survey, user_ids=user_ids
                )

            with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as executor:
                futures = {executor.submit(_fetch_row_task, row): i for i, row in daftarwilayah.iterrows()}
                for future in tqdm(as_completed(futures), total=len(futures), desc="📥 Progress"):
                    res = future.result()
                    if res:
                        AssignmentIds.extend(res)

        if not AssignmentIds:
            print("❌ Tidak ada assignment ditemukan.")
            return

        # Hilangkan duplikat berdasarkan ID
        unique_assignments = []
        seen_ids = set()
        for item in AssignmentIds:
            if item['id'] not in seen_ids and item['assignmentStatusAlias'] != 'Open':
                unique_assignments.append(item)
                seen_ids.add(item['id'])
        
        print(f"✅ {len(unique_assignments)} assignment unik ditemukan.")

        if aksi == "1":
            prelists = pd.DataFrame(unique_assignments)
            save_dir = pilih_folder_simpan("Pilih lokasi penyimpanan file Excel")
            timestamp = datetime.now().strftime("%Y%m%d")
            
            # Simpan daftar ID assignment
            assign_filename = f"{timestamp}_assignments - {nama_survey} - {surveyPeriodsName}.xlsx"
            prelists.to_excel(os.path.join(save_dir, assign_filename), index=False)
            print(f"💾 Daftar ID assignment disimpan ke: {assign_filename}")

            timestamp_full = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = os.path.join(save_dir, f"{nama_survey}_{surveyPeriodsName}_{timestamp_full}.xlsx")
            proses_semua_assignment(session, prelists, output_file, template_id=template_id, headers=headers)

        elif aksi == "2":
            approveByPML(id_survey, template_id, nama_kab, nama_survey,
                         unique_assignments, headers, cookies, session, driver)
        elif aksi == "3":
            revokeByPML(id_survey, template_id, nama_kab, nama_survey,
                        unique_assignments, headers, cookies, session, driver)
        elif aksi == "4":
            rejectByPML(id_survey, template_id, nama_kab, nama_survey,
                        unique_assignments, headers, cookies, session, driver)
    else:
        print("❌ Pilihan tidak dikenali.")


def flatten(val):
    if isinstance(val, list):
        return ', '.join([str(v.get('label', v)) if isinstance(v, dict) else str(v) for v in val])
    if isinstance(val, dict):
        return str(val)
    return val


def safe_extract(items):
    result = {}
    for item in items:
        key = item.get('dataKey')
        val = item.get('answer', None)
        result[key] = flatten(val)
    return result


def ambil_detail_assignment(session, assignment_id, mapping=None):
    url = f"{BASE_URL}/assignment-general/api/assignment/get-by-id-with-data-for-scm?id={assignment_id}"
    res = session.get(url).json()['data']

    prelist_dict = {
        'assignment_id': assignment_id,
        'code_identity': res.get('code_identity', ''),
        'longitude': res.get('longitude', ''),
        'latitude': res.get('latitude', ''),
        'current_user_username': res.get('current_user_username', ''),
        'current_user_fullname': res.get('current_user_fullname', ''),
        'current_user_survey_role_name': res.get('current_user_survey_role_name', ''),
        'assignment_status_alias': res.get('assignment_status_alias', ''),
    }
    
    # Isi data1 s/d data10 dengan nama kolom asli jika mapping tersedia
    for i in range(1, 11):
        key = f'data{i}'
        val = res.get(key, '')
        if mapping and key in mapping:
            col_name = mapping[key]
            prelist_dict[col_name] = val
        else:
            prelist_dict[key] = val

    pre_raw = json.loads(res['pre_defined_data'])['predata']
    pre_dict = safe_extract(pre_raw)
    ans_raw = json.loads(res['data'])['answers']
    ans_dict = safe_extract(ans_raw)
    pre_dict['assignment_id'] = assignment_id
    ans_dict['assignment_id'] = assignment_id

    return prelist_dict, pre_dict, ans_dict


def proses_semua_assignment(session, prelists, output_file="output.xlsx", template_id=None, headers=None):
    prelist_rows, pre_rows, ans_rows, errors = [], [], [], []
    checkpoint_file = output_file + ".checkpoint.json"
    
    # Load mapping kolom
    mapping = {}
    if template_id and headers:
        print("📥 Mengambil mapping kolom prelist...")
        mapping = get_prelist_column_mapping(template_id, session, headers)

    # Load checkpoint
    completed_ids = set()
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, 'r') as f:
                cp = json.load(f)
                prelist_rows = cp.get('prelist_rows', [])
                pre_rows = cp.get('pre_rows', [])
                ans_rows = cp.get('ans_rows', [])
                errors = cp.get('errors', [])
                completed_ids = set(cp.get('completed_ids', []))
            print(f"🔄 Resuming dari checkpoint: {len(completed_ids)} assignment selesai, {len(errors)} error.")
        except Exception as e:
            print(f"⚠️ Gagal load checkpoint: {e}")

    def _fetch_one(aid):
        try:
            return ambil_detail_assignment(session, aid, mapping=mapping)
        except Exception as e:
            return ('ERROR', aid, str(e))

    all_ids = list(prelists['id'])
    remaining_ids = [aid for aid in all_ids if aid not in completed_ids]
    
    if not remaining_ids:
        print("✅ Semua assignment sudah diproses.")
    else:
        print(f"\n🚀 Memproses {len(remaining_ids)} assignment secara paralel (max {MAX_WORKERS_WILAYAH} threads)...")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as executor:
            futures = {executor.submit(_fetch_one, aid): aid for aid in remaining_ids}
            
            for future in tqdm(as_completed(futures), desc="📥 Proses detail", total=len(futures)):
                result = future.result()
                aid = futures[future]
                
                if isinstance(result, tuple) and len(result) == 3 and result[0] == 'ERROR':
                    errors.append({'id': aid, 'error': result[2]})
                else:
                    prelist, pre, ans = result
                    prelist_rows.append(prelist)
                    pre_rows.append(pre)
                    ans_rows.append(ans)
                    completed_ids.add(aid)
                
                # Simpan checkpoint setiap 100 data
                if len(completed_ids) % 100 == 0:
                    try:
                        with open(checkpoint_file, 'w') as f:
                            json.dump({
                                'prelist_rows': prelist_rows,
                                'pre_rows': pre_rows,
                                'ans_rows': ans_rows,
                                'errors': errors,
                                'completed_ids': list(completed_ids)
                            }, f)
                    except: pass

    # Simpan ke Excel
    try:
        with pd.ExcelWriter(output_file) as writer:
            if prelist_rows: pd.DataFrame(prelist_rows).to_excel(writer, sheet_name='Prelist', index=False)
            if pre_rows: pd.DataFrame(pre_rows).to_excel(writer, sheet_name='Pre-defined', index=False)
            if ans_rows: pd.DataFrame(ans_rows).to_excel(writer, sheet_name='Answers', index=False)
            if errors: pd.DataFrame(errors).to_excel(writer, sheet_name='Errors', index=False)
        
        print(f"\n✅ Data disimpan ke: {output_file}")
        # Hapus checkpoint jika selesai semua tanpa error sisa?
        # User ingin menandai yg error, jadi biarkan saja jika masih ada error.
        if not remaining_ids or (len(completed_ids) + len(errors) == len(all_ids)):
             if os.path.exists(checkpoint_file) and not errors:
                 os.remove(checkpoint_file)
    except Exception as e:
        print(f"❌ Gagal simpan Excel: {e}")

    if errors:
        print(f"\n⚠️ {len(errors)} assignment gagal. Cek sheet 'Errors' di file output.")

    return pd.DataFrame(prelist_rows), pd.DataFrame(pre_rows), pd.DataFrame(ans_rows)


# ====================================================================
# ENTRY POINT
# ====================================================================

if __name__ == "__main__":
    driver = setup_driver()
    time.sleep(3)
    clear_screen()
    username = input("Masukkan username SSO: ")

    # Coba muat session tersimpan
    headers, cookies, session, password = muat_session(username)
    session_valid = False

    if session:
        print(f"🔄 Mencoba auto-login dengan session tersimpan untuk {username}...")
        # 1. Suntik cookies ke browser
        apply_cookies_to_driver(driver, session.cookies.get_dict(), "fasih-sm.bps.go.id")
        
        # 2. Klik Login (Halaman sudah dimuat & di-refresh oleh apply_cookies_to_driver)
        time.sleep(2)
        try:
            wait = WebDriverWait(driver, 10)
            login_btn = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="login-in"]/a[2]')))
            login_btn.click()
            print("🔗 Menunggu auto-redirect OAuth...")
            time.sleep(5)
            
            # 3. Cek apakah masuk ke dashboard atau tertahan di SSO
            if "fasih-sm.bps.go.id" in driver.current_url and "sso.bps.go.id" not in driver.current_url:
                print(f"✅ Session tersimpan valid. Auto-login berhasil!")
                # Refresh cookies/session dari keadaan browser terbaru
                cookies = get_authenticated_cookies(driver)
                session = create_resilient_session(cookies, headers)
                session.cookies.update(cookies)
                session_valid = True
            else:
                print("⚠️ Session tersimpan kadaluarsa (diarahkan ke SSO).")
        except Exception as e:
            print(f"⚠️ Gagal mencoba auto-login: {e}")

    if not session_valid:
        print("🔐 Login manual diperlukan.")
        headers, cookies, session, password = main(driver, username, password)
        simpan_session(username, headers, cookies, session, password)

    # Loop interaktif
    while True:
        try:
            main1(headers, cookies, session, driver)
            if input("\n✅ Selesai. Ulang? (Y/N): ").strip().upper() != "Y":
                break
        except Exception as e:
            print(f"\n⚠️ Error: {e}")
            if "expired" in str(e).lower() or "401" in str(e) or "403" in str(e):
                print("🔄 Session expired. Login ulang...")
                try:
                    headers, cookies, session, password = main(driver, username, password)
                    simpan_session(username, headers, cookies, session, password)
                except Exception as e2:
                    print(f"❌ Gagal: {e2}")
                    if input("Coba lagi? (Y/N): ").strip().upper() != "Y":
                        break
            else:
                if input("Coba lagi? (Y/N): ").strip().upper() != "Y":
                    break

    input("\n👋 Tekan ENTER untuk keluar...")

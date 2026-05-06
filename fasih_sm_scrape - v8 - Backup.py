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
MAX_WORKERS_WILAYAH = 10     # Jumlah thread untuk fetch wilayah
MAX_WORKERS_DETAIL = 5       # Jumlah thread untuk fetch detail (turunkan agar server tidak reset koneksi)
REQUEST_TIMEOUT = 30         # Timeout per request (detik) — dinaikkan untuk koneksi lambat
MAX_RETRIES = 5              # Jumlah retry untuk request yang gagal (dinaikkan)
MANUAL_MAX_RETRIES = 5       # Retry manual untuk ConnectionResetError
POOL_CONNECTIONS = 10        # Jumlah koneksi pool (turunkan agar tidak membanjiri server)
POOL_MAXSIZE = 10            # Ukuran maksimum pool
DETAIL_REQUEST_DELAY = 0.1   # Delay antar request detail (detik) — mencegah rate limiting
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
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver


def setup_driver_with_cookies(cookies, url='https://fasih-sm.bps.go.id') -> webdriver.Chrome:
    service = Service()
    chrome_options = Options()
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument("--disable-logging")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

    driver = webdriver.Chrome(service=service, options=chrome_options)
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
    print("\n🚀 [Parallel] Mengambil data wilayah hierarki (Kec -> Desa -> SLS -> SubSLS)...")
    
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


def fetch_assignments_dynamic(session, headers, surveyPeriodsId, group_id, region_filters, current_level=2, max_level=6, role="Admin"):
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
            
            # Tarik semua data (max 1000 sesuai limit server)
            all_collected = []
            for start_idx in range(0, total_hit, 1000):
                payload['start'] = start_idx
                payload['length'] = 1000
                payload['draw'] += 1
                r = session.post(url, headers=headers, json=payload).json()
                all_collected.extend(r.get('searchData', []))
                
                # Paging limit check
                if start_idx == 0 and len(all_collected) == 1000 and total_hit > 1000 and current_level == max_level:
                    print(f"   ⚠️ Warning: Level {current_level} memiliki {total_hit} data, melampaui limit server.")
                    break
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
            res = fetch_assignments_dynamic(session, headers, surveyPeriodsId, group_id, child_filters, current_level + 1, max_level, role)
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
# GET ALL SURVEY ANSWERS — VERSI CONCURRENT + CHECKPOINT (OPTIMIZED)
# ====================================================================

def _fetch_assignments_for_smallcode(smallCode, surveyPeriodsId, session, headers):
    """Fetch semua assignment untuk satu smallCode. Dijalankan di thread."""
    url = (f'https://fasih-sm.bps.go.id/assignment-general/api/assignments/'
           f'get-principal-values-by-smallest-code/{surveyPeriodsId}/{smallCode}')
    try:
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200 or not resp.text.strip():
            return smallCode, [], None
        data = resp.json().get('data', [])
        if not isinstance(data, list) or not data:
            return smallCode, [], None
        return smallCode, data, pd.DataFrame(data)
    except Exception as e:
        print(f"❌ Gagal ambil data untuk smallCode {smallCode}: {e}")
        return smallCode, [], None


def _fetch_assignment_detail(d, surveyPeriodsId, template_id, session, headers):
    """Fetch detail untuk satu assignment. Dijalankan di thread.
    
    Dilengkapi retry manual dengan exponential backoff untuk menangani
    ConnectionResetError yang tidak ditangkap oleh urllib3 Retry.
    """
    assignment_id = d['assignmentId']
    url_detail = (f'https://fasih-sm.bps.go.id/assignment-general/api/assignment/'
                  f'get-by-assignment-id?assignmentId={assignment_id}')
    review_url = (f'https://fasih-sm.bps.go.id/survey-collection/survey-review/'
                  f'{assignment_id}/{template_id}/{surveyPeriodsId}/a/1')

    # Retry manual dengan exponential backoff
    for attempt in range(1, MANUAL_MAX_RETRIES + 1):
        try:
            # Delay kecil antar request untuk mencegah server overload
            time.sleep(DETAIL_REQUEST_DELAY)

            resp_detail = session.get(url_detail, headers=headers, timeout=REQUEST_TIMEOUT)
            detail_json = resp_detail.json()
            detail_data = detail_json.get("data", {})

            # Ekstrak pre_defined_data (JSON string → flat dict)
            predata_values = {}
            raw_predata = detail_data.get("pre_defined_data", "")
            if raw_predata:
                try:
                    predata_json = json.loads(raw_predata)
                    for item in predata_json.get("predata", []):
                        key = item.get("dataKey", "")
                        val = item.get("answer", "")
                        if key:
                            predata_values[f"predata_{key}"] = val
                except (json.JSONDecodeError, TypeError):
                    pass

            # Ekstrak answers dari inner JSON
            inner_json = json.loads(detail_data.get("data", "{}"))
            answers = inner_json.get("answers", [])
            answer_values = extract_answers(answers)

            # Gabungkan predata + answers
            row = {}
            row.update(predata_values)     # predata_UPI, predata_UP3, dll
            row.update(answer_values)      # jawaban survei

            row['assignment_id'] = assignment_id
            row['link_preview'] = review_url

            # Ambil status & current_user dari history API (lebih reliable)
            try:
                status_assignment, current_user_username = get_last_history(
                    assignment_id, session, headers)
                row['status_assignment'] = status_assignment
                row['current_user_username'] = current_user_username
            except Exception:
                # Fallback: ambil dari detail response jika history gagal
                row['status_assignment'] = detail_data.get('assignment_status_alias', '')
                row['current_user_username'] = detail_data.get('current_user_username', '')

            row['current_user_fullname'] = detail_data.get('current_user_fullname', '')

            return row

        except (ConnectionError, requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                ConnectionResetError, OSError) as e:
            # Error koneksi — retry dengan exponential backoff
            wait_time = min(2 ** attempt, 60)  # 2s, 4s, 8s, 16s, 32s (max 60s)
            if attempt < MANUAL_MAX_RETRIES:
                print(f"⚠️ Koneksi terputus untuk {assignment_id} (percobaan {attempt}/{MANUAL_MAX_RETRIES}), "
                      f"menunggu {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"❌ Gagal ambil detail {assignment_id} setelah {MANUAL_MAX_RETRIES} percobaan: {e}")
                return None

        except Exception as e:
            print(f"⚠️ Gagal ambil detail assignment_id {assignment_id}: {e}")
            return None

    return None


def get_all_survey_answers(id_survey, template_id, nama_kab, nama_survey,
                           daftarwilayah, headers, cookies, session):
    """
    Mengambil semua jawaban survei secara CONCURRENT dengan CHECKPOINT/RESUME.
    
    Jika proses terhenti (crash, koneksi putus, dll), jalankan ulang dan proses
    akan dilanjutkan dari wilayah terakhir yang belum selesai.
    
    Optimasi:
    1. ThreadPoolExecutor untuk parallel HTTP requests
    2. Batch DataFrame construction (kumpulkan dict dulu, buat DataFrame sekali)
    3. Retry otomatis via HTTPAdapter
    4. Checkpoint/resume untuk melanjutkan scraping yang terhenti
    """

    # Ambil surveyPeriodsId
    try:
        surveyPeriodsId, surveyPeriodsName = get_survey_period(id_survey, session, headers)
    except Exception as e:
        print(f"❌ Gagal mengambil surveyPeriodsId: {e}")
        return pd.DataFrame()

    save_dir = pilih_folder_simpan("Pilih lokasi penyimpanan file Excel")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Raw_Data_{nama_kab}_{nama_survey}_{surveyPeriodsName}_{timestamp}.xlsx"
    filename2 = f"Assignment_{nama_kab}_{nama_survey}_{surveyPeriodsName}_{timestamp}.xlsx"
    filepath = os.path.join(save_dir, filename)
    filepath2 = os.path.join(save_dir, filename2)

    # ============================================================
    # CEK CHECKPOINT — Apakah ada proses sebelumnya yang terhenti?
    # ============================================================
    checkpoint_path = _get_checkpoint_path(save_dir, id_survey, surveyPeriodsId)
    checkpoint_data = _load_checkpoint(checkpoint_path)

    all_answer_rows = []       # List of dicts
    all_assignment_dfs = []    # List of DataFrames untuk assignment data
    completed_smallcodes = set()

    if checkpoint_data:
        lanjut = input("\n🔄 Lanjutkan dari checkpoint terakhir? (Y/N): ").strip().upper()
        if lanjut == 'Y':
            all_answer_rows = checkpoint_data.get('answer_rows', [])
            completed_smallcodes = set(checkpoint_data.get('completed_smallcodes', []))
            # Restore assignment data dari checkpoint
            assignment_raw = checkpoint_data.get('assignment_data', [])
            if assignment_raw:
                all_assignment_dfs = [pd.DataFrame(assignment_raw)]
            print(f"\n✅ Melanjutkan dari checkpoint: {len(completed_smallcodes)} wilayah sudah selesai, "
                  f"{len(all_answer_rows)} baris data terkumpul.")
        else:
            print("▶️ Memulai dari awal (checkpoint diabaikan).")
            _delete_checkpoint(checkpoint_path)

    start_time = time.time()
    checkpoint_lock = threading.Lock()  # Thread-safe checkpoint saving
    newly_completed = []  # Track wilayah baru yang selesai di sesi ini

    try:
        all_smallcodes = list(daftarwilayah['smallcode'])
        remaining_smallcodes = [sc for sc in all_smallcodes if sc not in completed_smallcodes]

        if not remaining_smallcodes:
            print("✅ Semua wilayah sudah selesai diproses (dari checkpoint).")
        else:
            # ============================================================
            # FASE 1: Ambil semua assignment per wilayah secara CONCURRENT
            # ============================================================
            print(f"\n🚀 Fase 1: Mengambil daftar assignment dari "
                  f"{len(remaining_smallcodes)}/{len(all_smallcodes)} wilayah (concurrent)...")

            all_new_assignments = []
            counter = 0

            with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as executor:
                futures = {
                    executor.submit(
                        _fetch_assignments_for_smallcode,
                        sc, surveyPeriodsId, session, headers
                    ): sc for sc in remaining_smallcodes
                }

                for future in tqdm(as_completed(futures), total=len(futures),
                                   desc="📥 Mengambil daftar assignment", unit="wilayah"):
                    smallCode, data, df_assign = future.result()

                    with checkpoint_lock:
                        completed_smallcodes.add(smallCode)
                        newly_completed.append(smallCode)

                        if data:
                            for d in data:
                                all_new_assignments.append(d)
                            if df_assign is not None:
                                all_assignment_dfs.append(df_assign)
                            print(f"✅ SLS '{smallCode}' | Assignment: {len(data)}")

                        counter += 1
                        # Auto-save checkpoint setiap N wilayah
                        if counter % CHECKPOINT_SAVE_INTERVAL == 0:
                            _save_checkpoint(
                                checkpoint_path,
                                list(completed_smallcodes),
                                all_answer_rows,  # jawaban dari sesi sebelumnya
                                []  # assignment data disimpan terpisah
                            )
                            print(f"💾 Checkpoint disimpan ({counter}/{len(remaining_smallcodes)} wilayah)")

            print(f"\n📊 Assignment baru ditemukan: {len(all_new_assignments)}")
            total_assignments_count = len(all_new_assignments)

            if not all_new_assignments and not all_answer_rows:
                print("⚠️ Tidak ada assignment ditemukan.")
                _delete_checkpoint(checkpoint_path)
                return pd.DataFrame()

            # ============================================================
            # FASE 2: Ambil detail setiap assignment secara CONCURRENT
            # ============================================================
            if all_new_assignments:
                print(f"\n🚀 Fase 2: Mengambil detail {len(all_new_assignments)} assignment (concurrent)...")

                detail_counter = 0
                with ThreadPoolExecutor(max_workers=MAX_WORKERS_DETAIL) as executor:
                    futures = {
                        executor.submit(
                            _fetch_assignment_detail,
                            d, surveyPeriodsId, template_id, session, headers
                        ): d['assignmentId'] for d in all_new_assignments
                    }

                    for future in tqdm(as_completed(futures), total=len(futures),
                                       desc="📥 Mengambil detail assignment", unit="assignment"):
                        result = future.result()
                        if result is not None:
                            with checkpoint_lock:
                                all_answer_rows.append(result)

                        detail_counter += 1
                        # Checkpoint setiap 50 detail
                        if detail_counter % 50 == 0:
                            with checkpoint_lock:
                                _save_checkpoint(
                                    checkpoint_path,
                                    list(completed_smallcodes),
                                    all_answer_rows,
                                    []
                                )
                            print(f"💾 Checkpoint detail disimpan ({detail_counter}/{len(all_new_assignments)})")

    except KeyboardInterrupt:
        print("\n\n⚠️ Proses dihentikan oleh user (Ctrl+C).")
        print("💾 Menyimpan checkpoint untuk dilanjutkan nanti...")
        _save_checkpoint(checkpoint_path, list(completed_smallcodes), all_answer_rows, [])
    except Exception as e:
        print("\n🚨 Terjadi error fatal saat mengambil data:")
        print(e)
        print("💾 Menyimpan checkpoint untuk dilanjutkan nanti...")
        _save_checkpoint(checkpoint_path, list(completed_smallcodes), all_answer_rows, [])
    finally:
        # Simpan data yang sempat terkumpul
        if all_answer_rows:
            try:
                df_main = pd.DataFrame(all_answer_rows)
                df_main.fillna('', inplace=True)
                df_main.to_excel(filepath, index=False)
                print(f"\n✅ Data utama ({len(all_answer_rows)} baris) disimpan ke: {filepath}")
            except Exception as e:
                print(f"⚠️ Gagal simpan data utama: {e}")
        else:
            print("⚠️ Tidak ada data 'answers' yang bisa disimpan.")

        if all_assignment_dfs:
            try:
                df_assign = pd.concat(all_assignment_dfs, ignore_index=True)
                df_assign.fillna('', inplace=True)
                df_assign.to_excel(filepath2, index=False)
                print(f"✅ Data assignment disimpan ke: {filepath2}")
            except Exception as e:
                print(f"⚠️ Gagal simpan data assignment: {e}")

        # Waktu proses
        elapsed = time.time() - start_time
        jam, sisa = divmod(elapsed, 3600)
        menit, detik = divmod(sisa, 60)
        print(f"⏱️ Proses selesai dalam {int(jam)} jam {int(menit)} menit {int(detik)} detik.")

        # Hapus checkpoint jika semua selesai tanpa error
        remaining_check = [sc for sc in list(daftarwilayah['smallcode'])
                           if sc not in completed_smallcodes]
        if not remaining_check:
            _delete_checkpoint(checkpoint_path)
            print("✅ Semua wilayah berhasil diproses. Checkpoint dihapus.")
        else:
            print(f"\n⚠️ Masih ada {len(remaining_check)} wilayah yang belum diproses.")
            print(f"   Jalankan ulang untuk melanjutkan dari checkpoint.")

    return pd.DataFrame(all_answer_rows) if all_answer_rows else pd.DataFrame()


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
            wait = WebDriverWait(driver, 30)

            wait.until(EC.presence_of_element_located((By.ID, button_id)))
            action_button = wait.until(EC.element_to_be_clickable((By.ID, button_id)))

            clicked = False
            attempt = 0
            max_attempts = 5

            while not clicked and attempt < max_attempts:
                try:
                    print(f"🔁 Mencoba klik tombol {action_type}... percobaan ke-{attempt+1}")
                    time.sleep(0.5)
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
                                 daftarwilayah, headers, cookies, session,
                                 driver, action_type, condition_checker):
    """
    Fungsi generik untuk approve/revoke/reject assignment.
    Menggantikan approveByPML, revokeByPML, rejectByPML yang sebelumnya duplikat.
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
    pilih1 = input("Ingin melakukan proses untuk semua wilayah? (Y/N): ").strip().upper()

    log_entries = []
    status_assignment_filter = ''
    start_time = time.time()

    for smallCode in tqdm(daftarwilayah['smallcode'],
                          desc=f"Memproses {action_type} data SLS", unit="Data"):
        url = (f'https://fasih-sm.bps.go.id/assignment-general/api/assignments/'
               f'get-principal-values-by-smallest-code/{surveyPeriodsId}/{smallCode}')
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

        if resp.status_code != 200 or not resp.text.strip():
            print(f"❌ Gagal mengambil data untuk smallCode {smallCode}, status_code={resp.status_code}")
            continue

        try:
            data = resp.json().get('data', [])
        except json.JSONDecodeError as e:
            print(f"❌ JSON decode error untuk smallCode {smallCode}: {e}")
            continue

        if not data:
            print(f"ℹ️ Tidak ada data isian untuk smallCode {smallCode}.")
            continue

        print("---------------------------------------------------------------------\n")
        print(f"\n📌 Ditemukan {len(data)} data isian untuk wilayah {smallCode}.")

        if pilih1 != 'Y':
            pilih2 = input("Ingin lanjut proses untuk wilayah ini? (Y/N): ").strip().upper()
            if pilih2 != "Y":
                print(f"⏭️ Melewati proses untuk {smallCode}.")
                continue

        for d in data:
            assignment_id = d['assignmentId']
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
                 daftarwilayah, headers, cookies, session, driver=None):
    process_assignments_generic(
        id_survey, template_id, nama_kab, nama_survey,
        daftarwilayah, headers, cookies, session, driver,
        action_type='approve', condition_checker=approve_condition
    )


def revokeByPML(id_survey, template_id, nama_kab, nama_survey,
                daftarwilayah, headers, cookies, session, driver=None):
    process_assignments_generic(
        id_survey, template_id, nama_kab, nama_survey,
        daftarwilayah, headers, cookies, session, driver,
        action_type='revoke', condition_checker=revoke_condition
    )


def rejectByPML(id_survey, template_id, nama_kab, nama_survey,
                daftarwilayah, headers, cookies, session, driver=None):
    process_assignments_generic(
        id_survey, template_id, nama_kab, nama_survey,
        daftarwilayah, headers, cookies, session, driver,
        action_type='reject', condition_checker=reject_condition
    )

# ====================================================================
# MAIN LOGIN FLOW
# ====================================================================

BASE_URL = "https://fasih-sm.bps.go.id"

def main(driver, username, password=None):
    """Login ke FASIH-SM via SSO. Return (headers, cookies, session, password)."""
    clear_screen()
    if not password:
        password = input("Masukkan password SSO: ")
    
    # 1. Akses FASIH langsung (satu-satunya driver.get)
    driver.get("https://fasih-sm.bps.go.id/")
    time.sleep(3)
    
    # 2. Klik tombol Login (OAuth redirect ke SSO)
    try:
        wait = WebDriverWait(driver, 15)
        login_btn = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="login-in"]/a[2]')))
        login_btn.click()
        print("🔗 Redirect ke SSO BPS...")
        time.sleep(3)
    except Exception as e:
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



def main1(headers, cookies, session, driver):
    """Pilih survey, wilayah, dan jalankan scraping/approve/revoke/reject."""
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
    daftarwilayah = ambil_semua_sls_parallel(
        id_kab, level_region, group_id, headers, cookies,
        region_level1=region_level1, region_level2=region_level2
    )

    clear_screen()
    print(f"✅ {len(daftarwilayah)} unit wilayah teridentifikasi.")

    # Menu
    print("\n=== Pilih tindakan ===")
    print("1. Ambil Raw Data (Scrape)")
    print("2. Approve Assignment")
    print("3. Revoke Assignment")
    print("4. Reject Assignment")
    aksi = input("Pilihan (1/2/3/4): ").strip()

    if aksi == "1":
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
            current_level=2, max_level=max_lvl, role=role
        )

        if not AssignmentIds:
            print("❌ Tidak ada assignment ditemukan.")
            return

        prelists = pd.DataFrame(AssignmentIds).drop_duplicates(subset=['id'])
        print(f"✅ {len(prelists)} assignment unik.")

        save_dir = pilih_folder_simpan("Pilih lokasi penyimpanan file Excel")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(save_dir, f"{nama_survey}_{surveyPeriodsName}_{timestamp}.xlsx")
        proses_semua_assignment(session, prelists, output_file)

    elif aksi == "2":
        approveByPML(id_survey, template_id, nama_kab, nama_survey,
                     daftarwilayah, headers, cookies, session, driver)
    elif aksi == "3":
        revokeByPML(id_survey, template_id, nama_kab, nama_survey,
                    daftarwilayah, headers, cookies, session, driver)
    elif aksi == "4":
        rejectByPML(id_survey, template_id, nama_kab, nama_survey,
                    daftarwilayah, headers, cookies, session, driver)
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


def ambil_detail_assignment(session, assignment_id):
    url = f"{BASE_URL}/assignment-general/api/assignment/get-by-id-with-data-for-scm?id={assignment_id}"
    res = session.get(url).json()['data']

    prelist_dict = {
        'assignment_id': assignment_id,
        'code_identity': res.get('code_identity', ''),
        'data1': res.get('data1', ''), 'data2': res.get('data2', ''),
        'data3': res.get('data3', ''), 'data4': res.get('data4', ''),
        'data5': res.get('data5', ''), 'data6': res.get('data6', ''),
        'data7': res.get('data7', ''), 'data8': res.get('data8', ''),
        'data9': res.get('data9', ''), 'data10': res.get('data10', ''),
        'longitude': res.get('longitude', ''),
        'latitude': res.get('latitude', ''),
        'current_user_username': res.get('current_user_username', ''),
        'current_user_fullname': res.get('current_user_fullname', ''),
        'current_user_survey_role_name': res.get('current_user_survey_role_name', ''),
        'assignment_status_alias': res.get('assignment_status_alias', ''),
    }

    pre_raw = json.loads(res['pre_defined_data'])['predata']
    pre_dict = safe_extract(pre_raw)
    ans_raw = json.loads(res['data'])['answers']
    ans_dict = safe_extract(ans_raw)
    pre_dict['assignment_id'] = assignment_id
    ans_dict['assignment_id'] = assignment_id

    return prelist_dict, pre_dict, ans_dict


def proses_semua_assignment(session, prelists, output_file="output.xlsx"):
    prelist_rows, pre_rows, ans_rows, errors = [], [], [], []

    def _fetch_one(aid):
        try:
            return ambil_detail_assignment(session, aid)
        except Exception as e:
            return ('ERROR', aid, str(e))

    ids = list(prelists['id'])
    print(f"\n🚀 Memproses {len(ids)} assignment secara paralel (max {MAX_WORKERS_WILAYAH} threads)...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_WILAYAH) as executor:
        futures = {executor.submit(_fetch_one, aid): aid for aid in ids}
        for future in tqdm(futures, desc="📥 Proses detail", total=len(futures)):
            result = future.result()
            if isinstance(result, tuple) and len(result) == 3 and result[0] == 'ERROR':
                errors.append(result[1])
                continue
            prelist, pre, ans = result
            prelist_rows.append(prelist)
            pre_rows.append(pre)
            ans_rows.append(ans)

    if errors:
        print(f"\n⚠️ {len(errors)} assignment gagal.")

    df_prelist = pd.DataFrame(prelist_rows)
    df_pre = pd.DataFrame(pre_rows)
    df_ans = pd.DataFrame(ans_rows)

    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        df_prelist.to_excel(writer, sheet_name='Prelist', index=False)
        df_pre.to_excel(writer, sheet_name='pre_defined_data', index=False)
        df_ans.to_excel(writer, sheet_name='answers', index=False)

    print(f"✅ Export selesai: {output_file} ({len(df_prelist)} prelist, {len(df_pre)} pre, {len(df_ans)} answers)")
    return df_prelist, df_pre, df_ans


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
        
        # 2. Akses landing page dan klik Login
        driver.get("https://fasih-sm.bps.go.id/")
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

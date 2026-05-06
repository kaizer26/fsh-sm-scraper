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
            'password_encoded': True,  # marker bahwa password sudah di-encode
            'headers': headers,
            'cookies': cookies,
            'session': session
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
            return data.get('headers'), data.get('cookies'), data.get('session'), password
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
    url = f'https://fasih-sm.bps.go.id/survey/api/v1/users/myinfo?surveyPeriodId={surveyPeriodeId}'
    resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    surveyRole = resp.json()['data']['surveyRole']['description']
    return surveyRole


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

def ambil_semua_sls_smallcode_dari_kabupaten(kabupaten_id, level_region, region_group_id,
                                              headers, cookies, region_level1, region_level2):
    print("\n=== Mengambil semua kecamatan, desa, dan sls dari kabupaten berdasarkan ID ===")

    if not isinstance(level_region, list) or len(level_region) < 3:
        if len(level_region) == 2:
            print("❌ Regionlevel Hanya Sampai Kabupaten.")
            return pd.DataFrame([region_level2])
        elif len(level_region) == 1:
            print("❌ Regionlevel Hanya Sampai Provinsi.")
            return pd.DataFrame([region_level1])
        else:
            print("❌ Regionlevel tidak valid.")
            return pd.DataFrame()

    result = []

    try:
        url_kecamatan = f"https://fasih-sm.bps.go.id/region/api/v1/region/level3?groupId={region_group_id}&level2Id={kabupaten_id}"
        resp_kec = requests.get(url_kecamatan, headers=headers, cookies=cookies, timeout=REQUEST_TIMEOUT)
        resp_kec.raise_for_status()
        daftar_kecamatan = resp_kec.json().get('data', [])
    except Exception as e:
        print(f"❌ Gagal mengambil data kecamatan: {e}")
        return pd.DataFrame()

    for kec in daftar_kecamatan:
        kecamatan_id = kec['id']
        kecamatan_name = kec['name']
        kecamatan_kode = kec['fullCode']
        print(f"📍 Kecamatan: {kecamatan_name}")

        if len(level_region) == 3:
            result.append({
                f'{level_region[2]["name"]}_id': kecamatan_id,
                f'{level_region[2]["name"]}': kecamatan_name,
                'smallcode': kecamatan_kode,
            })
            continue

        try:
            url_desa = f"https://fasih-sm.bps.go.id/region/api/v1/region/level4?groupId={region_group_id}&level3Id={kecamatan_id}"
            resp_desa = requests.get(url_desa, headers=headers, cookies=cookies, timeout=REQUEST_TIMEOUT)
            resp_desa.raise_for_status()
            daftar_desa = resp_desa.json().get('data', [])
        except Exception as e:
            print(f"❌ Gagal mengambil desa dari {kecamatan_name}: {e}")
            continue

        for desa in daftar_desa:
            desa_id = desa['id']
            desa_name = desa['name']
            desa_kode = desa['fullCode']
            print(f"  🏘️ Desa: {desa_name}")

            if len(level_region) == 4:
                result.append({
                    f'{level_region[2]["name"]}_id': kecamatan_id,
                    f'{level_region[2]["name"]}': kecamatan_name,
                    f'{level_region[3]["name"]}_id': desa_id,
                    f'{level_region[3]["name"]}': desa_name,
                    'smallcode': desa_kode,
                })
                continue

            try:
                url_sls = f"https://fasih-sm.bps.go.id/region/api/v1/region/level5?groupId={region_group_id}&level4Id={desa_id}"
                resp_sls = requests.get(url_sls, headers=headers, cookies=cookies, timeout=REQUEST_TIMEOUT)
                resp_sls.raise_for_status()
                daftar_sls = resp_sls.json().get('data', [])
            except Exception as e:
                print(f"❌ Gagal mengambil SLS dari {desa_name}: {e}")
                continue

            for sls in daftar_sls:
                sls_id = sls['id']
                sls_name = sls['name']
                sls_kode = sls['fullCode']
                print(f"    🧾 SLS: {sls_name} ({sls_kode})")

                if len(level_region) == 5:
                    result.append({
                        f'{level_region[2]["name"]}_id': kecamatan_id,
                        f'{level_region[2]["name"]}': kecamatan_name,
                        f'{level_region[3]["name"]}_id': desa_id,
                        f'{level_region[3]["name"]}': desa_name,
                        f'{level_region[4]["name"]}_id': sls_id,
                        f'{level_region[4]["name"]}': sls_name,
                        'smallcode': sls_kode,
                    })
                    continue

                try:
                    url_subsls = f"https://fasih-sm.bps.go.id/region/api/v1/region/level6?groupId={region_group_id}&level5Id={sls_id}"
                    resp_subsls = requests.get(url_subsls, headers=headers, cookies=cookies, timeout=REQUEST_TIMEOUT)
                    resp_subsls.raise_for_status()
                    daftar_subsls = resp_subsls.json().get('data', [])
                except Exception as e:
                    print(f"❌ Gagal mengambil SUB SLS dari {sls_name}: {e}")
                    continue

                for subsls in daftar_subsls:
                    subsls_id = subsls['id']
                    subsls_name = subsls['name']
                    subsls_kode = subsls['fullCode']
                    print(f"        🧾 SubSLS: {subsls_name} ({subsls_kode})")

                    result.append({
                        f'{level_region[2]["name"]}_id': kecamatan_id,
                        f'{level_region[2]["name"]}': kecamatan_name,
                        f'{level_region[3]["name"]}_id': desa_id,
                        f'{level_region[3]["name"]}': desa_name,
                        f'{level_region[4]["name"]}_id': sls_id,
                        f'{level_region[4]["name"]}': sls_name,
                        f'{level_region[5]["name"]}_id': subsls_id,
                        f'{level_region[5]["name"]}': subsls_name,
                        'smallcode': subsls_kode,
                    })

    if not result:
        print("⚠️ Tidak ada data yang berhasil diambil.")
    return pd.DataFrame(result)


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
# MAIN FLOW
# ====================================================================

def main(driver, username, password=None):
    res = 'N'
    while res.upper() != 'Y':
        res = input("Apakah sudah konek VPN? (Y/N): ")

    clear_screen()

    print("✅ Pastikan package berikut sudah terinstal:")
    for pkg in [
        "time", "urllib.parse", "datetime", "os", "getpass", "tkinter",
        "tqdm", "pandas", "requests", "json", "selenium", "platform", "http"
    ]:
        print(f"- {pkg}")
    input("Apakah semua package di atas sudah terinstall? Tekan ENTER untuk lanjut...")

    time.sleep(3)
    clear_screen()
    if not password:
        password = input("Masukkan password SSO: ")

    login_sso(driver, username, password)

    # Login ke FASIH
    driver.get("https://fasih-sm.bps.go.id/oauth2/authorization/ics")
    time.sleep(5)

    driver.get("https://fasih-sm.bps.go.id/survey-collection/survey")
    time.sleep(3)

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

    # Gunakan resilient session dengan retry dan connection pooling
    session = create_resilient_session(cookies, headers)
    session.cookies.update(cookies)

    print("Berhasil Login!")
    return headers, cookies, session, password


def main1(headers, cookies, session, driver):
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

    # Ambil regionGroupId dari metadata survei
    url_group = f"https://fasih-sm.bps.go.id/survey/api/v1/surveys/{id_survey}"
    resp_group = session.get(url_group, headers=headers, timeout=REQUEST_TIMEOUT)
    group_id = resp_group.json()['data']['regionGroupId']
    template_id = resp_group.json()['data']['surveyTemplates'][-1]['templateId']

    # Ambil Region Level
    url_level_region = f'https://fasih-sm.bps.go.id/region/api/v1/region-metadata?id={group_id}'
    resp_level_region = session.get(url_level_region, headers=headers, timeout=REQUEST_TIMEOUT)
    level_region = resp_level_region.json()['data']['level']

    # Ambil daftar provinsi
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

    # Ambil kabupaten
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
    name_kab = data_kab['data'][pilihan_kab - 1]['name']

    region_level1 = {'id': id_prov, 'fullCode': fullcode_prov, 'code': code_prov,
                     'name': name_prov, 'smallcode': fullcode_prov}
    region_level2 = {'id': id_kab, 'fullCode': fullcode_kab, 'code': code_kab,
                     'name': name_kab, 'smallcode': fullcode_kab}

    clear_screen()
    input("Sekarang akan memilih file daftarwilayah yang sebelumnya pernah diambil, "
          "klik tombol esc jika tidak ingin melanjutkan")
    file_dir = pilih_file()

    if file_dir and os.path.exists(file_dir):
        print("📥 Menggunakan file yang dipilih sebagai daftarwilayah...")
        daftarwilayah = pd.read_excel(file_dir)
    else:
        print("🌐 Mengambil daftarwilayah dari server (API)...")
        daftarwilayah = ambil_semua_sls_smallcode_dari_kabupaten(
            id_kab, level_region, group_id, headers, cookies,
            region_level1=region_level1, region_level2=region_level2
        )
        save_dir = pilih_folder_simpan("Pilih lokasi penyimpanan file Excel")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Daftar_wilayah_kabupaten_{fullcode_kab}_{nama_survey}_{timestamp}.xlsx"
        filepath = os.path.join(save_dir, filename)

        df = pd.DataFrame(daftarwilayah)
        df.to_excel(filepath, index=False)
        print(f"✅ Data berhasil disimpan ke '{filepath}'")
        time.sleep(2)

    # TAMPILKAN MENU OPSI LANJUTAN
    clear_screen()
    print("\n=== Pilih tindakan lanjutan ===")
    print("1. Ambil Raw Data")
    print("2. Approve Assignment")
    print("3. Revoke Assignment")
    print("4. Reject Assignment")
    aksi = input("Masukkan pilihan (1 / 2 / 3 / 4): ").strip()

    if aksi == "1":
        get_all_survey_answers(
            nama_kab=fullcode_kab,
            template_id=template_id,
            nama_survey=nama_survey,
            id_survey=id_survey,
            daftarwilayah=daftarwilayah,
            headers=headers,
            cookies=cookies,
            session=session
        )
    elif aksi == "2":
        approveByPML(
            id_survey=id_survey, template_id=template_id,
            nama_kab=nama_kab, nama_survey=nama_survey,
            daftarwilayah=daftarwilayah,
            headers=headers, cookies=cookies, session=session,
            driver=driver
        )
    elif aksi == "3":
        revokeByPML(
            id_survey=id_survey, template_id=template_id,
            nama_kab=nama_kab, nama_survey=nama_survey,
            daftarwilayah=daftarwilayah,
            headers=headers, cookies=cookies, session=session,
            driver=driver
        )
    elif aksi == "4":
        rejectByPML(
            id_survey=id_survey, template_id=template_id,
            nama_kab=nama_kab, nama_survey=nama_survey,
            daftarwilayah=daftarwilayah,
            headers=headers, cookies=cookies, session=session,
            driver=driver
        )
    else:
        print("❌ Pilihan tidak dikenali. Tidak ada aksi yang dilakukan.")


if __name__ == "__main__":
    driver = setup_driver()
    time.sleep(3)
    clear_screen()
    username = input("Masukkan username SSO: ")

    headers, cookies, session, password = muat_session(username)

    if session:
        print(f"🔄 Menggunakan session tersimpan untuk {username}")
        apply_cookies_to_driver(driver, session.cookies.get_dict(), "sso.bps.go.id")
        apply_cookies_to_driver(driver, session.cookies.get_dict(), "fasih-sm.bps.go.id")
        driver.get("https://fasih-sm.bps.go.id/survey-collection/survey")
        time.sleep(2)

        # Cek session valid — tunggu lebih lama dan gunakan pengecekan yang lebih toleran
        time.sleep(3)
        session_valid = False
        try:
            print("Cek session valid...")
            current = driver.current_url
            print(f"  URL saat ini: {current}")

            # Cek apakah masih di domain fasih (bukan redirect ke SSO login)
            if 'fasih-sm.bps.go.id' in current and 'sso.bps.go.id' not in current:
                # Double check: coba API call juga
                if is_session_valid(session):
                    session_valid = True
                    print("✅ Session masih valid (URL + API OK)")
                else:
                    # Browser OK tapi API session mungkin beda — rebuild session dari browser cookies
                    print("⚠️ Browser OK tapi API session expired. Mengambil cookies baru dari browser...")
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
                    session_valid = True
                    print("✅ Session berhasil diperbarui dari browser cookies")
                    simpan_session(username, headers, cookies, session, password)
            else:
                print(f"❌ Redirect ke halaman login SSO — session expired")

        except Exception as e:
            print(f"⚠️ Error saat cek session: {e}")

        if not session_valid:
            print("🔐 Session lama tidak valid. Login ulang diperlukan.")
            headers, cookies, session, password = main(driver, username, password)
            simpan_session(username, headers, cookies, session, password)
    else:
        print("🔐 Tidak ada session tersimpan. Login diperlukan.")
        headers, cookies, session, password = main(driver, username, password)
        simpan_session(username, headers, cookies, session, password)

    jalankan = True
    while jalankan:
        try:
            main1(headers, cookies, session, driver)
            ulang = input("\n✅ Proses selesai. Apakah ingin mengulang? (Y/N): ").strip().upper()
            if ulang != "Y":
                break

        except Exception as e:
            print(f"\n⚠️ Terjadi kesalahan: {e}")

            if "expired" in str(e).lower() or "401" in str(e) or "403" in str(e):
                print("🔄 Session kemungkinan expired. Mencoba login ulang otomatis...")
                try:
                    headers, cookies, session, password = main(driver, username, password)
                    simpan_session(username, headers, cookies, session, password)
                    print("✅ Session berhasil diperbarui. Melanjutkan proses...")
                except Exception as e2:
                    print(f"❌ Gagal login ulang: {e2}")
                    ulang = input("Coba lagi? (Y/N): ").strip().upper()
                    if ulang != "Y":
                        break
            else:
                ulang = input("Apakah ingin mencoba lagi? (Y/N): ").strip().upper()
                if ulang != "Y":
                    break

    input("\n👋 Terima kasih. Tekan ENTER untuk keluar...")

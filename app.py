import streamlit as st
import sqlite3
import os
import hashlib
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict
import re
import requests

st.set_page_config(
    page_title="패스파인더 과제 관리",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
SUPER_ADMIN_PASSWORD = "pathfinder2024"

def name_to_code(name):
    h = 5381
    for ch in name.strip():
        h = ((h * 33) ^ ord(ch)) & 0xFFFFFFFF
    return str((h % 900000) + 100000)

def verify_code(name, code):
    return name_to_code(name.strip()) == code.strip()

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def create_parent_account(student):
    """학생 등록/동기화 시 학부모 계정 생성. 비밀번호는 항상 학번으로 유지."""
    username  = str(student["student_code"]) + "p"
    pw_hash   = hash_pw(str(student["student_code"]))
    parent_phone = student.get("parent_phone") or ""
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO parents (username, password_hash, parent_phone)
            VALUES (?,?,?)
            ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash,
                parent_phone  = excluded.parent_phone
        """, (username, pw_hash, parent_phone))
        conn.commit()
    finally:
        conn.close()

def sync_all_parent_accounts():
    """기존 학생 전체 학부모 계정 생성 + 비밀번호 학번으로 동기화."""
    try:
        conn = get_db()
        students = conn.execute("SELECT * FROM students").fetchall()
        conn.close()
        for s in students:
            create_parent_account(dict(s))
    except Exception:
        pass

def render_timetable_html(tt_dict, pt_map, days, periods, highlight_fn=None):
    """모바일 대응 HTML 테이블 시간표 렌더러.
    tt_dict: {(day, period): row} 또는 {(day, period): [row, ...]}
    highlight_fn(cell): True면 초록 강조, None이면 기본 파랑
    """
    cell_style  = "background:#1e3a5f;border-radius:6px;padding:8px 6px;text-align:center;font-size:0.82rem;"
    hl_style    = "background:#1a4a2a;border:2px solid #22c55e;border-radius:6px;padding:8px 6px;text-align:center;font-size:0.82rem;"
    empty_style = "background:#0d1117;border-radius:6px;padding:8px 6px;text-align:center;color:#334155;font-size:0.82rem;"

    html = """
    <style>
    .tt-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
    .tt-table { border-collapse: separate; border-spacing: 4px; min-width: 480px; width: 100%; }
    .tt-table th { background:#1e293b; color:#94a3b8; font-size:0.8rem; padding:8px 4px; text-align:center; border-radius:6px; }
    .tt-period { background:#111827; border-left:3px solid #3b82f6; border-radius:4px; padding:8px 6px; min-width:72px; }
    .tt-period b { font-size:0.9rem; }
    .tt-time { font-size:0.75rem; color:#93c5fd; font-weight:500; display:block; margin-top:2px; }
    .tt-sub { font-weight:bold; display:block; }
    .tt-teacher { color:#94a3b8; font-size:0.72rem; display:block; margin-top:2px; }
    .tt-class { font-weight:bold; display:block; }
    </style>
    <div class="tt-wrap"><table class="tt-table">
    <thead><tr><th>교시</th>"""
    for d in days:
        html += f"<th>{d}요일</th>"
    html += "</tr></thead><tbody>"

    for p in periods:
        pt = pt_map.get(p)
        time_str = ""
        if pt and pt["start_time"]:
            time_str = f"{pt['start_time']}~{pt['end_time']}" if pt["end_time"] else pt["start_time"]
        time_html = f'<span class="tt-time">{time_str}</span>' if time_str else ""
        html += f'<tr><td><div class="tt-period"><b>{p}교시</b>{time_html}</div></td>'

        for d in days:
            val = tt_dict.get((d, p))
            if val is None:
                html += f'<td><div style="{empty_style}">—</div></td>'
            elif isinstance(val, list):
                # 여러 반 (선생님 시간표)
                inner = ""
                for c in val:
                    is_hl = highlight_fn(c) if highlight_fn else False
                    s = hl_style if is_hl else cell_style
                    cls_label = f'<span class="tt-class">{c["grade"]} {c["class_name"]}</span>'
                    sub_label = f'<span class="tt-teacher">{c["subject"]}</span>'
                    inner += f'<div style="{s};margin-bottom:3px;">{cls_label}{sub_label}</div>'
                html += f'<td>{inner}</td>'
            else:
                # 단일 셀 (학생/학부모 시간표)
                is_hl = highlight_fn(val) if highlight_fn else False
                s = hl_style if is_hl else cell_style
                sub  = val["subject"] if val["subject"] else "—"
                tchr = val["teacher_name"] if val["teacher_name"] else ""
                html += f'<td><div style="{s}"><span class="tt-sub">{sub}</span><span class="tt-teacher">{tchr}</span></div></td>'
        html += "</tr>"

    html += "</tbody></table></div>"
    return html

def youtube_embed_url(url):
    for p in [r"youtube\.com/watch\?v=([a-zA-Z0-9_-]+)",
              r"youtu\.be/([a-zA-Z0-9_-]+)",
              r"youtube\.com/embed/([a-zA-Z0-9_-]+)"]:
        m = re.search(p, url)
        if m:
            return f"https://www.youtube.com/embed/{m.group(1)}"
    return url

GRADE_LIST = ["초1","초2","초3","초4","초5","초6","중1","중2","중3","고1","고2","고3","일반"]
GRADE_ORDER = {g: i for i, g in enumerate(GRADE_LIST)}

def calc_current_grade(base_grade: str, enrollment_year: int) -> str:
    """등록 연도 기준으로 현재 학년 자동 계산. 일반/None이면 그대로 반환."""
    if not base_grade or not enrollment_year or base_grade == "일반":
        return base_grade or "일반"
    current_year = date.today().year
    years_passed = current_year - enrollment_year
    base_idx = GRADE_ORDER.get(base_grade, 0)
    new_idx = base_idx + years_passed
    if new_idx >= len(GRADE_LIST) - 1:  # 고3 이후는 일반
        return "일반"
    return GRADE_LIST[new_idx]

def send_aligo_sms(receivers: list, message: str, sender: str = None) -> dict:
    """알리고 API로 문자 전송. receivers = ['010-xxxx-xxxx', ...]"""
    try:
        api_key  = st.secrets.get("ALIGO_API_KEY", "")
        user_id  = st.secrets.get("ALIGO_USER_ID", "")
        sender   = sender or st.secrets.get("ALIGO_SENDER", "")
        if not all([api_key, user_id, sender]):
            return {"result_code": -99, "message": "알리고 API 설정이 없습니다. Streamlit Secrets를 확인하세요."}
        # 번호 정리 (하이픈 제거)
        cleaned = [r.replace("-", "").strip() for r in receivers if r]
        resp = requests.post("https://apis.aligo.in/send/", data={
            "key":      api_key,
            "user_id":  user_id,
            "sender":   sender.replace("-", ""),
            "receiver": ",".join(cleaned),
            "msg":      message,
            "msg_type": "SMS" if len(message) <= 90 else "LMS",
            "title":    "패스파인더 국어학원" if len(message) > 90 else "",
        }, timeout=10)
        return resp.json()
    except Exception as e:
        return {"result_code": -1, "message": str(e)}

def get_db():
    conn = sqlite3.connect("homework.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            subject TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            student_code TEXT UNIQUE NOT NULL,
            grade TEXT NOT NULL,
            class_name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            grade TEXT NOT NULL,
            class_name TEXT NOT NULL,
            due_date TEXT,
            teacher_id INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            assignment_id INTEGER NOT NULL,
            file_path TEXT,
            memo TEXT,
            submitted_at TEXT DEFAULT (datetime('now','localtime')),
            is_checked INTEGER DEFAULT 0,
            checked_at TEXT,
            teacher_comment TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (assignment_id) REFERENCES assignments(id),
            UNIQUE(student_id, assignment_id)
        );
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            teacher_id INTEGER,
            subject TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            image_paths TEXT,
            answer TEXT,
            is_answered INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            answered_at TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        );
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            youtube_url TEXT NOT NULL,
            grade TEXT NOT NULL,
            class_name TEXT NOT NULL,
            category TEXT DEFAULT '기본',
            teacher_id INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        );
    """)
    conn.commit()
    # 마이그레이션: 기존 DB에 없는 컬럼 추가
    for sql in [
        "ALTER TABLE videos ADD COLUMN category TEXT DEFAULT '기본'",
        "ALTER TABLE videos ADD COLUMN teacher_id INTEGER",
        "ALTER TABLE assignments ADD COLUMN teacher_id INTEGER",
        "ALTER TABLE questions ADD COLUMN image_paths TEXT",
        "ALTER TABLE students ADD COLUMN phone TEXT",
        "ALTER TABLE students ADD COLUMN parent_name TEXT",
        "ALTER TABLE students ADD COLUMN parent_phone TEXT",
        "ALTER TABLE students ADD COLUMN school TEXT",
        "ALTER TABLE students ADD COLUMN enrollment_year INTEGER",
        "ALTER TABLE students ADD COLUMN base_grade TEXT",
        """CREATE TABLE IF NOT EXISTS parents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            parent_phone TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
        """CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notice_type TEXT NOT NULL,
            teacher_id INTEGER,
            grade TEXT,
            class_name TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
        """CREATE TABLE IF NOT EXISTS student_teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            teacher_id INTEGER NOT NULL,
            subject TEXT,
            UNIQUE(student_id, teacher_id)
        )""",
        """CREATE TABLE IF NOT EXISTS period_times (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grade TEXT NOT NULL,
            class_name TEXT NOT NULL,
            period INTEGER NOT NULL,
            start_time TEXT,
            end_time TEXT,
            UNIQUE(grade, class_name, period)
        )""",
        """CREATE TABLE IF NOT EXISTS timetable (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grade TEXT NOT NULL,
            class_name TEXT NOT NULL,
            day TEXT NOT NULL,
            period INTEGER NOT NULL,
            subject TEXT,
            teacher_name TEXT,
            room TEXT,
            UNIQUE(grade, class_name, day, period)
        )""",
        """CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grade TEXT NOT NULL,
            class_name TEXT NOT NULL,
            event_date TEXT NOT NULL,
            start_time TEXT,
            end_time TEXT,
            title TEXT NOT NULL,
            description TEXT,
            teacher_name TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""",
        """CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            teacher_id INTEGER,
            subject TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            answer TEXT,
            is_answered INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            answered_at TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        )""",
    ]:
        try:
            c.execute(sql)
            conn.commit()
        except:
            pass
    conn.close()

init_db()

def save_uploaded_file(f, student_id, assignment_id, idx=0):
    ext = Path(f.name).suffix
    filename = f"s{student_id}_a{assignment_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{idx}{ext}"
    path = UPLOAD_DIR / filename
    with open(path, "wb") as out:
        out.write(f.read())
    return str(path)

def save_multiple_files(files, student_id, assignment_id):
    return "|".join([save_uploaded_file(f, student_id, assignment_id, i) for i, f in enumerate(files)])

def get_grades():
    conn = get_db()
    rows = conn.execute("SELECT DISTINCT grade FROM students ORDER BY grade").fetchall()
    conn.close()
    return [r["grade"] for r in rows]

def get_classes(grade=None):
    conn = get_db()
    if grade:
        rows = conn.execute("SELECT DISTINCT class_name FROM students WHERE grade=? ORDER BY class_name", (grade,)).fetchall()
    else:
        rows = conn.execute("SELECT DISTINCT class_name FROM students ORDER BY class_name").fetchall()
    conn.close()
    return [r["class_name"] for r in rows]

for key in ["role","student_id","student_info","teacher_id","teacher_info","parent_id","parent_info","pending_register","admin_selected_student"]:
    if key not in st.session_state:
        st.session_state[key] = None

# 기존 학생 전체 학부모 계정 동기화 (앱 시작 시 1회)
sync_all_parent_accounts()

st.markdown(
    f"<div style='text-align:center;padding:10px 0 0 0;'>"
    f"<img src='data:image/png;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAfQB9ADASIAAhEBAxEB/8QAHQABAAIDAQEBAQAAAAAAAAAAAAYIBAUHCQEDAv/EAGMQAQABAgMCAw4QCAsHAwQCAwABAgMEBQYHEQgSMRYhN0FRVmFxgZWxsrPSExQXMjU2VHJzdHWRk6Gk0yIjQlKDlKLBFTM0YmRlgpKjwtEkJSZDU2OEGCdERUbD4VXi8OO0/8QAHAEBAAEFAQEAAAAAAAAAAAAAAAcBAgMEBgUI/8QASREBAAECAQYHDAgFAwUBAQEAAAECAwQFBhE0kbESFjFTcXLRFBchMjNRUmFkgcHiFTVBkqGio+ETInOy8CMkQgclQ4LxYsLS/9oADAMBAAIRAxEAPwCmQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA2mntPZ3qHE+l8lyzE42uJ3VTbo/Bp99VPOp7sw6zpXYHjL1NF7UubU4WJ584fCRx6+7XPOie1FTRxeU8LhPK1xE+blnY9HBZKxeNn/RomY8/JG2XEn6WLN6/c9DsWrl2ufyaKZqn5oW1yHZbobKKYm3kdnF3I5bmMmb0z3KvwY7kQl2EwuGwlqLWEw1nD245KbVEUx80OevZ3WqZ0Wrcz0zo7XS2Myr1Uab12I6I09imOH0nqnEUcfD6azm9T+dRgbtUfVS/XmL1h1qZ73vu+aueNOc7r32W42y3ozKsfbdnZCmHMXrDrUz3vfd805i9Ydame977vmrninG69zcbZV4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9733fNOYvWHWpnve+75q54cbr3Nxtk4lWOdnZCmHMXrDrUz3vfd805i9Ydame977vmrnhxuvc3G2TiVY52dkKYcxesOtTPe993zTmL1h1qZ73vu+aueHG69zcbZOJVjnZ2QphzF6w61M9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9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7zTmC111l6j713vNDumz6cbYRwSPmC111l6j713vNOYLXXWXqPvXe80O6bPpxthHBI+YLXXWXqPvXe805gtddZeo+9d7zQ7ps+nG2EcEj5gtddZeo+9d7z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M914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24jnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4z3XiPpJY4d24nnKtsn0XguZp+7HYyPTuM914j6ST07jPdeI+kljh3biecq2yfReC5mn7sdjI9O4z3XiPpJPTuM914j6SWOHduJ5yrbJ9F4Lmafux2Mj07jPdeI+kk9O4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AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD/2Q==' style='max-width:280px;width:60%;'>"
    f"</div>",
    unsafe_allow_html=True)
st.markdown("""
<style>
div[data-testid="stRadio"] > div { display: flex; flex-direction: column; gap: 6px; }
div[data-testid="stRadio"] label { padding: 6px 10px !important; border-radius: 6px; }
</style>
""", unsafe_allow_html=True)

import streamlit.components.v1 as _components
_components.html("""
<script>
(function() {
    function closeSidebar() {
        try {
            var btn = window.parent.document.querySelector('[data-testid="stSidebarCollapseButton"]');
            if (btn && window.parent.innerWidth < 768) btn.click();
        } catch(e) {}
    }
    window.parent.document.addEventListener('click', function(e) {
        var label = e.target.closest('div[data-testid="stRadio"] label');
        if (label) setTimeout(closeSidebar, 200);
    }, true);
})();
</script>
""", height=0)
st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# 로그인
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.role is None:

    if st.session_state.pending_register is not None:
        info = st.session_state.pending_register
        st.info(f"✅ {info['name']} 학생 확인! 학년과 반을 입력해주세요. (최초 1회)")
        with st.form("register_form"):
            col1, col2 = st.columns(2)
            grade = col1.selectbox("학년", ["중1","중2","중3","고1","고2","고3"])
            class_name = col2.selectbox("반", ["A반","B반","C반","D반"])
            if st.form_submit_button("등록 완료 ✅", type="primary", use_container_width=True):
                conn = get_db()
                try:
                    conn.execute("INSERT INTO students (name, student_code, grade, class_name) VALUES (?,?,?,?)",
                                 (info["name"], info["code"], grade, class_name))
                    conn.commit()
                    row = conn.execute("SELECT * FROM students WHERE student_code=?", (info["code"],)).fetchone()
                    st.session_state.role = "student"
                    st.session_state.student_id = row["id"]
                    st.session_state.student_info = dict(row)
                    st.session_state.pending_register = None
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("이미 등록된 학번입니다.")
                finally:
                    conn.close()
        if st.button("← 뒤로"):
            st.session_state.pending_register = None
            st.rerun()
        st.stop()

    # 로그인 유형 선택 상태
    if "login_type" not in st.session_state:
        st.session_state.login_type = None

    if st.session_state.login_type is None:
        # ── 유형 선택 화면 ──────────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("<h3 style='text-align:center;margin-bottom:32px;'>어떤 분이신가요?</h3>", unsafe_allow_html=True)

        _, mid, _ = st.columns([1, 2, 1])
        with mid:
            if st.button("🎒  학생이신가요?", use_container_width=True, key="sel_student"):
                st.session_state.login_type = "student"
                st.rerun()
            if st.button("👩‍🏫  선생님이신가요?", use_container_width=True, key="sel_teacher"):
                st.session_state.login_type = "teacher"
                st.rerun()
            if st.button("👨‍👩‍👧  학부모이신가요?", use_container_width=True, key="sel_parent"):
                st.session_state.login_type = "parent"
                st.rerun()
            if st.button("🔑  통합 관리자이신가요?", use_container_width=True, key="sel_admin"):
                st.session_state.login_type = "admin"
                st.rerun()
        st.stop()

    # ── 선택된 유형 로그인 폼 ───────────────────────────────────────
    if st.button("← 뒤로"):
        st.session_state.login_type = None
        st.rerun()

    ltype = st.session_state.login_type

    if ltype == "student":
        st.subheader("🎒 학생 로그인")
        with st.form("student_login"):
            s_name = st.text_input("이름", placeholder="홍길동")
            s_code = st.text_input("학번 (6자리)", placeholder="739281")
            if st.form_submit_button("로그인", use_container_width=True, type="primary"):
                name, code = s_name.strip(), s_code.strip()
                if not name or not code:
                    st.error("이름과 학번을 입력해주세요.")
                elif not verify_code(name, code):
                    st.error("학번이 올바르지 않습니다.")
                else:
                    conn = get_db()
                    row = conn.execute("SELECT * FROM students WHERE name=? AND student_code=?", (name, code)).fetchone()
                    if row:
                        if row["enrollment_year"] and row["base_grade"]:
                            new_g = calc_current_grade(row["base_grade"], row["enrollment_year"])
                            if new_g != row["grade"]:
                                conn.execute("UPDATE students SET grade=? WHERE id=?", (new_g, row["id"]))
                                conn.commit()
                                row = conn.execute("SELECT * FROM students WHERE id=?", (row["id"],)).fetchone()
                        conn.close()
                        st.session_state.role = "student"
                        st.session_state.student_id = row["id"]
                        st.session_state.student_info = dict(row)
                        st.session_state.login_type = None
                        st.rerun()
                    else:
                        st.session_state.pending_register = {"name": name, "code": code}
                        st.rerun()

    elif ltype == "teacher":
        st.subheader("👩‍🏫 선생님 로그인")
        with st.form("teacher_login"):
            t_user = st.text_input("아이디", placeholder="teacher01")
            t_pw   = st.text_input("비밀번호", type="password")
            if st.form_submit_button("로그인", use_container_width=True, type="primary"):
                conn = get_db()
                row = conn.execute("SELECT * FROM teachers WHERE username=? AND password_hash=?",
                                   (t_user.strip(), hash_pw(t_pw))).fetchone()
                conn.close()
                if row:
                    st.session_state.role = "teacher"
                    st.session_state.teacher_id = row["id"]
                    st.session_state.teacher_info = dict(row)
                    st.session_state.login_type = None
                    st.rerun()
                else:
                    st.error("아이디 또는 비밀번호를 확인해주세요.")

    elif ltype == "parent":
        st.subheader("👨‍👩‍👧 학부모 로그인")
        with st.form("parent_login"):
            p_user = st.text_input("아이디", placeholder="예) 284713p  (자녀 학번+p)")
            p_pw   = st.text_input("비밀번호", type="password", placeholder="예) 284713  (자녀 학번)")
            if st.form_submit_button("로그인", use_container_width=True, type="primary"):
                conn = get_db()
                row = conn.execute(
                    "SELECT * FROM parents WHERE username=? AND password_hash=?",
                    (p_user.strip(), hash_pw(p_pw))).fetchone()
                conn.close()
                if row:
                    st.session_state.role = "parent"
                    st.session_state.parent_id = row["id"]
                    st.session_state.parent_info = dict(row)
                    st.session_state.login_type = None
                    st.rerun()
                else:
                    st.error("아이디 또는 비밀번호를 확인해주세요.")

    elif ltype == "admin":
        st.subheader("🔑 통합 관리자")
        with st.form("admin_login"):
            pw = st.text_input("관리자 비밀번호", type="password")
            if st.form_submit_button("로그인", use_container_width=True):
                if pw == SUPER_ADMIN_PASSWORD:
                    st.session_state.role = "admin"
                    st.session_state.login_type = None
                    st.rerun()
                else:
                    st.error("비밀번호를 확인해주세요.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# 학생 페이지
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.role == "student":
    info = st.session_state.student_info
    sid  = st.session_state.student_id

    with st.sidebar:
        st.markdown(f"### 👋 {info['name']} 학생")
        st.caption(f"{info['grade']} {info['class_name']} · {info['student_code']}")
        st.divider()
        page = st.radio("메뉴", ["🏠 홈","📋 내 과제 목록","✅ 제출 완료 목록","🎬 강의 영상","💬 질문하기","🗓 시간표"])
        st.divider()
        if st.button("로그아웃", use_container_width=True):
            st.session_state.role = None
            st.rerun()

    if page == "📋 내 과제 목록":
        st.subheader("📋 내 과제 목록")
        conn = get_db()
        assignments = conn.execute("""
            SELECT a.*, t.name AS teacher_name, t.subject,
                   s.id AS sub_id, s.submitted_at, s.is_checked, s.checked_at, s.teacher_comment
            FROM assignments a
            LEFT JOIN teachers t ON a.teacher_id = t.id
            LEFT JOIN submissions s ON a.id = s.assignment_id AND s.student_id = ?
            WHERE a.grade = ? AND a.class_name = ?
            ORDER BY t.subject, a.due_date ASC
        """, (sid, info["grade"], info["class_name"])).fetchall()
        conn.close()

        if not assignments:
            st.info("현재 등록된 과제가 없습니다.")
        else:
            total_pending = len([a for a in assignments if a["sub_id"] is None])
            st.caption(f"전체 미제출 {total_pending}개")
            subj_map = defaultdict(list)
            for a in assignments:
                subj_map[a["subject"] or "기타"].append(a)

            # 과목 토글
            for subject, alist in subj_map.items():
                pending = len([a for a in alist if a["sub_id"] is None])
                badge = f"🔴 미제출 {pending}개" if pending else "✅ 모두 완료"
                with st.expander(f"📖 {subject}  —  {badge}", expanded=(pending > 0)):
                    for a in alist:
                        due_str = a["due_date"] or "마감일 없음"
                        is_late = False
                        if a["due_date"]:
                            try:
                                is_late = date.today() > date.fromisoformat(a["due_date"]) and a["sub_id"] is None
                            except: pass
                        icon = "🔴" if is_late else ("🟡" if a["sub_id"] is None else "🟢")
                        teacher_tag = f" · {a['teacher_name']}" if a["teacher_name"] else ""
                        with st.expander(f"{icon} {a['title']}  —  마감: {due_str}{teacher_tag}"):
                            st.write(f"**설명:** {a['description'] or '없음'}")
                            if a["sub_id"] is None:
                                with st.form(f"submit_{a['id']}"):
                                    st.markdown("##### 📤 과제 제출")
                                    uploaded_files = st.file_uploader("사진 업로드 (여러 장 가능)",
                                        type=["jpg","jpeg","png","pdf"], key=f"file_{a['id']}",
                                        accept_multiple_files=True)
                                    memo = st.text_area("메모 (선택)", key=f"memo_{a['id']}")
                                    if st.form_submit_button("제출하기 ✅", type="primary", use_container_width=True):
                                        if not uploaded_files:
                                            st.error("파일을 첨부해 주세요.")
                                        else:
                                            fpath = save_multiple_files(uploaded_files, sid, a["id"])
                                            conn2 = get_db()
                                            try:
                                                conn2.execute(
                                                    "INSERT INTO submissions (student_id, assignment_id, file_path, memo) VALUES (?,?,?,?)",
                                                    (sid, a["id"], fpath, memo))
                                                conn2.commit()
                                                st.success(f"제출 완료! 🎉 ({len(uploaded_files)}개)")
                                                st.rerun()
                                            except sqlite3.IntegrityError:
                                                st.warning("이미 제출한 과제입니다.")
                                            finally:
                                                conn2.close()
                            else:
                                if a["is_checked"]:
                                    st.success("✔ 선생님 확인 완료")
                                else:
                                    st.info("📨 제출 완료 (검토 중)")
                                st.caption(f"제출 시각: {a['submitted_at']}")
                                if a["is_checked"] and a["checked_at"]:
                                    st.caption(f"확인 시각: {a['checked_at']}")
                                if a["teacher_comment"]:
                                    st.info(f"💬 선생님 코멘트: {a['teacher_comment']}")

    elif page == "🎬 강의 영상":
        st.subheader("🎬 강의 영상")
        conn = get_db()
        videos = conn.execute("""
            SELECT v.*, t.name AS teacher_name, t.subject
            FROM videos v
            LEFT JOIN teachers t ON v.teacher_id = t.id
            WHERE v.grade=? AND v.class_name=?
            ORDER BY t.subject, v.category, v.created_at DESC
        """, (info["grade"], info["class_name"])).fetchall()
        conn.close()

        if not videos:
            st.info("등록된 영상이 없습니다.")
        else:
            subj_map = defaultdict(lambda: defaultdict(list))
            for v in videos:
                subj_map[v["subject"] or "기타"][v["category"] or "기본"].append(v)

            # 과목 토글 > 폴더 토글 > 영상 토글
            for subject, cat_map in subj_map.items():
                total = sum(len(vl) for vl in cat_map.values())
                with st.expander(f"📖 {subject}  —  영상 {total}개"):
                    for cat, vlist in cat_map.items():
                        with st.expander(f"📁 {cat}  ({len(vlist)}개)"):
                            for v in vlist:
                                with st.expander(f"🎬 {v['title']}"):
                                    st.components.v1.iframe(youtube_embed_url(v["youtube_url"]), height=380)

    elif page == "💬 질문하기":
        st.subheader("💬 질문하기")
        tab1, tab2 = st.tabs(["새 질문", "내 질문 목록"])

        with tab1:
            conn = get_db()
            teachers = conn.execute("SELECT id, name, subject FROM teachers ORDER BY subject").fetchall()
            conn.close()
            with st.form("ask_question"):
                if teachers:
                    t_options = {f"{t['subject']} · {t['name']}": t["id"] for t in teachers}
                    t_options["선생님 미지정"] = None
                    selected_t = st.selectbox("선생님 선택", list(t_options.keys()))
                    t_id = t_options[selected_t]
                    subject = dict([(t["id"], t["subject"]) for t in teachers]).get(t_id, "")
                else:
                    t_id = None
                    subject = ""
                    st.info("등록된 선생님이 없습니다.")
                q_title   = st.text_input("제목 *", placeholder="예) 3강 2번 문제 질문")
                q_images  = st.file_uploader("📸 이미지 첨부 (선택, 여러 장 가능)",
                    type=["jpg","jpeg","png"], accept_multiple_files=True)
                q_content = st.text_area("질문 내용 *", placeholder="궁금한 내용을 자세히 적어주세요.", height=150)
                if st.form_submit_button("질문 등록 ✅", type="primary", use_container_width=True):
                    if not q_title.strip() or not q_content.strip():
                        st.error("제목과 내용을 모두 입력해주세요.")
                    else:
                        # 이미지 저장
                        img_paths = ""
                        if q_images:
                            paths = []
                            for i, img in enumerate(q_images):
                                ext = Path(img.name).suffix
                                fname = f"q_{sid}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{i}{ext}"
                                fpath = UPLOAD_DIR / fname
                                with open(fpath, "wb") as f:
                                    f.write(img.read())
                                paths.append(str(fpath))
                            img_paths = "|".join(paths)
                        conn = get_db()
                        conn.execute(
                            "INSERT INTO questions (student_id, teacher_id, subject, title, content, image_paths) VALUES (?,?,?,?,?,?)",
                            (sid, t_id, subject, q_title.strip(), q_content.strip(), img_paths or None))
                        conn.commit()
                        conn.close()
                        st.success(f"질문이 등록되었습니다! {'📸 이미지 ' + str(len(q_images)) + '장 첨부됨  ' if q_images else ''}선생님 답변을 기다려주세요. 📨")
                        st.rerun()

        with tab2:
            conn = get_db()
            questions = conn.execute("""
                SELECT q.*, t.name AS teacher_name, t.subject
                FROM questions q
                LEFT JOIN teachers t ON q.teacher_id = t.id
                WHERE q.student_id = ?
                ORDER BY q.created_at DESC
            """, (sid,)).fetchall()
            conn.close()

            if not questions:
                st.info("아직 등록한 질문이 없습니다.")
            else:
                for q in questions:
                    status = "✅ 답변 완료" if q["is_answered"] else "⏳ 답변 대기"
                    teacher_tag = f" · {q['teacher_name']}" if q["teacher_name"] else ""
                    with st.expander(f"{status}  |  {q['title']}{teacher_tag}  —  {q['created_at'][:10]}"):
                        st.markdown("**질문:**")
                        st.write(q["content"])
                        if q["image_paths"]:
                            st.markdown("**📸 첨부 이미지:**")
                            for fp in q["image_paths"].split("|"):
                                if os.path.exists(fp):
                                    st.image(fp, use_column_width=True)
                        if q["is_answered"] and q["answer"]:
                            st.divider()
                            st.markdown(f"**💬 선생님 답변** ({q['answered_at'][:10] if q['answered_at'] else ''}):")
                            st.info(q["answer"])
                        else:
                            st.caption("아직 답변이 등록되지 않았습니다.")

    elif page == "🏠 홈":
        from datetime import datetime as dt_now
        today = dt_now.now()
        day_kr = ["월","화","수","목","금","토","일"][today.weekday()]
        DAYS_KR = ["월","화","수","목","금"]
        PERIODS_H = [1,2,3,4]

        st.subheader(f"🏠 안녕하세요, {info['name']} 학생!")
        st.caption(f"📅 {today.year}년 {today.month}월 {today.day}일 ({day_kr}요일)")
        st.divider()

        # ── 오늘의 수업 ──────────────────────────────────────
        st.markdown("#### 📚 오늘의 수업")
        if day_kr not in DAYS_KR:
            st.info("오늘은 주말입니다. 즐거운 휴일 보내세요! 🎉")
        else:
            conn = get_db()
            today_tt = conn.execute(
                "SELECT * FROM timetable WHERE grade=? AND class_name=? AND day=? ORDER BY period",
                (info["grade"], info["class_name"], day_kr)).fetchall()
            pt_today = conn.execute(
                "SELECT * FROM period_times WHERE grade='공통' AND class_name='공통' ORDER BY period"
            ).fetchall()
            conn.close()
            pt_map_h = {r["period"]: r for r in pt_today}
            tt_today = {r["period"]: r for r in today_tt}

            if not today_tt:
                st.info("오늘 등록된 수업이 없습니다.")
            else:
                for p in PERIODS_H:
                    cell = tt_today.get(p)
                    pt   = pt_map_h.get(p)
                    time_str = ""
                    if pt and pt["start_time"]:
                        time_str = f"{pt['start_time']} ~ {pt['end_time']}" if pt["end_time"] else pt["start_time"]
                    if cell and cell["subject"]:
                        col1, col2 = st.columns([1, 4])
                        col1.markdown(
                            f"<div style='background:#1e293b;border-left:3px solid #3b82f6;padding:10px 8px;border-radius:4px;text-align:center;'>"
                            f"<span style='font-size:0.95rem;font-weight:bold;'>{p}교시</span><br><span style='font-size:0.78rem;color:#93c5fd;font-weight:500;'>{time_str}</span></div>",
                            unsafe_allow_html=True)
                        col2.markdown(
                            f"<div style='background:#1e3a5f;border-radius:8px;padding:10px 16px;'>"
                            f"<span style='font-size:1rem;font-weight:bold;'>{cell['subject']}</span>"
                            f"<span style='color:#94a3b8;font-size:0.85rem;margin-left:12px;'>{cell['teacher_name'] or ''} 선생님</span>"
                            f"</div>", unsafe_allow_html=True)
                        st.markdown("")
        st.divider()

        # ── 공지사항 ──────────────────────────────────────────
        st.markdown("#### 📢 공지사항")

        conn = get_db()
        # 전체 공지 (학원 공지)
        global_notices = conn.execute(
            "SELECT * FROM notices WHERE notice_type='global' ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        # 과목 선생님 공지 (본인 반 + 해당 선생님)
        subject_notices = conn.execute("""
            SELECT n.*, t.name as teacher_name_real, t.subject as teacher_subject
            FROM notices n
            LEFT JOIN teachers t ON n.teacher_id = t.id
            WHERE n.notice_type='subject'
              AND n.grade=? AND n.class_name=?
            ORDER BY n.created_at DESC LIMIT 20
        """, (info["grade"], info["class_name"])).fetchall()
        conn.close()

        # 학원 공지 + 과목별 공지를 시간순으로 합쳐서 한 화면에 표시
        all_notices_merged = []
        for n in global_notices:
            all_notices_merged.append(("global", n))
        for n in subject_notices:
            all_notices_merged.append(("subject", n))
        # 최신순 정렬
        all_notices_merged.sort(key=lambda x: (0 if x[0]=="global" else 1, -ord(x[1]["created_at"][0]) if x[1]["created_at"] else 0))
        all_notices_merged.sort(key=lambda x: (0 if x[0]=="global" else 1))
        # 각 그룹 내 최신순 유지
        global_part  = sorted([x for x in all_notices_merged if x[0]=="global"],  key=lambda x: x[1]["created_at"], reverse=True)
        subject_part = sorted([x for x in all_notices_merged if x[0]=="subject"], key=lambda x: x[1]["created_at"], reverse=True)
        all_notices_merged = global_part + subject_part

        if not all_notices_merged:
            st.info("공지사항이 없습니다.")
        else:
            for ntype, n in all_notices_merged:
                if ntype == "global":
                    st.markdown(
                        f"<div style='background:#1e293b;border-left:4px solid #f59e0b;border-radius:6px;padding:14px 16px;margin-bottom:10px;'>"
                        f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'>"
                        f"<span style='background:#92400e;color:#fde68a;font-size:0.78rem;font-weight:bold;padding:2px 9px;border-radius:20px;'>📣 학원 공지</span>"
                        f"<span style='color:#475569;font-size:0.75rem;margin-left:auto;'>{n['created_at'][:10]}</span>"
                        f"</div>"
                        f"<div style='font-weight:bold;font-size:1rem;margin-bottom:6px;'>{n['title']}</div>"
                        f"<div style='color:#cbd5e1;white-space:pre-wrap;font-size:0.9rem;'>{n['content']}</div>"
                        f"</div>", unsafe_allow_html=True)
                else:
                    label = n["teacher_subject"] or ""
                    tname = n["teacher_name_real"] or ""
                    st.markdown(
                        f"<div style='background:#1e293b;border-left:4px solid #3b82f6;border-radius:6px;padding:14px 16px;margin-bottom:10px;'>"
                        f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'>"
                        f"<span style='background:#1d4ed8;color:#fff;font-size:0.78rem;font-weight:bold;padding:2px 9px;border-radius:20px;'>{label}</span>"
                        f"<span style='color:#93c5fd;font-size:0.88rem;font-weight:600;'>{tname} 선생님</span>"
                        f"<span style='color:#475569;font-size:0.75rem;margin-left:auto;'>{n['created_at'][:10]}</span>"
                        f"</div>"
                        f"<div style='font-weight:bold;font-size:1rem;margin-bottom:6px;'>{n['title']}</div>"
                        f"<div style='color:#cbd5e1;white-space:pre-wrap;font-size:0.9rem;'>{n['content']}</div>"
                        f"</div>", unsafe_allow_html=True)

    elif page == "🗓 시간표":
        st.subheader(f"🗓 시간표  —  {info['grade']} {info['class_name']}")
        DAYS    = ["월","화","수","목","금"]
        PERIODS_S = [1,2,3,4]
        tab_tt, tab_sch = st.tabs(["📋 주간 시간표", "📅 날짜별 일정"])

        # ── 주간 시간표 탭 ──
        with tab_tt:
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM timetable WHERE grade=? AND class_name=?",
                (info["grade"], info["class_name"])).fetchall()
            pt_rows_s = conn.execute(
                "SELECT * FROM period_times WHERE grade='공통' AND class_name='공통' ORDER BY period"
            ).fetchall()
            conn.close()

            if not rows:
                st.info("아직 등록된 시간표가 없습니다.")
            else:
                tt = {(r["day"], r["period"]): r for r in rows}
                pt_map_s = {r["period"]: r for r in pt_rows_s}
                st.markdown(render_timetable_html(tt, pt_map_s, DAYS, PERIODS_S), unsafe_allow_html=True)

        # ── 날짜별 일정 탭 ──
        with tab_sch:
            conn = get_db()
            schedules = conn.execute("""
                SELECT * FROM schedule
                WHERE grade=? AND class_name=? AND event_date >= date('now','localtime')
                ORDER BY event_date, start_time
            """, (info["grade"], info["class_name"])).fetchall()
            conn.close()
            if not schedules:
                st.info("예정된 일정이 없습니다.")
            else:
                from itertools import groupby
                sch_map = defaultdict(list)
                for s in schedules:
                    sch_map[s["event_date"]].append(s)
                for event_date, items in sch_map.items():
                    try:
                        from datetime import datetime as dt2
                        d_obj = dt2.strptime(event_date, "%Y-%m-%d")
                        day_kr = ["월","화","수","목","금","토","일"][d_obj.weekday()]
                        date_label = f"{d_obj.month}/{d_obj.day} ({day_kr})"
                    except:
                        date_label = event_date
                    st.markdown(f"#### 📅 {date_label}")
                    for item in items:
                        time_str = ""
                        if item["start_time"]:
                            time_str = f"{item['start_time']}"
                            if item["end_time"]:
                                time_str += f" ~ {item['end_time']}"
                        teacher_str = f" · {item['teacher_name']}" if item["teacher_name"] else ""
                        with st.expander(f"🔹 {item['title']}  {time_str}{teacher_str}"):
                            if item["description"]:
                                st.write(item["description"])
                            else:
                                st.caption("상세 내용 없음")
                    st.divider()

    else:
        st.subheader("✅ 제출 완료 목록")
        conn = get_db()
        subs = conn.execute("""
            SELECT s.*, a.title, t.subject, t.name AS teacher_name
            FROM submissions s
            JOIN assignments a ON s.assignment_id = a.id
            LEFT JOIN teachers t ON a.teacher_id = t.id
            WHERE s.student_id = ? ORDER BY s.submitted_at DESC
        """, (sid,)).fetchall()
        conn.close()
        if not subs:
            st.info("제출한 과제가 없습니다.")
        for s in subs:
            checked = "✔ 확인 완료" if s["is_checked"] else "⏳ 검토 중"
            label = f"{s['subject']} · " if s["subject"] else ""
            with st.expander(f"📄 {label}{s['title']}  —  {checked}"):
                st.caption(f"제출: {s['submitted_at']}")
                if s["memo"]:
                    st.write(f"**메모:** {s['memo']}")
                if s["teacher_comment"]:
                    st.info(f"💬 선생님 코멘트: {s['teacher_comment']}")
                if s["file_path"]:
                    for fp in s["file_path"].split("|"):
                        if os.path.exists(fp):
                            ext = Path(fp).suffix.lower()
                            if ext in [".jpg",".jpeg",".png"]:
                                st.image(fp, use_column_width=True)
                            else:
                                st.download_button("📎 파일 다운로드",
                                    open(fp,"rb").read(), file_name=Path(fp).name, key=f"dl_{fp}")

# ══════════════════════════════════════════════════════════════════════════════
# 학부모 페이지
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.role == "parent":
    from datetime import datetime as dt_now
    pinfo = st.session_state.parent_info

    # 이 학부모 계정에 연결된 자녀 목록
    # 방법: 아이디에서 학번 추출 → 같은 학부모 연락처로 등록된 학생 전체
    base_code = pinfo["username"].rstrip("p")
    conn = get_db()
    primary_student = conn.execute("SELECT * FROM students WHERE student_code=?", (base_code,)).fetchone()
    if primary_student and primary_student["parent_phone"]:
        # 같은 학부모 연락처 학생 모두
        children = conn.execute(
            "SELECT * FROM students WHERE parent_phone=? ORDER BY grade, name",
            (primary_student["parent_phone"],)).fetchall()
    elif primary_student:
        children = [primary_student]
    else:
        children = []
    conn.close()

    # 사이드바
    with st.sidebar:
        st.markdown("### 👨‍👩‍👧 학부모 페이지")
        st.divider()

        # 자녀 선택
        if len(children) > 1:
            child_opts = {f"{c['name']} ({c['grade']} {c['class_name']})": c for c in children}
            sel_child_name = st.selectbox("👦 자녀 선택", list(child_opts.keys()), key="parent_child_sel")
            cur_child = child_opts[sel_child_name]
        elif len(children) == 1:
            cur_child = dict(children[0])
            st.markdown(f"**👦 {cur_child['name']}** ({cur_child['grade']} {cur_child['class_name']})")
        else:
            st.warning("연결된 학생 정보를 찾을 수 없습니다.")
            if st.button("로그아웃"):
                st.session_state.role = None
                st.rerun()
            st.stop()

        st.divider()
        page = st.radio("메뉴", ["🏠 홈", "📚 과제 현황", "🗓 시간표", "🔐 비밀번호 변경"])
        st.divider()
        if st.button("로그아웃", use_container_width=True):
            st.session_state.role = None
            st.rerun()

    DAYS_P   = ["월","화","수","목","금"]
    PERIODS_P = [1,2,3,4]

    # ── 홈 ────────────────────────────────────────────────────────
    if page == "🏠 홈":
        today = dt_now.now()
        day_kr = ["월","화","수","목","금","토","일"][today.weekday()]
        st.subheader(f"🏠 안녕하세요! {cur_child['name']} 학부모님")
        st.caption(f"📅 {today.year}년 {today.month}월 {today.day}일 ({day_kr}요일)")
        st.divider()

        # 오늘의 수업
        st.markdown("#### 📚 오늘의 수업")
        if day_kr not in DAYS_P:
            st.info("오늘은 주말입니다.")
        else:
            conn = get_db()
            today_tt = conn.execute(
                "SELECT * FROM timetable WHERE grade=? AND class_name=? AND day=? ORDER BY period",
                (cur_child["grade"], cur_child["class_name"], day_kr)).fetchall()
            pt_today = conn.execute(
                "SELECT * FROM period_times WHERE grade='공통' AND class_name='공통' ORDER BY period"
            ).fetchall()
            conn.close()
            pt_map_p = {r["period"]: r for r in pt_today}
            tt_today = {r["period"]: r for r in today_tt}
            if not today_tt:
                st.info("오늘 등록된 수업이 없습니다.")
            else:
                for p in PERIODS_P:
                    cell = tt_today.get(p)
                    pt   = pt_map_p.get(p)
                    time_str = ""
                    if pt and pt["start_time"]:
                        time_str = f"{pt['start_time']} ~ {pt['end_time']}" if pt["end_time"] else pt["start_time"]
                    if cell and cell["subject"]:
                        col1, col2 = st.columns([1, 4])
                        col1.markdown(
                            f"<div style='background:#1e293b;border-left:3px solid #3b82f6;padding:10px 8px;border-radius:4px;text-align:center;'>"
                            f"<span style='font-size:0.95rem;font-weight:bold;'>{p}교시</span><br><span style='font-size:0.78rem;color:#93c5fd;font-weight:500;'>{time_str}</span></div>",
                            unsafe_allow_html=True)
                        col2.markdown(
                            f"<div style='background:#1e3a5f;border-radius:8px;padding:10px 16px;'>"
                            f"<span style='font-size:1rem;font-weight:bold;'>{cell['subject']}</span>"
                            f"<span style='color:#94a3b8;font-size:0.85rem;margin-left:12px;'>{cell['teacher_name'] or ''} 선생님</span>"
                            f"</div>", unsafe_allow_html=True)
                        st.markdown("")
        st.divider()

        # 공지사항
        st.markdown("#### 📢 공지사항")
        conn = get_db()
        global_notices_p = conn.execute(
            "SELECT * FROM notices WHERE notice_type='global' ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        subject_notices_p = conn.execute("""
            SELECT n.*, t.name as teacher_name_real, t.subject as teacher_subject
            FROM notices n LEFT JOIN teachers t ON n.teacher_id = t.id
            WHERE n.notice_type='subject' AND n.grade=? AND n.class_name=?
            ORDER BY n.created_at DESC LIMIT 20
        """, (cur_child["grade"], cur_child["class_name"])).fetchall()
        conn.close()

        all_notices_merged_p = []
        for n in global_notices_p:
            all_notices_merged_p.append(("global", n))
        for n in subject_notices_p:
            all_notices_merged_p.append(("subject", n))
        global_part_p  = sorted([x for x in all_notices_merged_p if x[0]=="global"],  key=lambda x: x[1]["created_at"], reverse=True)
        subject_part_p = sorted([x for x in all_notices_merged_p if x[0]=="subject"], key=lambda x: x[1]["created_at"], reverse=True)
        all_notices_merged_p = global_part_p + subject_part_p

        if not all_notices_merged_p:
            st.info("공지사항이 없습니다.")
        else:
            for ntype, n in all_notices_merged_p:
                if ntype == "global":
                    st.markdown(
                        f"<div style='background:#1e293b;border-left:4px solid #f59e0b;border-radius:6px;padding:14px 16px;margin-bottom:10px;'>"
                        f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'>"
                        f"<span style='background:#92400e;color:#fde68a;font-size:0.78rem;font-weight:bold;padding:2px 9px;border-radius:20px;'>📣 학원 공지</span>"
                        f"<span style='color:#475569;font-size:0.75rem;margin-left:auto;'>{n['created_at'][:10]}</span>"
                        f"</div>"
                        f"<div style='font-weight:bold;font-size:1rem;margin-bottom:6px;'>{n['title']}</div>"
                        f"<div style='color:#cbd5e1;white-space:pre-wrap;font-size:0.9rem;'>{n['content']}</div>"
                        f"</div>", unsafe_allow_html=True)
                else:
                    label = n["teacher_subject"] or ""
                    tname = n["teacher_name_real"] or ""
                    st.markdown(
                        f"<div style='background:#1e293b;border-left:4px solid #3b82f6;border-radius:6px;padding:14px 16px;margin-bottom:10px;'>"
                        f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'>"
                        f"<span style='background:#1d4ed8;color:#fff;font-size:0.78rem;font-weight:bold;padding:2px 9px;border-radius:20px;'>{label}</span>"
                        f"<span style='color:#93c5fd;font-size:0.88rem;font-weight:600;'>{tname} 선생님</span>"
                        f"<span style='color:#475569;font-size:0.75rem;margin-left:auto;'>{n['created_at'][:10]}</span>"
                        f"</div>"
                        f"<div style='font-weight:bold;font-size:1rem;margin-bottom:6px;'>{n['title']}</div>"
                        f"<div style='color:#cbd5e1;white-space:pre-wrap;font-size:0.9rem;'>{n['content']}</div>"
                        f"</div>", unsafe_allow_html=True)

    # ── 과제 현황 ──────────────────────────────────────────────────
    elif page == "📚 과제 현황":
        st.subheader(f"📚 {cur_child['name']} 과제 현황")
        conn = get_db()
        # 이 학생에게 배정된 과제 전체
        assignments_p = conn.execute("""
            SELECT a.*, t.name as teacher_name_r, t.subject as teacher_subject_r
            FROM assignments a
            JOIN teachers t ON a.teacher_id = t.id
            WHERE a.grade=? AND a.class_name=?
            ORDER BY a.created_at DESC
        """, (cur_child["grade"], cur_child["class_name"])).fetchall()
        # 제출 현황
        submitted_ids = set(
            r["assignment_id"] for r in conn.execute(
                "SELECT assignment_id FROM submissions WHERE student_id=?",
                (cur_child["id"],)).fetchall()
        )
        # 제출 내용 (사진 포함)
        submissions_map = {
            r["assignment_id"]: r for r in conn.execute(
                "SELECT * FROM submissions WHERE student_id=?",
                (cur_child["id"],)).fetchall()
        }
        conn.close()

        if not assignments_p:
            st.info("등록된 과제가 없습니다.")
        else:
            submitted   = [a for a in assignments_p if a["id"] in submitted_ids]
            unsubmitted = [a for a in assignments_p if a["id"] not in submitted_ids]

            col_a, col_b = st.columns(2)
            col_a.metric("✅ 제출 완료", len(submitted))
            col_b.metric("⏳ 미제출", len(unsubmitted))
            st.divider()

            tab_sub, tab_unsub = st.tabs([f"✅ 제출 완료 ({len(submitted)})", f"⏳ 미제출 ({len(unsubmitted)})"])

            with tab_sub:
                if not submitted:
                    st.info("제출한 과제가 없습니다.")
                else:
                    for a in submitted:
                        sub = submissions_map.get(a["id"])
                        checked_str = "✔ 확인됨" if sub and sub["is_checked"] else "⏳ 미확인"
                        st.markdown(
                            f"<div style='background:#1e293b;border-left:4px solid #22c55e;border-radius:6px;padding:12px 16px;margin-bottom:8px;'>"
                            f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                            f"<span style='font-weight:bold;'>{a['title']}</span>"
                            f"<span style='font-size:0.75rem;color:#64748b;'>{checked_str}</span>"
                            f"</div>"
                            f"<div style='font-size:0.8rem;color:#94a3b8;margin-top:4px;'>{a['teacher_subject_r']} · {a['teacher_name_r']} 선생님 · 제출: {(sub['submitted_at'] or '')[:10]}</div>"
                            f"</div>", unsafe_allow_html=True)
                        if sub:
                            if sub.get("content"):
                                with st.expander("📝 제출 내용 보기"):
                                    st.write(sub["content"])
                            if sub.get("image_data"):
                                with st.expander("🖼 제출 사진 보기"):
                                    import base64
                                    img_data = sub["image_data"]
                                    if isinstance(img_data, bytes):
                                        img_b64 = base64.b64encode(img_data).decode()
                                    else:
                                        img_b64 = img_data
                                    st.markdown(f"<img src='data:image/jpeg;base64,{img_b64}' style='max-width:100%;border-radius:8px;'>", unsafe_allow_html=True)

            with tab_unsub:
                if not unsubmitted:
                    st.info("미제출 과제가 없습니다. 🎉")
                else:
                    for a in unsubmitted:
                        st.markdown(
                            f"<div style='background:#1e293b;border-left:4px solid #ef4444;border-radius:6px;padding:12px 16px;margin-bottom:8px;'>"
                            f"<div style='font-weight:bold;'>{a['title']}</div>"
                            f"<div style='font-size:0.8rem;color:#94a3b8;margin-top:4px;'>{a['teacher_subject_r']} · {a['teacher_name_r']} 선생님</div>"
                            f"</div>", unsafe_allow_html=True)

    # ── 시간표 ────────────────────────────────────────────────────
    elif page == "🗓 시간표":
        st.subheader(f"🗓 {cur_child['name']} 시간표 ({cur_child['grade']} {cur_child['class_name']})")
        conn = get_db()
        rows_p = conn.execute(
            "SELECT * FROM timetable WHERE grade=? AND class_name=?",
            (cur_child["grade"], cur_child["class_name"])).fetchall()
        pt_rows_p = conn.execute(
            "SELECT * FROM period_times WHERE grade='공통' AND class_name='공통' ORDER BY period"
        ).fetchall()
        conn.close()
        if not rows_p:
            st.info("아직 등록된 시간표가 없습니다.")
        else:
            tt_p = {(r["day"], r["period"]): r for r in rows_p}
            pt_map_p2 = {r["period"]: r for r in pt_rows_p}
            st.markdown(render_timetable_html(tt_p, pt_map_p2, DAYS_P, PERIODS_P), unsafe_allow_html=True)

    # ── 비밀번호 변경 ──────────────────────────────────────────────
    elif page == "🔐 비밀번호 변경":
        st.subheader("🔐 비밀번호 변경")
        with st.form("parent_pw_change"):
            cur_pw  = st.text_input("현재 비밀번호", type="password")
            new_pw1 = st.text_input("새 비밀번호", type="password")
            new_pw2 = st.text_input("새 비밀번호 확인", type="password")
            if st.form_submit_button("변경 ✅", type="primary", use_container_width=True):
                conn = get_db()
                row_pw = conn.execute(
                    "SELECT * FROM parents WHERE id=? AND password_hash=?",
                    (pinfo["id"], hash_pw(cur_pw))).fetchone()
                if not row_pw:
                    st.error("현재 비밀번호가 올바르지 않습니다.")
                elif new_pw1 != new_pw2:
                    st.error("새 비밀번호가 일치하지 않습니다.")
                elif len(new_pw1) < 4:
                    st.error("비밀번호는 4자 이상이어야 합니다.")
                else:
                    conn.execute("UPDATE parents SET password_hash=? WHERE id=?",
                                 (hash_pw(new_pw1), pinfo["id"]))
                    conn.commit()
                    st.success("✅ 비밀번호가 변경되었습니다!")
                conn.close()

# ══════════════════════════════════════════════════════════════════════════════
# 선생님 페이지
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.role == "teacher":
    tinfo = st.session_state.teacher_info
    tid   = st.session_state.teacher_id

    with st.sidebar:
        st.markdown(f"### 👩‍🏫 {tinfo['name']} 선생님")
        st.caption(f"과목: {tinfo['subject']}")
        st.divider()
        # 미답변 질문 수 표시
        conn = get_db()
        unanswered = conn.execute("SELECT COUNT(*) FROM questions WHERE teacher_id=? AND is_answered=0", (tid,)).fetchone()[0]
        conn.close()
        q_label = f"💬 질문 관리  🔴{unanswered}" if unanswered else "💬 질문 관리"
        page = st.radio("메뉴", ["📊 현황","📝 과제 등록","📋 과제 관리","🔍 제출 현황","🎬 영상 관리", q_label, "📢 공지사항"])
        st.divider()
        if st.button("로그아웃", use_container_width=True):
            st.session_state.role = None
            st.session_state.teacher_id = None
            st.rerun()

    if page == "📊 현황":
        st.subheader(f"📊 {tinfo['subject']} ({tinfo['name']}) 현황")
        conn = get_db()
        n_a = conn.execute("SELECT COUNT(*) FROM assignments WHERE teacher_id=?", (tid,)).fetchone()[0]
        n_s = conn.execute("SELECT COUNT(*) FROM submissions s JOIN assignments a ON s.assignment_id=a.id WHERE a.teacher_id=?", (tid,)).fetchone()[0]
        n_c = conn.execute("SELECT COUNT(*) FROM submissions s JOIN assignments a ON s.assignment_id=a.id WHERE a.teacher_id=? AND s.is_checked=1", (tid,)).fetchone()[0]
        conn.close()
        c1, c2, c3 = st.columns(3)
        c1.metric("등록 과제", n_a)
        c2.metric("제출 건수", n_s)
        c3.metric("확인 완료", n_c)
        st.divider()

        # 반별 학생 현황
        conn = get_db()
        assigned = conn.execute("""
            SELECT DISTINCT grade, class_name FROM timetable
            WHERE teacher_name=? ORDER BY grade, class_name
        """, (tinfo["name"],)).fetchall()
        conn.close()

        # 전체 인원 합산
        total_students = 0
        class_data = []
        for row in assigned:
            conn = get_db()
            sts = conn.execute(
                "SELECT * FROM students WHERE grade=? AND class_name=? ORDER BY name",
                (row["grade"], row["class_name"])).fetchall()
            conn.close()
            total_students += len(sts)
            class_data.append((row["grade"], row["class_name"], sts))

        st.subheader(f"👥 반별 학생 현황  —  전체 {total_students}명")

        if not class_data:
            st.info("시간표에 배정된 반이 없습니다. 관리자에게 시간표 등록을 요청하세요.")
        else:
            for grade, class_name, students in class_data:
                with st.expander(f"📋 {grade} {class_name}  —  {len(students)}명"):
                    if not students:
                        st.info("등록된 학생이 없습니다.")
                    else:
                        cols = st.columns(4)
                        for i, s in enumerate(students):
                            cols[i % 4].markdown(f"• {s['name']}")

        st.divider()

        # 선생님 시간표
        st.subheader("🗓 내 시간표")
        DAYS = ["월","화","수","목","금"]
        PERIODS_T = [1,2,3,4]

        # 내 모든 수업 한 번에 로드
        conn = get_db()
        my_tt_all = conn.execute(
            "SELECT * FROM timetable WHERE teacher_name=? AND subject IS NOT NULL ORDER BY period",
            (tinfo["name"],)).fetchall()
        pt_rows_t = conn.execute(
            "SELECT * FROM period_times WHERE grade='공통' AND class_name='공통' ORDER BY period"
        ).fetchall()
        conn.close()

        if not my_tt_all:
            st.info("시간표에 등록된 수업이 없습니다. 관리자에게 시간표 등록을 요청하세요.")
        else:
            # {(day, period): [ {grade, class_name, subject, teacher_name}, ... ]}
            # 한 칸에 여러 반이 있을 수 있으므로 list로
            tt_combined = {}
            for r in my_tt_all:
                key = (r["day"], r["period"])
                tt_combined.setdefault(key, []).append(r)
            pt_map_t = {r["period"]: r for r in pt_rows_t}
            st.markdown(render_timetable_html(
                tt_combined, pt_map_t, DAYS, PERIODS_T,
                highlight_fn=lambda c: (c["teacher_name"] or "").strip() == tinfo["name"].strip()
            ), unsafe_allow_html=True)
        st.divider()
        st.divider()
        st.subheader("📬 최근 제출")
        conn = get_db()
        recent = conn.execute("""
            SELECT s.submitted_at, st.name, st.grade, st.class_name, a.title, s.is_checked
            FROM submissions s
            JOIN students st ON s.student_id=st.id
            JOIN assignments a ON s.assignment_id=a.id
            WHERE a.teacher_id=? ORDER BY s.submitted_at DESC LIMIT 15
        """, (tid,)).fetchall()
        conn.close()
        if recent:
            import pandas as pd
            df = pd.DataFrame([dict(r) for r in recent])
            df.columns = ["제출시각","이름","학년","반","과제명","확인"]
            df["확인"] = df["확인"].map({1:"✔ 완료", 0:"⏳ 검토 중"})
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("아직 제출된 과제가 없습니다.")

    elif page == "📝 과제 등록":
        st.subheader("📝 새 과제 등록")
        with st.form("new_assignment"):
            title       = st.text_input("과제 제목 *", placeholder="예) 3강 문제풀이 사진 제출")
            description = st.text_area("설명 (선택)")
            col1, col2  = st.columns(2)
            grades      = get_grades() or ["중1","중2","중3","고1","고2","고3"]
            grade       = col1.selectbox("학년 *", grades)
            classes     = get_classes(grade) or ["A반","B반"]
            class_name  = col2.selectbox("반 *", classes)
            due_date    = st.date_input("마감일 (선택)", value=None)
            if st.form_submit_button("과제 등록 ✅", type="primary", use_container_width=True):
                if not title.strip():
                    st.error("제목을 입력해주세요.")
                else:
                    conn = get_db()
                    conn.execute(
                        "INSERT INTO assignments (title, description, grade, class_name, due_date, teacher_id) VALUES (?,?,?,?,?,?)",
                        (title.strip(), description.strip(), grade, class_name, str(due_date) if due_date else None, tid))
                    conn.commit()
                    conn.close()
                    st.success(f"✅ '{title}' 과제가 등록되었습니다!")

    elif page == "📋 과제 관리":
        st.subheader("📋 내 과제 목록")
        conn = get_db()
        assignments = conn.execute("""
            SELECT a.*, COUNT(s.id) AS sub_count FROM assignments a
            LEFT JOIN submissions s ON a.id=s.assignment_id
            WHERE a.teacher_id=? GROUP BY a.id ORDER BY a.created_at DESC
        """, (tid,)).fetchall()
        conn.close()
        if not assignments:
            st.info("등록된 과제가 없습니다.")
        for a in assignments:
            with st.expander(f"📄 [{a['grade']} {a['class_name']}] {a['title']}  —  제출 {a['sub_count']}건"):
                st.write(f"**설명:** {a['description'] or '없음'}")
                st.write(f"**마감일:** {a['due_date'] or '없음'}")
                if st.button("🗑 삭제", key=f"del_{a['id']}"):
                    conn = get_db()
                    conn.execute("DELETE FROM assignments WHERE id=?", (a["id"],))
                    conn.execute("DELETE FROM submissions WHERE assignment_id=?", (a["id"],))
                    conn.commit()
                    conn.close()
                    st.rerun()

    elif page == "🔍 제출 현황":
        st.subheader("🔍 제출 현황")
        conn = get_db()
        assignments = conn.execute("SELECT * FROM assignments WHERE teacher_id=? ORDER BY created_at DESC", (tid,)).fetchall()
        conn.close()
        if not assignments:
            st.info("등록된 과제가 없습니다.")
            st.stop()
        a_options = {f"[{a['grade']} {a['class_name']}] {a['title']}": a["id"] for a in assignments}
        selected = st.selectbox("과제 선택", list(a_options.keys()))
        a_id = a_options[selected]
        conn = get_db()
        sel_a       = conn.execute("SELECT * FROM assignments WHERE id=?", (a_id,)).fetchone()
        all_students= conn.execute("SELECT * FROM students WHERE grade=? AND class_name=? ORDER BY name", (sel_a["grade"], sel_a["class_name"])).fetchall()
        submissions = conn.execute("SELECT * FROM submissions WHERE assignment_id=?", (a_id,)).fetchall()
        conn.close()
        sub_map = {s["student_id"]: dict(s) for s in submissions}
        total = len(all_students)
        done  = len([s for s in all_students if s["id"] in sub_map])
        pct   = int(done/total*100) if total else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("전체 학생", total)
        c2.metric("제출 완료", done)
        c3.metric("제출률", f"{pct}%")
        st.progress(pct/100)

        # 미제출 학생 알림
        missing = [s for s in all_students if s["id"] not in sub_map]
        if missing:
            st.divider()
            with st.expander(f"📱 미제출 학생 알림 ({len(missing)}명)"):
                due_str = sel_a["due_date"] or "미정"
                default_tmpl = f"[패스파인더 국어학원] {{name}} 학생, 📚 {sel_a['title']} 과제(마감: {due_str})가 아직 제출되지 않았습니다. 빠른 제출 부탁드립니다!"
                tmpl = st.text_area("메시지 템플릿 ({name} 은 학생 이름으로 자동 치환)", value=default_tmpl, height=100)
                st.divider()

                import urllib.parse
                def get_contact(s):
                    return s["parent_phone"] or s["phone"] or ""
                def get_contact_label(s):
                    p = s["parent_phone"]
                    if p: return f"{s['parent_name'] or '학부모'} {p}"
                    if s["phone"]: return f"학생 {s['phone']}"
                    return ""

                has_phone = [s for s in missing if get_contact(s)]
                no_phone  = [s for s in missing if not get_contact(s)]

                if no_phone:
                    st.warning(f"연락처 미등록 {len(no_phone)}명: {', '.join([s['name'] for s in no_phone])}")

                # ── 일괄 문자 전송 (알리고) ────────────────────────────────
                # ── 문자 발송 ──────────────────────────────────────────
                st.markdown("**📱 문자 발송**")
                for s in missing:
                    phone    = get_contact(s)
                    msg_text = tmpl.replace("{name}", s["name"])
                    c1, c2, c3 = st.columns([2, 3, 3])
                    c1.markdown(f"**{s['name']}** ({s['grade']} {s['class_name']})")
                    if phone:
                        c2.markdown(f"📞 `{get_contact_label(s)}`")
                        encoded  = urllib.parse.quote(msg_text)
                        sms_link = f"sms:{phone}?body={encoded}"
                        btn_html = f"""<div style="display:flex;gap:6px;flex-wrap:wrap;">
  <button onclick="navigator.clipboard.writeText({repr(msg_text)}).then(()=>{{this.innerText='✅ 복사됨';setTimeout(()=>this.innerText='📋 복사',2000)}})"
    style="background:#4f86f7;color:white;border:none;padding:5px 10px;border-radius:6px;cursor:pointer;font-weight:bold;font-size:0.8rem;">📋 복사</button>
  <a href="{sms_link}" target="_blank"><button
    style="background:#4CAF50;color:white;border:none;padding:5px 10px;border-radius:6px;cursor:pointer;font-weight:bold;font-size:0.8rem;">📱 문자</button></a>
</div>"""
                        c3.markdown(btn_html, unsafe_allow_html=True)
                        if st.button("📤 알리고 자동전송", key=f"singleSend_{s['id']}"):
                            result = send_aligo_sms([get_contact(s)], msg_text)
                            if str(result.get("result_code")) == "1":
                                st.success(f"✅ {s['name']} 전송 완료!")
                            else:
                                st.error(f"전송 실패: {result.get('message','오류')}")
                    else:
                        c2.caption("연락처 미등록")
                    st.divider()

        st.divider()
        for s in all_students:
            sub = sub_map.get(s["id"])
            badge = ("🟢 제출완료" + (" ✔확인" if sub and sub["is_checked"] else "")) if sub else "🔴 미제출"
            with st.expander(f"{s['name']} ({s['student_code']})  —  {badge}"):
                if sub is None:
                    st.warning("아직 제출하지 않았습니다.")
                else:
                    st.caption(f"제출 시각: {sub['submitted_at']}")
                    if sub["memo"]: st.write(f"**메모:** {sub['memo']}")
                    if sub["file_path"]:
                        for fp in sub["file_path"].split("|"):
                            if os.path.exists(fp):
                                ext = Path(fp).suffix.lower()
                                if ext in [".jpg",".jpeg",".png"]:
                                    st.image(fp, width=400)
                                else:
                                    st.download_button("📎 다운로드", open(fp,"rb").read(),
                                        file_name=Path(fp).name, key=f"dl_{sub['id']}_{fp}")
                    col1, _ = st.columns([1,2])
                    if not sub["is_checked"]:
                        if col1.button("✔ 확인 처리", key=f"chk_{sub['id']}", type="primary"):
                            conn = get_db()
                            conn.execute("UPDATE submissions SET is_checked=1, checked_at=datetime('now','localtime') WHERE id=?", (sub["id"],))
                            conn.commit()
                            conn.close()
                            st.rerun()
                    else:
                        col1.success("✔ 확인 완료")
                        if sub["checked_at"]:
                            st.caption(f"확인 시각: {sub['checked_at']}")
                    with st.form(f"comment_{sub['id']}"):
                        comment = st.text_input("선생님 코멘트", value=sub["teacher_comment"] or "")
                        if st.form_submit_button("코멘트 저장 ✅", use_container_width=True):
                            if not comment.strip():
                                st.error("코멘트 내용을 입력해주세요.")
                            else:
                                conn = get_db()
                                conn.execute("UPDATE submissions SET teacher_comment=? WHERE id=?", (comment, sub["id"]))
                                conn.commit()
                                conn.close()
                                st.success("코멘트가 저장되었습니다! ✅")
                                st.rerun()

    elif "💬 질문 관리" in page:
        st.subheader("💬 질문 관리")
        tab1, tab2 = st.tabs(["미답변 질문", "전체 질문"])

        with tab1:
            conn = get_db()
            questions = conn.execute("""
                SELECT q.*, s.name AS student_name, s.grade, s.class_name
                FROM questions q
                JOIN students s ON q.student_id = s.id
                WHERE q.teacher_id=? AND q.is_answered=0
                ORDER BY q.created_at DESC
            """, (tid,)).fetchall()
            conn.close()
            if not questions:
                st.info("미답변 질문이 없습니다. 🎉")
            else:
                for q in questions:
                    student_tag = f"{q['student_name']} ({q['grade']} {q['class_name']})"
                    with st.expander(f"⏳ 미답변  |  {q['title']}  —  {student_tag}  {q['created_at'][:10]}"):
                        if q["image_paths"]:
                            st.markdown("**📸 첨부 이미지:**")
                            for fp in q["image_paths"].split("|"):
                                if os.path.exists(fp):
                                    st.image(fp, use_column_width=True)
                        st.markdown("**질문 내용:**")
                        st.write(q["content"])
                        st.divider()
                        with st.form(f"answer_unanswered_{q['id']}"):
                            answer_text = st.text_area("답변 입력", value="", height=120)
                            if st.form_submit_button("답변 등록 ✅", type="primary", use_container_width=True):
                                if not answer_text.strip():
                                    st.error("답변 내용을 입력해주세요.")
                                else:
                                    conn = get_db()
                                    conn.execute("""
                                        UPDATE questions SET answer=?, is_answered=1,
                                        answered_at=datetime('now','localtime') WHERE id=?
                                    """, (answer_text.strip(), q["id"]))
                                    conn.commit()
                                    conn.close()
                                    st.success("답변이 등록되었습니다! ✅")
                                    st.rerun()

        with tab2:
            conn = get_db()
            questions = conn.execute("""
                SELECT q.*, s.name AS student_name, s.grade, s.class_name
                FROM questions q
                JOIN students s ON q.student_id = s.id
                WHERE q.teacher_id=?
                ORDER BY q.is_answered ASC, q.created_at DESC
            """, (tid,)).fetchall()
            conn.close()
            if not questions:
                st.info("아직 질문이 없습니다.")
            else:
                for q in questions:
                    status = "✅ 답변 완료" if q["is_answered"] else "⏳ 미답변"
                    student_tag = f"{q['student_name']} ({q['grade']} {q['class_name']})"
                    with st.expander(f"{status}  |  {q['title']}  —  {student_tag}  {q['created_at'][:10]}"):
                        if q["image_paths"]:
                            st.markdown("**📸 첨부 이미지:**")
                            for fp in q["image_paths"].split("|"):
                                if os.path.exists(fp):
                                    st.image(fp, use_column_width=True)
                        st.markdown("**질문 내용:**")
                        st.write(q["content"])
                        st.divider()
                        if q["is_answered"] and q["answer"]:
                            st.markdown("**내 답변:**")
                            st.success(q["answer"])
                            st.caption(f"답변일: {q['answered_at'][:10] if q['answered_at'] else ''}")
                        with st.form(f"answer_all_{q['id']}"):
                            answer_text = st.text_area(
                                "답변 수정" if q["is_answered"] else "답변 입력",
                                value=q["answer"] or "", height=120)
                            if st.form_submit_button("수정 저장" if q["is_answered"] else "답변 등록 ✅", type="primary", use_container_width=True):
                                if not answer_text.strip():
                                    st.error("답변 내용을 입력해주세요.")
                                else:
                                    conn = get_db()
                                    conn.execute("""
                                        UPDATE questions SET answer=?, is_answered=1,
                                        answered_at=datetime('now','localtime') WHERE id=?
                                    """, (answer_text.strip(), q["id"]))
                                    conn.commit()
                                    conn.close()
                                    st.success("답변이 저장되었습니다! ✅")
                                    st.rerun()

    elif page == "🎬 영상 관리":
        st.subheader("🎬 영상 관리")
        tab1, tab2 = st.tabs(["영상 목록","영상 등록"])
        with tab2:
            with st.form("add_video"):
                v_title    = st.text_input("영상 제목 *", placeholder="예) 1강 문학 개념 정리")
                v_url      = st.text_input("유튜브 URL *", placeholder="https://www.youtube.com/watch?v=...")
                v_category = st.text_input("폴더(카테고리)", placeholder="예) 1단원, 문학, 중간고사 대비")
                col1, col2 = st.columns(2)
                grades     = get_grades() or ["중1","중2","중3","고1","고2","고3"]
                v_grade    = col1.selectbox("학년 *", grades, key="v_grade")
                classes    = get_classes(v_grade) or ["A반","B반"]
                v_class    = col2.selectbox("반 *", classes, key="v_class")
                if st.form_submit_button("영상 등록 ✅", type="primary", use_container_width=True):
                    if not v_title.strip() or not v_url.strip():
                        st.error("제목과 URL을 입력해주세요.")
                    else:
                        conn = get_db()
                        conn.execute(
                            "INSERT INTO videos (title, youtube_url, grade, class_name, category, teacher_id) VALUES (?,?,?,?,?,?)",
                            (v_title.strip(), v_url.strip(), v_grade, v_class, v_category.strip() or "기본", tid))
                        conn.commit()
                        conn.close()
                        st.success(f"✅ '{v_title}' 등록 완료!")
                        st.rerun()
        with tab1:
            conn = get_db()
            videos = conn.execute("SELECT * FROM videos WHERE teacher_id=? ORDER BY grade, class_name, category", (tid,)).fetchall()
            conn.close()
            if not videos:
                st.info("등록된 영상이 없습니다.")
            else:
                group_map = defaultdict(lambda: defaultdict(list))
                for v in videos:
                    group_map[f"{v['grade']} {v['class_name']}"][v["category"] or "기본"].append(v)
                for group, cat_map in group_map.items():
                    total = sum(len(vl) for vl in cat_map.values())
                    with st.expander(f"👥 {group}  —  영상 {total}개"):
                        for cat, vlist in cat_map.items():
                            with st.expander(f"📁 {cat}  ({len(vlist)}개)"):
                                for v in vlist:
                                    with st.expander(f"🎬 {v['title']}"):
                                        st.components.v1.iframe(youtube_embed_url(v["youtube_url"]), height=300)
                                        if st.button("🗑 삭제", key=f"vdel_{v['id']}"):
                                            conn = get_db()
                                            conn.execute("DELETE FROM videos WHERE id=?", (v["id"],))
                                            conn.commit()
                                            conn.close()
                                            st.rerun()

    elif page == "📢 공지사항":
        st.subheader("📢 과목별 공지사항")
        tid_n = st.session_state.teacher_id
        conn = get_db()
        tinfo_n = conn.execute("SELECT * FROM teachers WHERE id=?", (tid_n,)).fetchone()
        # 이 선생님이 담당하는 반 목록
        assigned_n = conn.execute(
            "SELECT DISTINCT grade, class_name FROM timetable WHERE teacher_name=? ORDER BY grade, class_name",
            (tinfo_n["name"],)).fetchall()
        conn.close()

        ntab_w, ntab_l = st.tabs(["✏️ 공지 작성", "📋 내 공지 목록"])

        with ntab_w:
            if not assigned_n:
                st.info("시간표에 배정된 반이 없습니다. 먼저 시간표를 등록해주세요.")
            else:
                cls_opts = [f"{r['grade']} {r['class_name']}" for r in assigned_n]
                with st.form("teacher_notice_form"):
                    sel_cls_n = st.selectbox("공지할 반 *", cls_opts)
                    n_title   = st.text_input("제목 *", placeholder="예) 다음 수업 준비물 안내")
                    n_content = st.text_area("내용 *", height=150)
                    if st.form_submit_button("공지 등록 ✅", type="primary", use_container_width=True):
                        if not n_title.strip() or not n_content.strip():
                            st.error("제목과 내용을 모두 입력해주세요.")
                        else:
                            g_n, cn_n = sel_cls_n.split(" ", 1)
                            conn = get_db()
                            conn.execute(
                                "INSERT INTO notices (notice_type, teacher_id, grade, class_name, title, content) VALUES ('subject',?,?,?,?,?)",
                                (tid_n, g_n, cn_n, n_title.strip(), n_content.strip()))
                            conn.commit()
                            conn.close()
                            st.success(f"✅ {sel_cls_n} 공지가 등록되었습니다!")
                            st.rerun()

        with ntab_l:
            conn = get_db()
            my_notices = conn.execute(
                "SELECT * FROM notices WHERE teacher_id=? ORDER BY created_at DESC",
                (tid_n,)).fetchall()
            conn.close()
            if not my_notices:
                st.info("작성한 공지가 없습니다.")
            else:
                for n in my_notices:
                    st.markdown(
                        f"<div style='background:#1e293b;border-left:4px solid #3b82f6;border-radius:6px;padding:14px 16px;margin-bottom:6px;'>"
                        f"<div style='font-size:0.75rem;color:#64748b;margin-bottom:4px;'>📘 {n['grade']} {n['class_name']} · {n['created_at'][:10]}</div>"
                        f"<div style='font-weight:bold;font-size:1rem;margin-bottom:6px;'>{n['title']}</div>"
                        f"<div style='color:#cbd5e1;white-space:pre-wrap;'>{n['content']}</div>"
                        f"</div>", unsafe_allow_html=True)
                    if st.button("🗑 삭제", key=f"del_tn_{n['id']}"):
                        conn = get_db()
                        conn.execute("DELETE FROM notices WHERE id=?", (n["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# 통합 관리자 페이지
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.role == "admin":
    with st.sidebar:
        st.markdown("### 🔑 통합 관리자")
        st.divider()
        page = st.radio("메뉴", ["📊 전체 현황","👩‍🏫 선생님 관리","👥 학생 관리","🏫 클래스 관리","🗓 시간표 관리","📢 공지사항"])
        st.divider()
        if st.button("로그아웃", use_container_width=True):
            st.session_state.role = None
            st.rerun()

    if page == "📊 전체 현황":
        st.subheader("📊 전체 현황")
        conn = get_db()
        n_t = conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0]
        n_s = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        n_a = conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
        n_sub = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        conn.close()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("선생님", n_t)
        c2.metric("학생", n_s)
        c3.metric("전체 과제", n_a)
        c4.metric("전체 제출", n_sub)
        st.divider()
        st.subheader("📬 최근 제출 현황")
        conn = get_db()
        recent = conn.execute("""
            SELECT s.submitted_at, st.name, st.grade, st.class_name,
                   a.title, t.subject, t.name AS teacher_name, s.is_checked
            FROM submissions s
            JOIN students st ON s.student_id=st.id
            JOIN assignments a ON s.assignment_id=a.id
            LEFT JOIN teachers t ON a.teacher_id=t.id
            ORDER BY s.submitted_at DESC LIMIT 20
        """).fetchall()
        conn.close()
        if recent:
            import pandas as pd
            df = pd.DataFrame([dict(r) for r in recent])
            df = df[["submitted_at","name","grade","class_name","subject","title","teacher_name","is_checked"]]
            df.columns = ["제출시각","학생","학년","반","과목","과제명","담당선생님","확인"]
            df["확인"] = df["확인"].map({1:"✔ 완료", 0:"⏳ 검토 중"})
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("제출 내역이 없습니다.")

    elif page == "👩‍🏫 선생님 관리":
        st.subheader("👩‍🏫 선생님 관리")
        tab1, tab2 = st.tabs(["선생님 목록","선생님 추가"])
        with tab1:
            conn = get_db()
            teachers = conn.execute("SELECT id, name, username, subject, created_at FROM teachers ORDER BY subject").fetchall()
            conn.close()
            if not teachers:
                st.info("등록된 선생님이 없습니다.")
            else:
                import pandas as pd
                df = pd.DataFrame([dict(t) for t in teachers])
                df = df[["name","username","subject","created_at"]]
                df.columns = ["이름","아이디","담당과목","등록일"]
                st.dataframe(df, use_container_width=True, hide_index=True)
                st.divider()
                st.markdown("#### 🗑 선생님 삭제")
                t_options = {f"{t['name']} ({t['subject']})": t["id"] for t in teachers}
                del_target = st.selectbox("삭제할 선생님", list(t_options.keys()))
                if st.button("삭제", type="secondary"):
                    conn = get_db()
                    conn.execute("DELETE FROM teachers WHERE id=?", (t_options[del_target],))
                    conn.commit()
                    conn.close()
                    st.success("삭제되었습니다.")
                    st.rerun()
        with tab2:
            st.markdown("#### 새 선생님 계정 추가")
            with st.form("add_teacher"):
                col1, col2 = st.columns(2)
                t_name    = col1.text_input("이름 *", placeholder="김철수")
                t_subject = col2.text_input("담당과목 *", placeholder="국어")
                col3, col4 = st.columns(2)
                t_user    = col3.text_input("아이디 *", placeholder="teacher01")
                t_pw      = col4.text_input("비밀번호 *", type="password")
                if st.form_submit_button("선생님 추가 ✅", type="primary", use_container_width=True):
                    if not all([t_name, t_subject, t_user, t_pw]):
                        st.error("모든 항목을 입력해주세요.")
                    else:
                        conn = get_db()
                        try:
                            conn.execute(
                                "INSERT INTO teachers (name, username, password_hash, subject) VALUES (?,?,?,?)",
                                (t_name.strip(), t_user.strip(), hash_pw(t_pw), t_subject.strip()))
                            conn.commit()
                            st.success(f"✅ {t_name} 선생님 계정 생성 완료! (아이디: {t_user})")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("이미 존재하는 아이디입니다.")
                        finally:
                            conn.close()

    elif page == "🏫 클래스 관리":
        st.subheader("🏫 클래스 관리")
        conn = get_db()
        all_students_c = conn.execute("SELECT * FROM students ORDER BY grade, class_name, name").fetchall()
        all_teachers_c = conn.execute("SELECT * FROM teachers ORDER BY name").fetchall()
        conn.close()

        ctab1, ctab2 = st.tabs(["📋 선생님별 반 현황", "🔄 반 편성 변경"])

        # ── 선생님별 반 현황 ────────────────────────────────────────
        with ctab1:
            if not all_teachers_c:
                st.info("등록된 선생님이 없습니다.")
            else:
                t_opts = {f"{t['name']} ({t['subject']})": t for t in all_teachers_c}
                sel_t_name = st.selectbox("👩‍🏫 선생님 선택", list(t_opts.keys()), key="class_mgmt_teacher")
                sel_t = t_opts[sel_t_name]
                st.divider()

                # 이 선생님의 시간표에서 담당 반 조회
                conn = get_db()
                t_classes = conn.execute("""
                    SELECT DISTINCT grade, class_name FROM timetable
                    WHERE teacher_name=? ORDER BY grade, class_name
                """, (sel_t["name"],)).fetchall()
                conn.close()

                if not t_classes:
                    st.info(f"{sel_t['name']} 선생님에게 배정된 반이 없습니다.")
                else:
                    total_t = 0
                    class_rows = []
                    for tc in t_classes:
                        conn = get_db()
                        sts = conn.execute(
                            "SELECT * FROM students WHERE grade=? AND class_name=? ORDER BY name",
                            (tc["grade"], tc["class_name"])).fetchall()
                        conn.close()
                        total_t += len(sts)
                        class_rows.append((tc["grade"], tc["class_name"], sts))

                    st.caption(f"담당 {len(class_rows)}개 반  ·  총 {total_t}명")

                    for grade, class_name, sts in class_rows:
                        with st.expander(f"📋 {grade} {class_name}  —  {len(sts)}명", expanded=True):
                            if not sts:
                                st.caption("등록된 학생이 없습니다.")
                            else:
                                cols = st.columns(4)
                                for i, s in enumerate(sts):
                                    cur_g = calc_current_grade(s["base_grade"] or s["grade"], s["enrollment_year"]) if s["enrollment_year"] else s["grade"]
                                    cols[i % 4].markdown(f"• {s['name']}  `{cur_g}`")

        # ── 반 편성 변경 ───────────────────────────────────────────
        with ctab2:
            st.markdown("#### 🔄 반 편성 일괄 변경")
            st.caption("특정 반의 모든 학생을 새 반으로 이동하거나, 학년을 일괄 업데이트합니다.")

            btab1, btab2 = st.tabs(["👥 반 전체 이동", "📅 학년 일괄 업데이트"])

            with btab1:
                st.markdown("**현재 반 → 새 반으로 전체 이동**")
                existing_classes = sorted(set(
                    f"{s['grade']} {s['class_name']}" for s in all_students_c if s['class_name']
                ))
                if not existing_classes:
                    st.info("편성된 반이 없습니다.")
                else:
                    with st.form("bulk_class_move"):
                        bc1, bc2 = st.columns(2)
                        from_class   = bc1.selectbox("이동할 반 (현재)", existing_classes)
                        new_grade_b  = bc2.selectbox("새 학년", GRADE_LIST)
                        bc3, bc4 = st.columns(2)
                        new_class_b  = bc3.selectbox("새 반", ["A반","B반","C반","D반","없음"])
                        new_enroll_b = bc4.number_input("등록 연도 (변경 시)", min_value=2020,
                            max_value=date.today().year, value=date.today().year, step=1)
                        st.caption("💡 등록 연도를 변경하면 학년이 자동 재계산됩니다.")
                        if st.form_submit_button("일괄 이동 ✅", type="primary", use_container_width=True):
                            from_g, from_cn = from_class.split(" ", 1)
                            new_cn = new_class_b if new_class_b != "없음" else ""
                            new_cur_g = calc_current_grade(new_grade_b, new_enroll_b)
                            conn = get_db()
                            affected = conn.execute(
                                "SELECT COUNT(*) FROM students WHERE grade=? AND class_name=?",
                                (from_g, from_cn)).fetchone()[0]
                            conn.execute(
                                "UPDATE students SET grade=?, class_name=?, base_grade=?, enrollment_year=? WHERE grade=? AND class_name=?",
                                (new_cur_g, new_cn, new_grade_b, int(new_enroll_b), from_g, from_cn))
                            conn.commit(); conn.close()
                            st.success(f"✅ {from_class} → {new_grade_b} {new_cn}  {affected}명 이동 완료!")
                            st.rerun()

            with btab2:
                st.markdown("**전체 학생 학년 자동 업데이트**")
                st.caption("등록 연도 기준으로 자동 계산된 학년을 DB에 반영합니다.")
                if st.button("🔄 전체 학생 학년 자동 업데이트", type="primary", use_container_width=True):
                    conn = get_db()
                    updated = 0
                    for s in all_students_c:
                        if s["base_grade"] and s["enrollment_year"]:
                            new_g = calc_current_grade(s["base_grade"], s["enrollment_year"])
                            if new_g != s["grade"]:
                                conn.execute("UPDATE students SET grade=? WHERE id=?", (new_g, s["id"]))
                                updated += 1
                    conn.commit(); conn.close()
                    st.success(f"✅ {updated}명의 학년이 업데이트되었습니다!")
                    st.rerun()

    elif page == "🗓 시간표 관리":
        st.subheader("🗓 시간표 관리")
        DAYS    = ["월","화","수","목","금"]
        PERIODS = [1,2,3,4]

        tab_tt, tab_sch = st.tabs(["📋 선생님별 시간표 편집", "📅 날짜별 일정 편집"])

        # ── 선생님별 시간표 편집 ──
        with tab_tt:
            conn = get_db()
            teachers_all = conn.execute("SELECT * FROM teachers ORDER BY subject, name").fetchall()
            conn.close()

            if not teachers_all:
                st.info("등록된 선생님이 없습니다. 선생님 관리에서 먼저 등록해주세요.")
            else:
                # 선생님 선택
                t_opts = {f"{t['subject']} - {t['name']}": t for t in teachers_all}
                sel_t_key = st.selectbox("선생님 선택", list(t_opts.keys()), key="tt_teacher_sel")
                sel_teacher = t_opts[sel_t_key]
                st.caption(f"📌 {sel_teacher['name']} 선생님의 수업을 요일×교시에 배치하세요.")

                # 수업 배정된 반만 표시 (student_teachers 기준)
                conn = get_db()
                assigned_classes = conn.execute("""
                    SELECT DISTINCT s.grade, s.class_name
                    FROM student_teachers st
                    JOIN students s ON st.student_id = s.id
                    WHERE st.teacher_id = ?
                    ORDER BY s.grade, s.class_name
                """, (sel_teacher["id"],)).fetchall()
                existing_tt = conn.execute(
                    "SELECT * FROM timetable WHERE teacher_name=?",
                    (sel_teacher["name"],)).fetchall()
                conn.close()

                if not assigned_classes:
                    st.warning(f"⚠️ {sel_teacher['name']} 선생님에게 배정된 학생이 없습니다. 학생 관리 → 수업 배정에서 먼저 배정해주세요.")
                    st.stop()

                CLASS_OPTIONS = [f"{r['grade']} {r['class_name']}" for r in assigned_classes]
                st.caption(f"📌 배정된 반: {', '.join(CLASS_OPTIONS)}")

                # 현재 저장된 데이터: {(day, period): ["고1 A반", "고1 B반", ...]}  ← 여러 반 가능
                cur_map = {}
                for r in existing_tt:
                    key = (r["day"], r["period"])
                    cur_map.setdefault(key, []).append(f"{r['grade']} {r['class_name']}")

                st.divider()

                # st.form 안에서 multiselect 사용 → 저장 버튼 눌러야 rerun
                with st.form(f"tt_teacher_form_{sel_teacher['id']}"):
                    h_cols = st.columns([1]+[2]*len(DAYS))
                    h_cols[0].markdown("**교시**")
                    for i, d in enumerate(DAYS):
                        h_cols[i+1].markdown(f"**{d}요일**")
                    st.markdown("<hr style='margin:2px 0;border:none;border-top:1px solid #1e293b;'>", unsafe_allow_html=True)

                    cell_selections = {}
                    for p in PERIODS:
                        row_bg = "#111827" if p % 2 == 1 else "#0d1117"
                        r_cols = st.columns([1]+[2]*len(DAYS))
                        r_cols[0].markdown(
                            f"<div style='background:{row_bg};border-left:3px solid #3b82f6;padding:8px;border-radius:4px;'><b>{p}교시</b></div>",
                            unsafe_allow_html=True)
                        for i, d in enumerate(DAYS):
                            cur_vals = cur_map.get((d, p), [])
                            # 저장된 값 중 현재 CLASS_OPTIONS에 있는 것만 기본 선택
                            valid_defaults = [v for v in cur_vals if v in CLASS_OPTIONS]
                            sel = r_cols[i+1].multiselect(
                                "", CLASS_OPTIONS,
                                default=valid_defaults,
                                key=f"tt_t_{sel_teacher['id']}_{d}_{p}",
                                label_visibility="collapsed"
                            )
                            cell_selections[(d, p)] = sel  # 리스트
                        st.markdown("<hr style='margin:2px 0;border:none;border-top:1px solid #1e293b;'>", unsafe_allow_html=True)

                    if st.form_submit_button("💾 시간표 저장", type="primary", use_container_width=True):
                        conn = get_db()
                        conn.execute("DELETE FROM timetable WHERE teacher_name=?", (sel_teacher["name"],))
                        for (d, p), cls_list in cell_selections.items():
                            for cls_val in cls_list:
                                g, cn = cls_val.split(" ", 1)
                                conn.execute("""
                                    INSERT INTO timetable (grade, class_name, day, period, subject, teacher_name)
                                    VALUES (?,?,?,?,?,?)
                                    ON CONFLICT(grade, class_name, day, period)
                                    DO UPDATE SET subject=excluded.subject, teacher_name=excluded.teacher_name
                                """, (g, cn, d, p, sel_teacher["subject"], sel_teacher["name"]))
                        conn.commit()
                        conn.close()
                        st.success(f"✅ {sel_teacher['name']} 선생님 시간표가 저장되었습니다!")
                        st.rerun()

                # 현재 저장된 시간표 미리보기 (중복 배치 지원)
                conn = get_db()
                preview = conn.execute(
                    "SELECT * FROM timetable WHERE teacher_name=? AND subject IS NOT NULL ORDER BY period",
                    (sel_teacher["name"],)).fetchall()
                conn.close()
                if preview:
                    st.divider()
                    st.markdown("#### 👁 저장된 시간표")
                    # 한 칸에 여러 반 리스트로 저장
                    tt_multi = {}
                    for r in preview:
                        tt_multi.setdefault((r["day"], r["period"]), []).append(r)
                    max_p = max(r["period"] for r in preview)
                    hc = st.columns([1]+[2]*len(DAYS))
                    hc[0].markdown("**교시**")
                    for i, d in enumerate(DAYS): hc[i+1].markdown(f"**{d}**")
                    for p in range(1, max_p+1):
                        row_bg = "#111827" if p % 2 == 1 else "#0d1117"
                        st.markdown("<hr style='margin:2px 0;border:none;border-top:1px solid #1e293b;'>", unsafe_allow_html=True)
                        rc = st.columns([1]+[2]*len(DAYS))
                        rc[0].markdown(
                            f"<div style='background:{row_bg};border-left:3px solid #3b82f6;padding:8px;border-radius:4px;'><b>{p}교시</b></div>",
                            unsafe_allow_html=True)
                        for i, d in enumerate(DAYS):
                            cells = tt_multi.get((d, p), [])
                            if cells:
                                inner = "".join([
                                    f"<div style='background:#1e3a5f;border-radius:5px;padding:5px 8px;"
                                    f"text-align:center;font-size:0.8rem;margin-bottom:3px;'>"
                                    f"<b>{c['grade']} {c['class_name']}</b>"
                                    f"<div style='color:#94a3b8;font-size:0.7rem;'>{c['subject']}</div>"
                                    f"</div>"
                                    for c in cells
                                ])
                                rc[i+1].markdown(inner, unsafe_allow_html=True)
                            else:
                                rc[i+1].markdown(
                                    f"<div style='background:{row_bg};border-radius:5px;padding:6px 8px;text-align:center;color:#334155;font-size:0.8rem;margin:2px;'>—</div>",
                                    unsafe_allow_html=True)

                # 교시 시간 설정
                st.divider()
                st.markdown("#### ⏰ 교시별 시간 설정")
                st.caption("교시 시간은 전체 공통으로 적용됩니다.")
                conn = get_db()
                pt_rows = conn.execute(
                    "SELECT * FROM period_times WHERE grade='공통' AND class_name='공통' ORDER BY period"
                ).fetchall()
                conn.close()
                pt_map = {r["period"]: dict(r) for r in pt_rows}
                DEFAULT_TIMES = {1:("09:00","09:50"), 2:("10:00","10:50"), 3:("11:00","11:50"), 4:("12:00","12:50")}

                with st.form("tt_period_times"):
                    pt_inputs = {}
                    for p in PERIODS:
                        pc1, pc2, pc3 = st.columns([1,2,2])
                        pc1.markdown(f"**{p}교시**")
                        cur = pt_map.get(p, {})
                        s_time = pc2.text_input("시작", value=cur.get("start_time") or DEFAULT_TIMES[p][0], key=f"pt_s_{p}")
                        e_time = pc3.text_input("종료", value=cur.get("end_time") or DEFAULT_TIMES[p][1], key=f"pt_e_{p}")
                        pt_inputs[p] = ((s_time or "").strip(), (e_time or "").strip())
                    if st.form_submit_button("⏰ 시간 저장", use_container_width=True):
                        conn = get_db()
                        for p, (s, e) in pt_inputs.items():
                            conn.execute("""
                                INSERT INTO period_times (grade, class_name, period, start_time, end_time)
                                VALUES ('공통','공통',?,?,?)
                                ON CONFLICT(grade, class_name, period)
                                DO UPDATE SET start_time=excluded.start_time, end_time=excluded.end_time
                            """, (p, s or None, e or None))
                        conn.commit()
                        conn.close()
                        st.success("⏰ 교시 시간이 저장되었습니다! ✅")
                        st.rerun()

        # ── 날짜별 일정 편집 ──
        with tab_sch:
            st.markdown("#### 날짜별 일정 편집")
            conn = get_db()
            all_grades_sch = [r["grade"] for r in conn.execute("SELECT DISTINCT grade FROM students ORDER BY grade").fetchall()]
            conn.close()
            if not all_grades_sch:
                st.info("등록된 학생이 없습니다.")
            else:
                col1, col2 = st.columns(2)
                sel_grade_sch = col1.selectbox("학년", all_grades_sch, key="sch_grade")
                conn = get_db()
                classes_sch = [r["class_name"] for r in conn.execute(
                    "SELECT DISTINCT class_name FROM students WHERE grade=? ORDER BY class_name", (sel_grade_sch,)).fetchall()]
                conn.close()
                sel_class_sch = col2.selectbox("반", classes_sch, key="sch_class")
                st.markdown(f"#### {sel_grade_sch} {sel_class_sch} 날짜별 일정")
                tab_add, tab_list = st.tabs(["➕ 일정 추가", "📋 일정 목록"])

                with tab_add:
                    with st.form("add_schedule"):
                        s_title = st.text_input("일정 제목 *", placeholder="예) 중간고사, 보충수업, 현장학습")
                        col1, col2 = st.columns(2)
                        s_date  = col1.date_input("날짜 *")
                        s_desc  = st.text_area("상세 내용 (선택)", placeholder="예) 3~4교시 수학, 5교시 국어")
                        col3, col4, col5 = st.columns(3)
                        s_start = col3.text_input("시작 시간", placeholder="09:00")
                        s_end   = col4.text_input("종료 시간", placeholder="12:00")
                        s_teacher = col5.text_input("담당 선생님 (선택)")
                        if st.form_submit_button("일정 추가 ✅", type="primary", use_container_width=True):
                            if not s_title.strip():
                                st.error("제목을 입력해주세요.")
                            else:
                                conn = get_db()
                                conn.execute("""
                                    INSERT INTO schedule (grade, class_name, event_date, start_time, end_time, title, description, teacher_name)
                                    VALUES (?,?,?,?,?,?,?,?)
                                """, (sel_grade_sch, sel_class_sch, str(s_date),
                                      s_start.strip() or None, s_end.strip() or None,
                                      s_title.strip(), s_desc.strip() or None, s_teacher.strip() or None))
                                conn.commit()
                                conn.close()
                                st.success(f"✅ '{s_title}' 일정이 추가되었습니다!")
                                st.rerun()

                with tab_list:
                    conn = get_db()
                    schedules = conn.execute(
                        "SELECT * FROM schedule WHERE grade=? AND class_name=? ORDER BY event_date, start_time",
                        (sel_grade_sch, sel_class_sch)).fetchall()
                    conn.close()
                    if not schedules:
                        st.info("등록된 일정이 없습니다.")
                    else:
                        sch_map = defaultdict(list)
                        for s in schedules:
                            sch_map[s["event_date"]].append(s)
                        for event_date, items in sch_map.items():
                            try:
                                from datetime import datetime as dt2
                                d_obj = dt2.strptime(event_date, "%Y-%m-%d")
                                day_kr = ["월","화","수","목","금","토","일"][d_obj.weekday()]
                                date_label = f"{d_obj.month}/{d_obj.day} ({day_kr})"
                            except:
                                date_label = event_date
                            st.markdown(f"**📅 {date_label}**")
                            for item in items:
                                time_str = item["start_time"] or ""
                                if time_str and item["end_time"]:
                                    time_str += f" ~ {item['end_time']}"
                                with st.expander(f"🔹 {item['title']}  {time_str}"):
                                    if item["description"]: st.write(item["description"])
                                    if item["teacher_name"]: st.caption(f"담당: {item['teacher_name']}")
                                    if st.button("🗑 삭제", key=f"del_sch_{item['id']}"):
                                        conn = get_db()
                                        conn.execute("DELETE FROM schedule WHERE id=?", (item["id"],))
                                        conn.commit()
                                        conn.close()
                                        st.rerun()
                            st.divider()

    elif page == "📢 공지사항":
        st.subheader("📢 공지사항 관리")
        ntab_write, ntab_list = st.tabs(["✏️ 공지 작성", "📋 공지 목록"])

        with ntab_write:
            st.markdown("#### 📣 학원 전체 공지 작성")
            with st.form("admin_notice_form"):
                n_title   = st.text_input("제목 *", placeholder="[공지] 학원 휴원 안내")
                n_content = st.text_area("내용 *", height=150)
                if st.form_submit_button("공지 등록 ✅", type="primary", use_container_width=True):
                    if not n_title.strip() or not n_content.strip():
                        st.error("제목과 내용을 모두 입력해주세요.")
                    else:
                        conn = get_db()
                        conn.execute(
                            "INSERT INTO notices (notice_type, title, content) VALUES ('global',?,?)",
                            (n_title.strip(), n_content.strip()))
                        conn.commit()
                        conn.close()
                        st.success("✅ 전체 공지가 등록되었습니다!")
                        st.rerun()

        with ntab_list:
            conn = get_db()
            all_notices = conn.execute(
                "SELECT * FROM notices WHERE notice_type='global' ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            if not all_notices:
                st.info("등록된 공지가 없습니다.")
            else:
                for n in all_notices:
                    st.markdown(
                        f"<div style='background:#1e293b;border-left:4px solid #f59e0b;border-radius:6px;padding:14px 16px;margin-bottom:6px;'>"
                        f"<div style='font-size:0.75rem;color:#64748b;margin-bottom:4px;'>📣 {n['created_at'][:10]}</div>"
                        f"<div style='font-weight:bold;font-size:1rem;margin-bottom:6px;'>{n['title']}</div>"
                        f"<div style='color:#cbd5e1;white-space:pre-wrap;'>{n['content']}</div>"
                        f"</div>", unsafe_allow_html=True)
                    if st.button("🗑 삭제", key=f"del_notice_{n['id']}"):
                        conn = get_db()
                        conn.execute("DELETE FROM notices WHERE id=?", (n["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()

    elif page == "👥 학생 관리":
        st.subheader("👥 학생 관리")

        # ── 학생 상세 편집 페이지 ──────────────────────────────────
        if st.session_state.admin_selected_student is not None:
            conn = get_db()
            sel_s_detail = conn.execute("SELECT * FROM students WHERE id=?",
                (st.session_state.admin_selected_student,)).fetchone()
            conn.close()
            if sel_s_detail:
                if st.button("← 목록으로 돌아가기"):
                    st.session_state.admin_selected_student = None
                    st.rerun()
                st.divider()
                cur_g_d = calc_current_grade(sel_s_detail["base_grade"] or sel_s_detail["grade"],
                    sel_s_detail["enrollment_year"]) if sel_s_detail["enrollment_year"] else sel_s_detail["grade"]
                st.markdown(f"### 👤 {sel_s_detail['name']}  `{sel_s_detail['student_code']}`")
                st.caption(f"현재 학년: **{cur_g_d}** {sel_s_detail['class_name']}  |  학부모 아이디: **{sel_s_detail['student_code']}p**")
                st.divider()

                det1, det2, det3 = st.tabs(["📝 기본 정보", "📱 연락처", "🗑 삭제"])

                with det1:
                    with st.form("detail_basic"):
                        d1, d2 = st.columns(2)
                        dn = d1.text_input("이름", value=sel_s_detail["name"])
                        ds = d2.text_input("학교", value=sel_s_detail["school"] or "")
                        d3, d4 = st.columns(2)
                        db_stored = sel_s_detail["base_grade"] or sel_s_detail["grade"] or GRADE_LIST[6]
                        db_idx    = GRADE_ORDER.get(db_stored, 6)
                        db_grade  = d3.selectbox("등록 당시 학년", GRADE_LIST, index=db_idx)
                        dcl_list  = ["A반","B반","C반","D반","없음"]
                        db_class  = d4.selectbox("반", dcl_list,
                            index=dcl_list.index(sel_s_detail["class_name"]) if sel_s_detail["class_name"] in dcl_list else 4)
                        db_enroll = st.number_input("등록 연도", min_value=2020, max_value=date.today().year,
                            value=int(sel_s_detail["enrollment_year"] or date.today().year), step=1)
                        dp = calc_current_grade(db_grade, db_enroll)
                        st.caption(f"📌 현재 자동 계산 학년: **{dp}**")
                        if st.form_submit_button("저장 ✅", type="primary", use_container_width=True):
                            conn = get_db()
                            conn.execute("UPDATE students SET name=?, school=?, base_grade=?, class_name=?, enrollment_year=?, grade=? WHERE id=?",
                                (dn.strip(), ds.strip() or None, db_grade,
                                 db_class if db_class != "없음" else "", int(db_enroll), dp,
                                 sel_s_detail["id"]))
                            conn.commit(); conn.close()
                            st.success("✅ 저장되었습니다!")
                            st.rerun()

                with det2:
                    with st.form("detail_contact"):
                        e1, e2 = st.columns(2)
                        dp_phone = e1.text_input("학생 전화번호", value=sel_s_detail["phone"] or "")
                        srel = sel_s_detail["parent_name"] or ""
                        rops = ["어머니","아버지","조모","조부","기타"]
                        drel = next((r for r in rops if f"({r})" in srel), rops[0])
                        dp_rel   = e2.selectbox("가족관계", rops, index=rops.index(drel))
                        dp_pphone = st.text_input("학부모 전화번호", value=sel_s_detail["parent_phone"] or "")
                        if st.form_submit_button("저장 ✅", type="primary", use_container_width=True):
                            conn = get_db()
                            conn.execute("UPDATE students SET phone=?, parent_name=?, parent_phone=? WHERE id=?",
                                (dp_phone.strip(), f"{sel_s_detail['name']}({dp_rel})", dp_pphone.strip(), sel_s_detail["id"]))
                            conn.commit(); conn.close()
                            st.success("✅ 저장되었습니다!")
                            st.rerun()

                with det3:
                    p_uname = str(sel_s_detail["student_code"]) + "p"
                    st.info(f"📱 학부모 아이디: **{p_uname}**")
                    if st.button("🔄 학부모 비밀번호 초기화 (→ 학번)", key="detail_pw_reset"):
                        conn = get_db()
                        conn.execute("UPDATE parents SET password_hash=? WHERE username=?",
                            (hash_pw(str(sel_s_detail["student_code"])), p_uname))
                        conn.commit(); conn.close()
                        st.success(f"✅ 비밀번호가 학번으로 초기화되었습니다.")
                    st.divider()
                    st.warning(f"**{sel_s_detail['name']}** 학생을 삭제하면 모든 기록이 삭제됩니다.")
                    del_confirm = st.text_input("삭제하려면 학생 이름을 정확히 입력하세요")
                    if st.button("🗑 영구 삭제", type="primary", use_container_width=True):
                        if del_confirm.strip() == sel_s_detail["name"]:
                            conn = get_db()
                            conn.execute("DELETE FROM student_teachers WHERE student_id=?", (sel_s_detail["id"],))
                            conn.execute("DELETE FROM submissions WHERE student_id=?", (sel_s_detail["id"],))
                            conn.execute("DELETE FROM parents WHERE username=?", (p_uname,))
                            conn.execute("DELETE FROM students WHERE id=?", (sel_s_detail["id"],))
                            conn.commit(); conn.close()
                            st.session_state.admin_selected_student = None
                            st.success(f"✅ {sel_s_detail['name']} 학생이 삭제되었습니다.")
                            st.rerun()
                        else:
                            st.error("이름이 일치하지 않습니다.")
                st.stop()
            else:
                st.session_state.admin_selected_student = None

        tab1, tab2, tab3, tab4 = st.tabs(["📋 학생 목록", "➕ 학생 등록", "🔍 학생 검색", "🏫 수업 배정"])

        # ── 학생 목록 ──────────────────────────────────────────────
        with tab1:
            conn = get_db()
            students = conn.execute("SELECT * FROM students ORDER BY grade, class_name, name").fetchall()
            conn.close()

            # 학부모 계정 동기화 버튼
            with st.expander("🔧 학부모 계정 관리"):
                st.caption("학생 등록 후 학부모 계정이 생성되지 않았다면 아래 버튼을 눌러 일괄 생성하세요.")
                col_sync1, col_sync2 = st.columns(2)
                if col_sync1.button("🔄 전체 학생 학부모 계정 동기화", use_container_width=True):
                    conn2 = get_db()
                    all_sts = conn2.execute("SELECT * FROM students").fetchall()
                    conn2.close()
                    cnt = 0
                    for s in all_sts:
                        uname = str(s["student_code"]) + "p"
                        conn2 = get_db()
                        existing = conn2.execute("SELECT id FROM parents WHERE username=?", (uname,)).fetchone()
                        conn2.close()
                        if not existing:
                            create_parent_account(dict(s))
                            cnt += 1
                    st.success(f"✅ 완료! 신규 생성: {cnt}명 / 전체: {len(all_sts)}명")
                    st.cache_data.clear()
                # 현재 parents 테이블 현황
                conn2 = get_db()
                parent_count = conn2.execute("SELECT COUNT(*) FROM parents").fetchone()[0]
                student_count = conn2.execute("SELECT COUNT(*) FROM students").fetchone()[0]
                conn2.close()
                col_sync2.metric("학부모 계정 수", f"{parent_count}개", f"학생 {student_count}명")

            import pandas as pd
            if not students:
                st.info("등록된 학생이 없습니다.")
            else:
                df = pd.DataFrame([dict(s) for s in students])
                df["phone"]        = df.get("phone", "")
                df["parent_name"]  = df.get("parent_name", "")
                df["parent_phone"] = df.get("parent_phone", "")
                df["school"]       = df.get("school", "")
                def apply_grade(row):
                    try:
                        bg = row.get("base_grade") or row.get("grade") or ""
                        ey = row.get("enrollment_year")
                        if not bg or not ey or bg not in GRADE_ORDER:
                            return row.get("grade") or ""
                        return calc_current_grade(bg, int(ey))
                    except:
                        return row.get("grade") or ""
                df["current_grade"] = df.apply(apply_grade, axis=1)
                df = df[["name","student_code","current_grade","class_name","school","phone","parent_name","parent_phone","created_at"]]
                df.columns = ["이름","학번","학년(현재)","반","학교","학생연락처","학부모","학부모연락처","등록일"]
                st.dataframe(df, use_container_width=True, hide_index=True)



        # ── 학생 등록 ──────────────────────────────────────────────
        with tab2:
            st.markdown("#### ➕ 학생 등록")
            with st.form("admin_add_student"):
                col1, col2 = st.columns(2)
                new_name   = col1.text_input("이름 *", placeholder="홍길동")
                new_school = col2.text_input("학교", placeholder="패스파인더중학교")
                col3, col4 = st.columns(2)
                new_grade  = col3.selectbox("학년 *", GRADE_LIST)
                new_class  = col4.selectbox("반 *", ["A반","B반","C반","D반","없음"])
                col5, col6 = st.columns(2)
                new_phone  = col5.text_input("학생 연락처", placeholder="010-0000-0000")
                new_enroll = col6.number_input("등록 연도 *", min_value=2020, max_value=date.today().year, value=date.today().year, step=1)
                st.caption("💡 등록 연도 기준으로 매년 자동으로 학년이 올라갑니다.")
                col7, col8 = st.columns(2)
                new_parent_rel   = col7.selectbox("가족관계", ["어머니","아버지","조모","조부","기타"])
                new_parent_phone = col8.text_input("학부모 연락처", placeholder="010-0000-0000")
                if st.form_submit_button("학생 등록 ✅", type="primary", use_container_width=True):
                    if not new_name.strip():
                        st.error("이름을 입력해주세요.")
                    else:
                        code = name_to_code(new_name.strip())
                        parent_label = f"{new_name.strip()}({new_parent_rel})"
                        current_grade = calc_current_grade(new_grade, new_enroll)
                        conn = get_db()
                        try:
                            conn.execute(
                                "INSERT INTO students (name, student_code, grade, class_name, phone, parent_name, parent_phone, school, enrollment_year, base_grade) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (new_name.strip(), code, current_grade,
                                 new_class if new_class != "없음" else "",
                                 new_phone.strip() or None,
                                 parent_label,
                                 new_parent_phone.strip() or None,
                                 new_school.strip() or None,
                                 int(new_enroll), new_grade))
                            conn.commit()
                            # 학부모 계정 자동 생성
                            new_student = conn.execute("SELECT * FROM students WHERE student_code=?", (code,)).fetchone()
                            if new_student:
                                create_parent_account(dict(new_student))
                            grade_note = f" (현재 {current_grade})" if current_grade != new_grade else ""
                            st.success(f"✅ {new_name} 학생 등록 완료!  학번: **{code}**  학부모 아이디: **{code}p** / 비번: **{code}**")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error(f"이미 등록된 학생입니다. (학번: {code})")
                        finally:
                            conn.close()

        # ── 학생 검색 ──────────────────────────────────────────────
        with tab3:
            conn = get_db()
            all_students_mgmt = conn.execute("SELECT * FROM students ORDER BY grade, class_name, name").fetchall()
            conn.close()

            st.markdown("#### 🔍 학생 검색 및 정보 수정")
            search_q = st.text_input("이름 검색", placeholder="이름을 입력하세요...", key="student_search")

            if not search_q.strip():
                st.info("검색어를 입력하면 학생을 찾을 수 있습니다.")
            else:
                filtered = [s for s in all_students_mgmt if search_q.strip().lower() in s["name"].lower()]
                if not filtered:
                    st.warning("검색 결과가 없습니다.")
                else:
                    st.caption(f"검색 결과 {len(filtered)}명")
                    sel_s_mgmt = st.selectbox(
                        "학생 선택",
                        [f"{s['name']} ({s['grade']} {s['class_name']})" for s in filtered],
                        key="search_sel_student"
                    )
                    sel_info = next(s for s in filtered if f"{s['name']} ({s['grade']} {s['class_name']})" == sel_s_mgmt)

                    st.divider()
                    # 등록 / 수정 탭
                    edit_tab1, edit_tab2, edit_tab3 = st.tabs(["📝 기본 정보", "📱 연락처", "🗑 삭제"])

                    with edit_tab1:
                        with st.form("edit_basic_info"):
                            c1, c2 = st.columns(2)
                            e_name   = c1.text_input("이름", value=sel_info["name"])
                            e_school = c2.text_input("학교", value=sel_info["school"] or "")
                            c3, c4 = st.columns(2)
                            stored_base = sel_info["base_grade"] or sel_info["grade"] or GRADE_LIST[6]
                            base_idx = GRADE_ORDER.get(stored_base, 6)
                            e_base_grade = c3.selectbox("등록 당시 학년", GRADE_LIST, index=base_idx)
                            class_list_e = ["A반","B반","C반","D반","없음"]
                            cur_class_e = sel_info["class_name"] if sel_info["class_name"] in class_list_e else "없음"
                            e_class = c4.selectbox("반", class_list_e, index=class_list_e.index(cur_class_e))
                            e_enroll = st.number_input("등록 연도", min_value=2020, max_value=date.today().year,
                                value=int(sel_info["enrollment_year"] or date.today().year), step=1)
                            preview_g = calc_current_grade(e_base_grade, e_enroll)
                            st.caption(f"📌 현재 자동 계산 학년: **{preview_g}**")
                            if st.form_submit_button("저장 ✅", type="primary", use_container_width=True):
                                conn = get_db()
                                conn.execute(
                                    "UPDATE students SET name=?, school=?, base_grade=?, class_name=?, enrollment_year=?, grade=? WHERE id=?",
                                    (e_name.strip(), e_school.strip() or None, e_base_grade,
                                     e_class if e_class != "없음" else "", int(e_enroll), preview_g, sel_info["id"]))
                                conn.commit()
                                conn.close()
                                st.success("✅ 기본 정보가 저장되었습니다!")
                                st.rerun()

                    with edit_tab2:
                        with st.form("edit_contact_info"):
                            c1, c2 = st.columns(2)
                            e_phone = c1.text_input("학생 전화번호", value=sel_info["phone"] or "", placeholder="010-0000-0000")
                            stored_rel = sel_info["parent_name"] or ""
                            rel_opts = ["어머니","아버지","조모","조부","기타"]
                            detected_rel = next((r for r in rel_opts if f"({r})" in stored_rel), rel_opts[0])
                            e_parent_rel = c2.selectbox("가족관계", rel_opts, index=rel_opts.index(detected_rel))
                            e_parent_phone = st.text_input("학부모 전화번호", value=sel_info["parent_phone"] or "", placeholder="010-0000-0000")
                            if st.form_submit_button("저장 ✅", type="primary", use_container_width=True):
                                parent_label = f"{sel_info['name']}({e_parent_rel})"
                                conn = get_db()
                                conn.execute(
                                    "UPDATE students SET phone=?, parent_name=?, parent_phone=? WHERE id=?",
                                    (e_phone.strip(), parent_label, e_parent_phone.strip(), sel_info["id"]))
                                conn.commit()
                                conn.close()
                                st.success("✅ 연락처가 저장되었습니다!")
                                st.rerun()

                    with edit_tab3:
                        # 학부모 계정 정보
                        p_username = str(sel_info["student_code"]) + "p"
                        conn = get_db()
                        p_row = conn.execute("SELECT * FROM parents WHERE username=?", (p_username,)).fetchone()
                        conn.close()
                        if p_row:
                            st.info(f"📱 학부모 아이디: **{p_username}**")
                        else:
                            st.warning(f"학부모 계정 없음 (아이디: {p_username})")
                        if st.button("🔄 학부모 비밀번호 초기화 (→ 학생 이름)", key=f"reset_pw_{sel_info['id']}"):
                            create_parent_account(dict(sel_info))
                            # 이미 있으면 비번만 재설정
                            conn = get_db()
                            conn.execute("UPDATE parents SET password_hash=? WHERE username=?",
                                         (hash_pw(str(sel_info["student_code"])), p_username))
                            conn.commit()
                            conn.close()
                            st.success(f"✅ 학부모 비밀번호가 학번 **{sel_info['student_code']}** 으로 초기화되었습니다.")
                        st.divider()
                        st.warning(f"**{sel_info['name']}** 학생을 삭제하면 제출 기록과 배정 정보도 함께 삭제됩니다.")
                        confirm_del = st.text_input("삭제하려면 학생 이름을 정확히 입력하세요")
                        if st.button("🗑 영구 삭제", type="primary", use_container_width=True):
                            if confirm_del.strip() == sel_info["name"]:
                                conn = get_db()
                                conn.execute("DELETE FROM student_teachers WHERE student_id=?", (sel_info["id"],))
                                conn.execute("DELETE FROM submissions WHERE student_id=?", (sel_info["id"],))
                                conn.execute("DELETE FROM parents WHERE username=?", (p_username,))
                                conn.execute("DELETE FROM students WHERE id=?", (sel_info["id"],))
                                conn.commit()
                                conn.close()
                                st.success(f"✅ {sel_info['name']} 학생이 삭제되었습니다.")
                                st.rerun()
                            else:
                                st.error("이름이 일치하지 않습니다.")

        with tab4:
            st.markdown("#### 🏫 수업 배정")
            st.caption("학생을 선택해 학년·반을 지정하거나, 반 전체를 한 번에 변경할 수 있습니다.")

            assign_tab1, assign_tab2 = st.tabs(["👤 개별 배정", "👥 반 일괄 배정"])

            # ── 개별 배정 ──────────────────────────────────────────
            with assign_tab1:
                conn = get_db()
                all_students = conn.execute(
                    "SELECT * FROM students ORDER BY grade, class_name, name").fetchall()
                all_teachers = conn.execute(
                    "SELECT * FROM teachers ORDER BY subject, name").fetchall()
                conn.close()

                if not all_students:
                    st.info("등록된 학생이 없습니다.")
                else:
                    assign_search = st.text_input("🔍 학생 이름 검색", placeholder="이름 입력...", key="assign_search")
                    filtered_a = [s for s in all_students if assign_search.strip().lower() in s["name"].lower()] if assign_search.strip() else all_students
                    if not filtered_a:
                        st.warning("검색 결과가 없습니다.")
                        st.stop()
                    s_opts = {f"{s['name']} ({s['grade']} {s['class_name']})": s for s in filtered_a}
                    sel_name = st.selectbox("학생 선택", list(s_opts.keys()), key="assign_sel")
                    sel_s = s_opts[sel_name]

                    # 현재 배정 정보
                    conn = get_db()
                    cur_teachers = conn.execute("""
                        SELECT st.teacher_id, t.name, t.subject FROM student_teachers st
                        JOIN teachers t ON st.teacher_id=t.id
                        WHERE st.student_id=?
                    """, (sel_s["id"],)).fetchall()
                    conn.close()
                    cur_teacher_ids = {r["teacher_id"] for r in cur_teachers}

                    st.markdown(f"**현재:** {sel_s['grade']} {sel_s['class_name']}  |  학교: {sel_s['school'] or '미등록'}")
                    if cur_teachers:
                        st.markdown("**담당 선생님:** " + "  ·  ".join([f"{r['subject']} {r['name']}" for r in cur_teachers]))
                    st.divider()

                    # 학년/반/학교 — 학생 ID를 key에 포함해 학생 변경 시 자동 반영
                    col1, col2, col3 = st.columns(3)
                    sid_key = sel_s["id"]
                    cur_grade_idx = GRADE_LIST.index(sel_s["grade"]) if sel_s["grade"] in GRADE_LIST else 6
                    new_grade_a  = col1.selectbox("학년", GRADE_LIST, index=cur_grade_idx, key=f"assign_grade_{sid_key}")
                    class_list   = ["A반","B반","C반","D반","없음"]
                    cur_class    = sel_s["class_name"] if sel_s["class_name"] in class_list else "없음"
                    new_class_a  = col2.selectbox("반", class_list, index=class_list.index(cur_class), key=f"assign_class_{sid_key}")
                    new_school_a = col3.text_input("학교", value=sel_s["school"] or "", key=f"assign_school_{sid_key}")

                    st.divider()
                    st.markdown("**👩‍🏫 담당 선생님 배정**")
                    st.caption("담당할 선생님을 선택하세요. 체크 해제 시 배정 해제됩니다.")

                    selected_tids = []
                    if all_teachers:
                        # 과목별 그룹화
                        from itertools import groupby
                        subject_groups = {}
                        for t in all_teachers:
                            subject_groups.setdefault(t["subject"], []).append(t)
                        for subj, tlist in subject_groups.items():
                            st.markdown(f"*{subj}*")
                            tcols = st.columns(min(len(tlist), 4))
                            for i, t in enumerate(tlist):
                                checked = tcols[i % 4].checkbox(
                                    t["name"],
                                    value=(t["id"] in cur_teacher_ids),
                                    key=f"assign_t_{sel_s['id']}_{t['id']}"
                                )
                                if checked:
                                    selected_tids.append(t["id"])
                    else:
                        st.info("등록된 선생님이 없습니다.")

                    if st.button("배정 저장 ✅", type="primary", use_container_width=True, key="assign_save"):
                        conn = get_db()
                        conn.execute(
                            "UPDATE students SET grade=?, class_name=?, school=?, base_grade=? WHERE id=?",
                            (new_grade_a,
                             new_class_a if new_class_a != "없음" else "",
                             new_school_a.strip() or None,
                             new_grade_a,
                             sel_s["id"]))
                        # 선생님 배정 갱신
                        conn.execute("DELETE FROM student_teachers WHERE student_id=?", (sel_s["id"],))
                        for tid_a in selected_tids:
                            t_info = next(t for t in all_teachers if t["id"] == tid_a)
                            conn.execute(
                                "INSERT OR IGNORE INTO student_teachers (student_id, teacher_id, subject) VALUES (?,?,?)",
                                (sel_s["id"], tid_a, t_info["subject"]))
                        conn.commit()
                        conn.close()
                        t_names = ", ".join([t["name"] for t in all_teachers if t["id"] in selected_tids])
                        st.success(f"✅ {sel_s['name']} → {new_grade_a} {new_class_a}  |  선생님: {t_names or '없음'}")
                        st.rerun()

            # ── 반 일괄 배정 ──────────────────────────────────────
            with assign_tab2:
                st.caption("특정 학년·반 전체 학생을 다른 반으로 이동하고 선생님을 일괄 배정합니다.")
                conn = get_db()
                grade_class_list = conn.execute(
                    "SELECT DISTINCT grade, class_name FROM students ORDER BY grade, class_name").fetchall()
                all_teachers_b = conn.execute(
                    "SELECT * FROM teachers ORDER BY subject, name").fetchall()
                conn.close()

                if not grade_class_list:
                    st.info("등록된 학생이 없습니다.")
                else:
                    gc_opts = [f"{r['grade']} {r['class_name']}" for r in grade_class_list]
                    src = st.selectbox("대상 반 선택", gc_opts, key="bulk_src")
                    src_grade, src_class = src.split(" ", 1)

                    conn = get_db()
                    src_students = conn.execute(
                        "SELECT * FROM students WHERE grade=? AND class_name=? ORDER BY name",
                        (src_grade, src_class)).fetchall()
                    conn.close()
                    st.markdown(f"**{src} 학생 {len(src_students)}명:** {', '.join([s['name'] for s in src_students])}")
                    st.divider()

                    # 학년/반 이동
                    st.markdown("**📋 학년/반 변경** (선택 사항)")
                    col1, col2 = st.columns(2)
                    cur_g_idx = GRADE_LIST.index(src_grade) if src_grade in GRADE_LIST else 6
                    dst_grade = col1.selectbox("학년", GRADE_LIST, index=cur_g_idx, key="bulk_dst_grade")
                    dst_class = col2.selectbox("반", ["A반","B반","C반","D반","없음"],
                        index=["A반","B반","C반","D반","없음"].index(src_class) if src_class in ["A반","B반","C반","D반"] else 4,
                        key="bulk_dst_class")

                    st.divider()
                    st.markdown("**👩‍🏫 담당 선생님 일괄 배정** (선택 사항)")
                    st.caption("체크한 선생님이 이 반 전체 학생에게 배정됩니다.")

                    bulk_tids = []
                    if all_teachers_b:
                        subject_groups_b = {}
                        for t in all_teachers_b:
                            subject_groups_b.setdefault(t["subject"], []).append(t)
                        for subj, tlist in subject_groups_b.items():
                            st.markdown(f"*{subj}*")
                            tcols = st.columns(min(len(tlist), 4))
                            for i, t in enumerate(tlist):
                                checked = tcols[i % 4].checkbox(t["name"], key=f"bulk_t_{t['id']}")
                                if checked:
                                    bulk_tids.append(t["id"])
                    else:
                        st.info("등록된 선생님이 없습니다.")

                    if st.button(f"🚀 {len(src_students)}명 일괄 적용", type="primary", use_container_width=True, key="bulk_move"):
                        conn = get_db()
                        conn.execute(
                            "UPDATE students SET grade=?, class_name=?, base_grade=? WHERE grade=? AND class_name=?",
                            (dst_grade, dst_class if dst_class != "없음" else "",
                             dst_grade, src_grade, src_class))
                        # 선생님 일괄 배정
                        if bulk_tids:
                            for s in src_students:
                                conn.execute("DELETE FROM student_teachers WHERE student_id=?", (s["id"],))
                                for tid_b in bulk_tids:
                                    t_info = next(t for t in all_teachers_b if t["id"] == tid_b)
                                    conn.execute(
                                        "INSERT OR IGNORE INTO student_teachers (student_id, teacher_id, subject) VALUES (?,?,?)",
                                        (s["id"], tid_b, t_info["subject"]))
                        conn.commit()
                        conn.close()
                        t_names_b = ", ".join([t["name"] for t in all_teachers_b if t["id"] in bulk_tids])
                        st.success(f"✅ {src} → {dst_grade} {dst_class}  {len(src_students)}명 완료!" +
                                   (f"  선생님: {t_names_b}" if t_names_b else ""))
                        st.rerun()

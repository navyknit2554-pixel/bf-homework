import streamlit as st
import sqlite3
import os
import hashlib
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict
import re
import requests
import base64

from PIL import Image
from io import BytesIO

def load_local_logo(filepath):
    try:
        with open(filepath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:image/png;base64,{b64}"
    except:
        return None

sub_logo = load_local_logo("brandlogo.png")
if sub_logo:
    st.markdown(
        f"<div style='text-align:right;opacity:0.4;'>"
        f"<img src='{sub_logo}' style='height:150px;'>"
        f"</div>",
        unsafe_allow_html=True)

icon = Image.open("icon.png")

st.set_page_config(
    page_title="모두의 학습 관리",
    page_icon=icon,  # ← 이모지 대신 이미지로
    layout="wide",
    initial_sidebar_state="expanded",
)

st.set_page_config(
    page_title="패스파인더 과제 관리",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

import streamlit.components.v1 as components
components.html("""
<script>
    var metas = [
        ['theme-color', '#0E1117'],
        ['apple-mobile-web-app-capable', 'yes'],
        ['apple-mobile-web-app-status-bar-style', 'black-translucent']
    ];
    metas.forEach(function(m) {
        var tag = document.createElement('meta');
        tag.name = m[0];
        tag.content = m[1];
        document.head.appendChild(tag);
    });
</script>
""", height=0)
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
# 관리자 비번: Streamlit Secrets 우선, 없으면 기본값
try:
    SUPER_ADMIN_PASSWORD = st.secrets["admin_password"]
except:
    SUPER_ADMIN_PASSWORD = "modoohakgwan1234"

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

# ── 메인 로고 ──
main_logo = load_local_logo("logo.png")
if main_logo:
    st.markdown(
        f"<div style='text-align:center;padding:20px 0 10px 0;'>"
        f"<img src='{main_logo}' style='max-width:320px;width:70%;'>"
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
    var doc = window.parent.document;

    // ── 사이드바 메뉴 클릭 시 모바일 자동 닫기 ──
    function closeSidebar() {
        try {
            var btn = doc.querySelector('[data-testid="stSidebarCollapseButton"]');
            if (btn && window.parent.innerWidth < 768) btn.click();
        } catch(e) {}
    }
    doc.addEventListener('click', function(e) {
        var label = e.target.closest('div[data-testid="stRadio"] label');
        if (label) setTimeout(closeSidebar, 200);
    }, true);

    // ── 개발자 도구 방지 ──
    // 우클릭 비활성화
    doc.addEventListener('contextmenu', function(e) { e.preventDefault(); });

    // F12, Ctrl+Shift+I/J/C/U, Ctrl+U 차단
    doc.addEventListener('keydown', function(e) {
        if (
            e.key === 'F12' ||
            (e.ctrlKey && e.shiftKey && ['I','J','C','i','j','c'].includes(e.key)) ||
            (e.ctrlKey && ['U','u'].includes(e.key))
        ) {
            e.preventDefault();
            e.stopPropagation();
            return false;
        }
    }, true);

    // 개발자 도구 열림 감지 → 경고
    var devOpen = false;
    setInterval(function() {
        var threshold = 160;
        var widthDiff  = window.parent.outerWidth  - window.parent.innerWidth  > threshold;
        var heightDiff = window.parent.outerHeight - window.parent.innerHeight > threshold;
        if ((widthDiff || heightDiff) && !devOpen) {
            devOpen = true;
            window.parent.document.body.style.filter = 'blur(8px)';
        } else if (!widthDiff && !heightDiff && devOpen) {
            devOpen = false;
            window.parent.document.body.style.filter = '';
        }
    }, 1000);
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

                no_phone = [s for s in missing if not s["phone"] and not s["parent_phone"]]
                if no_phone:
                    st.warning(f"연락처 미등록 {len(no_phone)}명: {', '.join([s['name'] for s in no_phone])}")

                st.markdown("**📱 문자 발송**")

                def sms_btn(label, phone, msg, btn_key):
                    encoded  = urllib.parse.quote(msg)
                    sms_link = f"sms:{phone}?body={encoded}"
                    btn_style = "display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:0.82rem;text-decoration:none;"
                    return f"""<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
  <button onclick="navigator.clipboard.writeText({repr(msg)}).then(()=>{{this.innerText='✅ 복사됨';setTimeout(()=>this.innerText='📋 복사',2000)}})"
    style="{btn_style}background:#3b5bdb;color:white;">📋 복사</button>
  <a href="{sms_link}" target="_blank" style="text-decoration:none;">
    <span style="{btn_style}background:#2f9e44;color:white;display:inline-flex;">📱 {label}</span>
  </a>
</div>"""

                for s in missing:
                    msg_text = tmpl.replace("{name}", s["name"])
                    with st.container(border=True):
                        st.markdown(f"**{s['name']}**  `{s['grade']} {s['class_name']}`")
                        has_student = bool(s["phone"])
                        has_parent  = bool(s["parent_phone"])

                        if has_student:
                            st.caption(f"학생  📞 {s['phone']}")
                            st.markdown(sms_btn("학생에게 문자", s["phone"], msg_text, f"s_{s['id']}"), unsafe_allow_html=True)

                        if has_parent:
                            pname = s["parent_name"] or "학부모"
                            st.caption(f"{pname}  📞 {s['parent_phone']}")
                            st.markdown(sms_btn(f"{pname}께 문자", s["parent_phone"], msg_text, f"p_{s['id']}"), unsafe_allow_html=True)

                        if not has_student and not has_parent:
                            st.caption("⚠️ 연락처 미등록")

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

import streamlit as st
import sqlite3
import os
from datetime import datetime, date
from pathlib import Path
import base64

# ── 기본 설정 ──────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="패스파인더 과제 관리",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

ADMIN_PASSWORD = "pathfinder2024"  # 관리자 비밀번호 (변경 가능)

# ── 키젠과 동일한 해시 함수 (JS djb2 완전 동일) ──────────────────────────────
def name_to_code(name: str) -> str:
    h = 5381
    for ch in name.strip():
        h = ((h * 33) ^ ord(ch)) & 0xFFFFFFFF
    return str((h % 900000) + 100000)

def verify_code(name: str, code: str) -> bool:
    return name_to_code(name.strip()) == code.strip()

# ── CSS (키젠과 동일한 다크 테마) ───────────────────────────────────────────
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
/* ── 전체 배경 ── */
html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] {
    background-color: #0a0e1a !important;
    color: #e2e8f0 !important;
    font-family: 'Noto Sans KR', sans-serif !important;
}

/* 그리드 배경 */
[data-testid="stAppViewContainer"]::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
        linear-gradient(rgba(0,102,255,0.05) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0,102,255,0.05) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
    z-index: 0;
}

/* ── 사이드바 ── */
[data-testid="stSidebar"] {
    background-color: #0d1323 !important;
    border-right: 1px solid #1e3a5f !important;
}
[data-testid="stSidebar"] * { color: #e2e8f0 !important; }
[data-testid="stSidebar"] .stRadio label { color: #cbd5e1 !important; font-size: 0.9rem !important; }
[data-testid="stSidebar"] hr { border-color: #1e3a5f !important; }

/* ── 메인 콘텐츠 패딩 ── */
[data-testid="stMainBlockContainer"] { padding-top: 1.5rem !important; }
section.main > div { background: transparent !important; }

/* ── 텍스트 전체 ── */
h1, h2, h3, h4, h5, h6, p, span, label, div {
    color: #e2e8f0 !important;
    font-family: 'Noto Sans KR', sans-serif !important;
}
h1 { font-size: 1.6rem !important; font-weight: 700 !important; }
h2 { font-size: 1.3rem !important; font-weight: 600 !important; color: #00d4ff !important; }
h3 { font-size: 1.1rem !important; font-weight: 600 !important; }

/* ── 입력창 ── */
input, textarea, [data-baseweb="input"] input, [data-baseweb="textarea"] textarea {
    background-color: rgba(0,0,0,0.5) !important;
    border: 1px solid #1e3a5f !important;
    border-radius: 8px !important;
    color: #ffffff !important;
    font-family: 'Noto Sans KR', sans-serif !important;
}
input:focus, textarea:focus {
    border-color: #00d4ff !important;
    box-shadow: 0 0 0 3px rgba(0,212,255,0.1) !important;
}
input::placeholder, textarea::placeholder { color: #4a5568 !important; }

/* ── 셀렉트박스 ── */
[data-baseweb="select"] > div {
    background-color: rgba(0,0,0,0.5) !important;
    border: 1px solid #1e3a5f !important;
    border-radius: 8px !important;
    color: #e2e8f0 !important;
}
[data-baseweb="popover"] { background-color: #111827 !important; border: 1px solid #1e3a5f !important; }
[data-baseweb="menu"] { background-color: #111827 !important; }
[role="option"] { background-color: #111827 !important; color: #e2e8f0 !important; }
[role="option"]:hover { background-color: #1e3a5f !important; }

/* ── 버튼 ── */
button[kind="primary"], [data-testid="stBaseButton-primary"] {
    background: linear-gradient(135deg, #0066ff, #00d4ff) !important;
    border: none !important;
    color: white !important;
    border-radius: 8px !important;
    font-family: 'Noto Sans KR', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.03em !important;
    transition: opacity 0.15s !important;
}
button[kind="secondary"], [data-testid="stBaseButton-secondary"] {
    background: transparent !important;
    border: 1px solid #1e3a5f !important;
    color: #e2e8f0 !important;
    border-radius: 8px !important;
    font-family: 'Noto Sans KR', sans-serif !important;
}
button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover {
    border-color: #00d4ff !important;
    color: #00d4ff !important;
}
button { border-radius: 8px !important; }

/* ── 알림창 ── */
[data-testid="stAlert"] {
    border-radius: 10px !important;
    border: 1px solid #1e3a5f !important;
}
[data-testid="stAlert"][data-baseweb="notification"] {
    background-color: rgba(0,212,255,0.08) !important;
}
div[data-testid="stNotificationContentInfo"] {
    background-color: rgba(0,212,255,0.08) !important;
    border: 1px solid rgba(0,212,255,0.25) !important;
    border-radius: 10px !important;
}
div[data-testid="stNotificationContentSuccess"] {
    background-color: rgba(0,255,136,0.08) !important;
    border: 1px solid rgba(0,255,136,0.25) !important;
    border-radius: 10px !important;
}
div[data-testid="stNotificationContentError"] {
    background-color: rgba(255,80,80,0.08) !important;
    border: 1px solid rgba(255,80,80,0.25) !important;
    border-radius: 10px !important;
}
div[data-testid="stNotificationContentWarning"] {
    background-color: rgba(255,200,0,0.08) !important;
    border: 1px solid rgba(255,200,0,0.25) !important;
    border-radius: 10px !important;
}

/* ── 데이터프레임 ── */
[data-testid="stDataFrame"], iframe {
    border: 1px solid #1e3a5f !important;
    border-radius: 10px !important;
    background: #0d1323 !important;
}

/* ── 익스팬더 ── */
[data-testid="stExpander"] {
    background-color: #111827 !important;
    border: 1px solid #1e3a5f !important;
    border-radius: 10px !important;
    margin-bottom: 0.5rem !important;
}
[data-testid="stExpander"]:hover { border-color: #00d4ff !important; }
details summary { color: #e2e8f0 !important; }
details > div { background-color: #0d1323 !important; }

/* ── 탭 ── */
[data-testid="stTabs"] button {
    color: #64748b !important;
    border-bottom: 2px solid transparent !important;
    background: transparent !important;
    font-family: 'Noto Sans KR', sans-serif !important;
}
[data-testid="stTabs"] button[aria-selected="true"] {
    color: #00d4ff !important;
    border-bottom-color: #00d4ff !important;
}

/* ── 구분선 ── */
hr { border-color: #1e3a5f !important; }

/* ── 파일 업로더 ── */
[data-testid="stFileUploader"] {
    background: rgba(0,0,0,0.3) !important;
    border: 1px dashed #1e3a5f !important;
    border-radius: 10px !important;
}
[data-testid="stFileUploader"]:hover { border-color: #00d4ff !important; }

/* ── 메트릭 ── */
[data-testid="stMetric"] {
    background: #111827 !important;
    border: 1px solid #1e3a5f !important;
    border-radius: 10px !important;
    padding: 1rem !important;
}
[data-testid="stMetricValue"] { color: #00d4ff !important; font-family: 'Share Tech Mono', monospace !important; }
[data-testid="stMetricLabel"] { color: #64748b !important; }

/* ── 프로그레스바 ── */
[data-testid="stProgressBar"] > div {
    background-color: #1e3a5f !important;
    border-radius: 4px !important;
}
[data-testid="stProgressBar"] > div > div {
    background: linear-gradient(90deg, #0066ff, #00d4ff) !important;
}

/* ── 커스텀 컴포넌트 ── */
.main-header {
    background: linear-gradient(135deg, #0a1628 0%, #0d2347 50%, #0a1628 100%);
    border: 1px solid #1e3a5f;
    border-top: 2px solid #00d4ff;
    color: white;
    padding: 1.8rem 2rem;
    border-radius: 12px;
    margin-bottom: 1.5rem;
    text-align: center;
    position: relative;
    overflow: hidden;
}
.main-header::after {
    content: '';
    position: absolute;
    top: 0; left: -100%;
    width: 60%; height: 100%;
    background: linear-gradient(90deg, transparent, rgba(0,212,255,0.05), transparent);
    animation: shimmer 4s infinite;
}
@keyframes shimmer { to { left: 150%; } }
.main-header h1 { margin: 0; font-size: 1.7rem !important; color: white !important; }
.main-header .sub { color: #00d4ff; font-size: 0.75rem; letter-spacing: 0.3em; text-transform: uppercase; margin-bottom: 0.5rem; opacity: 0.85; }
.main-header p { margin: 0.3rem 0 0; opacity: 0.6; font-size: 0.85rem !important; color: #94a3b8 !important; }

.stat-box {
    background: #111827;
    border: 1px solid #1e3a5f;
    border-radius: 10px;
    padding: 1.2rem;
    text-align: center;
    transition: border-color 0.2s;
}
.stat-box:hover { border-color: #00d4ff; }
.stat-num { font-family: 'Share Tech Mono', monospace; font-size: 2.2rem; color: #00d4ff !important; text-shadow: 0 0 12px rgba(0,212,255,0.4); }
.stat-label { font-size: 0.78rem; color: #64748b !important; margin-top: 4px; letter-spacing: 0.05em; }

.badge-pending  { background:rgba(255,200,0,0.12); color:#fbbf24; padding:3px 12px; border-radius:12px; font-size:0.78rem; font-weight:600; border:1px solid rgba(251,191,36,0.3); }
.badge-done     { background:rgba(0,255,136,0.1);  color:#00ff88; padding:3px 12px; border-radius:12px; font-size:0.78rem; font-weight:600; border:1px solid rgba(0,255,136,0.3); }
.badge-late     { background:rgba(255,80,80,0.1);  color:#ff6b6b; padding:3px 12px; border-radius:12px; font-size:0.78rem; font-weight:600; border:1px solid rgba(255,107,107,0.3); }
.badge-checked  { background:rgba(0,212,255,0.1);  color:#00d4ff; padding:3px 12px; border-radius:12px; font-size:0.78rem; font-weight:600; border:1px solid rgba(0,212,255,0.3); }

/* ── 캡션/스몰 텍스트 ── */
small, [data-testid="stCaptionContainer"], .stCaption {
    color: #4a5568 !important;
}

/* ── 라디오버튼 ── */
[data-testid="stRadio"] label { color: #cbd5e1 !important; }

/* ── 날짜 입력 ── */
[data-baseweb="datepicker"] input { background: rgba(0,0,0,0.5) !important; }
</style>
""", unsafe_allow_html=True)

# ── DB ─────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect("homework.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
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
            created_at TEXT DEFAULT (datetime('now','localtime')),
            created_by TEXT DEFAULT '선생님'
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
    """)
    conn.commit()
    conn.close()

init_db()

# ── 유틸 ───────────────────────────────────────────────────────────────────────
def save_uploaded_file(uploaded_file, student_id, assignment_id):
    ext = Path(uploaded_file.name).suffix
    filename = f"s{student_id}_a{assignment_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
    path = UPLOAD_DIR / filename
    with open(path, "wb") as f:
        f.write(uploaded_file.read())
    return str(path)

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

# ── 세션 초기화 ────────────────────────────────────────────────────────────────
for key in ["role", "student_id", "student_name", "student_info", "pending_register"]:
    if key not in st.session_state:
        st.session_state[key] = None

# ── 헤더 ───────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <div class="sub">Pathfinder Korean Academy</div>
    <h1>📚 학생 과제 제출 프로그램</h1>
    <p>패스파인더 국어학원 · 과제 제출 & 관리 시스템</p>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# 로그인 / 첫 등록 화면
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.role is None:

    # ── 신규 학생 학년/반 등록 단계 ──────────────────────────────────────────
    if st.session_state.pending_register is not None:
        info = st.session_state.pending_register
        st.info(f"✅ **{info['name']}** 학생 확인 완료! 학년과 반을 입력해주세요. (최초 1회)")

        GRADE_OPTIONS = ["중1", "중2", "중3", "고1", "고2", "고3"]
        CLASS_OPTIONS = ["A반", "B반", "C반", "D반"]

        with st.form("register_form"):
            col1, col2 = st.columns(2)
            grade = col1.selectbox("학년", GRADE_OPTIONS)
            class_name = col2.selectbox("반", CLASS_OPTIONS)
            ok = st.form_submit_button("등록 완료 ✅", type="primary", use_container_width=True)
            if ok:
                conn = get_db()
                try:
                    conn.execute(
                        "INSERT INTO students (name, student_code, grade, class_name) VALUES (?,?,?,?)",
                        (info["name"], info["code"], grade, class_name)
                    )
                    conn.commit()
                    row = conn.execute("SELECT * FROM students WHERE student_code=?", (info["code"],)).fetchone()
                    st.session_state.role = "student"
                    st.session_state.student_id = row["id"]
                    st.session_state.student_name = row["name"]
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

    # ── 일반 로그인 화면 ──────────────────────────────────────────────────────
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 🎒 학생 로그인")
        st.caption("학번은 [학번 생성기]에서 이름으로 발급받으세요.")
        with st.form("student_login"):
            s_name = st.text_input("이름", placeholder="홍길동")
            s_code = st.text_input("학번 (6자리 숫자)", placeholder="예) 739281")
            submitted = st.form_submit_button("로그인", use_container_width=True, type="primary")

            if submitted:
                name = s_name.strip()
                code = s_code.strip()

                if not name or not code:
                    st.error("이름과 학번을 모두 입력해주세요.")
                elif not verify_code(name, code):
                    st.error("학번이 올바르지 않습니다. 학번 생성기에서 다시 확인해주세요.")
                else:
                    # 코드 일치 → DB에서 학생 조회
                    conn = get_db()
                    row = conn.execute(
                        "SELECT * FROM students WHERE name=? AND student_code=?",
                        (name, code)
                    ).fetchone()
                    conn.close()

                    if row:
                        # 기존 학생
                        st.session_state.role = "student"
                        st.session_state.student_id = row["id"]
                        st.session_state.student_name = row["name"]
                        st.session_state.student_info = dict(row)
                        st.rerun()
                    else:
                        # 신규 학생 → 학년/반 등록 단계로
                        st.session_state.pending_register = {"name": name, "code": code}
                        st.rerun()

    with col2:
        st.markdown("### 👩‍🏫 선생님 로그인")
        with st.form("admin_login"):
            pw = st.text_input("관리자 비밀번호", type="password")
            submitted2 = st.form_submit_button("로그인", use_container_width=True)
            if submitted2:
                if pw == ADMIN_PASSWORD:
                    st.session_state.role = "admin"
                    st.rerun()
                else:
                    st.error("비밀번호를 확인해 주세요.")

    st.divider()
    st.caption("💡 학번은 학번 생성기(keygen.html)에서 이름을 입력하면 자동 발급됩니다.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# 학생 페이지
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.role == "student":
    info = st.session_state.student_info
    sid = st.session_state.student_id

    with st.sidebar:
        st.markdown(f"### 👋 {info['name']} 학생")
        st.caption(f"{info['grade']} {info['class_name']} · {info['student_code']}")
        st.divider()
        page = st.radio("메뉴", ["📋 내 과제 목록", "✅ 제출 완료 목록"])
        st.divider()
        if st.button("로그아웃", use_container_width=True):
            st.session_state.role = None
            st.session_state.student_id = None
            st.rerun()

    if page == "📋 내 과제 목록":
        st.markdown("## 📋 내 과제 목록")

        conn = get_db()
        assignments = conn.execute("""
            SELECT a.*,
                   s.id AS sub_id, s.submitted_at, s.is_checked, s.teacher_comment
            FROM assignments a
            LEFT JOIN submissions s ON a.id = s.assignment_id AND s.student_id = ?
            WHERE a.grade = ? AND a.class_name = ?
            ORDER BY a.due_date ASC
        """, (sid, info["grade"], info["class_name"])).fetchall()
        conn.close()

        if not assignments:
            st.info("현재 등록된 과제가 없습니다.")
        else:
            pending = [a for a in assignments if a["sub_id"] is None]
            done    = [a for a in assignments if a["sub_id"] is not None]
            st.markdown(f"**미제출** {len(pending)}개 &nbsp;|&nbsp; **제출 완료** {len(done)}개")

            for a in assignments:
                due_str = a["due_date"] or "마감일 없음"
                is_late = False
                if a["due_date"]:
                    try:
                        is_late = date.today() > date.fromisoformat(a["due_date"]) and a["sub_id"] is None
                    except:
                        pass

                icon = '🔴' if is_late else ('🟡' if a['sub_id'] is None else '🟢')
                with st.expander(f"{icon} {a['title']}  —  마감: {due_str}"):
                    st.markdown(f"**설명:** {a['description'] or '없음'}")

                    if a["sub_id"] is None:
                        with st.form(f"submit_{a['id']}"):
                            st.markdown("##### 📤 과제 제출")
                            uploaded = st.file_uploader("사진 업로드 (jpg/png/pdf)",
                                type=["jpg","jpeg","png","pdf"], key=f"file_{a['id']}")
                            memo = st.text_area("메모 (선택)", key=f"memo_{a['id']}")
                            if st.form_submit_button("제출하기 ✅", type="primary", use_container_width=True):
                                if uploaded is None:
                                    st.error("파일을 첨부해 주세요.")
                                else:
                                    fpath = save_uploaded_file(uploaded, sid, a["id"])
                                    conn2 = get_db()
                                    try:
                                        conn2.execute(
                                            "INSERT INTO submissions (student_id, assignment_id, file_path, memo) VALUES (?,?,?,?)",
                                            (sid, a["id"], fpath, memo)
                                        )
                                        conn2.commit()
                                        st.success("제출 완료! 🎉")
                                        st.rerun()
                                    except sqlite3.IntegrityError:
                                        st.warning("이미 제출한 과제입니다.")
                                    finally:
                                        conn2.close()
                    else:
                        status_label = (
                            "<span class='badge-checked'>✔ 선생님 확인 완료</span>" if a["is_checked"]
                            else "<span class='badge-done'>📨 제출 완료 (검토 중)</span>"
                        )
                        st.markdown(f"**상태:** {status_label}", unsafe_allow_html=True)
                        st.caption(f"제출 시각: {a['submitted_at']}")
                        if a["teacher_comment"]:
                            st.info(f"💬 선생님 코멘트: {a['teacher_comment']}")

    else:
        st.markdown("## ✅ 제출 완료 목록")
        conn = get_db()
        subs = conn.execute("""
            SELECT s.*, a.title, a.due_date
            FROM submissions s
            JOIN assignments a ON s.assignment_id = a.id
            WHERE s.student_id = ?
            ORDER BY s.submitted_at DESC
        """, (sid,)).fetchall()
        conn.close()

        if not subs:
            st.info("제출한 과제가 없습니다.")
        for s in subs:
            checked = "✔ 확인 완료" if s["is_checked"] else "⏳ 검토 중"
            with st.expander(f"📄 {s['title']}  —  {checked}"):
                st.caption(f"제출: {s['submitted_at']}")
                if s["memo"]:
                    st.markdown(f"**메모:** {s['memo']}")
                if s["teacher_comment"]:
                    st.info(f"💬 선생님 코멘트: {s['teacher_comment']}")
                if s["file_path"] and os.path.exists(s["file_path"]):
                    ext = Path(s["file_path"]).suffix.lower()
                    if ext in [".jpg", ".jpeg", ".png"]:
                        st.image(s["file_path"], caption="제출 파일", use_column_width=True)
                    else:
                        st.download_button("📎 파일 다운로드", open(s["file_path"],"rb").read(),
                                           file_name=Path(s["file_path"]).name)

# ══════════════════════════════════════════════════════════════════════════════
# 관리자(선생님) 페이지
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.role == "admin":

    with st.sidebar:
        st.markdown("### 👩‍🏫 선생님 관리자")
        st.divider()
        page = st.radio("메뉴", [
            "📊 대시보드",
            "📝 과제 등록",
            "📋 과제 관리",
            "🔍 제출 현황",
            "👥 학생 관리",
        ])
        st.divider()
        if st.button("로그아웃", use_container_width=True):
            st.session_state.role = None
            st.rerun()

    if page == "📊 대시보드":
        st.markdown("## 📊 대시보드")

        conn = get_db()
        n_students    = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        n_assignments = conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
        n_submissions = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        n_checked     = conn.execute("SELECT COUNT(*) FROM submissions WHERE is_checked=1").fetchone()[0]
        conn.close()

        c1, c2, c3, c4 = st.columns(4)
        for col, num, label in [
            (c1, n_students, "전체 학생"),
            (c2, n_assignments, "등록 과제"),
            (c3, n_submissions, "제출 건수"),
            (c4, n_checked, "확인 완료"),
        ]:
            col.markdown(f"""
            <div class="stat-box">
                <div class="stat-num">{num}</div>
                <div class="stat-label">{label}</div>
            </div>
            """, unsafe_allow_html=True)

        st.divider()
        st.markdown("### 📬 최근 제출 현황")
        conn = get_db()
        recent = conn.execute("""
            SELECT s.submitted_at, st.name, st.grade, st.class_name, a.title, s.is_checked
            FROM submissions s
            JOIN students st ON s.student_id = st.id
            JOIN assignments a ON s.assignment_id = a.id
            ORDER BY s.submitted_at DESC LIMIT 15
        """).fetchall()
        conn.close()

        if recent:
            import pandas as pd
            df = pd.DataFrame([dict(r) for r in recent])
            df.columns = ["제출 시각","이름","학년","반","과제명","확인"]
            df["확인"] = df["확인"].map({1:"✔ 완료", 0:"⏳ 검토 중"})
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("아직 제출된 과제가 없습니다.")

    elif page == "📝 과제 등록":
        st.markdown("## 📝 새 과제 등록")

        with st.form("new_assignment"):
            title = st.text_input("과제 제목 *", placeholder="예) 3강 문제풀이 사진 제출")
            description = st.text_area("설명 (선택)")
            col1, col2 = st.columns(2)
            grades = get_grades() or ["중1","중2","중3","고1","고2","고3"]
            grade = col1.selectbox("학년 *", grades)
            classes = get_classes(grade) or ["A반","B반"]
            class_name = col2.selectbox("반 *", classes)
            due_date = st.date_input("마감일 (선택)", value=None)

            if st.form_submit_button("과제 등록 ✅", type="primary", use_container_width=True):
                if not title.strip():
                    st.error("제목을 입력해 주세요.")
                else:
                    conn = get_db()
                    conn.execute(
                        "INSERT INTO assignments (title, description, grade, class_name, due_date) VALUES (?,?,?,?,?)",
                        (title.strip(), description.strip(), grade, class_name,
                         str(due_date) if due_date else None)
                    )
                    conn.commit()
                    conn.close()
                    st.success(f"✅ '{title}' 과제가 {grade} {class_name}에 등록되었습니다!")

    elif page == "📋 과제 관리":
        st.markdown("## 📋 등록된 과제 목록")

        conn = get_db()
        assignments = conn.execute("""
            SELECT a.*, COUNT(s.id) AS sub_count
            FROM assignments a
            LEFT JOIN submissions s ON a.id = s.assignment_id
            GROUP BY a.id ORDER BY a.created_at DESC
        """).fetchall()
        conn.close()

        if not assignments:
            st.info("등록된 과제가 없습니다.")
        for a in assignments:
            with st.expander(f"📄 [{a['grade']} {a['class_name']}] {a['title']}  —  제출 {a['sub_count']}건"):
                st.markdown(f"**설명:** {a['description'] or '없음'}")
                st.markdown(f"**마감일:** {a['due_date'] or '없음'}")
                if st.button("🗑 삭제", key=f"del_{a['id']}"):
                    conn = get_db()
                    conn.execute("DELETE FROM assignments WHERE id=?", (a["id"],))
                    conn.execute("DELETE FROM submissions WHERE assignment_id=?", (a["id"],))
                    conn.commit()
                    conn.close()
                    st.rerun()

    elif page == "🔍 제출 현황":
        st.markdown("## 🔍 과제별 제출 현황")

        conn = get_db()
        assignments = conn.execute("SELECT * FROM assignments ORDER BY created_at DESC").fetchall()
        conn.close()

        if not assignments:
            st.info("등록된 과제가 없습니다.")
            st.stop()

        a_options = {f"[{a['grade']} {a['class_name']}] {a['title']}": a["id"] for a in assignments}
        selected = st.selectbox("과제 선택", list(a_options.keys()))
        a_id = a_options[selected]

        conn = get_db()
        sel_a = conn.execute("SELECT * FROM assignments WHERE id=?", (a_id,)).fetchone()
        all_students = conn.execute(
            "SELECT * FROM students WHERE grade=? AND class_name=? ORDER BY name",
            (sel_a["grade"], sel_a["class_name"])
        ).fetchall()
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
        st.progress(pct / 100)
        st.divider()

        for s in all_students:
            sub = sub_map.get(s["id"])
            badge = ("🟢 제출완료" + (" ✔확인" if sub and sub["is_checked"] else "")) if sub else "🔴 미제출"

            with st.expander(f"{s['name']} ({s['student_code']})  —  {badge}"):
                if sub is None:
                    st.warning("아직 제출하지 않았습니다.")
                else:
                    st.caption(f"제출 시각: {sub['submitted_at']}")
                    if sub["memo"]:
                        st.markdown(f"**학생 메모:** {sub['memo']}")
                    if sub["file_path"] and os.path.exists(sub["file_path"]):
                        ext = Path(sub["file_path"]).suffix.lower()
                        if ext in [".jpg", ".jpeg", ".png"]:
                            st.image(sub["file_path"], caption="제출 파일", width=400)
                        else:
                            st.download_button("📎 파일 다운로드",
                                open(sub["file_path"],"rb").read(),
                                file_name=Path(sub["file_path"]).name,
                                key=f"dl_{sub['id']}")

                    col1, col2 = st.columns([1,2])
                    if not sub["is_checked"]:
                        if col1.button("✔ 확인 처리", key=f"chk_{sub['id']}", type="primary"):
                            conn = get_db()
                            conn.execute(
                                "UPDATE submissions SET is_checked=1, checked_at=datetime('now','localtime') WHERE id=?",
                                (sub["id"],)
                            )
                            conn.commit()
                            conn.close()
                            st.rerun()
                    else:
                        col1.success("✔ 확인 완료")

                    with st.form(f"comment_{sub['id']}"):
                        comment = st.text_input("선생님 코멘트", value=sub["teacher_comment"] or "")
                        if st.form_submit_button("코멘트 저장"):
                            conn = get_db()
                            conn.execute("UPDATE submissions SET teacher_comment=? WHERE id=?",
                                         (comment, sub["id"]))
                            conn.commit()
                            conn.close()
                            st.rerun()

    elif page == "👥 학생 관리":
        st.markdown("## 👥 학생 관리")
        tab1, tab2 = st.tabs(["학생 목록", "학번 조회"])

        with tab1:
            conn = get_db()
            students = conn.execute("SELECT * FROM students ORDER BY grade, class_name, name").fetchall()
            conn.close()
            import pandas as pd
            if students:
                df = pd.DataFrame([dict(s) for s in students])
                df = df[["name","student_code","grade","class_name","created_at"]]
                df.columns = ["이름","학번","학년","반","등록일"]
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("등록된 학생이 없습니다. (첫 로그인 시 자동 등록됩니다)")

        with tab2:
            st.markdown("#### 이름으로 학번 확인")
            st.caption("키젠 없이도 여기서 학번 확인 가능합니다.")
            name_check = st.text_input("이름 입력", placeholder="홍길동")
            if name_check.strip():
                code = name_to_code(name_check.strip())
                st.markdown(f"**{name_check.strip()}** 의 학번: `{code}`")

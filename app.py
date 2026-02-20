import streamlit as st
import sqlite3
import os
import hashlib
from datetime import datetime, date
from pathlib import Path
import base64
from PIL import Image
import io

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

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%);
        color: white;
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
        text-align: center;
    }
    .main-header h1 { margin: 0; font-size: 1.8rem; }
    .main-header p { margin: 0.3rem 0 0; opacity: 0.85; font-size: 0.95rem; }

    .card {
        background: white;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }
    .card-title { font-weight: 700; font-size: 1rem; color: #1e3a8a; margin-bottom: 0.4rem; }
    .card-sub { color: #6b7280; font-size: 0.85rem; }

    .badge-pending  { background:#fef3c7; color:#92400e; padding:2px 10px; border-radius:12px; font-size:0.78rem; font-weight:600; }
    .badge-done     { background:#d1fae5; color:#065f46; padding:2px 10px; border-radius:12px; font-size:0.78rem; font-weight:600; }
    .badge-late     { background:#fee2e2; color:#991b1b; padding:2px 10px; border-radius:12px; font-size:0.78rem; font-weight:600; }
    .badge-checked  { background:#dbeafe; color:#1e40af; padding:2px 10px; border-radius:12px; font-size:0.78rem; font-weight:600; }

    .stat-box {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    .stat-num { font-size: 2rem; font-weight: 800; color: #1e3a8a; }
    .stat-label { font-size: 0.8rem; color: #64748b; margin-top: 2px; }

    div[data-testid="stButton"] button {
        border-radius: 8px;
    }
    .stSuccess, .stError, .stWarning, .stInfo {
        border-radius: 8px;
    }
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

    # 샘플 데이터 (최초 1회)
    if c.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0:
        sample_students = [
            ("김민준", "S001", "중1", "A반"),
            ("이서연", "S002", "중1", "A반"),
            ("박지호", "S003", "중1", "B반"),
            ("최하은", "S004", "중2", "A반"),
            ("정도윤", "S005", "중2", "B반"),
        ]
        c.executemany("INSERT INTO students (name, student_code, grade, class_name) VALUES (?,?,?,?)", sample_students)
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

def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

# ── 세션 초기화 ────────────────────────────────────────────────────────────────
if "role" not in st.session_state:
    st.session_state.role = None
if "student_id" not in st.session_state:
    st.session_state.student_id = None
if "student_name" not in st.session_state:
    st.session_state.student_name = None

# ── 헤더 ───────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>📚 패스파인더 과제 관리 시스템</h1>
    <p>패스파인더 국어학원 · 과제 제출 & 관리</p>
</div>
""", unsafe_allow_html=True)

# ── 로그인 분기 ────────────────────────────────────────────────────────────────
if st.session_state.role is None:
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 🎒 학생 로그인")
        with st.form("student_login"):
            s_name = st.text_input("이름", placeholder="홍길동")
            s_code = st.text_input("학번(코드)", placeholder="S001")
            submitted = st.form_submit_button("로그인", use_container_width=True, type="primary")
            if submitted:
                conn = get_db()
                row = conn.execute(
                    "SELECT * FROM students WHERE name=? AND student_code=?",
                    (s_name.strip(), s_code.strip().upper())
                ).fetchone()
                conn.close()
                if row:
                    st.session_state.role = "student"
                    st.session_state.student_id = row["id"]
                    st.session_state.student_name = row["name"]
                    st.session_state.student_info = dict(row)
                    st.rerun()
                else:
                    st.error("이름 또는 학번을 확인해 주세요.")

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
    st.caption("💡 학번(코드)은 선생님께 문의하세요. 샘플 계정: 김민준 / S001")
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

    # ── 과제 목록 ────────────────────────────────────────────────────────────
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

                with st.expander(f"{'🔴' if is_late else '🟡' if a['sub_id'] is None else '🟢'} {a['title']}  —  마감: {due_str}"):
                    st.markdown(f"**설명:** {a['description'] or '없음'}")
                    st.markdown(f"**대상:** {a['grade']} {a['class_name']}")

                    if a["sub_id"] is None:
                        # 제출 폼
                        with st.form(f"submit_{a['id']}"):
                            st.markdown("##### 📤 과제 제출")
                            uploaded = st.file_uploader(
                                "사진 업로드 (jpg/png/pdf)",
                                type=["jpg","jpeg","png","pdf"],
                                key=f"file_{a['id']}"
                            )
                            memo = st.text_area("메모 (선택)", placeholder="추가 설명이 있으면 입력하세요", key=f"memo_{a['id']}")
                            submit_btn = st.form_submit_button("제출하기 ✅", type="primary", use_container_width=True)

                            if submit_btn:
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
                        # 제출 완료 상태
                        status_label = (
                            "<span class='badge-checked'>✔ 선생님 확인 완료</span>" if a["is_checked"]
                            else "<span class='badge-done'>📨 제출 완료 (검토 중)</span>"
                        )
                        st.markdown(f"**상태:** {status_label}", unsafe_allow_html=True)
                        st.caption(f"제출 시각: {a['submitted_at']}")
                        if a["teacher_comment"]:
                            st.info(f"💬 선생님 코멘트: {a['teacher_comment']}")

    # ── 제출 완료 목록 ────────────────────────────────────────────────────────
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
                # 이미지 미리보기
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

    # ── 대시보드 ──────────────────────────────────────────────────────────────
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

        # 최근 제출
        st.markdown("### 📬 최근 제출 현황")
        conn = get_db()
        recent = conn.execute("""
            SELECT s.submitted_at, st.name, st.grade, st.class_name,
                   a.title, s.is_checked
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

    # ── 과제 등록 ──────────────────────────────────────────────────────────────
    elif page == "📝 과제 등록":
        st.markdown("## 📝 새 과제 등록")

        with st.form("new_assignment"):
            title = st.text_input("과제 제목 *", placeholder="예) 3강 문제풀이 사진 제출")
            description = st.text_area("설명 (선택)", placeholder="과제 내용, 주의사항 등")
            
            col1, col2 = st.columns(2)
            grades = get_grades()
            grade = col1.selectbox("학년 *", grades if grades else ["중1","중2","중3","고1","고2","고3"])
            
            # 선택된 학년의 반 목록
            classes = get_classes(grade) or ["A반","B반"]
            class_name = col2.selectbox("반 *", classes)

            due_date = st.date_input("마감일 (선택)", value=None)

            submitted = st.form_submit_button("과제 등록 ✅", type="primary", use_container_width=True)
            if submitted:
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

    # ── 과제 관리 ──────────────────────────────────────────────────────────────
    elif page == "📋 과제 관리":
        st.markdown("## 📋 등록된 과제 목록")

        conn = get_db()
        assignments = conn.execute("""
            SELECT a.*,
                   COUNT(s.id) AS sub_count
            FROM assignments a
            LEFT JOIN submissions s ON a.id = s.assignment_id
            GROUP BY a.id
            ORDER BY a.created_at DESC
        """).fetchall()
        conn.close()

        if not assignments:
            st.info("등록된 과제가 없습니다.")
        else:
            for a in assignments:
                with st.expander(f"📄 [{a['grade']} {a['class_name']}] {a['title']}  —  제출 {a['sub_count']}건"):
                    st.markdown(f"**설명:** {a['description'] or '없음'}")
                    st.markdown(f"**마감일:** {a['due_date'] or '없음'}")
                    st.markdown(f"**등록일:** {a['created_at'][:10]}")

                    col1, col2 = st.columns([1,3])
                    if col1.button("🗑 삭제", key=f"del_{a['id']}", type="secondary"):
                        conn = get_db()
                        conn.execute("DELETE FROM assignments WHERE id=?", (a["id"],))
                        conn.execute("DELETE FROM submissions WHERE assignment_id=?", (a["id"],))
                        conn.commit()
                        conn.close()
                        st.success("삭제되었습니다.")
                        st.rerun()

    # ── 제출 현황 ──────────────────────────────────────────────────────────────
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

        # 해당 과제 대상 학생 전체 + 제출 여부
        all_students = conn.execute(
            "SELECT * FROM students WHERE grade=? AND class_name=? ORDER BY name",
            (sel_a["grade"], sel_a["class_name"])
        ).fetchall()

        submissions = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id=?", (a_id,)
        ).fetchall()
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

        # 학생별 목록
        for s in all_students:
            sub = sub_map.get(s["id"])
            if sub:
                badge = "🟢 제출완료" + (" ✔확인" if sub["is_checked"] else "")
            else:
                badge = "🔴 미제출"

            with st.expander(f"{s['name']} ({s['student_code']})  —  {badge}"):
                if sub is None:
                    st.warning("아직 제출하지 않았습니다.")
                else:
                    st.caption(f"제출 시각: {sub['submitted_at']}")
                    if sub["memo"]:
                        st.markdown(f"**학생 메모:** {sub['memo']}")

                    # 파일 미리보기
                    if sub["file_path"] and os.path.exists(sub["file_path"]):
                        ext = Path(sub["file_path"]).suffix.lower()
                        if ext in [".jpg", ".jpeg", ".png"]:
                            st.image(sub["file_path"], caption="제출 파일", width=400)
                        else:
                            st.download_button("📎 파일 다운로드",
                                               open(sub["file_path"],"rb").read(),
                                               file_name=Path(sub["file_path"]).name,
                                               key=f"dl_{sub['id']}")

                    # 확인 처리
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
                            st.success("확인 처리되었습니다.")
                            st.rerun()
                    else:
                        col1.success("✔ 확인 완료")

                    # 코멘트
                    with st.form(f"comment_{sub['id']}"):
                        comment = st.text_input("선생님 코멘트", value=sub["teacher_comment"] or "",
                                                placeholder="잘했어요! / 다시 풀어오세요.")
                        if st.form_submit_button("코멘트 저장"):
                            conn = get_db()
                            conn.execute("UPDATE submissions SET teacher_comment=? WHERE id=?",
                                         (comment, sub["id"]))
                            conn.commit()
                            conn.close()
                            st.success("저장되었습니다.")
                            st.rerun()

    # ── 학생 관리 ──────────────────────────────────────────────────────────────
    elif page == "👥 학생 관리":
        st.markdown("## 👥 학생 관리")

        tab1, tab2 = st.tabs(["학생 목록", "학생 추가"])

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
                st.info("등록된 학생이 없습니다.")

        with tab2:
            with st.form("add_student"):
                st.markdown("#### 새 학생 추가")
                col1, col2 = st.columns(2)
                name = col1.text_input("이름 *")
                code = col2.text_input("학번(코드) *", placeholder="예) S006")
                col3, col4 = st.columns(2)
                grade_input = col3.text_input("학년 *", placeholder="중1")
                class_input = col4.text_input("반 *", placeholder="A반")
                
                if st.form_submit_button("학생 추가 ✅", type="primary", use_container_width=True):
                    if not all([name, code, grade_input, class_input]):
                        st.error("모든 항목을 입력해 주세요.")
                    else:
                        conn = get_db()
                        try:
                            conn.execute(
                                "INSERT INTO students (name, student_code, grade, class_name) VALUES (?,?,?,?)",
                                (name.strip(), code.strip().upper(), grade_input.strip(), class_input.strip())
                            )
                            conn.commit()
                            st.success(f"✅ {name} 학생이 추가되었습니다.")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("이미 존재하는 학번입니다.")
                        finally:
                            conn.close()

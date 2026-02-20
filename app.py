import streamlit as st
import sqlite3
import os
from datetime import datetime, date
from pathlib import Path

st.set_page_config(
    page_title="BF 국어연구소 과제 관리",
    page_icon="st.image("images/BFlogo.png", width=200)",
    layout="wide",
    initial_sidebar_state="expanded",
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
ADMIN_PASSWORD = "pathfinder2024"

def name_to_code(name: str) -> str:
    h = 5381
    for ch in name.strip():
        h = ((h * 33) ^ ord(ch)) & 0xFFFFFFFF
    return str((h % 900000) + 100000)

def verify_code(name: str, code: str) -> bool:
    return name_to_code(name.strip()) == code.strip()

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
            created_at TEXT DEFAULT (datetime('now','localtime'))
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

for key in ["role", "student_id", "student_name", "student_info", "pending_register"]:
    if key not in st.session_state:
        st.session_state[key] = None

st.title("📚 패스파인더 학생 과제 제출 프로그램")
st.caption("Pathfinder Korean Academy")
st.divider()

if st.session_state.role is None:

    if st.session_state.pending_register is not None:
        info = st.session_state.pending_register
        st.info(f"✅ {info['name']} 학생 확인! 학년과 반을 입력해주세요. (최초 1회)")
        GRADE_OPTIONS = ["중1", "중2", "중3", "고1", "고2", "고3"]
        CLASS_OPTIONS = ["A반", "B반", "C반", "D반"]
        with st.form("register_form"):
            col1, col2 = st.columns(2)
            grade = col1.selectbox("학년", GRADE_OPTIONS)
            class_name = col2.selectbox("반", CLASS_OPTIONS)
            if st.form_submit_button("등록 완료 ✅", type="primary", use_container_width=True):
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

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("🎒 학생 로그인")
        st.caption("학번은 학번 생성기에서 이름으로 발급받으세요.")
        with st.form("student_login"):
            s_name = st.text_input("이름", placeholder="홍길동")
            s_code = st.text_input("학번 (6자리 숫자)", placeholder="예) 739281")
            if st.form_submit_button("로그인", use_container_width=True, type="primary"):
                name = s_name.strip()
                code = s_code.strip()
                if not name or not code:
                    st.error("이름과 학번을 모두 입력해주세요.")
                elif not verify_code(name, code):
                    st.error("학번이 올바르지 않습니다. 학번 생성기에서 다시 확인해주세요.")
                else:
                    conn = get_db()
                    row = conn.execute(
                        "SELECT * FROM students WHERE name=? AND student_code=?",
                        (name, code)
                    ).fetchone()
                    conn.close()
                    if row:
                        st.session_state.role = "student"
                        st.session_state.student_id = row["id"]
                        st.session_state.student_name = row["name"]
                        st.session_state.student_info = dict(row)
                        st.rerun()
                    else:
                        st.session_state.pending_register = {"name": name, "code": code}
                        st.rerun()

    with col2:
        st.subheader("👩‍🏫 선생님 로그인")
        with st.form("admin_login"):
            pw = st.text_input("관리자 비밀번호", type="password")
            if st.form_submit_button("로그인", use_container_width=True):
                if pw == ADMIN_PASSWORD:
                    st.session_state.role = "admin"
                    st.rerun()
                else:
                    st.error("비밀번호를 확인해 주세요.")
    st.stop()

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
            st.rerun()

    if page == "📋 내 과제 목록":
        st.subheader("📋 내 과제 목록")
        conn = get_db()
        assignments = conn.execute("""
            SELECT a.*, s.id AS sub_id, s.submitted_at, s.is_checked, s.teacher_comment
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
            done = [a for a in assignments if a["sub_id"] is not None]
            st.caption(f"미제출 {len(pending)}개  |  제출 완료 {len(done)}개")

            for a in assignments:
                due_str = a["due_date"] or "마감일 없음"
                is_late = False
                if a["due_date"]:
                    try:
                        is_late = date.today() > date.fromisoformat(a["due_date"]) and a["sub_id"] is None
                    except:
                        pass
                icon = "🔴" if is_late else ("🟡" if a["sub_id"] is None else "🟢")
                with st.expander(f"{icon} {a['title']}  —  마감: {due_str}"):
                    st.write(f"**설명:** {a['description'] or '없음'}")
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
                        if a["is_checked"]:
                            st.success("✔ 선생님 확인 완료")
                        else:
                            st.info("📨 제출 완료 (검토 중)")
                        st.caption(f"제출 시각: {a['submitted_at']}")
                        if a["teacher_comment"]:
                            st.info(f"💬 선생님 코멘트: {a['teacher_comment']}")

    else:
        st.subheader("✅ 제출 완료 목록")
        conn = get_db()
        subs = conn.execute("""
            SELECT s.*, a.title FROM submissions s
            JOIN assignments a ON s.assignment_id = a.id
            WHERE s.student_id = ? ORDER BY s.submitted_at DESC
        """, (sid,)).fetchall()
        conn.close()
        if not subs:
            st.info("제출한 과제가 없습니다.")
        for s in subs:
            checked = "✔ 확인 완료" if s["is_checked"] else "⏳ 검토 중"
            with st.expander(f"📄 {s['title']}  —  {checked}"):
                st.caption(f"제출: {s['submitted_at']}")
                if s["memo"]:
                    st.write(f"**메모:** {s['memo']}")
                if s["teacher_comment"]:
                    st.info(f"💬 선생님 코멘트: {s['teacher_comment']}")
                if s["file_path"] and os.path.exists(s["file_path"]):
                    ext = Path(s["file_path"]).suffix.lower()
                    if ext in [".jpg", ".jpeg", ".png"]:
                        st.image(s["file_path"], use_column_width=True)
                    else:
                        st.download_button("📎 파일 다운로드",
                            open(s["file_path"],"rb").read(),
                            file_name=Path(s["file_path"]).name)

elif st.session_state.role == "admin":
    with st.sidebar:
        st.markdown("### 👩‍🏫 선생님")
        st.divider()
        page = st.radio("메뉴", ["📊 대시보드","📝 과제 등록","📋 과제 관리","🔍 제출 현황","👥 학생 관리"])
        st.divider()
        if st.button("로그아웃", use_container_width=True):
            st.session_state.role = None
            st.rerun()

    if page == "📊 대시보드":
        st.subheader("📊 대시보드")
        conn = get_db()
        n_students    = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        n_assignments = conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
        n_submissions = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        n_checked     = conn.execute("SELECT COUNT(*) FROM submissions WHERE is_checked=1").fetchone()[0]
        conn.close()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("전체 학생", n_students)
        c2.metric("등록 과제", n_assignments)
        c3.metric("제출 건수", n_submissions)
        c4.metric("확인 완료", n_checked)
        st.divider()
        st.subheader("📬 최근 제출 현황")
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
        st.subheader("📝 새 과제 등록")
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
        st.subheader("📋 등록된 과제 목록")
        conn = get_db()
        assignments = conn.execute("""
            SELECT a.*, COUNT(s.id) AS sub_count FROM assignments a
            LEFT JOIN submissions s ON a.id = s.assignment_id
            GROUP BY a.id ORDER BY a.created_at DESC
        """).fetchall()
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
        st.subheader("🔍 과제별 제출 현황")
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
        done = len([s for s in all_students if s["id"] in sub_map])
        pct = int(done/total*100) if total else 0
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
                        st.write(f"**학생 메모:** {sub['memo']}")
                    if sub["file_path"] and os.path.exists(sub["file_path"]):
                        ext = Path(sub["file_path"]).suffix.lower()
                        if ext in [".jpg", ".jpeg", ".png"]:
                            st.image(sub["file_path"], width=400)
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
        st.subheader("👥 학생 관리")
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
                st.info("등록된 학생이 없습니다. (첫 로그인 시 자동 등록)")
        with tab2:
            st.markdown("#### 이름으로 학번 확인")
            name_check = st.text_input("이름 입력", placeholder="홍길동")
            if name_check.strip():
                code = name_to_code(name_check.strip())
                st.success(f"{name_check.strip()} 의 학번: **{code}**")

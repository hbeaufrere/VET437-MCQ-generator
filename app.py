import os
import json
import re
import random
import sqlite3
import hashlib
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, render_template, request, jsonify, session, g
from dotenv import load_dotenv
import anthropic
import pdfplumber
from pptx import Presentation

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or "dev-secret-change-me"

COURSE_MATERIALS = Path(__file__).parent / "course_materials"
COURSE_MATERIALS.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "pptx", "ppt", "txt"}

DATABASE = Path(__file__).parent / "mcq.db"

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DATABASE))
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(str(DATABASE))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            file_hash TEXT NOT NULL UNIQUE,
            text_content TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS quiz_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            questions_json TEXT NOT NULL,
            score INTEGER,
            total INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.close()


init_db()

# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------

def extract_text_pdf(filepath):
    text_parts = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n\n".join(text_parts)


def extract_text_pptx(filepath):
    prs = Presentation(filepath)
    text_parts = []
    for slide in prs.slides:
        slide_texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    line = paragraph.text.strip()
                    if line:
                        slide_texts.append(line)
        if slide_texts:
            text_parts.append("\n".join(slide_texts))
    return "\n\n".join(text_parts)


def extract_text_txt(filepath):
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def extract_text(filepath):
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        return extract_text_pdf(filepath)
    elif ext in (".pptx", ".ppt"):
        return extract_text_pptx(filepath)
    elif ext == ".txt":
        return extract_text_txt(filepath)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def file_hash(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

# ---------------------------------------------------------------------------
# Auto-seed: import all files from course_materials/ on startup
# ---------------------------------------------------------------------------

def seed_course_materials():
    """Scan course_materials/ and import any new files into the database."""
    db = sqlite3.connect(str(DATABASE))
    db.row_factory = sqlite3.Row
    count = 0

    for filepath in sorted(COURSE_MATERIALS.iterdir()):
        if filepath.is_file() and filepath.suffix.lower().lstrip(".") in ALLOWED_EXTENSIONS:
            try:
                fhash = file_hash(str(filepath))
                # Skip if already imported
                existing = db.execute(
                    "SELECT id FROM documents WHERE file_hash = ?", (fhash,)
                ).fetchone()
                if existing:
                    continue

                text = extract_text(str(filepath))
                if not text.strip():
                    print(f"  Skipped {filepath.name} (no text extracted)")
                    continue

                db.execute(
                    "INSERT INTO documents (filename, file_hash, text_content) VALUES (?, ?, ?)",
                    (filepath.name, fhash, text),
                )
                db.commit()
                count += 1
                print(f"  Imported: {filepath.name}")
            except Exception as e:
                print(f"  Error importing {filepath.name}: {e}")

    db.close()
    if count:
        print(f"Seeded {count} new document(s) from course_materials/")
    else:
        print("No new documents to import from course_materials/")


seed_course_materials()

# ---------------------------------------------------------------------------
# Quiz cache – serves recently generated quizzes to avoid redundant API calls
# ---------------------------------------------------------------------------

CACHE_TTL = 30 * 60          # 30 minutes
CACHE_POOL_SIZE = 5          # keep up to 5 quiz variations per parameter combo

_quiz_cache = {}             # {cache_key: [(questions, cost, timestamp), ...]}
_cache_lock = threading.Lock()


def _cache_key(num_questions, topic_focus):
    return (num_questions, topic_focus or "")


def cache_get(num_questions, topic_focus, exclude_hashes=None):
    """Return a random cached quiz that the student hasn't seen, or None."""
    key = _cache_key(num_questions, topic_focus)
    now = time.time()
    exclude_hashes = exclude_hashes or set()

    with _cache_lock:
        entries = _quiz_cache.get(key, [])
        # Purge expired
        entries = [(q, c, t) for q, c, t in entries if now - t < CACHE_TTL]
        _quiz_cache[key] = entries

        # Filter out quizzes this student already saw
        unseen = [(q, c, t) for q, c, t in entries
                  if _quiz_hash(q) not in exclude_hashes]

        if unseen:
            q, c, _ = random.choice(unseen)
            return q, c
    return None


def cache_put(num_questions, topic_focus, questions, cost):
    """Add a quiz to the pool, evicting oldest if pool is full."""
    key = _cache_key(num_questions, topic_focus)
    now = time.time()

    with _cache_lock:
        entries = _quiz_cache.setdefault(key, [])
        # Purge expired
        entries[:] = [(q, c, t) for q, c, t in entries if now - t < CACHE_TTL]
        # Evict oldest if at capacity
        if len(entries) >= CACHE_POOL_SIZE:
            entries.sort(key=lambda x: x[2])
            entries.pop(0)
        entries.append((questions, cost, now))


def _quiz_hash(questions):
    """Quick hash of a quiz for dedup purposes."""
    return hashlib.md5(json.dumps(questions, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Async task store – generation runs in background, frontend polls for result
# ---------------------------------------------------------------------------

_tasks = {}        # {task_id: {"status": ..., "result": ..., "error": ...}}
_tasks_lock = threading.Lock()
TASK_TTL = 10 * 60  # clean up completed tasks after 10 minutes


def _purge_old_tasks():
    now = time.time()
    with _tasks_lock:
        expired = [tid for tid, t in _tasks.items()
                   if t.get("completed_at") and now - t["completed_at"] > TASK_TTL]
        for tid in expired:
            del _tasks[tid]


# ---------------------------------------------------------------------------
# Claude API – MCQ generation
# ---------------------------------------------------------------------------

def sanitize_text(text):
    """Remove control characters and null bytes that break the API."""
    # Remove null bytes and other control chars (keep newlines, tabs, carriage returns)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Replace surrogate pairs / invalid unicode
    text = text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
    return text


def generate_mcqs(text_content, num_questions=10, topic_focus=None):
    """Call Claude to generate MCQs from document text."""
    client = anthropic.Anthropic(timeout=240.0)

    focus_instruction = ""
    if topic_focus:
        focus_instruction = f"\nFocus the questions specifically on: {topic_focus}"

    # Sanitize text to remove control characters from PDF extraction
    text_content = sanitize_text(text_content)

    # Truncate very long documents to fit context
    max_chars = 80_000
    if len(text_content) > max_chars:
        text_content = text_content[:max_chars] + "\n\n[Content truncated...]"

    prompt = f"""You are an expert veterinary science educator specializing in avian and reptile medicine (birds — including psittacines, raptors, poultry, waterfowl, and passerines — and reptiles — including lizards, snakes, chelonians/turtles, and crocodilians). You are creating exam-style multiple choice questions (MCQs) for veterinary students.

Based on the following lecture/course material on bird and reptile medicine, generate exactly {num_questions} high-quality MCQs. All questions must be relevant to avian and/or reptile medicine.{focus_instruction}

Question type distribution (approximate):
- 25% Recall/knowledge questions: straightforward factual recall (e.g., anatomy, normal physiology, definitions)
- 50% Clinical scenario questions: present a patient case or clinical situation and ask for the best diagnosis, treatment, or next step
- 25% Comparative/species-differentiation questions: highlight differences between species or taxa (e.g., "Which species is the exception...", "How does X differ between snakes and lizards?", "How does X differ between psittacines and raptors?")

Requirements for each question:
- Write a clear, specific question stem
- Provide exactly 4 answer options labeled A, B, C, D
- Exactly one option must be correct
- Include plausible distractors that test understanding, not just recall
- After the correct answer, provide a brief explanation (2-3 sentences) of WHY the correct answer is right and why key distractors are wrong
- Do NOT include questions about specific drug dosages, drug doses, or numerical blood/lab values (e.g., no "What is the normal blood glucose range..." or "What dose of meloxicam...")

Return your response as a JSON array with this exact structure:
[
  {{
    "question": "The question text",
    "options": {{
      "A": "First option",
      "B": "Second option",
      "C": "Third option",
      "D": "Fourth option"
    }},
    "correct_answer": "A",
    "explanation": "Explanation of why A is correct and why other options are incorrect."
  }}
]

Return ONLY the JSON array, no other text.

--- COURSE MATERIAL ---
{text_content}
"""

    message_params = dict(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    last_error = None
    for attempt in range(3):
        message = client.messages.create(**message_params)

        # Calculate cost (Sonnet 4.6: $3/M input, $15/M output)
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        cost = (input_tokens / 1_000_000) * 3.0 + (output_tokens / 1_000_000) * 15.0

        response_text = message.content[0].text.strip()

        # Extract the JSON array, tolerating any preamble or ```json fence.
        start = response_text.find("[")
        end = response_text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(response_text[start:end + 1]), cost
            except json.JSONDecodeError as e:
                last_error = e
                print(f"MCQ parse attempt {attempt + 1}/3 failed: {e}; "
                      f"stop_reason={message.stop_reason}; tail={response_text[-160:]!r}")
        else:
            last_error = json.JSONDecodeError("No JSON array in response", response_text or "", 0)
            print(f"MCQ parse attempt {attempt + 1}/3: no array found; "
                  f"stop_reason={message.stop_reason}; head={response_text[:160]!r}")

    raise last_error

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    db = get_db()
    doc_count = db.execute("SELECT COUNT(*) as cnt FROM documents").fetchone()["cnt"]
    return render_template("index.html", doc_count=doc_count)


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Start MCQ generation – returns immediately with a task_id to poll."""
    data = request.get_json()
    num_questions = min(int(data.get("num_questions", 10)), 15)
    topic_focus = (data.get("topic_focus") or "").strip() or None

    db = get_db()
    rows = db.execute("SELECT text_content, filename FROM documents").fetchall()

    if not rows:
        return jsonify({"error": "No course materials loaded. Place files in course_materials/ and restart."}), 400

    # Track which quizzes this student already saw (via session cookie)
    seen = set(session.get("seen_quizzes", []))

    # Try serving a cached quiz the student hasn't seen yet
    cached = cache_get(num_questions, topic_focus, exclude_hashes=seen)
    if cached:
        questions, cost = cached
        qh = _quiz_hash(questions)
        seen.add(qh)
        session["seen_quizzes"] = list(seen)[-20:]  # keep last 20

        _save_quiz_session(questions)
        return jsonify({"questions": questions, "cost": round(cost, 4), "cached": True})

    # No usable cache entry – kick off generation in background thread
    # Build the combined text now (while we have the DB rows)
    max_total = 80_000
    budget_per_doc = max_total // max(len(rows), 1)
    chunk_size = 1500

    parts = []
    for row in rows:
        doc_text = row['text_content']
        if len(doc_text) <= budget_per_doc:
            parts.append(f"--- {row['filename']} ---\n{doc_text}")
        else:
            chunks = [doc_text[i:i + chunk_size]
                      for i in range(0, len(doc_text), chunk_size)]
            num_chunks = max(budget_per_doc // chunk_size, 1)
            sampled = random.sample(chunks, min(num_chunks, len(chunks)))
            sampled_text = "\n[...]\n".join(sampled)
            parts.append(f"--- {row['filename']} (sampled excerpts) ---\n{sampled_text}")
    combined_text = "\n\n".join(parts)

    task_id = uuid.uuid4().hex
    with _tasks_lock:
        _tasks[task_id] = {"status": "pending"}

    def _run_generation():
        try:
            questions, cost = generate_mcqs(combined_text, num_questions, topic_focus)
            cache_put(num_questions, topic_focus, questions, cost)
            _save_quiz_session(questions)
            with _tasks_lock:
                _tasks[task_id] = {
                    "status": "done",
                    "questions": questions,
                    "cost": round(cost, 4),
                    "quiz_hash": _quiz_hash(questions),
                    "completed_at": time.time(),
                }
        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            with _tasks_lock:
                _tasks[task_id] = {"status": "error", "error": "Failed to parse generated questions. Please try again.", "completed_at": time.time()}
        except anthropic.APIError as e:
            with _tasks_lock:
                _tasks[task_id] = {"status": "error", "error": f"AI service error: {e.message}", "completed_at": time.time()}
        except Exception as e:
            print(f"Unexpected error: {type(e).__name__}: {e}")
            with _tasks_lock:
                _tasks[task_id] = {"status": "error", "error": str(e), "completed_at": time.time()}

    _purge_old_tasks()
    threading.Thread(target=_run_generation, daemon=True).start()

    return jsonify({"task_id": task_id})


@app.route("/api/generate/status/<task_id>")
def api_generate_status(task_id):
    """Poll for generation result."""
    with _tasks_lock:
        task = _tasks.get(task_id)

    if not task:
        return jsonify({"error": "Unknown task"}), 404

    if task["status"] == "pending":
        return jsonify({"status": "pending"})

    if task["status"] == "error":
        return jsonify({"status": "error", "error": task["error"]}), 500

    # Done – track this quiz as seen by this student
    seen = set(session.get("seen_quizzes", []))
    seen.add(task["quiz_hash"])
    session["seen_quizzes"] = list(seen)[-20:]

    return jsonify({
        "status": "done",
        "questions": task["questions"],
        "cost": task["cost"],
        "cached": False,
    })


def _save_quiz_session(questions):
    """Save quiz to DB (works outside request context)."""
    db = sqlite3.connect(str(DATABASE))
    db.execute(
        "INSERT INTO quiz_sessions (questions_json, total) VALUES (?, ?)",
        (json.dumps(questions), len(questions)),
    )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Canvas LTI / Embedding support
# ---------------------------------------------------------------------------

@app.after_request
def allow_iframe_embedding(response):
    """Allow the app to be embedded in Canvas iframes."""
    response.headers.pop("X-Frame-Options", None)
    response.headers["Content-Security-Policy"] = "frame-ancestors *"
    return response


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

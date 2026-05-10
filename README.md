# VET437 MCQ Generator

An AI-powered multiple choice question generator for veterinary students taking VET437 (bird and reptile medicine). Place your lecture PDFs, PowerPoint slides, or text notes in the `course_materials/` folder and students can generate practice exam questions on-the-fly using Claude.

## Features

- **Auto-loaded materials** — drop files into `course_materials/` and they're imported on startup
- **AI-generated MCQs** — Claude analyzes all course materials and creates exam-style questions on avian and reptile medicine
- **Instant feedback** — correct answer and explanation shown after each question
- **Topic focus** — optionally narrow questions to a specific topic
- **Full review** — review all questions with answers and explanations after completing a quiz
- **Canvas-ready** — embeddable in Canvas LMS as an external link or iframe

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/hbeaufrere/VET437-MCQ-generator.git
cd VET437-MCQ-generator
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Add course materials

Place your lecture files in the `course_materials/` folder:

```
course_materials/
  lecture01.pdf
  lecture02.pptx
  notes.txt
```

Supported formats: PDF, PPTX, TXT

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` and add your Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-...
SECRET_KEY=some-random-string
```

### 4. Run locally

```bash
python app.py
```

Open http://localhost:5000 in your browser. Documents are automatically imported from `course_materials/` on startup.

## Adding New Materials

Drop new files into `course_materials/` and restart the server. Already-imported files are detected by hash and skipped.

## Deploy to Render

1. Push this repo to GitHub (with your files in `course_materials/`)
2. Create a new **Web Service** on [Render](https://render.com)
3. Connect your GitHub repo
4. Set the environment variable `ANTHROPIC_API_KEY`
5. Deploy — Render will auto-detect the `render.yaml` config

## Canvas LMS Integration

### Option A: External URL (simplest)

1. Deploy the app (e.g., to Render)
2. In Canvas, go to your course → Modules
3. Add an **External URL** item with the deployed app URL
4. Students click the link to access the quiz generator

### Option B: Embed in a Page

1. In Canvas, create or edit a Page
2. Switch to HTML editor
3. Add: `<iframe src="https://your-app-url.onrender.com/" width="100%" height="800" frameborder="0"></iframe>`

## How It Works

1. **Instructor places** lecture materials (PDF/PPTX/TXT) in `course_materials/`
2. On startup, text is extracted and stored in a local SQLite database
3. **Students choose** number of questions and optional topic focus
4. The app sends all course text to **Claude** with a structured prompt
5. Claude generates MCQs with correct answers and explanations focused on avian and reptile medicine
6. Students answer questions and see **instant feedback with explanations**
7. A final score and full review with all answers are shown at the end

## Tech Stack

- **Backend**: Python / Flask
- **AI**: Anthropic Claude API
- **Document parsing**: pdfplumber (PDF), python-pptx (PowerPoint)
- **Database**: SQLite
- **Frontend**: Vanilla HTML/CSS/JS
- **Deployment**: Gunicorn / Render

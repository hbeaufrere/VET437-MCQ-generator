let questions = [];
let quizCost = 0;
let currentIndex = 0;
let userAnswers = {};
let revealed = {};

async function fetchJSON(url, opts) {
    const resp = await fetch(url, opts);
    const text = await resp.text();
    let data;
    try {
        data = JSON.parse(text);
    } catch (e) {
        const snippet = text.slice(0, 300).replace(/\s+/g, " ");
        throw new Error(`Server returned non-JSON (HTTP ${resp.status}). Body starts with: ${snippet}`);
    }
    if (!resp.ok) {
        throw new Error(data.error || `HTTP ${resp.status}`);
    }
    return data;
}

async function pollForResult(taskId) {
    const maxAttempts = 120;  // up to ~4 minutes with 2s intervals
    for (let i = 0; i < maxAttempts; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const data = await fetchJSON(`/api/generate/status/${taskId}`);
        if (data.status === "done") return data;
        if (data.status === "error") throw new Error(data.error || "Generation failed.");
        // still pending – keep polling
    }
    throw new Error("Generation timed out. Please try again.");
}

function escapeHTML(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// ----- Setup & Generation -----

const setupForm = document.getElementById("quizSetupForm");
if (setupForm) {
    setupForm.addEventListener("submit", async (e) => {
        e.preventDefault();

        const numQuestions = parseInt(document.getElementById("numQuestions").value);
        const topicFocus = document.getElementById("topicFocus").value.trim();

        document.getElementById("setupPanel").style.display = "none";
        document.getElementById("loadingPanel").style.display = "block";

        try {
            const resp = await fetch("/api/generate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    num_questions: numQuestions,
                    topic_focus: topicFocus || null,
                }),
            });

            const data = await resp.json();

            if (!resp.ok) {
                throw new Error(data.error || "Generation failed.");
            }

            // If response has questions directly (cache hit), use them
            if (data.questions) {
                questions = data.questions;
                quizCost = data.cost;
                currentIndex = 0;
                userAnswers = {};
                revealed = {};
                startQuiz();
                return;
            }

            // Otherwise poll for async generation result
            const taskId = data.task_id;
            const result = await pollForResult(taskId);
            questions = result.questions;
            quizCost = result.cost;
            currentIndex = 0;
            userAnswers = {};
            revealed = {};
            startQuiz();
        } catch (err) {
            alert("Error: " + err.message);
            document.getElementById("loadingPanel").style.display = "none";
            document.getElementById("setupPanel").style.display = "block";
        }
    });
}

// ----- Quiz Logic -----

function startQuiz() {
    document.getElementById("loadingPanel").style.display = "none";
    document.getElementById("quizPanel").style.display = "block";
    document.getElementById("totalQ").textContent = questions.length;

    // Show cost and disclaimer
    const infoDiv = document.getElementById("quizInfo");
    if (infoDiv) {
        infoDiv.innerHTML =
            `<p class="disclaimer">AI-generated questions may be inaccurate. If you are unsure about the validity of an answer or confused, just email Dr. Beaufrère at <a href="mailto:hbeaufrere@ucdavis.edu">hbeaufrere@ucdavis.edu</a></p>`;
        infoDiv.style.display = "block";
    }

    renderQuestion();
}

function renderQuestion() {
    const q = questions[currentIndex];
    document.getElementById("currentQ").textContent = currentIndex + 1;
    document.getElementById("questionNumber").textContent = `Question ${currentIndex + 1}`;
    document.getElementById("questionText").textContent = q.question;

    // Progress bar
    const pct = ((currentIndex + 1) / questions.length) * 100;
    document.getElementById("progressBar").style.width = pct + "%";

    // Options
    const optionsList = document.getElementById("optionsList");
    optionsList.innerHTML = "";

    const isRevealed = revealed[currentIndex];

    for (const [letter, text] of Object.entries(q.options)) {
        const btn = document.createElement("button");
        btn.className = "option-btn";
        btn.innerHTML = `<span class="option-letter">${escapeHTML(letter)}</span><span class="option-text">${escapeHTML(text)}</span>`;

        if (isRevealed) {
            btn.classList.add("disabled");
            if (letter === q.correct_answer) {
                btn.classList.add("correct");
            } else if (letter === userAnswers[currentIndex]) {
                btn.classList.add("incorrect");
            }
        } else if (userAnswers[currentIndex] === letter) {
            btn.classList.add("selected");
        }

        if (!isRevealed) {
            btn.addEventListener("click", () => selectOption(letter));
        }

        optionsList.appendChild(btn);
    }

    // Explanation
    const expBox = document.getElementById("explanationBox");
    if (isRevealed) {
        document.getElementById("explanationText").textContent = q.explanation;
        expBox.style.display = "block";
    } else {
        expBox.style.display = "none";
    }

    // Navigation buttons
    document.getElementById("prevBtn").style.display = currentIndex > 0 ? "inline-block" : "none";

    const isLast = currentIndex === questions.length - 1;
    document.getElementById("nextBtn").style.display = isLast ? "none" : "inline-block";
    document.getElementById("finishBtn").style.display = isLast ? "inline-block" : "none";
}

function selectOption(letter) {
    userAnswers[currentIndex] = letter;
    revealed[currentIndex] = true;
    renderQuestion();
}

function nextQuestion() {
    if (currentIndex < questions.length - 1) {
        currentIndex++;
        renderQuestion();
        window.scrollTo({ top: 0, behavior: "smooth" });
    }
}

function prevQuestion() {
    if (currentIndex > 0) {
        currentIndex--;
        renderQuestion();
        window.scrollTo({ top: 0, behavior: "smooth" });
    }
}

// ----- Results -----

function showResults() {
    document.getElementById("quizPanel").style.display = "none";
    document.getElementById("resultsPanel").style.display = "block";

    let correct = 0;
    questions.forEach((q, i) => {
        if (userAnswers[i] === q.correct_answer) correct++;
    });

    document.getElementById("scoreNumber").textContent = correct;
    document.getElementById("scoreDenom").textContent = questions.length;
    document.getElementById("scorePercent").textContent =
        Math.round((correct / questions.length) * 100) + "%";

    // Review each question
    const reviewSection = document.getElementById("reviewSection");
    reviewSection.innerHTML = "<h3>Review All Questions</h3>";

    questions.forEach((q, i) => {
        const isCorrect = userAnswers[i] === q.correct_answer;
        const div = document.createElement("div");
        div.className = `review-item ${isCorrect ? "review-correct" : "review-incorrect"}`;

        let html = `<div class="review-question">${i + 1}. ${escapeHTML(q.question)}</div>`;

        if (!isCorrect && userAnswers[i]) {
            html += `<div class="review-answer your-answer">Your answer: ${escapeHTML(userAnswers[i])}. ${escapeHTML(q.options[userAnswers[i]])}</div>`;
        } else if (!userAnswers[i]) {
            html += `<div class="review-answer your-answer">Not answered</div>`;
        }

        html += `<div class="review-answer correct-answer">Correct answer: ${escapeHTML(q.correct_answer)}. ${escapeHTML(q.options[q.correct_answer])}</div>`;
        html += `<div class="review-explanation">${escapeHTML(q.explanation)}</div>`;

        div.innerHTML = html;
        reviewSection.appendChild(div);
    });

    window.scrollTo({ top: 0, behavior: "smooth" });
}

function downloadPDF() {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    const pageWidth = doc.internal.pageSize.getWidth();
    const margin = 15;
    const maxWidth = pageWidth - margin * 2;
    let y = 20;

    function checkPage(needed) {
        if (y + needed > doc.internal.pageSize.getHeight() - 15) {
            doc.addPage();
            y = 20;
        }
    }

    // Title
    doc.setFontSize(18);
    doc.setFont("helvetica", "bold");
    doc.text("VET437 MCQ Practice - Results", margin, y);
    y += 10;

    // Score
    let correct = 0;
    questions.forEach((q, i) => {
        if (userAnswers[i] === q.correct_answer) correct++;
    });
    doc.setFontSize(14);
    doc.setFont("helvetica", "normal");
    doc.text(`Score: ${correct} / ${questions.length} (${Math.round((correct / questions.length) * 100)}%)`, margin, y);
    y += 12;

    // Questions
    doc.setFontSize(11);
    questions.forEach((q, i) => {
        checkPage(50);

        // Question number and text
        doc.setFont("helvetica", "bold");
        const qLines = doc.splitTextToSize(`${i + 1}. ${q.question}`, maxWidth);
        doc.text(qLines, margin, y);
        y += qLines.length * 5 + 2;

        // Options
        doc.setFont("helvetica", "normal");
        for (const [letter, text] of Object.entries(q.options)) {
            checkPage(8);
            let prefix = "  ";
            if (letter === q.correct_answer) prefix = "[correct] ";
            else if (letter === userAnswers[i] && userAnswers[i] !== q.correct_answer) prefix = "[wrong] ";
            const optLines = doc.splitTextToSize(`${prefix}${letter}. ${text}`, maxWidth - 5);
            doc.text(optLines, margin + 3, y);
            y += optLines.length * 5;
        }
        y += 2;

        // Your answer vs correct
        checkPage(12);
        const isCorrect = userAnswers[i] === q.correct_answer;
        doc.setFont("helvetica", "bold");
        if (!userAnswers[i]) {
            doc.text("Not answered", margin + 3, y);
        } else if (!isCorrect) {
            doc.text(`Your answer: ${userAnswers[i]}. ${q.options[userAnswers[i]]}`, margin + 3, y);
        }
        if (!isCorrect) y += 6;
        doc.text(`Correct answer: ${q.correct_answer}. ${q.options[q.correct_answer]}`, margin + 3, y);
        y += 6;

        // Explanation
        checkPage(15);
        doc.setFont("helvetica", "italic");
        doc.setFontSize(10);
        const expLines = doc.splitTextToSize(`Explanation: ${q.explanation}`, maxWidth - 5);
        doc.text(expLines, margin + 3, y);
        y += expLines.length * 4.5 + 8;
        doc.setFontSize(11);
    });

    doc.save("VET437_quiz_results.pdf");
}

function resetQuiz() {
    questions = [];
    currentIndex = 0;
    userAnswers = {};
    revealed = {};

    document.getElementById("resultsPanel").style.display = "none";
    document.getElementById("quizPanel").style.display = "none";
    document.getElementById("setupPanel").style.display = "block";
}

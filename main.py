from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import requests, random, html, string, secrets, typing

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# OpenTDB categories (you provided these)
CATEGORIES = {
    "general": 9,
    "tech": 18,
    "movies": 11
}

# In-memory rooms storage for multiplayer (room_code -> dict)
ROOMS: typing.Dict[str, dict] = {}

# Helper: fetch 10 multiple choice questions for category key
def fetch_questions(category_key: str, amount: int = 10):
    cat_id = CATEGORIES.get(category_key, CATEGORIES["general"])
    url = f"https://opentdb.com/api.php?amount={amount}&category={cat_id}&type=multiple"
    try:
        resp = requests.get(url, timeout=8)
        data = resp.json()
    except Exception:
        data = {}
    questions = []
    if data.get("results"):
        for item in data["results"]:
            qtext = html.unescape(item.get("question", ""))
            correct = html.unescape(item.get("correct_answer", ""))
            incorrect = [html.unescape(i) for i in item.get("incorrect_answers", [])]
            options = incorrect + [correct]
            random.shuffle(options)
            questions.append({
                "question": qtext,
                "answer": correct,
                "options": options
            })
    return questions

def gen_room_code(n=6):
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(n))

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

@app.get("/contact", response_class=HTMLResponse)
async def contact(request: Request):
    return templates.TemplateResponse("contact.html", {"request": request})

# ---- SINGLE PLAYER ----
@app.get("/single_player_quiz", response_class=HTMLResponse)
async def single_quiz_start(request: Request, domain: str = "general"):
    # domain param expected: 'general', 'tech', 'movies'
    if domain not in CATEGORIES: domain = "general"
    questions = fetch_questions(domain)
    # Ensure we always have at most 10 questions and pass count (could be zero if API fails)
    total_questions = len(questions)
    return templates.TemplateResponse("single_player_quiz.html", {
        "request": request,
        "questions": questions,
        "domain": domain,
        "total_questions": total_questions
    })

@app.post("/single_player_results", response_class=HTMLResponse)
async def single_player_results(request: Request):
    form = await request.form()
    # the page will have q0..q9 fields (value: question||correct||selected)
    # Also we passed hidden total_questions to know expected total count
    total_questions = int(form.get("total_questions") or 0)
    answers = []
    for key in sorted([k for k in form.keys() if k.startswith("q")]):
        val = form.get(key)
        if not val:
            continue
        try:
            qtext, correct, selected = val.split("||")
        except Exception:
            qtext = val; correct = ""; selected = ""
        answers.append({
            "question": qtext,
            "correct": correct,
            "selected": selected,
            "is_correct": (selected == correct)
        })
    # compute score based on answers captured; unanswered count = total_questions - len(answers)
    score = sum(1 for a in answers if a["is_correct"])
    # show full total_questions (so result shows 10 even if user left blanks)
    return templates.TemplateResponse("single_player_results.html", {
        "request": request,
        "results": answers,
        "score": score,
        "total": total_questions
    })

# ---- MULTIPLAYER LOBBY (create/join) ----
@app.get("/lobby", response_class=HTMLResponse)
async def lobby(request: Request):
    return templates.TemplateResponse("lobby.html", {"request": request})

@app.post("/create_room", response_class=HTMLResponse)
async def create_room(request: Request, domain: str = Form("general")):
    if domain not in CATEGORIES: domain = "general"
    code = gen_room_code(6)
    questions = fetch_questions(domain)
    # Room structure:
    ROOMS[code] = {
        "domain": domain,
        "questions": questions,
        "players": {},        # username -> {"answers": {...}, "submitted": bool}
        "created": True
    }
    # Redirect to join page so host can copy code or start
    return RedirectResponse(url=f"/room/{code}", status_code=303)

@app.get("/room/{code}", response_class=HTMLResponse)
async def room_join_page(request: Request, code: str):
    room = ROOMS.get(code)
    if not room:
        # invalid code
        return templates.TemplateResponse("room_join.html", {"request": request, "error": "Invalid room code"})
    return templates.TemplateResponse("room_join.html", {
        "request": request,
        "code": code,
        "domain": room["domain"],
        "question_count": len(room["questions"])
    })

@app.post("/room/{code}/join", response_class=HTMLResponse)
async def room_join(request: Request, code: str, username: str = Form(...)):
    room = ROOMS.get(code)
    if not room:
        return templates.TemplateResponse("room_join.html", {"request": request, "error": "Invalid room code"})
    # ensure username unique in room
    uname = username.strip() or "Player"
    if uname in room["players"]:
        uname = uname + "_" + ''.join(secrets.choice(string.digits) for _ in range(3))
    # add player placeholder
    room["players"][uname] = {"answers": {}, "submitted": False}
    # Redirect player to quiz page
    return RedirectResponse(url=f"/room/{code}/quiz?username={uname}", status_code=303)

@app.get("/room/{code}/quiz", response_class=HTMLResponse)
async def room_quiz(request: Request, code: str, username: str):
    room = ROOMS.get(code)
    if not room:
        return templates.TemplateResponse("room_join.html", {"request": request, "error": "Invalid room code"})
    if username not in room["players"]:
        # If user not present, redirect to join page
        return RedirectResponse(url=f"/room/{code}", status_code=303)
    questions = room["questions"]
    total_questions = len(questions)
    return templates.TemplateResponse("room_quiz.html", {
        "request": request,
        "code": code,
        "username": username,
        "questions": questions,
        "total_questions": total_questions
    })

@app.post("/room/{code}/submit", response_class=HTMLResponse)
async def room_submit(request: Request, code: str):
    form = await request.form()
    username = form.get("username")
    room = ROOMS.get(code)
    if not room:
        return templates.TemplateResponse("room_join.html", {"request": request, "error": "Invalid room code"})
    if username not in room["players"]:
        # invalid player
        return RedirectResponse(url=f"/room/{code}", status_code=303)
    # collect q* answers
    answers = {}
    for key in [k for k in form.keys() if k.startswith("q")]:
        val = form.get(key)
        if not val:
            continue
        try:
            qtext, correct, selected = val.split("||")
        except Exception:
            qtext = val; correct = ""; selected = ""
        answers[key] = {"question": qtext, "correct": correct, "selected": selected, "is_correct": (selected == correct)}
    room["players"][username]["answers"] = answers
    room["players"][username]["submitted"] = True
    # After submitting, redirect to room results page
    return RedirectResponse(url=f"/room/{code}/results?username={username}", status_code=303)

@app.get("/room/{code}/results", response_class=HTMLResponse)
async def room_results(request: Request, code: str, username: str = None):
    room = ROOMS.get(code)
    if not room:
        return templates.TemplateResponse("room_join.html", {"request": request, "error": "Invalid room code"})
    # build aggregated leaderboard and details
    players = []
    for uname, info in room["players"].items():
        ansmap = info.get("answers", {})
        score = sum(1 for a in ansmap.values() if a.get("is_correct"))
        total_q = len(room["questions"])
        players.append({"username": uname, "score": score, "submitted": info.get("submitted", False)})
    # sort leaderboard
    players_sorted = sorted(players, key=lambda x: x["score"], reverse=True)
    # If username param provided, get that player's detailed answers
    user_details = room["players"].get(username) if username else None
    detailed = []
    if user_details:
        # convert answers dict to ordered list by q index
        for i in range(len(room["questions"])):
            key = f"q{i}"
            a = user_details["answers"].get(key)
            if a:
                detailed.append(a)
            else:
                # unanswered placeholder
                qtext = room["questions"][i]["question"] if i < len(room["questions"]) else ""
                detailed.append({"question": qtext, "correct": room["questions"][i]["answer"] if i < len(room["questions"]) else "", "selected": "", "is_correct": False})
    return templates.TemplateResponse("room_results.html", {
        "request": request,
        "code": code,
        "players": players_sorted,
        "details": detailed,
        "username": username,
        "total_questions": len(room["questions"])
    })

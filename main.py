# main.py
from fastapi import FastAPI, HTTPException, Depends, Query, Header, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import pandas as pd
import random
import os
import requests
import json
from io import BytesIO

DEFAULT_XLSX_URL = "https://dst-de.s3.eu-west-3.amazonaws.com/fastapi_en/questions_en.xlsx"
USERS = {"alice": "wonderland", "bob": "builder", "clementine": "mandarine"}
ADMIN_PASSWORD = "4dm1N"

app = FastAPI(
    title="Questionnaire API",
    description="Return randomized MCQs filtered by use & subject. Basic auth required.",
    version="0.1",
)


# -----------------------------
# Data loading utility
# -----------------------------
def load_questions_from_path_or_url(path_or_url: Optional[str] = None) -> List[Dict[str, Any]]:
    path_or_url = path_or_url or os.getenv("QUESTIONS_PATH") or "questions_en.xlsx"
    df = None

    def _read_excel_bytes(content_bytes):
        return pd.read_excel(BytesIO(content_bytes), engine="openpyxl")

    try:
        if str(path_or_url).lower().startswith("http"):
            r = requests.get(path_or_url, timeout=20)
            r.raise_for_status()
            df = _read_excel_bytes(r.content)
        elif os.path.exists(path_or_url):
            df = pd.read_excel(path_or_url, engine="openpyxl")
        else:
            r = requests.get(DEFAULT_XLSX_URL, timeout=20)
            r.raise_for_status()
            df = _read_excel_bytes(r.content)
    except Exception as e:
        raise RuntimeError(f"Failed to load questions file from '{path_or_url}' or default URL: {e}")

    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.fillna("")

    questions = []
    for i, row in df.iterrows():
        q = {
            "qid": int(i) + 1,
            "question": str(row.get("question", "")),
            "subject": str(row.get("subject", "")),
            "correct": str(row.get("correct", "")),
            "use": str(row.get("use", "")),
            "responseA": str(row.get("responsea", "")),
            "responseB": str(row.get("responseb", "")),
            "responseC": str(row.get("responsec", "")),
            "responseD": str(row.get("responsed", "")),
        }
        questions.append(q)

    return questions


try:
    QUESTIONS_DB: List[Dict[str, Any]] = load_questions_from_path_or_url()
except RuntimeError as e:
    QUESTIONS_DB = []
    print(f"[Warning] Could not load questions at startup: {e}")


# -----------------------------
# Authentication dependency
# -----------------------------
def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    if not authorization.startswith("Basic "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization must start with 'Basic '")

    creds = authorization[len("Basic "):].strip()
    if ":" not in creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credential format, expected username:password")

    username, password = creds.split(":", 1)

    if password == ADMIN_PASSWORD:
        return {"username": username, "is_admin": True}

    expected = USERS.get(username)
    if expected and expected == password:
        return {"username": username, "is_admin": False}

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")


# -----------------------------
# Pydantic models
# -----------------------------
class QuestionIn(BaseModel):
    question: str
    subject: str
    correct: str
    use: str
    responseA: Optional[str] = None
    responseB: Optional[str] = None
    responseC: Optional[str] = None
    responseD: Optional[str] = None


class QuestionOut(QuestionIn):
    qid: int


# -----------------------------
# Utility: pretty JSON response
# -----------------------------
def pretty_json(content: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        content=json.loads(json.dumps(content, indent=2)),
        status_code=status_code
    )


# -----------------------------
# Endpoints
# -----------------------------
@app.get("/health", tags=["misc"])
def health():
    return pretty_json({"status": "ok"})


@app.get("/questions", tags=["questions"])
def get_questions(
    use: str = Query(..., description="Test type / use (e.g. 'exam', 'training')"),
    subject: List[str] = Query(..., description="One or more subject categories"),
    count: int = Query(5, description="Number of questions requested (allowed: 5, 10, 20)"),
    user: Dict[str, Any] = Depends(get_current_user),
):
    if count not in (5, 10, 20):
        raise HTTPException(status_code=400, detail="count must be one of 5, 10 or 20")

    normalized_subjects = []
    for s in subject:
        normalized_subjects.extend([part.strip() for part in s.split(",") if part.strip()])
    normalized_subjects = [s.lower() for s in normalized_subjects]

    matches = [
        q for q in QUESTIONS_DB
        if (q.get("use", "").strip().lower() == use.strip().lower())
        and (q.get("subject", "").strip().lower() in normalized_subjects)
    ]

    if not matches:
        raise HTTPException(status_code=404, detail=f"No questions found for use='{use}' and subjects={normalized_subjects}")

    if len(matches) < count:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough questions available ({len(matches)}) for the requested count {count}.",
        )

    selected = random.sample(matches, count)
    random.shuffle(selected)
    return pretty_json(selected)


@app.post("/questions", status_code=201, tags=["questions"])
def create_question(new_question: QuestionIn, user: Dict[str, Any] = Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin privileges required to create a question")

    max_id = max((q["qid"] for q in QUESTIONS_DB), default=0)
    created = new_question.dict()
    created["qid"] = max_id + 1
    QUESTIONS_DB.append(created)

    persist_path = os.getenv("QUESTIONS_PATH", "questions_en.xlsx")
    try:
        if os.path.exists(persist_path):
            df = pd.DataFrame(
                [
                    {
                        "question": q.get("question", ""),
                        "subject": q.get("subject", ""),
                        "correct": q.get("correct", ""),
                        "use": q.get("use", ""),
                        "responseA": q.get("responseA", ""),
                        "responseB": q.get("responseB", ""),
                        "responseC": q.get("responseC", ""),
                        "responseD": q.get("responseD", ""),
                    }
                    for q in QUESTIONS_DB
                ]
            )
            df.to_excel(persist_path, index=False)
    except Exception:
        pass

    return pretty_json(created, status_code=201)


@app.get("/", tags=["misc"])
def root():
    return pretty_json({
        "message": "Questionnaire API is running. See /docs for interactive API docs.",
        "docs": "/docs",
        "openapi": "/openapi.json",
    })
